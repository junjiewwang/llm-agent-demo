"""对话历史管理模块。

管理对话上下文，支持：
- 消息追加与历史维护
- 消息数量硬上限（防止消息条数爆炸）
- 同步压缩：由上层（Agent/ContextBuilder）调用 compress() 方法触发
- Scratchpad 快照/回滚：Plan-Execute 步骤级上下文隔离
- System Prompt 始终保留

注意：
- Token 级别的容量管理由 ContextBuilder 负责（它知道全局 budget）
- ConversationMemory 作为纯存储 + 摘要执行器角色
- KB/长期记忆的临时注入已由 ContextBuilder 负责，不再写入本模块

Scratchpad 机制：
    Plan-Execute Agent 的每个步骤在执行前调用 snapshot() 记录消息列表长度，
    步骤完成后调用 rollback_to_snapshot() 回滚中间过程消息，
    仅通过 settle_step_result() 沉淀一条精简的结果摘要。
    这将步骤执行的 Token 消耗从 O(k²) 降低到 O(k)。
"""

from typing import Optional, List, TYPE_CHECKING

from src.llm.base_client import Message, Role
from src.memory.token_counter import TokenCounter
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.llm.base_client import BaseLLMClient


class CompressionError(Exception):
    """上下文压缩失败时抛出的异常。

    由 compress() 方法在 LLM 摘要调用失败或超时时抛出，
    上层（Agent/Service）应捕获此异常并如实返回错误给用户。
    """


class ConversationMemory:
    """对话历史管理器。

    维护有序的消息列表，仅首条 System Prompt 始终保留且不被截断。
    消息数量硬上限防止条数爆炸，Token 级别的容量管理由上层 ContextBuilder 负责。
    提供 compress(target_tokens) 方法供上层调用以执行 LLM 摘要压缩。
    """

    def __init__(
        self,
        system_prompt: Optional[str] = None,
        max_tokens: int = 8000,
        max_messages: int = 40,
        model: str = "gpt-4o",
    ):
        """
        Args:
            system_prompt: 系统提示词。
            max_tokens: 对话历史的最大 Token 数（用于兜底安全检查）。
            max_messages: 最大保留消息数（不含 system prompt），作为硬性上限。
            model: 用于 Token 计数的模型名称。
        """
        self._messages: List[Message] = []
        self._system_prompt_count = 0
        self._max_tokens = max_tokens
        self._max_messages = max_messages
        self._token_counter = TokenCounter(model=model)
        self._llm_client: Optional["BaseLLMClient"] = None
        self._compression_count: int = 0  # 累计压缩次数
        self._active_snapshot_pos: Optional[int] = None  # 活跃的 Scratchpad 快照位置

        if system_prompt:
            self._messages.append(Message(role=Role.SYSTEM, content=system_prompt))
            self._system_prompt_count = 1

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

    @property
    def active_snapshot_pos(self) -> Optional[int]:
        """当前活跃的 Scratchpad 快照位置（已同步 _smart_truncate 的偏移）。"""
        return self._active_snapshot_pos

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

    # ── Scratchpad 快照/回滚（Plan-Execute 步骤级上下文隔离） ──

    def snapshot(self) -> int:
        """创建当前消息列表的快照点，返回快照位置索引。

        Plan-Execute 的每个步骤开始前调用，记录当前消息数量。
        步骤完成后可通过 rollback_to_snapshot() 回滚中间过程消息。

        注意：快照激活后，_smart_truncate 截断旧消息时会同步调整
        快照位置，确保 messages_from(snapshot_pos) 始终返回正确范围。

        Returns:
            快照位置索引（当前消息列表长度）。
        """
        pos = len(self._messages)
        self._active_snapshot_pos = pos
        logger.debug("Scratchpad 快照 | position={}", pos)
        return pos

    def rollback_to_snapshot(self, snapshot_pos: int) -> int:
        """回滚到快照位置，移除该位置之后的所有消息。

        Args:
            snapshot_pos: snapshot() 返回的位置索引。

        Returns:
            被移除的消息数量。
        """
        if snapshot_pos < self._system_prompt_count:
            snapshot_pos = self._system_prompt_count

        current_len = len(self._messages)
        if snapshot_pos >= current_len:
            self._active_snapshot_pos = None
            return 0

        removed_count = current_len - snapshot_pos
        self._messages = self._messages[:snapshot_pos]
        self._active_snapshot_pos = None  # 快照已消费，清除
        logger.debug("Scratchpad 回滚 | 移除 {} 条中间消息 | 当前消息数: {}",
                     removed_count, len(self._messages))
        return removed_count

    def messages_from(self, snapshot_pos: int) -> List[Message]:
        """返回 System Prompt + 指定位置之后的消息（Scratchpad 局部视图）。

        用于 Plan-Execute 执行器的上下文隔离：只给 LLM 看当前步骤
        的工作消息，不携带 Scratchpad 之前的全局对话历史。

        对应 LangGraph 的 State Scoping 理念：每个执行节点只看自己的局部状态。

        Args:
            snapshot_pos: snapshot() 返回的位置索引。

        Returns:
            [System Prompt(s)] + [snapshot_pos 之后的消息] 的副本。
        """
        protected = self._messages[:self._system_prompt_count]
        scoped = self._messages[snapshot_pos:] if snapshot_pos < len(self._messages) else []
        return list(protected) + list(scoped)

    def settle_step_result(self, step_description: str, result_summary: str) -> None:
        """沉淀步骤执行结果为一条精简的 assistant 消息。

        在 rollback_to_snapshot() 之后调用，将步骤的最终结论
        以简洁形式写入对话历史，供后续步骤参考。

        Args:
            step_description: 步骤描述。
            result_summary: 步骤执行结果摘要（建议 ≤500 字符）。
        """
        content = f"[步骤完成] {step_description}\n结果: {result_summary}"
        self._messages.append(Message(role=Role.ASSISTANT, content=content))
        logger.debug("Scratchpad 结果沉淀 | 步骤: {} | 结果: {}",
                     step_description[:50], result_summary[:80])

    def clear(self) -> None:
        """清空对话历史，仅保留初始 system prompt。"""
        self._messages = self._messages[:self._system_prompt_count]
        logger.info("对话历史已清空")

    # ── 序列化/反序列化（用于会话持久化） ──

    def serialize(self) -> dict:
        """将对话记忆序列化为可 JSON 化的字典。"""
        return {
            "messages": [msg.model_dump(mode="json") for msg in self._messages],
            "system_prompt_count": self._system_prompt_count,
        }

    def restore_from(self, data: dict) -> None:
        """从序列化数据恢复对话记忆（替换当前消息列表）。

        Args:
            data: serialize() 生成的字典，包含 messages 和 system_prompt_count。
        """
        raw_messages = data.get("messages", [])
        restored: List[Message] = []
        for item in raw_messages:
            if "role" in item and isinstance(item["role"], str):
                item["role"] = Role(item["role"])
            restored.append(Message.model_validate(item))
        self._messages = restored
        self._system_prompt_count = data.get("system_prompt_count", 0)
        logger.debug("对话记忆已恢复，消息数={}", len(self._messages))

    def _smart_truncate(self) -> None:
        """消息数量硬上限保护。

        仅保护消息条数不爆炸（max_messages），Token 级别的容量管理
        由上层 ContextBuilder.estimate_compression_needed() + compress() 负责。

        注意：当存在活跃的 Scratchpad 快照时，截断会同步调整快照位置，
        确保 messages_from(snapshot_pos) 始终指向正确的消息范围。
        """
        protected = self._messages[:self._system_prompt_count]
        truncatable = self._messages[self._system_prompt_count:]

        if len(truncatable) > self._max_messages:
            removed = len(truncatable) - self._max_messages
            truncatable = truncatable[-self._max_messages:]
            self._messages = protected + truncatable
            logger.debug("消息数量截断，移除了 {} 条旧消息", removed)

            # 同步调整活跃的快照位置，防止 snapshot_pos 指向已截断的区域
            if self._active_snapshot_pos is not None:
                self._active_snapshot_pos = max(
                    self._active_snapshot_pos - removed,
                    self._system_prompt_count,
                )
                logger.debug("Scratchpad 快照位置同步调整 | new_pos={}",
                             self._active_snapshot_pos)

    @property
    def compression_count(self) -> int:
        """累计压缩次数。"""
        return self._compression_count

    def compress(self, target_tokens: int) -> None:
        """同步压缩对话历史到目标 token 数。

        由上层（Agent/_check_and_compress）在检测到 History Zone 超过水位线时调用。
        使用 LLM 对旧消息进行结构化摘要，替换为压缩后的摘要消息。

        Args:
            target_tokens: 压缩后 History Zone 的目标 token 数。

        Raises:
            CompressionError: LLM 摘要调用失败或超时时抛出。
        """
        if not self._llm_client:
            raise CompressionError("LLM 客户端未设置，无法执行上下文压缩")

        protected = self._messages[:self._system_prompt_count]
        truncatable = self._messages[self._system_prompt_count:]

        if not truncatable:
            return

        current_tokens = self._token_counter.count_messages(truncatable)
        if current_tokens <= target_tokens:
            return

        # 取可截断消息的前半部分进行摘要，保留后半部分
        half = max(len(truncatable) // 2, 1)
        old_msgs = truncatable[:half]
        recent_msgs = truncatable[half:]

        logger.info(
            "开始上下文压缩 | 当前={} tokens, 目标={} tokens, 压缩 {} 条旧消息",
            current_tokens, target_tokens, len(old_msgs),
        )

        summary = self._summarize(old_msgs)
        if not summary:
            raise CompressionError("LLM 摘要压缩返回空结果")

        summary_msg = Message(
            role=Role.SYSTEM,
            content=f"[对话历史摘要] {summary}",
        )
        self._messages = protected + [summary_msg] + recent_msgs
        self._compression_count += 1
        new_tokens = self._token_counter.count_messages(self._messages[self._system_prompt_count:])
        logger.info(
            "上下文压缩完成 | Token: {} -> {} | 累计压缩 {} 次",
            current_tokens, new_tokens, self._compression_count,
        )

    def _summarize(self, messages: List[Message]) -> Optional[str]:
        """使用 LLM 对旧消息进行结构化摘要压缩。

        相比简单的"概括为几句话"，结构化摘要会分类保留关键信息：
        - 关键事实：工具返回的具体结果、数值、事实
        - 已做决策：用户确认的决策、已达成的结论
        - 未解决问题：尚未解决的问题、用户未回应的建议
        - 用户偏好：用户表达的偏好和约束

        Raises:
            CompressionError: LLM 调用失败时抛出。
        """
        if not self._llm_client:
            raise CompressionError("LLM 客户端未设置")

        conversation_text = "\n".join(
            f"{m.role.value}: {m.content}"
            for m in messages
            if m.content
        )
        if not conversation_text.strip():
            return None

        try:
            summary_prompt = [
                Message(
                    role=Role.SYSTEM,
                    content=(
                        "你是一个对话历史摘要专家。请将以下对话内容压缩为结构化摘要。\n\n"
                        "要求：\n"
                        "1. 保留所有关键事实和数据（如数字、名称、配置值）\n"
                        "2. 保留所有已做决策和结论\n"
                        "3. 保留所有未解决的问题和待办事项\n"
                        "4. 保留用户的偏好和约束\n"
                        "5. 丢弃寒暄、重复确认、中间调试过程\n\n"
                        "输出格式（只包含有内容的分类，无内容的分类直接省略）：\n"
                        "## 关键事实\n- ...\n"
                        "## 已做决策\n- ...\n"
                        "## 未解决问题\n- ...\n"
                        "## 用户偏好\n- ...\n\n"
                        "要求：每个分类用简短要点概括，总长度不超过 300 字。"
                        "保留具体数值和关键名词，去除寒暄和冗余解释。"
                    ),
                ),
                Message(
                    role=Role.USER,
                    content=conversation_text,
                ),
            ]

            response = self._llm_client.chat(
                messages=summary_prompt,
                temperature=0.2,
                max_tokens=600,
            )
            return response.content
        except Exception as e:
            raise CompressionError(f"LLM 摘要调用失败: {e}") from e
