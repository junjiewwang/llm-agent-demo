"""Agent 抽象基类，定义 Agent 的通用接口。"""

from abc import ABC, abstractmethod
from typing import Callable, Optional

from src.agent.events import AgentEvent
from src.llm.base_client import BaseLLMClient
from src.memory.conversation import ConversationMemory
from src.tools.base_tool import ToolRegistry

# 事件回调类型：接收 AgentEvent，无返回值
OnEventCallback = Optional[Callable[[AgentEvent], None]]

# 确认等待回调类型：接收 confirm_id，返回 True(批准)/False(拒绝)/None(超时)
WaitForConfirmation = Optional[Callable[[str], Optional[bool]]]


class BaseAgent(ABC):
    """Agent 抽象基类。

    定义所有 Agent 的通用接口，子类实现具体的推理和执行策略。
    """

    def __init__(
        self,
        llm_client: BaseLLMClient,
        tool_registry: ToolRegistry,
        memory: ConversationMemory,
    ):
        self._llm = llm_client
        self._tools = tool_registry
        self._memory = memory

    @property
    def memory(self) -> ConversationMemory:
        return self._memory

    @abstractmethod
    def run(
        self,
        user_input: str,
        on_event: OnEventCallback = None,
        wait_for_confirmation: WaitForConfirmation = None,
    ) -> str:
        """处理用户输入，返回最终回答。

        Args:
            user_input: 用户输入的文本。
            on_event: 可选的事件回调，用于实时传递思考过程。
            wait_for_confirmation: 可选的确认等待回调，用于高风险工具执行前的用户审批。

        Returns:
            Agent 的最终回答文本。
        """
