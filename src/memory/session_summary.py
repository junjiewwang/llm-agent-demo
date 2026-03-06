"""会话级增量摘要管理器。

维护当前会话的全局概要，每 N 轮交互增量更新，
使 LLM 在 100+ 轮对话后仍能保持全局视野。

设计要点：
- 增量更新：每次只将新的 Q&A 交互与现有 summary 合并，而非从头重建
- 使用 LLM 做增量摘要（相比全量摘要，token 消耗更低）
- summary 缓存在内存中，可序列化到会话持久化层

消息三级生命周期集成：
    Recent Window（完整）→ 被挤出时自动归档 → 同时更新 Session Summary
    → 超过 max_messages 硬上限 → 驱逐
"""

from typing import Any, Dict, List, Optional, TYPE_CHECKING

from src.config import settings
from src.llm.base_client import Message, Role
from src.utils.logger import logger

if TYPE_CHECKING:
    from src.llm.base_client import BaseLLMClient


# 增量摘要 LLM prompt
_SUMMARY_UPDATE_PROMPT = """\
你是一个会话摘要专家。请将「当前会话概要」与「新的交互内容」合并，生成更新后的概要。

要求：
1. 保留所有已讨论的**话题**和**关键结论**
2. 新增新交互中的话题和结论
3. 用简洁的要点列表格式，每个话题一行
4. 丢弃寒暄、重复确认、中间调试细节
5. 总长度不超过 200 字

输出格式：
- 话题1: 关键结论
- 话题2: 关键结论
..."""

_SUMMARY_INIT_PROMPT = """\
你是一个会话摘要专家。请从以下对话中提取会话概要。

要求：
1. 提取已讨论的**话题**和**关键结论**
2. 用简洁的要点列表格式，每个话题一行
3. 丢弃寒暄、重复确认、中间调试细节
4. 总长度不超过 200 字

输出格式：
- 话题1: 关键结论
- 话题2: 关键结论
..."""


class SessionSummary:
    """会话级增量摘要管理器。

    每 N 轮 Q&A 交互（由 session_summary_interval 配置）触发一次
    LLM 增量摘要，将新交互内容与现有 summary 合并。

    同时维护 archive_watermark（已归档水位线），
    追踪哪些消息已被归档到 ConversationArchive，避免重复归档。
    """

    def __init__(self):
        self._summary: str = ""
        self._interaction_count: int = 0
        self._last_update_count: int = 0
        # 已归档水位线：ConversationMemory 中已归档到的消息索引
        # 仅追踪 system_prompt 之后的非 Recent Window 消息
        self._archive_watermark: int = 0

    @property
    def summary(self) -> str:
        """当前会话概要文本。"""
        return self._summary

    @property
    def interaction_count(self) -> int:
        """本次会话的 Q&A 交互计数。"""
        return self._interaction_count

    @property
    def archive_watermark(self) -> int:
        """已归档到的消息位置索引（相对于 system_prompt 之后）。"""
        return self._archive_watermark

    @archive_watermark.setter
    def archive_watermark(self, value: int) -> None:
        self._archive_watermark = max(value, 0)

    def should_update(self) -> bool:
        """判断是否需要更新 summary。

        每 session_summary_interval 轮交互触发一次。
        """
        interval = settings.agent.session_summary_interval
        return (self._interaction_count - self._last_update_count) >= interval

    def record_interaction(self) -> None:
        """记录一次 Q&A 交互完成。

        在 Agent.run() 最终回答后调用。
        """
        self._interaction_count += 1

    def update(
        self,
        llm_client: "BaseLLMClient",
        recent_interactions: str,
    ) -> None:
        """增量更新 session summary。

        使用 LLM 将当前 summary 与新交互内容合并。
        如果 LLM 调用失败，静默保留旧 summary，不阻塞对话。

        Args:
            llm_client: LLM 客户端。
            recent_interactions: 自上次更新以来的新 Q&A 交互文本。
        """
        if not recent_interactions.strip():
            return

        try:
            if self._summary:
                # 增量更新：合并旧 summary + 新交互
                messages = [
                    Message(
                        role=Role.SYSTEM,
                        content=_SUMMARY_UPDATE_PROMPT,
                    ),
                    Message(
                        role=Role.USER,
                        content=(
                            f"当前会话概要：\n{self._summary}\n\n"
                            f"新的交互内容：\n{recent_interactions}"
                        ),
                    ),
                ]
            else:
                # 首次生成
                messages = [
                    Message(
                        role=Role.SYSTEM,
                        content=_SUMMARY_INIT_PROMPT,
                    ),
                    Message(
                        role=Role.USER,
                        content=recent_interactions,
                    ),
                ]

            response = llm_client.chat(
                messages=messages,
                temperature=0.2,
                max_tokens=settings.agent.session_summary_max_tokens,
            )

            result = (response.content or "").strip()
            if result and result != "无":
                old_len = len(self._summary)
                self._summary = result
                self._last_update_count = self._interaction_count
                logger.info(
                    "Session Summary 已更新 | 交互数={} | 摘要长度: {} → {}",
                    self._interaction_count, old_len, len(result),
                )
            else:
                logger.debug("LLM 判断无需更新 Session Summary")

        except Exception as e:
            # 静默降级：保留旧 summary，不阻塞对话
            logger.warning("Session Summary 更新失败（保留旧摘要）: {}", e)

    def serialize(self) -> Dict[str, Any]:
        """序列化为可 JSON 化的字典（用于会话持久化）。"""
        return {
            "summary": self._summary,
            "interaction_count": self._interaction_count,
            "last_update_count": self._last_update_count,
            "archive_watermark": self._archive_watermark,
        }

    def restore_from(self, data: Dict[str, Any]) -> None:
        """从持久化数据恢复。

        Args:
            data: serialize() 生成的字典。
        """
        if not data:
            return
        self._summary = data.get("summary", "")
        self._interaction_count = data.get("interaction_count", 0)
        self._last_update_count = data.get("last_update_count", 0)
        self._archive_watermark = data.get("archive_watermark", 0)
        logger.debug(
            "SessionSummary 已恢复 | interactions={} | watermark={} | summary_len={}",
            self._interaction_count, self._archive_watermark, len(self._summary),
        )

    @staticmethod
    def extract_recent_interactions(
        messages: List[Message],
        system_prompt_count: int,
        start_index: int,
    ) -> str:
        """从消息列表中提取指定范围的 Q&A 交互文本。

        用于构造传给 LLM 的 recent_interactions 参数。

        Args:
            messages: ConversationMemory 的完整消息列表。
            system_prompt_count: system prompt 消息数量。
            start_index: 起始索引（相对于 messages，含 system prompt 偏移）。

        Returns:
            提取的交互文本，user/assistant 消息拼接。
        """
        parts = []
        for msg in messages[start_index:]:
            if msg.role == Role.USER and msg.content:
                parts.append(f"用户: {msg.content[:200]}")
            elif msg.role == Role.ASSISTANT and msg.content and not msg.tool_calls:
                parts.append(f"助手: {msg.content[:300]}")
        return "\n".join(parts)
