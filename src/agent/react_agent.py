"""ReAct Agent 实现。

核心循环: 用户输入 → 检索知识库 → 检索记忆 → LLM 思考 → 选择工具 → 执行 → 观察结果 → 继续思考 → ... → 最终回答 → 存储记忆

基于 OpenAI Function Calling 实现工具调用，比纯 Prompt 解析更可靠。

上下文组装通过 ContextBuilder 实现 Zone 分层：
- System Zone: system prompt（稳定前缀，缓存友好）
- Environment Zone: 运行时环境信息（当前时间等，每次请求动态生成）
- Inject Zone: KB/长期记忆（按需临时注入，不污染对话历史）
- History Zone: 对话消息（动态）

循环控制：
- LoopDetector 检测重复工具调用模式，提前中断无限重试
- 检测到循环时自动插入引导 prompt，让 LLM 换种方式回答
"""

from __future__ import annotations

import re
import time
from typing import Any, Callable, TYPE_CHECKING

from typing_extensions import override

from src.agent.base_agent import BaseAgent, OnEventCallback, WaitForConfirmation
from src.agent.events import AgentEvent, AgentStoppedError, EventType
from src.agent.loop_detector import LoopDetector
from src.agent.metrics import RunMetrics
from src.agent.tool_executor import ToolExecutorMixin
from src.config import settings
from src.context.builder import ContextBuilder
from src.environment.adapter_base import EnvironmentAdapter
from src.llm.base_client import BaseLLMClient, Message, Role
from src.memory.conversation import ConversationMemory
from src.memory.conversation_archive import ConversationArchive
from src.memory.session_summary import SessionSummary
from src.memory.vector_store import VectorStore
from src.observability import get_tracer
from src.observability.instruments import (
    record_agent_run_metrics, trace_span,
    set_span_content, set_span_distances,
)
from src.tools.base_tool import ToolRegistry
from src.utils.logger import logger

if TYPE_CHECKING:
    from opentelemetry.trace import Span
    from src.rag.knowledge_base import KnowledgeBase
    from src.skills.router import SkillRouter

_tracer = get_tracer(__name__)


class ReActAgent(BaseAgent, ToolExecutorMixin):
    """ReAct (Reasoning + Acting) Agent。

    通过 LLM 的 Function Calling 能力，自主决定何时调用工具、
    调用哪个工具、以什么参数调用，并根据工具返回结果继续推理，
    直到得出最终答案。

    支持：
    - 长期记忆：每次对话前检索相关记忆，通过 ContextBuilder 临时注入
    - 知识库预检索：每次对话前自动检索知识库，通过 ContextBuilder 临时注入
    - 可观测性：每次 run() 生成 RunMetrics，记录迭代/工具/耗时等指标
    - 循环控制：LoopDetector 检测重复工具调用，提前中断无效重试
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        tool_registry: ToolRegistry,
        memory: ConversationMemory,
        context_builder: ContextBuilder,
        vector_store: VectorStore | None = None,
        conversation_archive: ConversationArchive | None = None,
        session_summary: SessionSummary | None = None,
        knowledge_base: KnowledgeBase | None = None,
        skill_router: SkillRouter | None = None,
        env_adapter: EnvironmentAdapter | None = None,
        max_iterations: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        super().__init__(llm_client, tool_registry, memory)
        self._context_builder: ContextBuilder = context_builder
        self._vector_store: VectorStore | None = vector_store
        self._conversation_archive: ConversationArchive | None = conversation_archive
        self._session_summary: SessionSummary | None = session_summary
        self._knowledge_base: KnowledgeBase | None = knowledge_base
        self._skill_router: SkillRouter | None = skill_router
        self._env_adapter: EnvironmentAdapter | None = env_adapter
        self._max_iterations: int = max_iterations or settings.agent.max_iterations
        self._temperature: float = temperature or settings.agent.temperature
        self._max_tokens: int = max_tokens or settings.agent.max_tokens
        self._last_metrics: RunMetrics | None = None
        self._loop_detector: LoopDetector = LoopDetector()

    @property
    def context_builder(self) -> ContextBuilder:
        """暴露 ContextBuilder 实例，供外部获取 build 统计信息。"""
        return self._context_builder

    @property
    def last_metrics(self) -> RunMetrics | None:
        """获取最近一次 run() 的运行指标。"""
        return self._last_metrics

    @override
    def run(
        self,
        user_input: str,
        on_event: OnEventCallback = None,
        wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """处理用户输入，执行 ReAct 循环直到得到最终回答。

        如果外部通过 on_event 回调抛出 AgentStoppedError，
        将在迭代间的安全点中断，执行清理后重新抛出。
        """
        metrics = RunMetrics(max_iterations=self._max_iterations)
        self._loop_detector.reset()

        def _emit(event: AgentEvent) -> None:
            """安全地发送事件。AgentStoppedError 不被吞掉，直接向上传播。"""
            if on_event:
                try:
                    on_event(event)
                except AgentStoppedError:
                    raise
                except Exception as e:
                    logger.warning("事件回调异常: {}", e)

        with trace_span(_tracer, "agent.run", {"agent.max_iterations": self._max_iterations}) as span:
            set_span_content(span, "agent.input", user_input)
            try:
                result = self._run_loop(
                    user_input, metrics, _emit,
                    wait_for_confirmation=wait_for_confirmation,
                )
                set_span_content(span, "agent.output", result)
                self._set_metrics_on_span(span, metrics)
                return result
            except AgentStoppedError:
                logger.info("用户停止了 Agent 运行 | 迭代: {}", metrics.iterations)
                self._context_builder.clear_injections()
                metrics.finish()
                self._last_metrics = metrics
                logger.info("运行指标（用户中断） | {}", metrics.summary())
                self._set_metrics_on_span(span, metrics, stopped=True)
                raise

    @staticmethod
    def _set_metrics_on_span(span: Span, metrics: RunMetrics, stopped: bool = False) -> None:
        """将 RunMetrics 批量写入 span attributes。"""
        span.set_attribute("agent.iterations", metrics.iterations)
        span.set_attribute("agent.llm_calls", metrics.llm_call_count)
        span.set_attribute("agent.total_input_tokens", metrics.total_input_tokens)
        span.set_attribute("agent.total_output_tokens", metrics.total_output_tokens)
        span.set_attribute("agent.total_tokens", metrics.total_tokens)
        span.set_attribute("agent.tool_calls", metrics.tool_call_count)
        span.set_attribute("agent.tool_failures", metrics.tool_failure_count)
        span.set_attribute("agent.hit_max_iterations", metrics.hit_max_iterations)
        span.set_attribute("agent.loop_detected", metrics.loop_detected)
        span.set_attribute("agent.duration_ms", round(metrics.duration_ms, 1))
        span.set_attribute("agent.stopped", stopped)
        if metrics.kb_chunks_injected:
            span.set_attribute("agent.kb_chunks_injected", metrics.kb_chunks_injected)
        if metrics.memory_items_injected:
            span.set_attribute("agent.memory_items_injected", metrics.memory_items_injected)

        # 记录 agent run metrics
        record_agent_run_metrics(
            duration_ms=metrics.duration_ms,
            hit_max_iterations=metrics.hit_max_iterations,
        )

    def _run_loop(
        self, user_input: str, metrics: RunMetrics,
        _emit: Callable[[AgentEvent], None],
        wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """ReAct 核心循环，从 run() 中分离以便统一异常处理。"""
        # 1. 检索知识库，通过 ContextBuilder 临时注入（不写入 ConversationMemory）
        self._inject_knowledge(user_input, metrics)
        # 2. 检索长期记忆，通过 ContextBuilder 临时注入
        self._inject_long_term_memory(user_input, metrics)
        # 3. 匹配并注入 Skills（领域专家 prompt）
        self._inject_skills(user_input)
        # 4. 检索对话归档，通过 ContextBuilder 临时注入
        self._inject_archive(user_input)

        # 5. 用户消息写入对话历史（这是真正应该持久化的）
        self._memory.add_user_message(user_input)

        # 6. 检查是否需要压缩 History Zone（同步阻塞）
        self._check_and_compress(_emit)

        # 7. 自动归档被挤出 Recent Window 的 Q&A 交互
        self._auto_archive_evicted()

        # 8. 注入 Session Summary（当前会话概要）
        self._inject_session_summary()

        tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None

        # 将 tools schema 的 token 占用纳入上下文预算，避免 messages + tools 超限
        self._context_builder.set_tools_reserve(tools_schema)

        for iteration in range(1, self._max_iterations + 1):
            metrics.iterations = iteration
            logger.info("ReAct 迭代 [{}/{}]", iteration, self._max_iterations)

            _emit(AgentEvent(
                type=EventType.THINKING,
                iteration=iteration,
                max_iterations=self._max_iterations,
            ))

            # 通过 ContextBuilder 组装完整上下文（System + Inject + History）
            context_messages = self._context_builder.build(self._memory.messages)

            # 调用 LLM
            response = self._llm.chat(
                messages=context_messages,
                tools=tools_schema,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )
            metrics.record_llm_call(response.usage, call_type="chat")

            # 情况1: LLM 直接给出最终回答（没有 tool_calls）
            if not response.tool_calls:
                self._memory.add_assistant_message(response)
                logger.info("Agent 给出最终回答")

                _emit(AgentEvent(
                    type=EventType.ANSWERING,
                    iteration=iteration,
                    max_iterations=self._max_iterations,
                ))

                answer = response.content or ""
                self._store_to_long_term_memory(user_input, answer, metrics)
                # 9. 交互完成后更新 Session Summary
                self._post_interaction_update(user_input, answer, metrics)
                self._context_builder.clear_injections()
                metrics.finish()
                self._last_metrics = metrics
                logger.info("运行指标 | {}", metrics.summary())
                return answer

            # 情况2: LLM 决定调用工具
            self._memory.add_assistant_message(response)
            self.execute_tool_calls(response.tool_calls, metrics, _emit, wait_for_confirmation)

            # 循环检测：如果检测到重复调用模式，插入引导 prompt
            loop_hint = self._loop_detector.get_loop_summary()
            if loop_hint:
                metrics.loop_detected = True
                logger.warning("循环检测触发，插入引导 prompt")
                self._memory.add_message(
                    Message(role=Role.USER, content=loop_hint)
                )

        # 达到最大迭代次数，强制让 LLM 总结
        metrics.hit_max_iterations = True
        logger.warning("达到最大迭代次数 {}，强制总结", self._max_iterations)

        _emit(AgentEvent(
            type=EventType.MAX_ITERATIONS,
            iteration=self._max_iterations,
            max_iterations=self._max_iterations,
        ))

        answer = self._force_final_answer(metrics)
        self._store_to_long_term_memory(user_input, answer, metrics)
        # 达到最大迭代也更新 Session Summary
        self._post_interaction_update(user_input, answer, metrics)
        self._context_builder.clear_injections()
        metrics.finish()
        self._last_metrics = metrics
        logger.info("运行指标 | {}", metrics.summary())
        return answer

    def _inject_knowledge(self, query: str, metrics: RunMetrics) -> None:
        """检索知识库，通过 ContextBuilder 临时注入上下文。

        仅当检索结果与 query 的 cosine distance 低于阈值时才注入，
        避免不相关的知识片段浪费 token。
        检索 distance 分数记录到当前 Span，便于可观测和阈值调优。
        """
        if not self._knowledge_base or self._knowledge_base.count() == 0:
            self._context_builder.set_knowledge([])
            return

        with trace_span(_tracer, "rag.knowledge_search", {"rag.type": "knowledge_base"}) as span:
            threshold = settings.agent.kb_relevance_threshold
            results = self._knowledge_base.search(query, top_k=3, relevance_threshold=threshold)
            self._context_builder.set_knowledge(results)

            # 记录检索 distance 到 Span（含被过滤掉的候选，用于阈值调优）
            all_candidates = self._knowledge_base.search(query, top_k=3, relevance_threshold=2.0)
            set_span_distances(
                "kb.distances", all_candidates, threshold, injected_count=len(results),
            )

            span.set_attribute("rag.threshold", threshold)
            span.set_attribute("rag.candidates", len(all_candidates))
            span.set_attribute("rag.injected", len(results))

            if results:
                metrics.kb_chunks_injected = len(results)
                logger.info("注入 {} 条知识库片段（threshold={}）", len(results), threshold)

    def _inject_long_term_memory(self, query: str, metrics: RunMetrics) -> None:
        """检索长期记忆，通过 ContextBuilder 临时注入上下文。

        仅当检索结果与 query 的 cosine distance 低于阈值时才注入，
        避免不相关的记忆浪费 token。
        检索 distance 分数记录到当前 Span，便于可观测和阈值调优。

        命中的记忆会异步更新 hit_count 和 last_hit（供 Governor 评估价值）。
        """
        if not self._vector_store or self._vector_store.count() == 0:
            self._context_builder.set_memory([])
            return

        with trace_span(_tracer, "rag.memory_search", {"rag.type": "long_term_memory"}) as span:
            threshold = settings.agent.memory_relevance_threshold
            results = self._vector_store.search(query, top_k=3)
            self._context_builder.set_memory(results, relevance_threshold=threshold)

            # 记录检索 distance 到 Span（全部候选，含被过滤的）
            set_span_distances(
                "memory.distances", results, threshold,
                injected_count=len([r for r in results if r.get("distance", 1.0) < threshold]),
            )

            relevant = [r for r in results if r.get("distance", 1.0) < threshold]
            span.set_attribute("rag.threshold", threshold)
            span.set_attribute("rag.candidates", len(results))
            span.set_attribute("rag.injected", len(relevant))

            if relevant:
                metrics.memory_items_injected = len(relevant)
                logger.info("注入 {} 条长期记忆（threshold={}）", len(relevant), threshold)

                # 异步 hit writeback：更新命中记忆的 hit_count 和 last_hit
                if settings.agent.memory_governor_enabled:
                    self._writeback_memory_hits(relevant)

    def _writeback_memory_hits(self, relevant_memories: list[dict[str, Any]]) -> None:
        """异步更新命中记忆的 hit_count 和 last_hit。

        使用后台线程执行，不阻塞主请求链路。
        """
        if not self._vector_store:
            return

        import threading
        store = self._vector_store

        def _do_writeback():
            now = time.time()
            for mem in relevant_memories:
                mem_id = mem.get("id")
                if not mem_id:
                    continue
                meta = mem.get("metadata", {})
                store.update_metadata(mem_id, {
                    "hit_count": meta.get("hit_count", 0) + 1,
                    "last_hit": now,
                })

        threading.Thread(
            target=_do_writeback,
            name="memory-hit-writeback",
            daemon=True,
        ).start()

    def _inject_skills(self, user_input: str) -> None:
        """根据用户意图匹配 Skills，通过 ContextBuilder 临时注入领域专家 prompt。

        Skills 在每次对话开始时匹配一次，注入后在整个 ReAct 循环中持续生效。
        """
        if not self._skill_router:
            self._context_builder.set_skills([])
            return

        matches = self._skill_router.match(user_input)
        if matches:
            skills = [m.skill for m in matches]
            self._context_builder.set_skills(skills)
            skill_names = [f"{m.skill.display_name}({m.score:.2f})" for m in matches]
            logger.info("激活 Skills: {}", ", ".join(skill_names))
        else:
            self._context_builder.set_skills([])

    def _inject_archive(self, query: str) -> None:
        """检索对话归档，通过 ContextBuilder 临时注入历史交互摘要。

        从 ConversationArchive 中按语义检索与当前查询相关的历史交互片段，
        注入 Archive Zone。与长期记忆（关键事实）互补——归档保留完整交互上下文。
        """
        if not self._conversation_archive or self._conversation_archive.count() == 0:
            self._context_builder.set_archive([])
            return

        threshold = settings.agent.archive_relevance_threshold
        top_k = settings.agent.archive_top_k
        results = self._conversation_archive.search(query, top_k=top_k)
        self._context_builder.set_archive(results, relevance_threshold=threshold)

        relevant = [r for r in results if r.get("distance", 1.0) < threshold]
        if relevant:
            logger.info("注入 {} 条对话归档片段（threshold={}）", len(relevant), threshold)

    def _auto_archive_evicted(self) -> None:
        """自动归档被挤出 Recent Window 的 Q&A 交互。

        将 archive_watermark 之后、Recent Window 之前的消息配对归档到
        ConversationArchive，实现消息三级生命周期的第二级：
            Recent Window（完整）→ 自动归档（摘要化存入向量库）

        归档粒度为完整的 Q&A 交互对（user + assistant），
        跳过工具调用中间过程（assistant(tool_calls) + tool）。

        依赖 SessionSummary.archive_watermark 追踪已归档进度，
        避免重复归档。
        """
        if not self._conversation_archive or not self._session_summary:
            return

        messages = self._memory.messages
        system_count = self._memory.system_prompt_count
        recent_window = settings.agent.recent_window_size

        # 可归档区域：system_prompt 之后、Recent Window 之前
        non_system_msgs = messages[system_count:]
        if len(non_system_msgs) <= recent_window:
            return  # 所有消息都在 Recent Window 内

        # 可归档区域的结束边界（Recent Window 之前）
        archive_end = len(non_system_msgs) - recent_window
        # 已归档水位线（相对于 system_prompt 之后的消息）
        watermark = self._session_summary.archive_watermark

        if watermark >= archive_end:
            return  # 无新的可归档消息

        # 收集待归档的 Q&A 交互对
        new_msgs = non_system_msgs[watermark:archive_end]
        interaction_pairs = []
        current_pair: list[Message] = []

        for msg in new_msgs:
            if msg.role == Role.USER:
                # 新交互开始：保存上一对
                if current_pair:
                    interaction_pairs.append(current_pair)
                current_pair = [msg]
            elif msg.role == Role.ASSISTANT and not msg.tool_calls:
                # 最终回答（非工具调用），加入当前对
                current_pair.append(msg)
                interaction_pairs.append(current_pair)
                current_pair = []
            # 跳过 tool_calls 和 tool result 消息

        # 逐对归档
        archived = 0
        for pair in interaction_pairs:
            try:
                self._conversation_archive.archive(pair)
                archived += 1
            except Exception as e:
                logger.warning("自动归档失败: {}", e)

        # 更新水位线
        self._session_summary.archive_watermark = archive_end
        if archived > 0:
            logger.info("自动归档 {} 个 Q&A 交互 | watermark: {} → {}",
                        archived, watermark, archive_end)

    def _inject_session_summary(self) -> None:
        """注入 Session Summary 到 ContextBuilder。

        如果存在有效的会话概要，通过 ContextBuilder.set_session_summary()
        注入到 History Zone 头部，使 LLM 在长对话中保持全局视野。
        """
        if not self._session_summary or not self._session_summary.summary:
            return
        self._context_builder.set_session_summary(self._session_summary.summary)

    def _post_interaction_update(
        self, user_input: str, answer: str, metrics: RunMetrics,
    ) -> None:
        """交互完成后更新 Session Summary。

        在每次 Agent 给出最终回答后调用：
        1. 记录交互计数
        2. 检查是否达到更新间隔（每 N 轮）
        3. 达到间隔时提取近期交互文本，调用 LLM 增量更新 summary

        LLM 调用失败时静默降级（保留旧 summary），不阻塞对话流。
        """
        if not self._session_summary:
            return

        self._session_summary.record_interaction()

        if not self._session_summary.should_update():
            return

        # 提取自上次更新以来的交互文本
        messages = self._memory.messages
        system_count = self._memory.system_prompt_count
        # 从 archive_watermark 开始提取（归档之后的部分 + 当前 Recent Window）
        start_index = system_count + self._session_summary.archive_watermark
        recent_text = SessionSummary.extract_recent_interactions(
            messages, system_count, start_index,
        )

        if not recent_text.strip():
            return

        logger.info("触发 Session Summary 增量更新 | interaction_count={}",
                    self._session_summary.interaction_count)
        self._session_summary.update(self._llm, recent_text)
        # 记录 summary 更新的 LLM 调用（不在 RunMetrics 中独立记录，
        # 因为 SessionSummary.update 内部的 LLM 调用不影响主链路统计）

    def _check_and_compress(self, _emit: Callable[[AgentEvent], None]) -> None:
        """检查 History Zone 是否超过水位线，需要时同步触发压缩。

        在 ReAct 循环开始前调用。使用 ContextBuilder.estimate_compression_needed()
        估算动态预算，超过阈值时调用 ConversationMemory.compress() 同步压缩。

        压缩过程通过 STATUS 事件通知前端展示进度。
        如果压缩失败，抛出 CompressionError，由上层 AgentService 捕获返回用户错误。
        """
        estimate = self._context_builder.estimate_compression_needed(self._memory.messages)
        if not estimate:
            return

        logger.info(
            "History Zone 超过水位线 | history={} tokens, budget={} tokens, 阈值={}",
            estimate.history_tokens, estimate.history_budget,
            settings.agent.compression_threshold,
        )

        _emit(AgentEvent(
            type=EventType.STATUS,
            message="🧠 正在整理长期记忆...",
        ))

        # 同步阻塞执行压缩（CompressionError 会自然向上传播）
        self._memory.compress(target_tokens=estimate.target_tokens)

        _emit(AgentEvent(
            type=EventType.STATUS,
            message="✅ 记忆整理完成",
        ))

    def _store_to_long_term_memory(self, user_input: str, answer: str,
                                   metrics: RunMetrics | None = None) -> None:
        """将对话中的关键事实提取并存储到长期记忆。

        使用 LLM 从 Q&A 中提取值得记住的关键事实（偏好、结论、数据），
        并根据时变性分类设置不同的 TTL：
        - 时变数据（状态、列表等）：TTL = 1 天
        - 稳定数据（偏好、配置等）：使用默认 TTL
        """
        if not self._vector_store:
            return

        if len(user_input.strip()) < 5 or len(answer.strip()) < 10:
            return

        from datetime import datetime

        # 尝试用 LLM 提取关键事实（含时变性判断）
        result = self._extract_key_facts(user_input, answer, metrics)
        if result:
            facts = result["facts"]
            volatile = result["volatile"]

            # A-1: 自动添加日期前缀
            date_prefix = datetime.now().strftime("[%Y-%m-%d]")
            facts_with_date = f"{date_prefix} {facts}"

            # A-3: 时变记忆设置短 TTL（1 天）
            metadata = {
                "type": "key_facts",
                "question": user_input[:200],
                "volatile": volatile,
                "collected_at": time.time(),
            }
            if volatile:
                metadata["ttl"] = time.time() + 86400  # 1 天后过期
                logger.debug("时变记忆（TTL=1天）已存入: {}", facts_with_date[:100])
            else:
                logger.debug("稳定记忆已存入: {}", facts_with_date[:100])

            self._vector_store.add(
                text=facts_with_date,
                metadata=metadata,
            )
        else:
            # LLM 提取失败时回退到简单存储，清洗格式装饰以减少噪声
            clean_answer = _clean_text_for_memory(answer[:300])
            date_prefix = datetime.now().strftime("[%Y-%m-%d]")
            summary = f"{date_prefix} 用户问: {user_input[:200]} | 回答: {clean_answer}"
            self._vector_store.add(
                text=summary,
                metadata={
                    "type": "conversation",
                    "question": user_input[:200],
                    "volatile": False,
                    "collected_at": time.time(),
                },
            )
            logger.debug("对话已存入长期记忆（回退模式）")

    def _extract_key_facts(self, user_input: str, answer: str,
                           metrics: RunMetrics | None = None) -> dict[str, Any] | None:
        """使用 LLM 从对话中提取值得长期记住的关键事实，并判断时变性。

        Returns:
            dict with keys:
                - "facts": 提取的关键事实文本
                - "volatile": 是否为时变数据（如状态、列表、实时指标等）
            如果对话不值得记忆或提取失败，返回 None。
        """
        try:
            extract_prompt = [
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "从以下对话中提取值得长期记住的关键事实，并判断数据的时变性。\n\n"
                        "提取规则：\n"
                        "1. 只提取客观事实、用户偏好、明确结论、重要数据\n"
                        "2. 跳过寒暄、闲聊、重复的常识性问答\n"
                        "3. 用简洁的纯文本陈述句输出，每条事实一行，最多 3 条\n"
                        "4. 不要使用 Markdown 格式、表情符号或装饰性符号\n"
                        '5. 如果对话没有值得记忆的关键事实，只输出"无"\n\n'
                        "时变性判断规则：\n"
                        "- 时变数据：任务状态、工单状态、服务状态、资源用量、列表数据、"
                        "实时指标、告警信息等可能随时间变化的数据\n"
                        "- 稳定数据：用户偏好、配置信息、架构设计、人员分工、项目名称等"
                        "短期内不会变化的事实\n\n"
                        "输出格式（严格遵守）：\n"
                        "第一行输出 VOLATILE 或 STABLE 表示时变性\n"
                        "后续行输出提取的关键事实\n\n"
                        "示例输出 1：\n"
                        "STABLE\n"
                        "用户偏好使用 Python 进行数据分析\n"
                        "项目部署在 3 个 Kubernetes namespace 中\n\n"
                        "示例输出 2：\n"
                        "VOLATILE\n"
                        "TAPD 需求 #12345 当前状态为开发中\n"
                        "用户有 3 个待办缺陷未处理"
                    ),
                ),
                Message(
                    role=Role.USER,
                    content=f"用户: {user_input[:300]}\n助手: {answer[:500]}",
                ),
            ]

            response = self._llm.chat(
                messages=extract_prompt,
                temperature=0.1,
                max_tokens=200,
            )
            if metrics:
                metrics.record_llm_call(response.usage, call_type="extract_facts")

            result = (response.content or "").strip()
            # 如果 LLM 判断不值得记忆
            if not result or result == "无":
                logger.debug("LLM 判断对话不含值得记忆的关键事实")
                return None

            # 解析时变性标签和事实内容
            lines = result.split("\n", 1)
            first_line = lines[0].strip().upper()
            volatile = first_line == "VOLATILE"
            facts = lines[1].strip() if len(lines) > 1 else first_line

            # 如果第一行不是标签（兼容旧格式），将整个结果作为事实
            if first_line not in ("VOLATILE", "STABLE"):
                facts = result
                volatile = False

            if not facts or facts == "无":
                return None

            return {"facts": facts, "volatile": volatile}
        except Exception as e:
            logger.warning("关键事实提取失败: {}", e)
            return None

    def _force_final_answer(self, metrics: RunMetrics | None = None) -> str:
        """强制 LLM 基于当前上下文给出最终回答（不再调用工具）。"""
        self._memory.add_user_message(
            "请根据以上所有工具调用的结果，直接给出最终的完整回答，不要再调用任何工具。"
        )
        context_messages = self._context_builder.build(self._memory.messages)
        response = self._llm.chat(
            messages=context_messages,
            tools=None,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        if metrics:
            metrics.record_llm_call(response.usage, call_type="force_answer")
        self._memory.add_assistant_message(response)
        return response.content or "抱歉，我无法得出结论。"


# Emoji Unicode 范围正则
_EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001F9FF"
    + "\U00002702-\U000027B0"
    + "\U0000FE00-\U0000FE0F"
    + "\U0000200D"
    + "]+",
    flags=re.UNICODE,
)

# Markdown 格式标记正则
_MARKDOWN_PATTERN = re.compile(
    r"#{1,6}\s+"
    + r"|(?<!\S)\*{1,3}|"
    + r"\*{1,3}(?!\S)"
    + r"|(?<!\S)_{1,2}|"
    + r"_{1,2}(?!\S)"
    + r"|^[-*+]\s+"
    + r"|^\d+\.\s+",
    flags=re.MULTILINE,
)


def _clean_text_for_memory(text: str) -> str:
    """清洗文本中的格式装饰，用于记忆存储。

    移除 emoji、Markdown 标记，压缩多余空白，
    使存入向量库的文本干净、利于 embedding 语义匹配。
    """
    text = _EMOJI_PATTERN.sub("", text)
    text = _MARKDOWN_PATTERN.sub("", text)
    # 压缩连续空行和空白
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)
    return text.strip()
