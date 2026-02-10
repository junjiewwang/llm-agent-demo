"""Agent 抽象基类，定义 Agent 的通用接口。"""

from abc import ABC, abstractmethod

from src.llm.base_client import BaseLLMClient
from src.memory.conversation import ConversationMemory
from src.tools.base_tool import ToolRegistry


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
    def run(self, user_input: str) -> str:
        """处理用户输入，返回最终回答。

        Args:
            user_input: 用户输入的文本。

        Returns:
            Agent 的最终回答文本。
        """
