"""Memory Governor - 长期记忆治理模块。

负责长期记忆的生命周期管理，包括：
- TTL 衰减：定期降低记忆的 value_score
- ANN 归并：利用 ChromaDB HNSW 索引合并相似记忆
- 过期驱逐：移除 value_score 过低或超过 TTL 的记忆
- 价值刷新：根据命中频率和新鲜度更新 value_score

设计要点：
- 后台线程运行，不阻塞主请求链路
- 通过 VectorStore 的 _lock 保证与主线程的原子性
- Feature Flag 控制（memory_governor_enabled），默认关闭
"""

import math
import threading
import time
from typing import Optional, List, Dict, Any

from src.config.settings import settings
from src.memory.vector_store import VectorStore
from src.utils.logger import logger


class MemoryGovernor:
    """长期记忆治理器。

    通过定期后台维护任务，对 VectorStore 中的记忆执行：
    1. value_score 衰减（时间越久分值越低）
    2. 相似记忆归并（ANN 加速，非 N²）
    3. 低价值/过期记忆驱逐
    4. 高频命中记忆的价值刷新
    """

    # ── 衰减参数 ──
    # 半衰期（天）：经过此天数后 value_score 衰减到原来的一半
    DECAY_HALF_LIFE_DAYS: float = 14.0

    def __init__(self, vector_store: VectorStore):
        self._store = vector_store
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    # ── 公共接口 ────────────────────────────────────────────────────────

    def run_maintenance(self) -> Dict[str, int]:
        """执行一次完整的治理周期。

        Returns:
            各阶段处理统计：decayed, merged, evicted, refreshed。
        """
        stats = {"decayed": 0, "merged": 0, "evicted": 0, "refreshed": 0}

        memories = self._store.get_all()
        if not memories:
            logger.debug("Governor: 无记忆需要治理")
            return stats

        logger.info("Governor: 开始治理周期 | 记忆总数={}", len(memories))

        # 阶段顺序：先刷新 → 衰减 → 归并 → 驱逐
        stats["refreshed"] = self._refresh_scores(memories)
        stats["decayed"] = self._decay_scores(memories)
        stats["merged"] = self._merge_similar(memories)
        stats["evicted"] = self._evict_expired()

        logger.info(
            "Governor: 治理完成 | refreshed={} decayed={} merged={} evicted={}",
            stats["refreshed"], stats["decayed"],
            stats["merged"], stats["evicted"],
        )
        return stats

    def start_background(self) -> None:
        """启动后台治理线程。"""
        if self._running:
            logger.warning("Governor: 后台线程已在运行")
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._background_loop,
            name="memory-governor",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "Governor: 后台治理已启动 | 间隔={}s",
            settings.agent.memory_governor_interval,
        )

    def stop_background(self) -> None:
        """停止后台治理线程。"""
        if not self._running:
            return

        self._running = False
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
        self._thread = None
        logger.info("Governor: 后台治理已停止")

    # ── 后台循环 ────────────────────────────────────────────────────────

    def _background_loop(self) -> None:
        """后台线程主循环。"""
        interval = settings.agent.memory_governor_interval
        while not self._stop_event.is_set():
            try:
                self.run_maintenance()
            except Exception as e:
                logger.error("Governor: 治理异常 | error={}", e)
            self._stop_event.wait(timeout=interval)

    # ── 阶段 1: 价值刷新 ──────────────────────────────────────────────

    def _refresh_scores(self, memories: List[Dict[str, Any]]) -> int:
        """根据命中频率和新鲜度刷新 value_score。

        刷新公式：bonus = log2(1 + hit_count) * freshness_factor
        - freshness_factor = 1 / (1 + days_since_last_hit / 7)
        - 最终 value_score = max(current_score, min(bonus, 2.0))
        """
        refreshed = 0
        now = time.time()

        for mem in memories:
            meta = mem["metadata"]
            hit_count = meta.get("hit_count", 0)
            if hit_count == 0:
                continue

            last_hit = meta.get("last_hit", 0.0)
            days_since_hit = (now - last_hit) / 86400 if last_hit > 0 else 30

            freshness = 1.0 / (1.0 + days_since_hit / 7.0)
            bonus = math.log2(1 + hit_count) * freshness
            bonus = min(bonus, 2.0)  # 上限

            current_score = meta.get("value_score", 1.0)
            if bonus > current_score:
                self._store.update_metadata(mem["id"], {"value_score": bonus})
                refreshed += 1

        return refreshed

    # ── 阶段 2: TTL 衰减 ──────────────────────────────────────────────

    def _decay_scores(self, memories: List[Dict[str, Any]]) -> int:
        """基于时间的 value_score 指数衰减。

        衰减公式：new_score = old_score * 2^(-days_elapsed / half_life)
        """
        decayed = 0
        now = time.time()
        half_life = self.DECAY_HALF_LIFE_DAYS

        for mem in memories:
            meta = mem["metadata"]
            timestamp = meta.get("timestamp", now)
            days_elapsed = (now - timestamp) / 86400

            if days_elapsed <= 0:
                continue

            old_score = meta.get("value_score", 1.0)
            new_score = old_score * (2.0 ** (-days_elapsed / half_life))
            new_score = round(new_score, 4)

            if abs(new_score - old_score) > 0.001:
                self._store.update_metadata(mem["id"], {"value_score": new_score})
                decayed += 1

        return decayed

    # ── 阶段 3: ANN 归并 ──────────────────────────────────────────────

    def _merge_similar(self, memories: List[Dict[str, Any]]) -> int:
        """利用 ANN 查找并合并高度相似的记忆。

        使用 ChromaDB HNSW 索引加速邻居搜索（O(N*K)），避免 N² 全量比较。
        合并策略：保留最新文本，累加 hit_count，取较高 value_score。
        """
        merged = 0
        threshold = settings.agent.memory_merge_threshold
        # 已被合并掉的 ID 集合，避免重复处理
        merged_ids: set = set()

        for mem in memories:
            mid = mem["id"]
            if mid in merged_ids:
                continue

            neighbors = self._store.find_neighbors(mid, top_k=3)
            # 筛选出距离低于阈值且未被处理过的邻居
            candidates = [
                n for n in neighbors
                if n["distance"] < threshold and n["id"] not in merged_ids
            ]

            if not candidates:
                continue

            # 收集要合并的记忆（自身 + 候选邻居）
            group = [mem] + candidates
            ids_to_remove = [m["id"] for m in group]

            # 合并策略
            # 文本：取最新的那条
            newest = max(
                group,
                key=lambda m: m["metadata"].get("timestamp", 0),
            )
            merged_text = newest["text"]

            # 元数据合并
            total_hits = sum(
                m["metadata"].get("hit_count", 0) for m in group
            )
            best_score = max(
                m["metadata"].get("value_score", 0) for m in group
            )
            latest_hit = max(
                m["metadata"].get("last_hit", 0) for m in group
            )

            new_meta = {
                "hit_count": total_hits,
                "value_score": best_score,
                "last_hit": latest_hit,
                "source": "governor_merge",
            }

            result_id = self._store.merge_memories(
                ids_to_remove, merged_text, new_meta
            )
            if result_id:
                merged_ids.update(ids_to_remove)
                merged += len(ids_to_remove) - 1  # 减少的记忆数

        return merged

    # ── 阶段 4: 过期驱逐 ──────────────────────────────────────────────

    def _evict_expired(self) -> int:
        """驱逐低价值和超过 TTL 的记忆。

        驱逐条件（满足任一即驱逐）：
        1. value_score < min_value_score
        2. ttl > 0 且当前时间已超过 ttl
        """
        evicted = 0
        now = time.time()
        min_score = settings.agent.memory_min_value_score

        # 重新获取最新数据（前面阶段可能已修改）
        memories = self._store.get_all()
        ids_to_evict = []

        for mem in memories:
            meta = mem["metadata"]
            score = meta.get("value_score", 1.0)
            ttl = meta.get("ttl", 0.0)

            should_evict = False
            if score < min_score:
                should_evict = True
            elif ttl > 0 and now > ttl:
                should_evict = True

            if should_evict:
                ids_to_evict.append(mem["id"])

        if ids_to_evict:
            try:
                # 直接批量删除（使用 VectorStore 内部锁）
                with self._store._lock:
                    self._store._collection.delete(ids=ids_to_evict)
                evicted = len(ids_to_evict)
                logger.info(
                    "Governor: 驱逐 {} 条记忆 | ids={}",
                    evicted, ids_to_evict[:5],
                )
            except Exception as e:
                logger.error("Governor: 驱逐失败 | error={}", e)

        return evicted
