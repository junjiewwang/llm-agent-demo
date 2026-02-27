"""工具环境适配器 - 将 ToolRegistry 适配为 EnvironmentAdapter。

作为 Environment Adapter 的首个实现，包装现有的 ToolRegistry，
使 Agent 可以通过统一的 EnvironmentAdapter 接口调用工具。

这是一个 **桥接层**（Adapter Pattern），不新增任何工具能力，
仅将 ToolRegistry 的接口转换为 EnvironmentAdapter 协议。
"""

from typing import Any, Dict, List

from src.environment.adapter_base import ActionResult, EnvironmentAdapter
from src.tools.base_tool import ToolRegistry
from src.utils.logger import logger


class ToolEnvAdapter(EnvironmentAdapter):
    """将 ToolRegistry 适配为 EnvironmentAdapter 接口。

    映射关系：
    - observe() → 返回已注册工具列表及数量
    - act(name, **kwargs) → 调用 ToolRegistry.execute(name, **kwargs)
    - capabilities() → 委托 ToolRegistry.to_openai_tools()
    """

    def __init__(self, tool_registry: ToolRegistry):
        self._registry = tool_registry

    def observe(self) -> Dict[str, Any]:
        """感知环境状态：返回可用工具信息。"""
        return {
            "type": "tool_environment",
            "available_tools": self._registry.tool_names,
            "tool_count": len(self._registry),
        }

    def act(self, action_name: str, **kwargs: Any) -> ActionResult:
        """执行工具调用。

        将 ToolRegistry.execute() 返回的 ToolResult
        转换为 ActionResult。
        """
        try:
            tool_result = self._registry.execute(action_name, **kwargs)
            return ActionResult(
                success=tool_result.success,
                output=tool_result.output,
                error=None if tool_result.success else tool_result.output,
                metadata={
                    "tool_name": action_name,
                    "source": "tool_registry",
                },
            )
        except KeyError:
            logger.warning("ToolEnvAdapter: 未知工具 '{}'", action_name)
            return ActionResult.fail(
                error=f"未知工具: {action_name}",
                tool_name=action_name,
            )
        except Exception as e:
            logger.error(
                "ToolEnvAdapter: 工具执行异常 | tool={} | error={}",
                action_name, e,
            )
            return ActionResult.fail(
                error=str(e),
                tool_name=action_name,
            )

    def capabilities(self) -> List[Dict[str, Any]]:
        """返回所有已注册工具的 OpenAI tools schema。"""
        return self._registry.to_openai_tools()
