"""对话历史管理模块。

管理对话上下文，支持：
- 消息追加与历史维护
- 基于 Token 数的智能截断（防止超出上下文窗口）
- 被截断的消息自动摘要压缩（保留关键信息）
- System Prompt 始终保留
"""

from typing import Optional, List, TYPE_CHECKING

from src.llm.base_client import Message, Role
from src.memory.token_counter import TokenCounter
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.llm.base_client import BaseLLMClient


class ConversationMemory:
    """对话历史管理器。

    维护有序的消息列表，System Prompt 始终位于首位且不被截断。
    当 Token 总数超过阈值时，对最早的非系统消息进行摘要压缩。
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        max_messages: int = 50,
        model: str = "gpt-4o",
    ):
        """
        Args:
            system_prompt: 系统提示词。
            max_tokens: 对话历史的最大 Token 数（超出则触发摘要压缩或截断）。
            max_messages: 最大保留消息数（不含 system prompt），作为硬性上限。
            model: 用于 Token 计数的模型名称。
        """
        self._messages: List[Message] = []
        self._max_tokens = max_tokens
        self._max_messages = max_messages
        self._token_counter = TokenCounter(model=model)
        self._llm_client: Optional["BaseLLMClient"] = None

        if system_prompt:
            self._messages.append(Message(role=Role.SYSTEM, content=system_prompt))

    def set_llm_client(self, client: "BaseLLMClient") -> None:
        """设置 LLM 客户端，用于摘要压缩。"""
        self._llm_client = client

    @property
    def messages(self) -> List[Message]:
        """返回当前所有消息的副本。"""
        return list(self._messages)

    @property
    def token_count(self) -> int:
        """当前对话历史的 Token 总数。"""
        return self._token_counter.count_messages(self._messages)

    def add_message(self, message: Message) -> None:
        """添加消息并执行智能截断。"""
        self._messages.append(message)
        self._smart_truncate()

    def add_user_message(self, content: str) -> None:
        """快捷方法：添加用户消息。"""
        self.add_message(Message(role=Role.USER, content=content))

    def add_assistant_message(self, message: Message) -> None:
        """添加助手消息（可能包含 tool_calls）。"""
        self.add_message(message)

    def add_tool_result(self, tool_call_id: str, name: str, content: str) -> None:
        """添加工具执行结果消息。"""
        self.add_message(
            Message(
                role=Role.TOOL,
                content=content,
                tool_call_id=tool_call_id,
                name=name,
            )
        )

    def clear(self) -> None:
        """清空对话历史，保留 system prompt。"""
        system_msgs = [m for m in self._messages if m.role == Role.SYSTEM]
        self._messages = system_msgs
        logger.info("对话历史已清空")

    def _smart_truncate(self) -> None:
        """智能截断：优先摘要压缩，否则滑动窗口截断。

        策略：
        1. 先检查消息数量硬上限
        2. 再检查 Token 数，超出则尝试摘要压缩旧消息
        3. 摘要不可用时直接丢弃最早的消息
        """
        system_msgs = [m for m in self._messages if m.role == Role.SYSTEM]
        non_system_msgs = [m for m in self._messages if m.role != Role.SYSTEM]

        # 硬性消息数量上限
        if len(non_system_msgs) > self._max_messages:
            removed = len(non_system_msgs) - self._max_messages
            non_system_msgs = non_system_msgs[-self._max_messages:]
            self._messages = system_msgs + non_system_msgs
            logger.debug("消息数量截断，移除了 {} 条旧消息", removed)

        # Token 数检查
        current_tokens = self._token_counter.count_messages(self._messages)
        if current_tokens <= self._max_tokens:
            return

        logger.info(
            "Token 数超限 ({}/{}), 执行截断",
            current_tokens, self._max_tokens,
        )

        # 收集需要压缩的旧消息（保留最近一半）
        non_system_msgs = [m for m in self._messages if m.role != Role.SYSTEM]
        half = max(len(non_system_msgs) // 2, 1)
        old_msgs = non_system_msgs[:half]
        recent_msgs = non_system_msgs[half:]

        # 尝试用 LLM 做摘要压缩
        summary = self._summarize(old_msgs)
        if summary:
            summary_msg = Message(
                role=Role.SYSTEM,
                content=f"[对话历史摘要] {summary}",
            )
            self._messages = system_msgs + [summary_msg] + recent_msgs
            new_tokens = self._token_counter.count_messages(self._messages)
            logger.info(
                "摘要压缩完成，Token: {} -> {}",
                current_tokens, new_tokens,
            )
        else:
            # 回退：直接丢弃旧消息
            self._messages = system_msgs + recent_msgs
            new_tokens = self._token_counter.count_messages(self._messages)
            logger.info(
                "直接截断旧消息，Token: {} -> {}",
                current_tokens, new_tokens,
            )

    def _summarize(self, messages: List[Message]) -> Optional[str]:
        """使用 LLM 对旧消息进行摘要压缩。"""
        if not self._llm_client:
            return None

        try:
            conversation_text = "\n".join(
                f"{m.role.value}: {m.content}"
                for m in messages
                if m.content
            )
            if not conversation_text.strip():
                return None

            summary_prompt = [
                Message(
                    role=Role.SYSTEM,
                    content="请将以下对话历史压缩为简洁的摘要，保留关键信息和结论，去除冗余细节。用 2-3 句话概括。",
                ),
                Message(
                    role=Role.USER,
                    content=conversation_text,
                ),
            ]

            response = self._llm_client.chat(
                messages=summary_prompt,
                temperature=0.3,
                max_tokens=300,
            )
            return response.content
        except Exception as e:
            logger.warning("摘要压缩失败: {}", e)
            return None
