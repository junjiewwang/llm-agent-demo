"""长期记忆管理命令。

子命令：
- /memory          查看长期记忆概览和最近记忆列表
- /memory search   按语义搜索长期记忆
- /memory clear    清空所有长期记忆
"""

import time

from src.commands import BaseCommand, CommandContext


class MemoryCommand(BaseCommand):

    @property
    def name(self) -> str:
        return "memory"

    @property
    def description(self) -> str:
        return "查看和管理长期记忆"

    @property
    def usage(self) -> str:
        return (
            "`/memory` — 查看记忆概览\n"
            "`/memory search <关键词>` — 搜索记忆\n"
            "`/memory clear` — 清空所有记忆"
        )

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        if not args:
            return self._overview(ctx)

        sub = args[0].lower()
        if sub == "search":
            query = " ".join(args[1:])
            return self._search(ctx, query)
        if sub == "clear":
            return self._clear(ctx)
        return f"未知子命令 `{sub}`。\n\n用法：\n{self.usage}"

    def _overview(self, ctx: CommandContext) -> str:
        """长期记忆概览：总数 + 最近记忆列表。"""
        vs = ctx.vector_store
        if not vs:
            return "⚠️ 长期记忆未初始化（VectorStore 不可用）。"

        total = vs.count()
        if total == 0:
            return "📭 长期记忆为空，暂无存储的记忆条目。"

        lines = [f"🧠 **长期记忆** — 共 {total} 条\n"]

        # 获取最近的记忆（按时间倒序）
        try:
            result = vs._collection.get(
                limit=min(total, 20),
                include=["documents", "metadatas"],
            )
            items = []
            for i in range(len(result["ids"])):
                doc = result["documents"][i] if result["documents"] else ""
                meta = result["metadatas"][i] if result["metadatas"] else {}
                ts = meta.get("timestamp", 0)
                items.append((ts, result["ids"][i], doc))

            # 按时间倒序排列
            items.sort(key=lambda x: x[0], reverse=True)

            lines.append("| # | 时间 | 内容摘要 |")
            lines.append("|---|------|---------|")
            for idx, (ts, mem_id, doc) in enumerate(items[:15], 1):
                time_str = _format_time(ts) if ts else "—"
                preview = doc[:60].replace("\n", " ") + ("..." if len(doc) > 60 else "")
                lines.append(f"| {idx} | {time_str} | {preview} |")

            if total > 15:
                lines.append(f"\n*（仅显示最近 15 条，共 {total} 条）*")
        except Exception:
            lines.append("*（无法获取记忆列表详情）*")

        lines.append(f"\n💡 使用 `/memory search <关键词>` 按语义搜索记忆")
        return "\n".join(lines)

    def _search(self, ctx: CommandContext, query: str) -> str:
        """按语义搜索长期记忆。"""
        vs = ctx.vector_store
        if not vs:
            return "⚠️ 长期记忆未初始化。"
        if not query.strip():
            return "请提供搜索关键词。用法：`/memory search <关键词>`"

        results = vs.search(query, top_k=10)
        if not results:
            return f"🔍 未找到与「{query}」相关的记忆。"

        lines = [f"🔍 搜索「{query}」— 找到 {len(results)} 条相关记忆\n"]
        lines.append("| # | 相关度 | 内容 |")
        lines.append("|---|--------|------|")
        for idx, item in enumerate(results, 1):
            distance = item.get("distance", 0)
            relevance = f"{(1 - distance) * 100:.0f}%"
            text = item["text"][:80].replace("\n", " ") + ("..." if len(item["text"]) > 80 else "")
            lines.append(f"| {idx} | {relevance} | {text} |")

        return "\n".join(lines)

    def _clear(self, ctx: CommandContext) -> str:
        """清空所有长期记忆。"""
        vs = ctx.vector_store
        if not vs:
            return "⚠️ 长期记忆未初始化。"

        count = vs.count()
        if count == 0:
            return "长期记忆已经是空的。"

        vs.clear()
        return f"🗑️ 已清空 {count} 条长期记忆。"


def _format_time(timestamp: float) -> str:
    """将 Unix 时间戳格式化为人类可读的相对/绝对时间。"""
    if not timestamp:
        return "—"
    try:
        now = time.time()
        diff = now - timestamp
        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return f"{int(diff / 60)}分钟前"
        if diff < 86400:
            return f"{int(diff / 3600)}小时前"
        if diff < 604800:
            return f"{int(diff / 86400)}天前"
        return time.strftime("%m-%d %H:%M", time.localtime(timestamp))
    except Exception:
        return "—"
