"""向量存储模块 - 长期记忆。

基于 ChromaDB 实现语义检索的长期记忆，支持：
- 存储对话片段和重要信息
- 基于语义相似度检索相关记忆
- 持久化存储到本地磁盘
"""

import time
from typing import Optional, List, Dict, Any

from src.utils.logger import logger

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False
    logger.warning("chromadb 未安装，长期记忆功能不可用")


class VectorStore:
    """基于 ChromaDB 的向量存储。

    提供长期记忆的存储和语义检索能力。
    使用 ChromaDB 内置的 Embedding 模型（无需额外 API 调用）。
    """

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_directory: Optional[str] = None,
    ):
        """
        Args:
            collection_name: 集合名称。
            persist_directory: 持久化目录，为 None 则使用内存模式。
        """
        if not _CHROMADB_AVAILABLE:
            raise RuntimeError("chromadb 未安装，请执行: pip install chromadb")

        if persist_directory:
            self._client = chromadb.PersistentClient(path=persist_directory)
            logger.info("向量存储初始化（持久化模式）| 路径: {}", persist_directory)
        else:
            self._client = chromadb.Client()
            logger.info("向量存储初始化（内存模式）")

        self._collection = self._client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    # 去重阈值：cosine distance 低于此值认为是重复记忆
    DEDUP_DISTANCE_THRESHOLD = 0.3

    def add(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        dedup: bool = True,
    ) -> Optional[str]:
        """存储一条记忆，支持自动去重。

        Args:
            text: 要存储的文本内容。
            metadata: 附加元数据（如来源、时间等）。
            dedup: 是否启用去重，为 True 时会先检索是否已有高度相似的记忆。

        Returns:
            记忆 ID（新增或更新的），如果完全重复则返回已有 ID。
        """
        meta = metadata or {}
        meta["timestamp"] = time.time()

        # 去重检查：如果已有高度相似的记忆，更新而非新增
        if dedup and self._collection.count() > 0:
            existing = self._find_duplicate(text)
            if existing:
                # 更新已有记忆（用新内容替换旧内容）
                self._collection.update(
                    ids=[existing["id"]],
                    documents=[text],
                    metadatas=[meta],
                )
                logger.debug(
                    "更新已有记忆（去重）| id={} | distance={:.3f}",
                    existing["id"], existing["distance"],
                )
                return existing["id"]

        # 新增记忆
        doc_id = f"mem_{int(time.time() * 1000)}"
        self._collection.add(
            documents=[text],
            metadatas=[meta],
            ids=[doc_id],
        )
        logger.debug("存储新记忆 | id={} | text={}", doc_id, text[:100])
        return doc_id

    def _find_duplicate(self, text: str) -> Optional[Dict[str, Any]]:
        """查找与给定文本高度相似的已有记忆。

        Returns:
            最相似的记忆（如果 distance < 阈值），否则返回 None。
        """
        results = self._collection.query(
            query_texts=[text],
            n_results=1,
        )
        if not results["ids"] or not results["ids"][0]:
            return None

        distance = results["distances"][0][0] if results["distances"] else 1.0
        if distance < self.DEDUP_DISTANCE_THRESHOLD:
            return {
                "id": results["ids"][0][0],
                "text": results["documents"][0][0],
                "distance": distance,
            }
        return None

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """语义检索相关记忆。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 K 条结果。

        Returns:
            结果列表，每项包含 id, text, metadata, distance。
        """
        if self._collection.count() == 0:
            return []

        # 确保 top_k 不超过已存储的文档数
        actual_k = min(top_k, self._collection.count())

        results = self._collection.query(
            query_texts=[query],
            n_results=actual_k,
        )

        items = []
        for i in range(len(results["ids"][0])):
            items.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })

        logger.debug("检索记忆 | query={} | 返回 {} 条", query[:50], len(items))
        return items

    def count(self) -> int:
        """返回已存储的记忆条数。"""
        return self._collection.count()

    def clear(self) -> None:
        """清空所有记忆。"""
        # ChromaDB 没有直接的 clear 方法，需要删除并重建集合
        name = self._collection.name
        metadata = self._collection.metadata
        self._client.delete_collection(name)
        self._collection = self._client.get_or_create_collection(
            name=name,
            metadata=metadata,
        )
        logger.info("长期记忆已清空")
