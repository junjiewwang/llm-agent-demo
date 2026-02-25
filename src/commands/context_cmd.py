"""当前对话上下文命令。

展示当前对话的内存状态：消息数、token 用量、消息列表概览。
"""

from src.commands import BaseCommand, CommandContext


class ContextCommand(BaseCommand):

    @property
    def name(self) -> str:
        return "context"

    @property
    def description(self) -> str:
        return "查看当前对话的上下文状态"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        conv = ctx.conversation
        if not conv:
            return "⚠️ 当前没有活跃的对话。"

        memory = conv.memory
        messages = memory.messages
        token_count = memory.token_count

        # 按角色统计
        role_counts: dict[str, int] = {}
        for msg in messages:
            role = msg.role.value
            role_counts[role] = role_counts.get(role, 0) + 1

        lines = [
            f"📋 **当前对话上下文**\n",
            f"| 指标 | 值 |",
            f"|------|------|",
            f"| 对话 ID | `{conv.id}` |",
            f"| 对话标题 | {conv.title} |",
            f"| 消息总数 | {len(messages)} |",
            f"| Token 用量 | {token_count:,} / {memory._max_tokens:,} |",
            f"| Token 使用率 | {token_count / memory._max_tokens * 100:.1f}% |",
        ]

        # 角色分布
        role_display = {
            "system": "系统", "user": "用户",
            "assistant": "助手", "tool": "工具",
        }
        role_parts = [
            f"{role_display.get(r, r)} {c}"
            for r, c in sorted(role_counts.items())
        ]
        lines.append(f"| 角色分布 | {' / '.join(role_parts)} |")

        # 最近消息预览
        recent = messages[-8:] if len(messages) > 8 else messages
        if recent:
            lines.append(f"\n**最近 {len(recent)} 条消息：**\n")
            for msg in recent:
                role_tag = role_display.get(msg.role.value, msg.role.value)
                content = (msg.content or "").replace("\n", " ")[:60]
                if msg.tool_calls:
                    tool_names = []
                    for tc in msg.tool_calls:
                        fn = tc.get("function", {}) if isinstance(tc, dict) else getattr(tc, "function", {})
                        name = fn.get("name", "?") if isinstance(fn, dict) else getattr(fn, "name", "?")
                        tool_names.append(name)
                    content = f"[调用工具: {', '.join(tool_names)}]"
                elif msg.role.value == "tool":
                    content = f"[{msg.name}] {content}"

                suffix = "..." if len(msg.content or "") > 60 else ""
                lines.append(f"- **{role_tag}**: {content}{suffix}")

        return "\n".join(lines)
