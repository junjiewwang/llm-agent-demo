"""MCP Tool → BaseTool 适配器。

将 MCP Server 暴露的每个 tool 适配为 BaseTool 实例，
无缝融入 ToolRegistry，复用执行、截断、可观测等全部机制。

关键点：
- MCP tool 的 inputSchema 就是标准 JSON Schema，直接映射 BaseTool.parameters
- 命名采用 mcp__{server}__{tool} 格式防冲突
- 结构化契约默认关闭（MCP 工具参数验证由 Server 端负责）
- session 通过 resolver 间接获取，支持重连后自动切换到新 session
"""

import asyncio
from typing import Any, Callable, Dict, Optional

from mcp import ClientSession, types as mcp_types

from src.observability import get_tracer
from src.observability.instruments import trace_span, set_span_content
from src.tools.base_tool import BaseTool
from src.tools.mcp.config import MCPDefaults
from src.utils.logger import logger

_tracer = get_tracer(__name__)

# session_resolver: 返回当前活跃的 ClientSession，若 Server 已断开则返回 None
SessionResolver = Callable[[], Optional[ClientSession]]

# reconnect_hook: 触发 Server 重连，返回是否重连成功
ReconnectHook = Callable[[], bool]


class MCPTool(BaseTool):
    """MCP tool → BaseTool 适配器。

    每个实例对应 MCP Server 暴露的一个 tool。
    通过 session_resolver 间接获取 ClientSession，
    使得 Manager 重连 Server 后，工具自动使用新 session，无需重新注册。
    """

    _enable_structured_contract = False  # MCP 工具参数验证由 Server 端负责

    def __init__(
        self,
        server_name: str,
        tool_def: mcp_types.Tool,
        session_resolver: SessionResolver,
        event_loop: asyncio.AbstractEventLoop,
        reconnect_hook: Optional[ReconnectHook] = None,
        call_timeout_s: float = MCPDefaults.CALL_TOOL_TIMEOUT_S,
        max_retries: int = MCPDefaults.MAX_RETRIES,
        transport_type: str = "unknown",
    ):
        """
        Args:
            server_name: MCP Server 名称（用于命名空间前缀）。
            tool_def: MCP SDK 返回的 Tool 定义（含 name, description, inputSchema）。
            session_resolver: 返回当前活跃 ClientSession 的回调（支持重连后热替换）。
            event_loop: MCP 专用事件循环（用于同步/异步桥接）。
            reconnect_hook: 触发 Server 重连的回调（可选），返回是否成功。
            call_timeout_s: 单次 call_tool 超时（秒）。
            max_retries: 连接异常时的最大重试次数。
            transport_type: 传输类型标识（用于可观测性），如 "stdio" / "streamable-http"。
        """
        self._server_name = server_name
        self._tool_def = tool_def
        self._session_resolver = session_resolver
        self._event_loop = event_loop
        self._reconnect_hook = reconnect_hook
        self._call_timeout_s = call_timeout_s
        self._max_retries = max_retries
        self._transport_type = transport_type

    @property
    def name(self) -> str:
        """命名格式: mcp__{server}__{tool}，防止与内置工具冲突。"""
        return f"mcp__{self._server_name}__{self._tool_def.name}"

    @property
    def description(self) -> str:
        return self._tool_def.description or f"MCP tool from {self._server_name}"

    @property
    def parameters(self) -> Dict[str, Any]:
        """MCP inputSchema 就是标准 JSON Schema，直接返回。"""
        return self._tool_def.inputSchema or {"type": "object", "properties": {}}

    def execute(self, **kwargs) -> str:
        """同步桥接：将异步 call_tool 提交到 MCP 专用事件循环执行。

        使用 run_coroutine_threadsafe 而非 asyncio.run()，
        因为 FastAPI 的主事件循环已在运行，不能嵌套。

        连接异常时自动触发重连并重试（最多 max_retries 次）。
        """
        future = asyncio.run_coroutine_threadsafe(
            self._execute_with_retry(**kwargs),
            self._event_loop,
        )
        try:
            return future.result(timeout=self._call_timeout_s * (self._max_retries + 1))
        except TimeoutError:
            logger.error("MCP tool '{}' 调用超时（{}s）", self.name, self._call_timeout_s)
            raise RuntimeError(f"MCP tool '{self.name}' 调用超时")
        except Exception as e:
            logger.error("MCP tool '{}' 调用失败: {}", self.name, e)
            raise

    async def _execute_with_retry(self, **kwargs) -> str:
        """带重连重试的异步执行逻辑，包含 OTel span。

        首次调用失败且为连接类异常时，触发 reconnect_hook 并重试。
        非连接类异常（如参数错误）不重试。
        """
        with trace_span(_tracer, f"mcp.call_tool.{self._server_name}", {
            "mcp.server": self._server_name,
            "mcp.transport": self._transport_type,
            "mcp.tool": self._tool_def.name,
            "mcp.timeout_s": self._call_timeout_s,
        }) as span:
            set_span_content(span, "mcp.input", str(kwargs))
            last_error: Optional[Exception] = None
            reconnected = False

            for attempt in range(1 + self._max_retries):
                span.set_attribute("mcp.attempt", attempt + 1)
                try:
                    result = await self._async_execute(**kwargs)
                    span.set_attribute("mcp.success", True)
                    span.set_attribute("mcp.reconnected", reconnected)
                    set_span_content(span, "mcp.output", result[:500])
                    return result
                except (ConnectionError, OSError, EOFError, BrokenPipeError) as e:
                    last_error = e
                    if attempt < self._max_retries and self._reconnect_hook:
                        logger.warning(
                            "MCP tool '{}' 连接异常（第 {}/{} 次），触发重连: {}",
                            self.name, attempt + 1, self._max_retries, e,
                        )
                        try:
                            reconnected = self._reconnect_hook()
                            if reconnected:
                                logger.info("MCP Server '{}' 重连成功，重试工具调用", self._server_name)
                                continue
                            else:
                                logger.warning("MCP Server '{}' 重连失败", self._server_name)
                        except Exception as re:
                            logger.error("MCP Server '{}' 重连出错: {}", self._server_name, re)
                    # 最后一次尝试或无 reconnect_hook，直接抛出
                    span.set_attribute("mcp.success", False)
                    span.set_attribute("mcp.error", str(last_error))
                    raise RuntimeError(
                        f"MCP tool '{self.name}' 连接失败（已重试 {attempt} 次）: {last_error}"
                    ) from last_error
                except asyncio.TimeoutError:
                    span.set_attribute("mcp.success", False)
                    span.set_attribute("mcp.error", "timeout")
                    raise RuntimeError(
                        f"MCP tool '{self.name}' 调用超时（{self._call_timeout_s}s）"
                    )

            # 不应到达这里，但 defensive
            span.set_attribute("mcp.success", False)
            raise RuntimeError(f"MCP tool '{self.name}' 执行失败: {last_error}") from last_error

    async def _async_execute(self, **kwargs) -> str:
        """实际的异步 MCP call_tool 调用。

        Raises:
            ConnectionError: session 不可用（Server 已断开）。
            asyncio.TimeoutError: call_tool 超时。
        """
        session = self._session_resolver()
        if session is None:
            raise ConnectionError(f"MCP Server '{self._server_name}' 未连接")

        result = await asyncio.wait_for(
            session.call_tool(self._tool_def.name, arguments=kwargs),
            timeout=self._call_timeout_s,
        )

        # 提取所有文本内容块
        texts = []
        for content in result.content:
            if isinstance(content, mcp_types.TextContent):
                texts.append(content.text)
            elif isinstance(content, mcp_types.EmbeddedResource):
                texts.append(f"[Resource: {content.resource.uri}]")
            else:
                texts.append(str(content))

        output = "\n".join(texts)
        if not output:
            output = "(MCP tool returned empty result)"

        return output
