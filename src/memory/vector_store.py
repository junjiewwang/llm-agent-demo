"""向量存储模块 - 长期记忆。

基于 ChromaDB 实现语义检索的长期记忆，支持：
- 存储对话片段和重要信息
- 基于语义相似度检索相关记忆
- 持久化存储到本地磁盘
- Governor 治理所需的 metadata 扩展（value_score / ttl / hit_count 等）
- 原子合并操作（merge_memories），防止并发竞态
"""

import threading
import time
from typing import Optional, List, Dict, Any

from src.utils.logger import logger

try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False
    logger.warning("chromadb 未安装，长期记忆功能不可用")

# ── Governor metadata 默认值 ──────────────────────────────────────────
# 用于向后兼容：旧记忆缺少这些字段时，自动补齐
_GOVERNOR_META_DEFAULTS: Dict[str, Any] = {
    "value_score": 1.0,
    "hit_count": 0,
    "last_hit": 0.0,
    "ttl": 0.0,       # 0 表示永不过期（旧数据默认策略）
    "cluster_id": "",
}

# Governor metadata 所有字段名
_GOVERNOR_META_KEYS = frozenset(_GOVERNOR_META_DEFAULTS.keys())


def _ensure_governor_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """为缺少 Governor 字段的旧记忆补齐默认值（向后兼容）。"""
    for key, default in _GOVERNOR_META_DEFAULTS.items():
        if key not in meta:
            meta[key] = default
    return meta


class VectorStore:
    """基于 ChromaDB 的向量存储。

    提供长期记忆的存储和语义检索能力。
    使用 ChromaDB 内置的 Embedding 模型（无需额外 API 调用）。

    Governor 扩展 metadata 字段：
    - value_score (float): 价值评分，初始 1.0，由 Governor 衰减/刷新
    - hit_count (int): 命中次数
    - last_hit (float): 上次命中时间戳
    - ttl (float): 过期时间戳（0 = 永不过期）
    - cluster_id (str): 归并簇 ID
    """

    def __init__(
        self,
        collection_name: str = "agent_memory",
        persist_directory: Optional[str] = None,
        default_ttl_days: float = 0,
    ):
        """
        Args:
            collection_name: 集合名称。
            persist_directory: 持久化目录，为 None 则使用内存模式。
            default_ttl_days: 新记忆默认 TTL 天数，0 表示永不过期。
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

        self._default_ttl_days = default_ttl_days
        # 应用级锁：保护 merge_memories 等复合操作的原子性
        self._lock = threading.Lock()

    # 去重阈值：cosine distance 低于此值认为是重复记忆
    DEDUP_DISTANCE_THRESHOLD = 0.3

    # ── 写入 ────────────────────────────────────────────────────────────

    def add(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        dedup: bool = True,
    ) -> Optional[str]:
        """存储一条记忆，支持自动去重。

        新增记忆时自动填充 Governor metadata 字段。

        Args:
            text: 要存储的文本内容。
            metadata: 附加元数据（如来源、时间等）。
            dedup: 是否启用去重，为 True 时会先检索是否已有高度相似的记忆。

        Returns:
            记忆 ID（新增或更新的），如果完全重复则返回已有 ID。
        """
        now = time.time()
        meta = metadata or {}
        meta["timestamp"] = now

        # 填充 Governor metadata 默认值
        meta.setdefault("value_score", 1.0)
        meta.setdefault("hit_count", 0)
        meta.setdefault("last_hit", now)
        meta.setdefault("cluster_id", "")
        # TTL：调用方未指定则使用默认策略
        if "ttl" not in meta:
            meta["ttl"] = (
                now + self._default_ttl_days * 86400
                if self._default_ttl_days > 0
                else 0.0
            )

        with self._lock:
            # 去重检查：如果已有高度相似的记忆，更新而非新增
            if dedup and self._collection.count() > 0:
                existing = self._find_duplicate(text)
                if existing:
                    # 去重更新时保留原有 hit_count 并累加
                    meta["hit_count"] = existing.get("hit_count", 0) + 1
                    meta["last_hit"] = now
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
            doc_id = f"mem_{int(now * 1000)}"
            self._collection.add(
                documents=[text],
                metadatas=[meta],
                ids=[doc_id],
            )
            logger.debug("存储新记忆 | id={} | text={}", doc_id, text[:100])
            return doc_id

    # ── 查询 ────────────────────────────────────────────────────────────

    def _find_duplicate(self, text: str) -> Optional[Dict[str, Any]]:
        """查找与给定文本高度相似的已有记忆。

        Returns:
            最相似的记忆（如果 distance < 阈值），否则返回 None。
            结果包含 id, text, distance 以及 hit_count（用于去重累加）。
        """
        results = self._collection.query(
            query_texts=[text],
            n_results=1,
        )
        if not results["ids"] or not results["ids"][0]:
            return None

        distance = results["distances"][0][0] if results["distances"] else 1.0
        if distance < self.DEDUP_DISTANCE_THRESHOLD:
            meta = results["metadatas"][0][0] if results["metadatas"] else {}
            return {
                "id": results["ids"][0][0],
                "text": results["documents"][0][0],
                "distance": distance,
                "hit_count": meta.get("hit_count", 0),
            }
        return None

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """语义检索相关记忆。

        返回结果的 metadata 自动补齐 Governor 字段（向后兼容旧数据）。

        Args:
            query: 查询文本。
            top_k: 返回最相关的 K 条结果。

        Returns:
            结果列表，每项包含 id, text, metadata, distance。
        """
        if self._collection.count() == 0:
            return []

        actual_k = min(top_k, self._collection.count())

        results = self._collection.query(
            query_texts=[query],
            n_results=actual_k,
        )

        items = []
        for i in range(len(results["ids"][0])):
            raw_meta = results["metadatas"][0][i] if results["metadatas"] else {}
            items.append({
                "id": results["ids"][0][i],
                "text": results["documents"][0][i],
                "metadata": _ensure_governor_meta(raw_meta),
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })

        logger.debug("检索记忆 | query={} | 返回 {} 条", query[:50], len(items))
        return items

    def find_neighbors(
        self, memory_id: str, top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """基于 ANN 查找与指定记忆最相似的邻居。

        利用 ChromaDB 内置的 HNSW 索引加速，避免 N² 全量比较。

        Args:
            memory_id: 目标记忆 ID。
            top_k: 返回最近邻数量（不含自身）。

        Returns:
            邻居列表，每项包含 id, text, metadata, distance。
            如果目标记忆不存在则返回空列表。
        """
        try:
            target = self._collection.get(ids=[memory_id], include=["embeddings"])
        except Exception:
            logger.warning("find_neighbors: 记忆 {} 不存在", memory_id)
            return []

        if not target["ids"] or target["embeddings"] is None or len(target["embeddings"]) == 0:
            return []

        embedding = target["embeddings"][0]
        # +1 因为结果可能包含自身
        actual_k = min(top_k + 1, self._collection.count())
        results = self._collection.query(
            query_embeddings=[embedding],
            n_results=actual_k,
        )

        items = []
        for i in range(len(results["ids"][0])):
            rid = results["ids"][0][i]
            if rid == memory_id:
                continue  # 排除自身
            raw_meta = results["metadatas"][0][i] if results["metadatas"] else {}
            items.append({
                "id": rid,
                "text": results["documents"][0][i],
                "metadata": _ensure_governor_meta(raw_meta),
                "distance": results["distances"][0][i] if results["distances"] else 0,
            })

        return items[:top_k]

    # ── Governor 操作 ──────────────────────────────────────────────────

    def get_all(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """获取全部记忆（供 Governor 批量扫描）。

        Args:
            limit: 最大返回条数，防止内存溢出。

        Returns:
            记忆列表，每项包含 id, text, metadata。
            metadata 自动补齐 Governor 字段。
        """
        total = self._collection.count()
        if total == 0:
            return []

        actual_limit = min(limit, total)
        results = self._collection.get(
            limit=actual_limit,
            include=["documents", "metadatas"],
        )

        items = []
        for i in range(len(results["ids"])):
            raw_meta = results["metadatas"][i] if results["metadatas"] else {}
            items.append({
                "id": results["ids"][i],
                "text": results["documents"][i],
                "metadata": _ensure_governor_meta(raw_meta),
            })
        return items

    def update_metadata(
        self, memory_id: str, updates: Dict[str, Any]
    ) -> bool:
        """更新指定记忆的 metadata 字段。

        仅合并更新传入的字段，不影响其他字段。

        Args:
            memory_id: 记忆 ID。
            updates: 需要更新的 metadata 键值对。

        Returns:
            True 表示更新成功，False 表示记忆不存在。
        """
        try:
            existing = self._collection.get(
                ids=[memory_id], include=["metadatas"]
            )
        except Exception:
            return False

        if not existing["ids"]:
            return False

        current_meta = existing["metadatas"][0] if existing["metadatas"] else {}
        current_meta.update(updates)

        self._collection.update(
            ids=[memory_id],
            metadatas=[current_meta],
        )
        return True

    def merge_memories(
        self,
        ids_to_remove: List[str],
        new_text: str,
        new_metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """原子合并多条记忆：删除旧记忆 + 新增合并后的记忆。

        使用应用级锁保证操作原子性，防止 Governor 后台线程
        与主请求线程之间的竞态。

        Args:
            ids_to_remove: 需要删除的旧记忆 ID 列表。
            new_text: 合并后的文本内容。
            new_metadata: 合并后的 metadata（未指定则自动填充默认值）。

        Returns:
            新记忆 ID，失败返回 None。
        """
        if not ids_to_remove:
            return None

        now = time.time()
        meta = new_metadata or {}
        meta["timestamp"] = now
        meta.setdefault("value_score", 1.0)
        meta.setdefault("hit_count", 0)
        meta.setdefault("last_hit", now)
        meta.setdefault("cluster_id", "")
        if "ttl" not in meta:
            meta["ttl"] = (
                now + self._default_ttl_days * 86400
                if self._default_ttl_days > 0
                else 0.0
            )

        new_id = f"mem_{int(now * 1000)}"

        with self._lock:
            try:
                # 先删除旧记忆
                self._collection.delete(ids=ids_to_remove)
                # 再新增合并后的记忆
                self._collection.add(
                    documents=[new_text],
                    metadatas=[meta],
                    ids=[new_id],
                )
                logger.info(
                    "合并记忆 | 删除 {} 条 → 新增 {} | text={}",
                    len(ids_to_remove), new_id, new_text[:80],
                )
                return new_id
            except Exception as e:
                logger.error("合并记忆失败 | ids={} | error={}", ids_to_remove, e)
                return None

    # ── 基础操作 ────────────────────────────────────────────────────────

    def count(self) -> int:
        """返回已存储的记忆条数。"""
        return self._collection.count()

    def clear(self) -> None:
        """清空所有记忆。"""
        with self._lock:
            name = self._collection.name
            metadata = self._collection.metadata
            self._client.delete_collection(name)
            self._collection = self._client.get_or_create_collection(
                name=name,
                metadata=metadata,
            )
        logger.info("长期记忆已清空")
