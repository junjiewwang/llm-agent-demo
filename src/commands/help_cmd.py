"""帮助命令。

展示所有可用的系统命令及其用法。
"""

from src.commands import BaseCommand, CommandContext, CommandRegistry


class HelpCommand(BaseCommand):
    """帮助命令，需要引用 CommandRegistry 获取所有已注册命令。"""

    def __init__(self, registry: CommandRegistry):
        self._registry = registry

    @property
    def name(self) -> str:
        return "help"

    @property
    def description(self) -> str:
        return "显示帮助信息"

    def execute(self, args: list[str], ctx: CommandContext) -> str:
        # 如果指定了命令名，显示该命令的详细用法
        if args:
            cmd_name = args[0].lower().lstrip("/")
            cmd = self._registry.get(cmd_name)
            if cmd:
                return f"**/{cmd.name}** — {cmd.description}\n\n用法：\n{cmd.usage}"
            return f"未知命令 `/{cmd_name}`。输入 `/help` 查看所有命令。"

        # 显示所有命令
        lines = ["📖 **可用系统命令**\n"]
        lines.append("| 命令 | 说明 |")
        lines.append("|------|------|")
        for name in sorted(self._registry.commands):
            cmd = self._registry.commands[name]
            lines.append(f"| `/{name}` | {cmd.description} |")

        lines.append("\n输入 `/help <命令名>` 查看详细用法。")
        return "\n".join(lines)
