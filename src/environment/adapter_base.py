"""环境适配器抽象基类。

定义 Agent 与外部环境交互的统一接口：
- observe(): 感知环境当前状态
- act(): 在环境中执行动作
- capabilities(): 声明可用能力（供 LLM 做工具选择）

该抽象层使 Agent 不再直接耦合 ToolRegistry，
为后续对接多种环境后端（API、Sandbox、模拟器等）提供扩展点。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ActionResult:
    """环境动作执行结果。

    Attributes:
        success: 执行是否成功。
        output: 执行输出（文本形式，供 LLM 消费）。
        error: 错误信息（仅失败时有值）。
        metadata: 附加元数据（如耗时、来源等）。
    """

    success: bool
    output: str
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls, output: str, **metadata: Any) -> "ActionResult":
        """构建成功结果。"""
        return cls(success=True, output=output, metadata=metadata)

    @classmethod
    def fail(cls, error: str, **metadata: Any) -> "ActionResult":
        """构建失败结果。"""
        return cls(success=False, output="", error=error, metadata=metadata)


class EnvironmentAdapter(ABC):
    """环境适配器抽象基类。

    每个适配器实现代表一种环境交互方式（工具调用、API、沙箱等）。
    Agent 通过此接口与环境解耦。
    """

    @abstractmethod
    def observe(self) -> Dict[str, Any]:
        """感知环境当前状态。

        Returns:
            环境状态信息字典，包含可用能力、资源状态等。
        """

    @abstractmethod
    def act(self, action_name: str, **kwargs: Any) -> ActionResult:
        """在环境中执行指定动作。

        Args:
            action_name: 动作名称（对应工具名、API 操作等）。
            **kwargs: 动作参数。

        Returns:
            ActionResult 执行结果。
        """

    @abstractmethod
    def capabilities(self) -> List[Dict[str, Any]]:
        """声明当前环境支持的能力列表。

        返回格式兼容 OpenAI Function Calling tools schema，
        使 LLM 能够基于此选择可用工具/动作。

        Returns:
            能力列表（OpenAI tools 格式）。
        """
