"""系统状态命令。

展示 Agent 系统的全局运行状态：模型、工具、知识库、长期记忆等。
"""

from src.commands import BaseCommand, CommandContext


class StatusCommand(BaseCommand):

    @property
    def name(self) -> str:
        return "status"

    @property
    def description(self) -> str:
        return "查看系统全局状态"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        shared = ctx.shared
        if not shared:
            return "⚠️ 系统未初始化。"

        lines = ["⚙️ **系统状态**\n"]

        # 模型信息
        lines.append("**模型配置：**\n")
        lines.append(f"| 配置 | 值 |")
        lines.append(f"|------|------|")
        lines.append(f"| 模型 | `{shared.llm_client.model}` |")

        # 工具列表
        tool_names = shared.tool_registry.tool_names
        if tool_names:
            tool_display = ", ".join(f"`{n}`" for n in tool_names)
            lines.append(f"| 已注册工具 | {tool_display} ({len(tool_names)} 个) |")
        else:
            lines.append(f"| 已注册工具 | 无 |")

        # 知识库
        kb = shared.knowledge_base
        if kb:
            lines.append(f"| 知识库 | {kb.count()} 个文档块 |")
        else:
            lines.append(f"| 知识库 | 未启用 |")

        # 长期记忆
        vs = ctx.vector_store
        if vs:
            lines.append(f"| 长期记忆 | {vs.count()} 条 |")
        else:
            lines.append(f"| 长期记忆 | 未启用 |")

        # 当前对话
        conv = ctx.conversation
        if conv:
            lines.append(f"| 当前对话 | {conv.title} (`{conv.id}`) |")
            lines.append(f"| 对话 Token | {conv.memory.token_count:,} |")

        return "\n".join(lines)
