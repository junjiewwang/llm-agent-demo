"""工具抽象基类。

所有工具继承 BaseTool，实现 execute 方法即可被 Agent 自动发现和调用。
工具通过 JSON Schema 描述参数，与 OpenAI Function Calling 协议对齐。
"""

from abc import ABC, abstractmethod
from typing import Any, Dict

from src.observability import get_tracer
from src.observability.instruments import trace_span, set_span_content
from src.tools.result import ToolResult

_tracer = get_tracer(__name__)


class BaseTool(ABC):
    """工具抽象基类。

    子类需要实现：
        - name: 工具名称（唯一标识）
        - description: 工具描述（LLM 根据此描述决定是否调用）
        - parameters: 参数的 JSON Schema
        - execute: 实际执行逻辑
    """

    _STRUCTURED_TOOL_CONTRACT: str = (
        "\n\n[结构化调用约束] "
        "本工具是结构化参数 API，不是 shell 终端。"
        "参数中不得使用 shell 语法（| > < ; &）。"
        "如需筛选或处理，请使用工具原生参数，或先获取结果再分析。"
    )
    """统一注入到所有工具 description 的结构化调用契约。"""

    _enable_structured_contract: bool = True
    """是否启用结构化调用契约。子类可覆写为 False 以关闭。"""

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

    def should_confirm(self, **kwargs) -> bool:
        """判断本次调用是否需要用户确认。

        子类可覆写此方法，根据具体参数判断是否为高风险操作。
        默认返回 False（不需要确认）。

        Args:
            **kwargs: 与 execute 相同的参数。

        Returns:
            True 表示需要用户确认后才能执行。
        """
        return False

    def _build_description(self) -> str:
        """组合子类 description + 统一结构化工具调用契约。"""
        if self._enable_structured_contract:
            return self.description + self._STRUCTURED_TOOL_CONTRACT
        return self.description

    def to_openai_tool(self) -> Dict[str, Any]:
        """转换为 OpenAI Function Calling 的 tool 格式。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self._build_description(),
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """工具注册中心。

    管理所有可用工具，提供注册、查找、批量导出等功能。
    Agent 通过 ToolRegistry 获取可用工具列表和执行工具。

    支持别名机制：通过 register_alias() 为工具注册标准化别名，
    使 Skill 的 required_tools 可以使用通用名称（如 fs_read）
    而非具体实现名称（如 file_reader），实现 Skill 与工具实现的解耦。
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._aliases: Dict[str, str] = {}  # alias → canonical name

    def register(self, tool: BaseTool) -> "ToolRegistry":
        """注册工具，支持链式调用。"""
        if tool.name in self._tools:
            raise ValueError(f"工具 '{tool.name}' 已注册，不允许重复注册")
        self._tools[tool.name] = tool
        return self

    def register_alias(self, alias: str, target: str) -> "ToolRegistry":
        """为已注册的工具注册别名，支持链式调用。

        别名用于 Skill 的 required_tools 校验和运行时工具查找，
        使 Skill 声明与工具实现名称解耦。

        Args:
            alias: 别名（如 'fs_read'）。
            target: 目标工具的真实名称（如 'file_reader'），必须已注册。

        Raises:
            ValueError: 别名与已有工具名或别名冲突，或目标工具未注册。
        """
        if alias in self._tools:
            raise ValueError(f"别名 '{alias}' 与已注册工具名冲突")
        if alias in self._aliases:
            raise ValueError(f"别名 '{alias}' 已存在（指向 '{self._aliases[alias]}'）")
        if target not in self._tools:
            raise ValueError(f"目标工具 '{target}' 未注册，无法创建别名 '{alias}'")
        self._aliases[alias] = target
        return self

    def _resolve(self, name: str) -> str:
        """将名称解析为真实工具名（若为别名则转换，否则原样返回）。"""
        return self._aliases.get(name, name)

    def get(self, name: str) -> BaseTool:
        """根据名称或别名获取工具。"""
        canonical = self._resolve(name)
        if canonical not in self._tools:
            raise KeyError(f"工具 '{name}' 未注册，可用工具: {list(self._tools.keys())}")
        return self._tools[canonical]

    def execute(self, name: str, **kwargs) -> ToolResult:
        """执行指定工具，返回结构化结果。

        自动捕获异常并返回 ToolResult.fail()，
        成功时通过 ToolResult.ok() 自动执行智能截断。
        每次执行创建 tool.execute.{name} span 用于可观测性。
        """
        canonical = self._resolve(name)
        try:
            tool = self.get(canonical)
        except KeyError as e:
            return ToolResult.fail(str(e))

        with trace_span(_tracer, f"tool.execute.{canonical}", {"tool.name": canonical}) as span:
            set_span_content(span, "tool.input", str(kwargs))
            try:
                raw_output = tool.execute(**kwargs)
                result = ToolResult.ok(raw_output)
                span.set_attribute("tool.success", True)
                set_span_content(span, "tool.output", result.to_message()[:500])
                return result
            except Exception as e:
                result = ToolResult.fail(f"工具 '{canonical}' 执行失败: {e}")
                span.set_attribute("tool.success", False)
                span.set_attribute("tool.error", str(e))
                return result

    def to_openai_tools(self):
        """导出所有工具为 OpenAI Function Calling 格式。"""
        return [tool.to_openai_tool() for tool in self._tools.values()]

    @property
    def tool_names(self):
        """返回所有已注册工具名称（含别名）。"""
        return list(self._tools.keys()) + list(self._aliases.keys())

    def get_tools_summary(self) -> str:
        """生成可读的工具列表摘要，用于注入 LLM 上下文。

        格式示例：
            已注册工具（共 6 个）：
            - calculator: 数学计算器
            - file_reader (别名: fs_read): 读取文件内容
        """
        # 构建反向别名映射：canonical → [alias1, alias2]
        reverse_aliases: Dict[str, list] = {}
        for alias, canonical in self._aliases.items():
            reverse_aliases.setdefault(canonical, []).append(alias)

        lines = [f"已注册工具（共 {len(self._tools)} 个）："]
        for name, tool in self._tools.items():
            aliases = reverse_aliases.get(name)
            alias_suffix = f" (别名: {', '.join(sorted(aliases))})" if aliases else ""
            # 取 description 第一句作为简要说明
            desc = tool.description.split("。")[0].split("\n")[0].strip()
            lines.append(f"- {name}{alias_suffix}: {desc}")

        return "\n".join(lines)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        """支持 `name in registry` 检查（含别名）。"""
        return name in self._tools or name in self._aliases
