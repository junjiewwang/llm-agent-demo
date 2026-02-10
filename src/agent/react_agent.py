"""ReAct Agent 实现。

核心循环: 用户输入 → 检索知识库 → 检索记忆 → LLM 思考 → 选择工具 → 执行 → 观察结果 → 继续思考 → ... → 最终回答 → 存储记忆

基于 OpenAI Function Calling 实现工具调用，比纯 Prompt 解析更可靠。
"""

import json
from typing import Optional, TYPE_CHECKING

from src.agent.base_agent import BaseAgent
from src.config import settings
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
    - 长期记忆：每次对话前检索相关记忆注入上下文
    - 知识库预检索：每次对话前自动检索知识库，将相关片段注入上下文
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        tool_registry: ToolRegistry,
        memory: ConversationMemory,
        vector_store: Optional[VectorStore] = None,
        knowledge_base: Optional["KnowledgeBase"] = None,
        max_iterations: Optional[int] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(llm_client, tool_registry, memory)
        self._vector_store = vector_store
        self._knowledge_base = knowledge_base
        self._max_iterations = max_iterations or settings.agent.max_iterations
        self._temperature = temperature or settings.agent.temperature
        self._max_tokens = max_tokens or settings.agent.max_tokens

    def run(self, user_input: str) -> str:
        """处理用户输入，执行 ReAct 循环直到得到最终回答。"""
        # 1. 自动检索知识库，注入相关文档片段
        self._inject_knowledge(user_input)
        # 2. 检索长期记忆，注入上下文
        self._inject_long_term_memory(user_input)

        self._memory.add_user_message(user_input)

        tools_schema = self._tools.to_openai_tools() if len(self._tools) > 0 else None

        for iteration in range(1, self._max_iterations + 1):
            logger.info("ReAct 迭代 [{}/{}]", iteration, self._max_iterations)

            # 调用 LLM
            response = self._llm.chat(
                messages=self._memory.messages,
                tools=tools_schema,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            )

            # 情况1: LLM 直接给出最终回答（没有 tool_calls）
            if not response.tool_calls:
                self._memory.add_assistant_message(response)
                logger.info("Agent 给出最终回答")
                # 存储到长期记忆
                self._store_to_long_term_memory(user_input, response.content or "")
                return response.content or ""

            # 情况2: LLM 决定调用工具
            self._memory.add_assistant_message(response)
            self._execute_tool_calls(response.tool_calls)

        # 达到最大迭代次数，强制让 LLM 总结
        logger.warning("达到最大迭代次数 {}，强制总结", self._max_iterations)
        answer = self._force_final_answer()
        self._store_to_long_term_memory(user_input, answer)
        return answer

    def _inject_knowledge(self, query: str) -> None:
        """自动检索知识库，将相关文档片段注入上下文。

        这是 RAG 的核心环节：不依赖 LLM 主动调用工具，
        而是在每次对话前主动检索并注入，确保 LLM 能看到相关知识。
        """
        if not self._knowledge_base or self._knowledge_base.count() == 0:
            return

        results = self._knowledge_base.search(query, top_k=3)
        if not results:
            return

        kb_text = "\n\n".join(
            f"[文档片段 {i+1}] (来源: {r['metadata'].get('filename', '未知')})\n{r['text']}"
            for i, r in enumerate(results)
        )
        self._memory.add_message(
            Message(
                role=Role.SYSTEM,
                content=(
                    f"[知识库检索结果]\n{kb_text}\n\n"
                    "以上是从知识库中检索到的相关内容，请优先基于这些内容回答用户问题。"
                    "如果知识库内容不足以回答，再结合你自身的知识补充。"
                ),
            )
        )
        logger.info("注入 {} 条知识库片段", len(results))

    def _inject_long_term_memory(self, query: str) -> None:
        """检索长期记忆并注入对话上下文。"""
        if not self._vector_store or self._vector_store.count() == 0:
            return

        results = self._vector_store.search(query, top_k=3)
        if not results:
            return

        # 过滤掉相似度太低的结果（cosine distance > 1.0 认为不相关）
        relevant = [r for r in results if r["distance"] < 1.0]
        if not relevant:
            return

        # 对检索结果做文本去重（多条记忆可能内容高度重复）
        seen_texts = set()
        unique_results = []
        for r in relevant:
            # 用前100个字符作为去重 key
            text_key = r["text"][:100]
            if text_key not in seen_texts:
                seen_texts.add(text_key)
                unique_results.append(r)

        if not unique_results:
            return

        memory_text = "\n".join(
            f"- {r['text']}" for r in unique_results
        )
        # 作为系统消息注入（临时的，不会持久化到 system prompt）
        self._memory.add_message(
            Message(
                role=Role.SYSTEM,
                content=f"[相关历史记忆]\n{memory_text}\n\n请参考以上记忆回答用户问题（如果相关的话）。",
            )
        )
        logger.info("注入 {} 条长期记忆（去重后）", len(unique_results))

    def _store_to_long_term_memory(self, user_input: str, answer: str) -> None:
        """将对话的关键信息存储到长期记忆。"""
        if not self._vector_store:
            return

        # 只存储有实质内容的对话
        if len(user_input.strip()) < 5 or len(answer.strip()) < 10:
            return

        summary = f"用户问: {user_input[:200]} | 回答: {answer[:300]}"
        self._vector_store.add(
            text=summary,
            metadata={"type": "conversation", "question": user_input[:200]},
        )
        logger.debug("对话已存入长期记忆")

    def _execute_tool_calls(self, tool_calls):
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
                result = f"参数解析失败: {e}"
                logger.error("工具参数解析失败: {} | 原始参数: {}", e, func_args_str)
                self._memory.add_tool_result(tool_call_id, func_name, result)
                continue

            # 执行工具
            result = self._tools.execute(func_name, **func_args)
            logger.info("工具 {} 执行完成 | 结果: {}", func_name, result[:200])

            # 将结果写入对话历史
            self._memory.add_tool_result(tool_call_id, func_name, result)

    def _force_final_answer(self) -> str:
        """强制 LLM 基于当前上下文给出最终回答（不再调用工具）。"""
        self._memory.add_user_message(
            "请根据以上所有工具调用的结果，直接给出最终的完整回答，不要再调用任何工具。"
        )
        response = self._llm.chat(
            messages=self._memory.messages,
            tools=None,  # 不提供工具，强制直接回答
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )
        self._memory.add_assistant_message(response)
        return response.content or "抱歉，我无法得出结论。"
