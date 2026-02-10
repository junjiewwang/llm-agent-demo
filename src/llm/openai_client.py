"""基于 OpenAI 兼容协议的 LLM 客户端实现。

支持所有兼容 OpenAI API 的服务商（OpenAI、DeepSeek、通义千问等），
只需配置不同的 base_url 和 api_key 即可切换。
"""

from typing import Any, Generator, Optional, List, Dict

from openai import OpenAI

from src.config import settings
from src.llm.base_client import BaseLLMClient, Message, Role
from src.utils.logger import logger
from src.utils.retry import llm_retry


class OpenAIClient(BaseLLMClient):
    """OpenAI 兼容协议的 LLM 客户端。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self._api_key = api_key or settings.llm.api_key
        self._base_url = base_url or settings.llm.base_url
        self._model = model or settings.llm.model

        if not self._api_key:
            raise ValueError(
                "LLM API Key 未配置，请在 .env 文件中设置 LLM_API_KEY"
            )

        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )
        logger.info(
            "LLM Client 初始化完成 | model={} | base_url={}",
            self._model,
            self._base_url,
        )

    @property
    def model(self) -> str:
        return self._model

    @llm_retry(max_attempts=3)
    def chat(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Message:
        """同步对话调用。"""
        kwargs = self._build_request_kwargs(messages, tools, temperature, max_tokens)

        logger.debug("发送请求 | messages={}", len(messages))
        response = self._client.chat.completions.create(**kwargs)
        choice = response.choices[0].message

        return self._parse_response(choice)

    @llm_retry(max_attempts=3)
    def chat_stream(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> Generator[str, None, None]:
        """流式对话调用。"""
        kwargs = self._build_request_kwargs(messages, tools, temperature, max_tokens)
        kwargs["stream"] = True

        logger.debug("发送流式请求 | messages={}", len(messages))
        stream = self._client.chat.completions.create(**kwargs)

        for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def _build_request_kwargs(
        self,
        messages: List[Message],
        tools: Optional[List[Dict[str, Any]]],
        temperature: Optional[float],
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        """构建 API 请求参数。"""
        kwargs: Dict[str, Any] = {
            "model": self._model,
            "messages": [msg.to_dict() for msg in messages],
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        return kwargs

    @staticmethod
    def _parse_response(choice) -> Message:
        """解析 API 响应为 Message。"""
        tool_calls = None
        if choice.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in choice.tool_calls
            ]

        return Message(
            role=Role.ASSISTANT,
            content=choice.content,
            tool_calls=tool_calls,
        )
