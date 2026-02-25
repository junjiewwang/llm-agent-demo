"""LLM 客户端抽象基类，定义统一的调用接口。"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional, List

from pydantic import BaseModel


class Role(str, Enum):
    """消息角色枚举。"""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class Message(BaseModel):
    """对话消息模型。"""

    role: Role
    content: Optional[str] = None
    # Function Calling 相关字段
    tool_calls: Optional[List[dict]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None
    # Token 用量（仅 LLM 响应时填充，用于可观测性）
    usage: Optional[dict] = None  # { prompt_tokens, completion_tokens, total_tokens }

    def to_dict(self) -> dict[str, Any]:
        """转换为 API 请求格式，过滤 None 字段。"""
        data: dict[str, Any] = {"role": self.role.value}
        if self.content is not None:
            data["content"] = self.content
        if self.tool_calls is not None:
            data["tool_calls"] = self.tool_calls
        if self.tool_call_id is not None:
            data["tool_call_id"] = self.tool_call_id
        if self.name is not None:
            data["name"] = self.name
        # usage 不参与 API 请求，仅用于内部追踪
        return data


class BaseLLMClient(ABC):
    """LLM 客户端抽象基类。

    所有 LLM 实现（OpenAI、DeepSeek 等）都应继承此基类，
    实现统一的调用接口，使上层 Agent 不依赖具体实现。
    """

    @abstractmethod
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Message:
        """同步对话调用。

        Args:
            messages: 对话消息列表。
            tools: 可用工具的 JSON Schema 描述列表。
            temperature: 生成温度。
            max_tokens: 最大生成 token 数。

        Returns:
            LLM 返回的 Message。
        """

    @abstractmethod
    def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[dict]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        """流式对话调用（生成器）。

        Args:
            messages: 对话消息列表。
            tools: 可用工具的 JSON Schema 描述列表。
            temperature: 生成温度。
            max_tokens: 最大生成 token 数。

        Yields:
            逐步返回的内容片段（str）。
        """
