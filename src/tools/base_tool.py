"""工具抽象基类。

所有工具继承 BaseTool，实现 execute 方法即可被 Agent 自动发现和调用。
工具通过 JSON Schema 描述参数，与 OpenAI Function Calling 协议对齐。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.tools.result import ToolResult


class BaseTool(ABC):
    """工具抽象基类。

    子类需要实现：
        - name: 工具名称（唯一标识）
        - description: 工具描述（LLM 根据此描述决定是否调用）
        - parameters: 参数的 JSON Schema
        - execute: 实际执行逻辑
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """工具名称，作为 Function Calling 的 function name。"""

    @property
    @abstractmethod
    def description(self) -> str:
        """工具描述，帮助 LLM 理解何时该使用此工具。"""

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]:
        """参数定义，JSON Schema 格式。"""

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """执行工具逻辑。

        Args:
            **kwargs: 由 LLM 传入的参数，与 parameters schema 对应。

        Returns:
            执行结果的字符串表示（将作为 tool message 回传给 LLM）。
        """

    def to_openai_tool(self) -> Dict[str, Any]:
        """转换为 OpenAI Function Calling 的 tool 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册中心。

    管理所有可用工具，提供注册、查找、批量导出等功能。
    Agent 通过 ToolRegistry 获取可用工具列表和执行工具。
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> "ToolRegistry":
        """注册工具，支持链式调用。"""
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册，不允许重复注册")
        self._tools[tool.name] = tool
        return self

    def get(self, name: str) -> BaseTool:
        """根据名称获取工具。"""
        if name not in self._tools:
            raise KeyError(f"工具 '{name}' 未注册，可用工具: {list(self._tools.keys())}")
        return self._tools[name]

    def execute(self, name: str, **kwargs) -> ToolResult:
        """执行指定工具，返回结构化结果。

        自动捕获异常并返回 ToolResult.fail()，
        成功时通过 ToolResult.ok() 自动执行智能截断。
        """
        try:
            tool = self.get(name)
        except KeyError as e:
            return ToolResult.fail(str(e))

        try:
            raw_output = tool.execute(**kwargs)
            return ToolResult.ok(raw_output)
        except Exception as e:
            return ToolResult.fail(f"工具 '{name}' 执行失败: {e}")

    def to_openai_tools(self):
        """导出所有工具为 OpenAI Function Calling 格式。"""
        return [tool.to_openai_tool() for tool in self._tools.values()]

    @property
    def tool_names(self):
        """返回所有已注册工具名称。"""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)
