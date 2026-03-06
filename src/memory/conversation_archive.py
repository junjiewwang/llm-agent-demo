"""对话归档模块 — 历史交互的向量化存储与语义检索。

将 Recent Window 之外的 Q&A 交互对摘要化后存入 ChromaDB，
支持按语义检索历史交互片段，实现分层对话记忆架构。

设计理念：
- 借鉴 MemGPT 的 Recall Storage：全量对话历史的向量化归档
- 与现有 VectorStore（长期记忆）独立：使用专用 collection，互不干扰
- 无需 LLM 调用：摘要直接拼接 user + assistant 文本，避免额外 API 开销
- 轻量检索：返回精简摘要（~100 tokens/条），不占用大量 History Zone 预算

消息生命周期：
    Recent Window（完整消息）→ 被挤出 → archive()（摘要存入 ChromaDB）
    → search(query)（语义检索 top-K 相关交互摘要）→ 注入 Archive Zone
"""

import time
from typing import Dict, List, Optional, Any

from src.llm.base_client import Message, Role
from src.utils.logger import logger

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False


class ConversationArchive:
    """对话历史的向量化归档存储。

    每个租户拥有独立的 ChromaDB collection（archive-{tenant_id}），
    存储该租户所有对话中被挤出 Recent Window 的 Q&A 交互摘要。

    与 VectorStore（长期记忆）的区别：
    - VectorStore：存储 LLM 提取的**关键事实**（如"用户偏好 Python"），跨会话复用
    - ConversationArchive：存储完整的**交互摘要**（如"用户问了 TAPD 需求状态，查到 30 条"），
      保留对话上下文，支持按语义召回相关历史
    """

    # 摘要截断限制
    MAX_SUMMARY_CHARS = 500

    def __init__(
        self,
        collection_name: str = "conversation_archive",
        persist_directory: Optional[str] = None,
    ):
        """
        Args:
            collection_name: ChromaDB collection 名称。
            persist_directory: 持久化目录，为 None 则使用内存模式。
        """
        if not _CHROMADB_AVAILABLE:
            raise RuntimeError("chromadb 未安装，请执行: pip install chromadb")

        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
        else:
            self._client = chromadb.Client()

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.debug(
            "ConversationArchive 初始化 | collection={} | persist={}",
            collection_name, persist_directory or "memory",
        )

    def archive(
        self,
        messages: List[Message],
        session_id: str = "",
        conversation_id: str = "",
    ) -> Optional[str]:
        """将一组消息（通常是一个完整的 Q&A 交互）归档。

        从消息列表中提取 user 和 assistant 内容，拼接为交互摘要，
        存入 ChromaDB 进行向量索引。

        不依赖 LLM 调用，直接拼接文本——这是有意为之的设计权衡：
        - 优点：零 API 开销、零延迟、确定性
        - 缺点：摘要不如 LLM 生成的精炼
        - 但 Archive 的主要价值是**语义检索**（靠 Embedding 模型），
          而非摘要本身的阅读体验

        Args:
            messages: 要归档的消息列表（可以是完整的 Q&A 交互对）。
            session_id: 所属会话 ID（用于 metadata 过滤）。
            conversation_id: 所属对话 ID。

        Returns:
            归档记录 ID；消息为空或无有效内容时返回 None。
        """
        if not messages:
            return None

        summary = self._build_summary(messages)
        if not summary.strip():
            return None

        now = time.time()
        doc_id = f"arc_{int(now * 1000)}"
        metadata: Dict[str, Any] = {
            "timestamp": now,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "message_count": len(messages),
        }

        # 提取 topic hint：取第一条 user 消息的前 100 字符
        for msg in messages:
            if msg.role == Role.USER and msg.content:
                metadata["topic"] = msg.content[:100]
                break

        self._collection.add(
            documents=[summary],
            metadatas=[metadata],
            ids=[doc_id],
        )
        logger.debug(
            "对话归档 | id={} | conv={} | msgs={} | summary={}",
            doc_id, conversation_id[:8] if conversation_id else "?",
            len(messages), summary[:80],
        )
        return doc_id

    def search(
        self,
        query: str,
        top_k: int = 3,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按语义检索相关的历史交互摘要。

        Args:
            query: 查询文本（通常是用户的当前输入）。
            top_k: 返回最相关的 K 条结果。
            conversation_id: 可选，限定只检索指定对话的归档。

        Returns:
            结果列表，每项包含 id, text, metadata, distance。
        """
        if self._collection.count() == 0:
            return []

        actual_k = min(top_k, self._collection.count())
        where_filter = None
        if conversation_id:
            where_filter = {"conversation_id": conversation_id}

        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=actual_k,
                where=where_filter,
            )
        except Exception as e:
            logger.warning("对话归档检索失败: {}", e)
            return []

        items = []
        for i in range(len(results["ids"][0])):
            items.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })

        logger.debug("对话归档检索 | query={} | 返回 {} 条", query[:50], len(items))
        return items

    def count(self) -> int:
        """返回已归档的记录数。"""
        return self._collection.count()

    def clear(self) -> None:
        """清空所有归档。"""
        name = self._collection.name
        metadata = self._collection.metadata
        self._client.delete_collection(name)
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata=metadata,
        )
        logger.info("对话归档已清空")

    @classmethod
    def _build_summary(cls, messages: List[Message]) -> str:
        """从消息列表中提取交互摘要。

        策略：
        - 提取 USER 和 ASSISTANT（非 tool_calls）消息的文本内容
        - TOOL 消息跳过（已在 Sprint 1 工具结果精简中处理）
        - 截断到 MAX_SUMMARY_CHARS 防止单条归档过大

        Returns:
            交互摘要文本。
        """
        parts = []
        for msg in messages:
            if msg.role == Role.USER and msg.content:
                text = msg.content[:200]
                parts.append(f"用户: {text}")
            elif msg.role == Role.ASSISTANT and msg.content and not msg.tool_calls:
                text = msg.content[:300]
                parts.append(f"助手: {text}")

        summary = "\n".join(parts)
        if len(summary) > cls.MAX_SUMMARY_CHARS:
            summary = summary[:cls.MAX_SUMMARY_CHARS] + "..."
        return summary
