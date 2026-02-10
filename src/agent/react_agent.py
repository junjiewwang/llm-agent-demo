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
import time
from typing import Optional, TYPE_CHECKING

from src.agent.base_agent import BaseAgent
from src.agent.loop_detector import LoopDetector
from src.agent.metrics import RunMetrics
from src.config import settings
from src.context.builder import ContextBuilder
from src.llm.base_client import BaseLLMClient, Message, Role
from src.memory.conversation import ConversationMemory
from src.memory.vector_store import VectorStore
from src.tools.base_tool import ToolRegistry
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.rag.knowledge_base import KnowledgeBase


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
        max_iterations: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(llm_client, tool_registry, memory)
        self._context_builder = context_builder
        self._vector_store = vector_store
        self._knowledge_base = knowledge_base
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._temperature = temperature or settings.agent.temperature
        self._max_tokens = max_tokens or settings.agent.max_tokens
        self._last_metrics: Optional[RunMetrics] = None
        self._loop_detector = LoopDetector()

    @property
    def last_metrics(self) -> Optional[RunMetrics]:
        """获取最近一次 run() 的运行指标。"""
        return self._last_metrics

    def run(self, user_input: str) -> str:
        """处理用户输入，执行 ReAct 循环直到得到最终回答。"""
        metrics = RunMetrics(max_iterations=self._max_iterations)
        self._loop_detector.reset()

        # 1. 检索知识库，通过 ContextBuilder 临时注入（不写入 ConversationMemory）
        self._inject_knowledge(user_input, metrics)
        # 2. 检索长期记忆，通过 ContextBuilder 临时注入
        self._inject_long_term_memory(user_input, metrics)

        # 3. 用户消息写入对话历史（这是真正应该持久化的）
        self._memory.add_user_message(user_input)

        tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None

        for iteration in range(1, self._max_iterations + 1):
            metrics.iterations = iteration
            logger.info("ReAct 迭代 [{}/{}]", iteration, self._max_iterations)

            # 通过 ContextBuilder 组装完整上下文（System + Inject + History）
            context_messages = self._context_builder.build(self._memory.messages)

            # 调用 LLM
            response = self._llm.chat(
                messages=context_messages,
                tools=tools_schema,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            # 情况1: LLM 直接给出最终回答（没有 tool_calls）
            if not response.tool_calls:
                self._memory.add_assistant_message(response)
                logger.info("Agent 给出最终回答")
                self._store_to_long_term_memory(user_input, response.content or "")
                self._context_builder.clear_injections()
                metrics.finish()
                self._last_metrics = metrics
                logger.info("运行指标 | {}", metrics.summary())
                return response.content or ""

            # 情况2: LLM 决定调用工具
            self._memory.add_assistant_message(response)
            self._execute_tool_calls(response.tool_calls, metrics)

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
        answer = self._force_final_answer()
        self._store_to_long_term_memory(user_input, answer)
        self._context_builder.clear_injections()
        metrics.finish()
        self._last_metrics = metrics
        logger.info("运行指标 | {}", metrics.summary())
        return answer

    def _inject_knowledge(self, query: str, metrics: RunMetrics) -> None:
        """检索知识库，通过 ContextBuilder 临时注入上下文。"""
        if not self._knowledge_base or self._knowledge_base.count() == 0:
            self._context_builder.set_knowledge([])
            return

        results = self._knowledge_base.search(query, top_k=3)
        self._context_builder.set_knowledge(results)
        if results:
            metrics.kb_chunks_injected = len(results)
            logger.info("注入 {} 条知识库片段（通过 ContextBuilder）", len(results))

    def _inject_long_term_memory(self, query: str, metrics: RunMetrics) -> None:
        """检索长期记忆，通过 ContextBuilder 临时注入上下文。"""
        if not self._vector_store or self._vector_store.count() == 0:
            self._context_builder.set_memory([])
            return

        results = self._vector_store.search(query, top_k=3)
        self._context_builder.set_memory(results)

        relevant = [r for r in results if r.get("distance", 1.0) < 1.0]
        if relevant:
            metrics.memory_items_injected = len(relevant)
            logger.info("注入 {} 条长期记忆（通过 ContextBuilder）", len(relevant))

    def _store_to_long_term_memory(self, user_input: str, answer: str) -> None:
        """将对话中的关键事实提取并存储到长期记忆。

        使用 LLM 从 Q&A 中提取值得记住的关键事实（偏好、结论、数据），
        而非存储原始的"用户问/回答"拼接，提高记忆质量和检索精度。
        """
        if not self._vector_store:
            return

        if len(user_input.strip()) < 5 or len(answer.strip()) < 10:
            return

        # 尝试用 LLM 提取关键事实
        key_facts = self._extract_key_facts(user_input, answer)
        if key_facts:
            self._vector_store.add(
                text=key_facts,
                metadata={"type": "key_facts", "question": user_input[:200]},
            )
            logger.debug("结构化记忆已存入长期记忆: {}", key_facts[:100])
        else:
            # LLM 提取失败时回退到简单存储
            summary = f"用户问: {user_input[:200]} | 回答: {answer[:300]}"
            self._vector_store.add(
                text=summary,
                metadata={"type": "conversation", "question": user_input[:200]},
            )
            logger.debug("对话已存入长期记忆（回退模式）")

    def _extract_key_facts(self, user_input: str, answer: str) -> Optional[str]:
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
                        "3. 用简洁的陈述句输出，每条事实一行，最多 3 条\n"
                        '4. 如果对话没有值得记忆的关键事实，只输出"无"\n\n'
                        "示例输出：\n"
                        "用户偏好使用 Python 进行数据分析\n"
                        "上海今日气温 25°C，多云"
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

            result = (response.content or "").strip()
            # 如果 LLM 判断不值得记忆
            if not result or result == "无":
                logger.debug("LLM 判断对话不含值得记忆的关键事实")
                return None
            return result
        except Exception as e:
            logger.warning("关键事实提取失败: {}", e)
            return None

    def _execute_tool_calls(self, tool_calls, metrics: RunMetrics):
        """执行 LLM 请求的所有工具调用。"""
        for tc in tool_calls:
            func_name = tc["function"]["name"]
            func_args_str = tc["function"]["arguments"]
            tool_call_id = tc["id"]

            logger.info("调用工具: {} | 参数: {}", func_name, func_args_str)

            # 解析参数
            try:
                func_args = json.loads(func_args_str) if func_args_str else {}
            except json.JSONDecodeError as e:
                error_msg = f"参数解析失败: {e}"
                logger.error("工具参数解析失败: {} | 原始参数: {}", e, func_args_str)
                self._memory.add_tool_result(tool_call_id, func_name, error_msg)
                metrics.record_tool_call(func_name, success=False, duration_ms=0, error=str(e))
                self._loop_detector.record(func_name, func_args_str)
                continue

            # 执行工具并计时（ToolRegistry 返回 ToolResult）
            start = time.monotonic()
            result = self._tools.execute(func_name, **func_args)
            duration_ms = (time.monotonic() - start) * 1000

            metrics.record_tool_call(
                func_name,
                success=result.success,
                duration_ms=duration_ms,
                error=result.error,
            )

            # 记录到循环检测器
            self._loop_detector.record(func_name, func_args_str)

            # 生成 tool message 内容
            message_content = result.to_message()
            truncated_info = " (已截断)" if result.truncated else ""
            logger.info("工具 {} 执行完成 | 耗时: {:.0f}ms{} | 结果: {}",
                        func_name, duration_ms, truncated_info, message_content[:200])

            self._memory.add_tool_result(tool_call_id, func_name, message_content)

    def _force_final_answer(self) -> str:
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
        self._memory.add_assistant_message(response)
        return response.content or "抱歉，我无法得出结论。"
