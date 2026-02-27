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

import json
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.agent.base_agent import BaseAgent, OnEventCallback, WaitForConfirmation
from src.agent.events import AgentEvent, AgentStoppedError, EventType
from src.agent.loop_detector import LoopDetector
from src.agent.metrics import RunMetrics
from src.config import settings
from src.context.builder import ContextBuilder
from src.environment.adapter_base import EnvironmentAdapter
from src.llm.base_client import BaseLLMClient, Message, Role
from src.memory.conversation import ConversationMemory
from src.memory.vector_store import VectorStore
from src.observability import get_tracer
from src.observability.instruments import (
    propagate_context, record_agent_run_metrics, trace_span,
    set_span_content, set_span_distances,
)
from src.tools.base_tool import ToolRegistry
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.rag.knowledge_base import KnowledgeBase
    from src.skills.router import SkillRouter

_tracer = get_tracer(__name__)


class ReActAgent(BaseAgent):
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
        vector_store: Optional[VectorStore] = None,
        knowledge_base: Optional["KnowledgeBase"] = None,
        skill_router: Optional["SkillRouter"] = None,
        env_adapter: Optional[EnvironmentAdapter] = None,
        max_iterations: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(llm_client, tool_registry, memory)
        self._context_builder = context_builder
        self._vector_store = vector_store
        self._knowledge_base = knowledge_base
        self._skill_router = skill_router
        self._env_adapter = env_adapter
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._temperature = temperature or settings.agent.temperature
        self._max_tokens = max_tokens or settings.agent.max_tokens
        self._last_metrics: Optional[RunMetrics] = None
        self._loop_detector = LoopDetector()

    @property
    def last_metrics(self) -> Optional[RunMetrics]:
        """获取最近一次 run() 的运行指标。"""
        return self._last_metrics

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
                    on_event is not None, wait_for_confirmation,
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
    def _set_metrics_on_span(span, metrics: RunMetrics, stopped: bool = False) -> None:
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
        self, user_input: str, metrics: RunMetrics, _emit,
        has_callback: bool, wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """ReAct 核心循环，从 run() 中分离以便统一异常处理。"""
        # 1. 检索知识库，通过 ContextBuilder 临时注入（不写入 ConversationMemory）
        self._inject_knowledge(user_input, metrics)
        # 2. 检索长期记忆，通过 ContextBuilder 临时注入
        self._inject_long_term_memory(user_input, metrics)
        # 3. 匹配并注入 Skills（领域专家 prompt）
        self._inject_skills(user_input)

        # 4. 用户消息写入对话历史（这是真正应该持久化的）
        self._memory.add_user_message(user_input)

        tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None

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

                self._store_to_long_term_memory(user_input, response.content or "", metrics)
                self._context_builder.clear_injections()
                metrics.finish()
                self._last_metrics = metrics
                logger.info("运行指标 | {}", metrics.summary())
                return response.content or ""

            # 情况2: LLM 决定调用工具
            self._memory.add_assistant_message(response)
            self._execute_tool_calls(response.tool_calls, metrics, _emit, wait_for_confirmation)

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

    def _writeback_memory_hits(self, relevant_memories: List[Dict[str, Any]]) -> None:
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

    def _store_to_long_term_memory(self, user_input: str, answer: str,
                                   metrics: Optional[RunMetrics] = None) -> None:
        """将对话中的关键事实提取并存储到长期记忆。

        使用 LLM 从 Q&A 中提取值得记住的关键事实（偏好、结论、数据），
        而非存储原始的"用户问/回答"拼接，提高记忆质量和检索精度。
        """
        if not self._vector_store:
            return

        if len(user_input.strip()) < 5 or len(answer.strip()) < 10:
            return

        # 尝试用 LLM 提取关键事实
        key_facts = self._extract_key_facts(user_input, answer, metrics)
        if key_facts:
            self._vector_store.add(
                text=key_facts,
                metadata={"type": "key_facts", "question": user_input[:200]},
            )
            logger.debug("结构化记忆已存入长期记忆: {}", key_facts[:100])
        else:
            # LLM 提取失败时回退到简单存储，清洗格式装饰以减少噪声
            clean_answer = _clean_text_for_memory(answer[:300])
            summary = f"用户问: {user_input[:200]} | 回答: {clean_answer}"
            self._vector_store.add(
                text=summary,
                metadata={"type": "conversation", "question": user_input[:200]},
            )
            logger.debug("对话已存入长期记忆（回退模式）")

    def _extract_key_facts(self, user_input: str, answer: str,
                           metrics: Optional[RunMetrics] = None) -> Optional[str]:
        """使用 LLM 从对话中提取值得长期记住的关键事实。

        Returns:
            提取的关键事实文本；如果对话不值得记忆或提取失败，返回 None。
        """
        try:
            extract_prompt = [
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "从以下对话中提取值得长期记住的关键事实。\n\n"
                        "提取规则：\n"
                        "1. 只提取客观事实、用户偏好、明确结论、重要数据\n"
                        "2. 跳过寒暄、闲聊、重复的常识性问答\n"
                        "3. 用简洁的纯文本陈述句输出，每条事实一行，最多 3 条\n"
                        "4. 不要使用 Markdown 格式、表情符号或装饰性符号\n"
                        '5. 如果对话没有值得记忆的关键事实，只输出"无"\n\n'
                        "示例输出：\n"
                        "用户偏好使用 Python 进行数据分析\n"
                        "项目部署在 3 个 Kubernetes namespace 中"
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
            return result
        except Exception as e:
            logger.warning("关键事实提取失败: {}", e)
            return None

    # 工具并发执行的最大线程数
    _TOOL_MAX_WORKERS = 5

    def _execute_tool_calls(
        self, tool_calls, metrics: RunMetrics, emit=None,
        wait_for_confirmation: WaitForConfirmation = None,
    ):
        """执行 LLM 请求的所有工具调用。

        单个 tool_call 时串行执行；多个 tool_call 时并发执行以减少总耗时。
        如果并发批次中有需要确认的工具，退化为串行以保证确认体验。
        无论并发还是串行，结果都按原始顺序写入 Memory（保证上下文一致性）。
        """
        if len(tool_calls) == 1:
            self._execute_single_tool(tool_calls[0], metrics, emit, wait_for_confirmation)
            return

        # 多个 tool_calls：检查是否有需要确认的工具
        # 如果有，退化为串行执行（V1 简化策略，避免并发确认的 UX 复杂度）
        if wait_for_confirmation and self._has_confirmable_tool(tool_calls):
            logger.info("并发批次中有需要确认的工具，退化为串行执行")
            for tc in tool_calls:
                self._execute_single_tool(tc, metrics, emit, wait_for_confirmation)
            return

        # 多个 tool_calls 且无需确认：并发执行
        total = len(tool_calls)
        logger.info("并发执行 {} 个工具调用", total)

        # 先发送所有 TOOL_CALL 事件 + 解析参数
        parsed: List[Optional["_ParsedToolCall"]] = []
        for idx, tc in enumerate(tool_calls):
            p = self._parse_and_emit_tool_call(
                tc, metrics, emit,
                parallel_total=total, parallel_index=idx + 1,
            )
            parsed.append(p)

        # 并发执行所有已成功解析的工具（propagate_context 确保子线程 span 关联到父 trace）
        results: Dict[int, _ToolExecResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), self._TOOL_MAX_WORKERS)) as pool:
            future_to_idx = {}
            for i, p in enumerate(parsed):
                if p is not None:
                    future = pool.submit(
                        propagate_context(self._tools.execute),
                        p.func_name, **p.func_args,
                    )
                    future_to_idx[future] = i

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                p = parsed[idx]
                assert p is not None
                start_time = p.start_time
                try:
                    result = future.result()
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    results[idx] = _ToolExecResult(
                        result=result, duration_ms=duration_ms,
                    )
                except Exception as e:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    from src.tools.result import ToolResult
                    results[idx] = _ToolExecResult(
                        result=ToolResult.fail(f"工具执行异常: {e}"),
                        duration_ms=duration_ms,
                    )

        # 按原始顺序写入 Memory 和发送事件（保证上下文一致性）
        for i, tc in enumerate(tool_calls):
            p = parsed[i]
            if p is None:
                continue  # 解析失败的已经在 _parse_and_emit_tool_call 中处理

            exec_result = results.get(i)
            if exec_result is None:
                continue

            self._record_tool_result(
                tc, p, exec_result.result, exec_result.duration_ms, metrics, emit,
                parallel_total=total, parallel_index=i + 1,
            )

    def _execute_single_tool(
        self, tc, metrics: RunMetrics, emit=None,
        wait_for_confirmation: WaitForConfirmation = None,
    ):
        """串行执行单个工具调用，支持确认拦截。"""
        p = self._parse_and_emit_tool_call(tc, metrics, emit)
        if p is None:
            return

        # 确认拦截：检查工具是否需要用户确认
        result = self._maybe_confirm_and_execute(p, metrics, emit, wait_for_confirmation)
        duration_ms = int((time.monotonic() - p.start_time) * 1000)
        self._record_tool_result(tc, p, result, duration_ms, metrics, emit)

    def _maybe_confirm_and_execute(
        self, parsed: "_ParsedToolCall", metrics: RunMetrics, emit=None,
        wait_for_confirmation: WaitForConfirmation = None,
    ):
        """确认拦截 + 执行工具。

        如果工具需要确认且有确认回调，发送 TOOL_CONFIRM 事件并阻塞等待。
        用户批准后执行，拒绝或超时则返回失败结果。
        """
        from src.tools.result import ToolResult

        confirm_mode = settings.agent.tool_confirm_mode

        # 判断是否需要确认
        needs_confirm = False
        if confirm_mode == "always":
            needs_confirm = True
        elif confirm_mode == "smart":
            needs_confirm = self._should_confirm_tool(parsed.func_name, parsed.func_args)

        if needs_confirm and wait_for_confirmation:
            confirm_id = str(uuid.uuid4())
            logger.info("工具 {} 需要用户确认 | confirm_id={}", parsed.func_name, confirm_id[:8])

            if emit:
                emit(AgentEvent(
                    type=EventType.TOOL_CONFIRM,
                    iteration=metrics.iterations,
                    max_iterations=metrics.max_iterations,
                    tool_name=parsed.func_name,
                    tool_args=parsed.func_args,
                    confirm_id=confirm_id,
                ))

            # 阻塞等待用户决策
            approved = wait_for_confirmation(confirm_id)

            if approved is None:
                logger.info("工具确认超时或被停止 | {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])
                return ToolResult.fail("用户确认超时或对话已停止，已跳过执行")
            elif not approved:
                logger.info("用户拒绝执行工具 {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])
                return ToolResult.fail("用户已拒绝执行此操作")
            else:
                logger.info("用户批准执行工具 {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])

        return self._tools.execute(parsed.func_name, **parsed.func_args)

    def _should_confirm_tool(self, tool_name: str, tool_args: dict) -> bool:
        """根据工具的 should_confirm 方法判断是否需要确认。"""
        try:
            tool = self._tools.get(tool_name)
            return tool.should_confirm(**tool_args)
        except (KeyError, Exception):
            return False

    def _has_confirmable_tool(self, tool_calls) -> bool:
        """检查 tool_calls 批次中是否有需要确认的工具。"""
        for tc in tool_calls:
            try:
                func_name = tc["function"]["name"]
                func_args_str = tc["function"]["arguments"]
                func_args = json.loads(func_args_str) if func_args_str else {}
                if self._should_confirm_tool(func_name, func_args):
                    return True
            except (json.JSONDecodeError, KeyError):
                continue
        return False

    def _parse_and_emit_tool_call(
        self, tc, metrics: RunMetrics, emit=None,
        parallel_total: int = 0, parallel_index: int = 0,
    ) -> Optional["_ParsedToolCall"]:
        """解析工具调用参数，发送 TOOL_CALL 事件。

        Returns:
            解析成功返回 _ParsedToolCall，失败返回 None（已记录错误到 Memory）。
        """
        func_name = tc["function"]["name"]
        func_args_str = tc["function"]["arguments"]
        tool_call_id = tc["id"]

        logger.info("调用工具: {} | 参数: {}", func_name, func_args_str)

        try:
            func_args = json.loads(func_args_str) if func_args_str else {}
        except json.JSONDecodeError as e:
            error_msg = f"参数解析失败: {e}"
            logger.error("工具参数解析失败: {} | 原始参数: {}", e, func_args_str)
            self._memory.add_tool_result(tool_call_id, func_name, error_msg)
            metrics.record_tool_call(func_name, success=False, duration_ms=0, error=str(e))
            self._loop_detector.record(func_name, func_args_str)
            if emit:
                emit(AgentEvent(
                    type=EventType.TOOL_RESULT,
                    tool_name=func_name,
                    tool_args={},
                    tool_result_preview=error_msg[:100],
                    success=False,
                    message=error_msg,
                ))
            return None

        if emit:
            emit(AgentEvent(
                type=EventType.TOOL_CALL,
                iteration=metrics.iterations,
                max_iterations=metrics.max_iterations,
                tool_name=func_name,
                tool_args=func_args,
                parallel_total=parallel_total,
                parallel_index=parallel_index,
            ))

        return _ParsedToolCall(
            func_name=func_name,
            func_args=func_args,
            func_args_str=func_args_str,
            start_time=time.monotonic(),
        )

    def _record_tool_result(
        self, tc, parsed: "_ParsedToolCall", result, duration_ms: int,
        metrics: RunMetrics, emit=None,
        parallel_total: int = 0, parallel_index: int = 0,
    ) -> None:
        """记录工具执行结果到 Memory、Metrics、LoopDetector，并发送事件。"""
        tool_call_id = tc["id"]

        metrics.record_tool_call(
            parsed.func_name,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
        )

        self._loop_detector.record(parsed.func_name, parsed.func_args_str)

        message_content = result.to_message()
        truncated_info = " (已截断)" if result.truncated else ""
        logger.info("工具 {} 执行完成 | 耗时: {:.0f}ms{} | 结果: {}",
                    parsed.func_name, duration_ms, truncated_info, message_content[:200])

        self._memory.add_tool_result(tool_call_id, parsed.func_name, message_content)

        if emit:
            emit(AgentEvent(
                type=EventType.TOOL_RESULT,
                iteration=metrics.iterations,
                max_iterations=metrics.max_iterations,
                tool_name=parsed.func_name,
                tool_args=parsed.func_args,
                tool_result_preview=message_content[:150],
                duration_ms=duration_ms,
                success=result.success,
                parallel_total=parallel_total,
                parallel_index=parallel_index,
            ))

    def _force_final_answer(self, metrics: Optional[RunMetrics] = None) -> str:
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


@dataclass
class _ParsedToolCall:
    """工具调用解析结果（内部使用）。"""
    func_name: str
    func_args: dict
    func_args_str: str
    start_time: float


@dataclass
class _ToolExecResult:
    """工具执行结果包装（内部使用）。"""
    result: object  # ToolResult
    duration_ms: int


# Emoji Unicode 范围正则
_EMOJI_PATTERN = re.compile(
    "[\U0001F300-\U0001F9FF"   # 各类表情符号
    "\U00002702-\U000027B0"    # 杂项符号
    "\U0000FE00-\U0000FE0F"    # 变体选择符
    "\U0000200D"               # 零宽连接符
    "]+",
    flags=re.UNICODE,
)

# Markdown 格式标记正则
_MARKDOWN_PATTERN = re.compile(
    r"#{1,6}\s+"               # 标题 ## xxx
    r"|(?<!\S)\*{1,3}|"       # 加粗/斜体 **xxx** *xxx*
    r"\*{1,3}(?!\S)"
    r"|(?<!\S)_{1,2}|"        # 下划线强调 __xxx__
    r"_{1,2}(?!\S)"
    r"|^[-*+]\s+"              # 列表项 - xxx / * xxx
    r"|^\d+\.\s+",             # 有序列表 1. xxx
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
