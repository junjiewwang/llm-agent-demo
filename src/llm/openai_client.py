"""基于 OpenAI 兼容协议的 LLM 客户端实现。

支持所有兼容 OpenAI API 的服务商（OpenAI、DeepSeek、通义千问等），
只需配置不同的 base_url 和 api_key 即可切换。
"""

import json
import time
from typing import Any, Generator, Optional, List, Dict

from openai import OpenAI
from opentelemetry.trace import StatusCode

from src.config import settings
from src.llm.base_client import BaseLLMClient, Message, Role
from src.observability import get_tracer
from src.observability.instruments import record_llm_metrics, set_span_content, set_span_messages
from src.utils.logger import logger
from src.utils.retry import llm_retry

_tracer = get_tracer(__name__)


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

        with _tracer.start_as_current_span("llm.chat") as span:
            span.set_attribute("llm.model", self._model)
            span.set_attribute("llm.message_count", len(messages))
            span.set_attribute("llm.has_tools", bool(tools))

            # 记录输入 messages
            set_span_messages(span, "llm.input_messages", [m.to_dict() for m in messages])

            logger.debug("发送请求 | messages={}", len(messages))
            start = time.monotonic()
            response = self._client.chat.completions.create(**kwargs)
            duration_ms = (time.monotonic() - start) * 1000
            choice = response.choices[0].message

            msg = self._parse_response(choice)

            # 提取 token 用量，附加到 Message 上（用于可观测性）
            prompt_tokens = 0
            completion_tokens = 0
            if response.usage:
                prompt_tokens = response.usage.prompt_tokens or 0
                completion_tokens = response.usage.completion_tokens or 0
                msg.usage = {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": response.usage.total_tokens or 0,
                }

            # Span attributes
            span.set_attribute("llm.prompt_tokens", prompt_tokens)
            span.set_attribute("llm.completion_tokens", completion_tokens)
            span.set_attribute("llm.total_tokens", prompt_tokens + completion_tokens)
            span.set_attribute("llm.has_tool_calls", bool(msg.tool_calls))
            span.set_attribute("llm.duration_ms", round(duration_ms, 1))

            # 记录输出内容
            set_span_content(span, "llm.output_content", msg.content or "")
            if msg.tool_calls:
                set_span_content(
                    span, "llm.output_tool_calls",
                    json.dumps(msg.tool_calls, ensure_ascii=False),
                )

            # Metrics
            record_llm_metrics(
                model=self._model,
                call_type="chat",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                duration_ms=duration_ms,
            )

            return msg

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
