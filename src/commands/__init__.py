"""系统命令模块。

拦截以 "/" 开头的用户输入，在 Agent 之前短路处理，
直接返回系统信息，不消耗 LLM token，不污染对话历史。

架构：
- BaseCommand: 命令抽象基类
- CommandRegistry: 命令注册与路由分发

使用方式：
    registry = CommandRegistry()
    registry.register(MemoryCommand())
    result = registry.dispatch("/memory list", session)
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.utils.logger import logger


class CommandContext:
    """命令执行上下文，封装命令处理所需的各类组件引用。

    避免命令直接依赖 TenantSession / SharedComponents 等内部数据结构，
    保持命令模块的独立性。
    """

    def __init__(
        self,
        tenant_id: str,
        vector_store: Optional[object] = None,
        conversation: Optional[object] = None,
        knowledge_base: Optional[object] = None,
        shared: Optional[object] = None,
    ):
        self.tenant_id = tenant_id
        self.vector_store = vector_store
        self.conversation = conversation
        self.knowledge_base = knowledge_base
        self.shared = shared


class BaseCommand(ABC):
    """系统命令抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """命令名称（不含 /），如 "memory"。"""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """命令简短描述，用于 /help 展示。"""
        ...

    @property
    def usage(self) -> str:
        """命令用法说明，子类可覆写提供详细用法。"""
        return f"/{self.name}"

    @abstractmethod
    def execute(self, args: list[str], ctx: CommandContext) -> str:
        """执行命令并返回 Markdown 格式的结果文本。

        Args:
            args: 命令参数列表（去除命令名后按空格拆分）。
            ctx: 命令执行上下文。

        Returns:
            Markdown 格式的回复文本。
        """
        ...


class CommandRegistry:
    """命令注册器，负责命令注册与路由分发。"""

    def __init__(self):
        self._commands: dict[str, BaseCommand] = {}

    def register(self, command: BaseCommand) -> None:
        """注册一个命令。"""
        self._commands[command.name] = command

    def get(self, name: str) -> Optional[BaseCommand]:
        """按名称获取命令。"""
        return self._commands.get(name)

    @property
    def commands(self) -> dict[str, BaseCommand]:
        """返回所有已注册命令。"""
        return dict(self._commands)

    def dispatch(self, raw_input: str, ctx: CommandContext) -> Optional[str]:
        """解析并分发系统命令。

        Args:
            raw_input: 用户原始输入（如 "/memory search 关键词"）。
            ctx: 命令执行上下文。

        Returns:
            命令执行结果（Markdown 文本），如果不是有效命令则返回 None。
        """
        text = raw_input.strip()
        if not text.startswith("/"):
            return None

        parts = text[1:].split()
        if not parts:
            return None

        cmd_name = parts[0].lower()
        args = parts[1:]

        command = self._commands.get(cmd_name)
        if not command:
            available = ", ".join(f"`/{n}`" for n in sorted(self._commands))
            return f"未知命令 `/{cmd_name}`。可用命令：{available}\n\n输入 `/help` 查看帮助。"

        try:
            return command.execute(args, ctx)
        except Exception as e:
            logger.error("系统命令 /{} 执行失败: {}", cmd_name, e)
            return f"命令执行失败：{e}"
