"""MCP Server 生命周期管理器。

负责：
1. 解析 .mcp.json 配置
2. 启动 MCP Server 连接（stdio / streamable-http）
3. 发现工具（list_tools）并适配为 BaseTool 注册到 ToolRegistry
4. 健康监控（ping）与异常重连
5. 优雅关闭（进程清理 / 连接释放）

设计要点：
- 单个 Server 连接失败不阻塞其他 Server 的初始化
- 维护独立事件循环线程，用于 MCP 的异步通信
- ServerState 追踪每个 Server 的连接状态和重连历史
- MCPTool 通过 session_resolver 间接获取 session，支持重连后热替换
- shutdown() 在 FastAPI lifespan 退出时调用
"""

import asyncio
import threading
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import httpx
from mcp import ClientSession
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.streamable_http import streamable_http_client

from src.tools.base_tool import ToolRegistry
from src.tools.mcp.config import (
    MCPDefaults,
    MCPServerConfig,
    TransportType,
    load_mcp_config,
)
from src.tools.mcp.mcp_tool import MCPTool
from src.utils.logger import logger


# ── Server 状态追踪 ──


class ConnectionStatus(Enum):
    """MCP Server 连接状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class ServerState:
    """单个 MCP Server 的运行时状态。

    记录连接状态、配置、session 引用、重连历史等信息，
    是 MCPToolManager 内部管理的核心数据结构。
    """

    config: MCPServerConfig
    status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    session: Optional[ClientSession] = None
    exit_stack: Optional[AsyncExitStack] = None
    tool_count: int = 0
    last_error: Optional[str] = None
    retry_count: int = 0
    connected_at: Optional[float] = None
    last_ping_at: Optional[float] = None
    # 该 Server 下所有 MCPTool 的原始 tool_def 列表（重连后重建 session 需要）
    tool_defs: list = field(default_factory=list)


# ── Manager ──


class MCPToolManager:
    """MCP Server 生命周期管理器。

    使用独立的事件循环线程运行 MCP 的异步通信，
    通过 run_coroutine_threadsafe 桥接到同步的 BaseTool.execute()。

    Sprint 2 增强：
    - 连接阶段超时控制（每个 Server 独立超时）
    - ServerState 状态追踪
    - ping 健康检查
    - 连接异常时自动重连（MCPTool 触发）
    """

    def __init__(self, config_path: str = ".mcp.json"):
        self._config_path = config_path
        self._servers: dict[str, ServerState] = {}
        self._reconnect_lock = threading.Lock()

        # 独立事件循环线程（MCP SDK 需要持续运行的 event loop）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._loop_thread: Optional[threading.Thread] = None

    # ── 事件循环管理 ──

    def _ensure_event_loop(self) -> asyncio.AbstractEventLoop:
        """确保 MCP 专用事件循环线程已启动。"""
        if self._loop is not None and self._loop.is_running():
            return self._loop

        ready = threading.Event()

        def _run_loop():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._loop = loop
            ready.set()
            loop.run_forever()

        self._loop_thread = threading.Thread(
            target=_run_loop,
            name="mcp-event-loop",
            daemon=True,
        )
        self._loop_thread.start()
        ready.wait(timeout=MCPDefaults.EVENT_LOOP_READY_TIMEOUT_S)
        logger.debug("MCP 专用事件循环线程已启动")
        return self._loop

    # ── 连接管理 ──

    async def _connect_server(self, state: ServerState) -> list[MCPTool]:
        """连接单个 MCP Server，返回适配后的 MCPTool 列表。

        包含连接超时控制：整个连接 + initialize + list_tools 过程
        受 MCPDefaults.CONNECT_TIMEOUT_S 限制。

        Raises:
            asyncio.TimeoutError: 连接超时。
            Exception: 连接或初始化失败。
        """
        config = state.config
        state.status = ConnectionStatus.CONNECTING

        # 为每个 Server 创建独立的 AsyncExitStack
        stack = AsyncExitStack()
        await stack.__aenter__()

        try:
            session = await asyncio.wait_for(
                self._establish_session(config, stack),
                timeout=MCPDefaults.CONNECT_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            await stack.aclose()
            raise asyncio.TimeoutError(
                f"MCP Server '{config.name}' 连接超时（{MCPDefaults.CONNECT_TIMEOUT_S}s）"
            )
        except Exception:
            await stack.aclose()
            raise

        state.session = session
        state.exit_stack = stack
        state.status = ConnectionStatus.CONNECTED
        state.connected_at = time.time()
        state.last_error = None

        # 发现工具
        result = await session.list_tools()
        state.tool_defs = list(result.tools)
        state.tool_count = len(result.tools)

        tools = self._create_mcp_tools(config.name, result.tools)

        logger.info(
            "MCP Server '{}' 连接成功 | 传输: {} | 发现 {} 个工具: {}",
            config.name,
            config.transport.value,
            len(tools),
            [t.name for t in tools],
        )
        return tools

    async def _establish_session(
        self, config: MCPServerConfig, stack: AsyncExitStack
    ) -> ClientSession:
        """建立传输连接并初始化 ClientSession。"""
        if config.transport == TransportType.STDIO:
            params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env=config.env or None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))

        elif config.transport == TransportType.STREAMABLE_HTTP:
            timeout_s = config.timeout_ms / 1000
            http_client = httpx.AsyncClient(
                headers=config.headers,
                timeout=httpx.Timeout(timeout_s),
            )
            read, write, _ = await stack.enter_async_context(
                streamable_http_client(config.url, http_client=http_client)
            )
        else:
            raise ValueError(f"不支持的传输类型: {config.transport}")

        session = await stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        return session

    def _create_mcp_tools(
        self, server_name: str, tool_defs: list
    ) -> list[MCPTool]:
        """为一组 tool_def 创建 MCPTool 实例。

        MCPTool 通过 session_resolver 间接获取 session，
        使得重连后自动使用新 session。
        """
        state = self._servers.get(server_name)
        tools = []
        for tool_def in tool_defs:
            mcp_tool = MCPTool(
                server_name=server_name,
                tool_def=tool_def,
                session_resolver=lambda name=server_name: self._get_session(name),
                event_loop=self._loop,
                reconnect_hook=lambda name=server_name: self.reconnect_server(name),
                call_timeout_s=state.config.call_timeout_s
                if state
                else MCPDefaults.CALL_TOOL_TIMEOUT_S,
                max_retries=state.config.max_retries
                if state
                else MCPDefaults.MAX_RETRIES,
                transport_type=state.config.transport.value
                if state
                else "unknown",
            )
            tools.append(mcp_tool)
        return tools

    def _get_session(self, server_name: str) -> Optional[ClientSession]:
        """session_resolver 的实现：返回指定 Server 的当前活跃 session。"""
        state = self._servers.get(server_name)
        if state and state.status == ConnectionStatus.CONNECTED:
            return state.session
        return None

    # ── 健康检查 ──

    async def _async_ping(self, server_name: str) -> bool:
        """异步 ping 指定 Server，验证连接是否健康。"""
        state = self._servers.get(server_name)
        if not state or not state.session:
            return False

        try:
            await asyncio.wait_for(state.session.send_ping(), timeout=5.0)
            state.last_ping_at = time.time()
            return True
        except Exception as e:
            logger.debug("MCP Server '{}' ping 失败: {}", server_name, e)
            return False

    def ping(self, server_name: str) -> bool:
        """同步接口：ping 指定 Server。"""
        if not self._loop or not self._loop.is_running():
            return False
        future = asyncio.run_coroutine_threadsafe(
            self._async_ping(server_name), self._loop
        )
        try:
            return future.result(timeout=10)
        except Exception:
            return False

    def health_check(self) -> dict[str, dict]:
        """返回所有 Server 的健康状态摘要。"""
        result = {}
        for name, state in self._servers.items():
            result[name] = {
                "status": state.status.value,
                "tool_count": state.tool_count,
                "retry_count": state.retry_count,
                "last_error": state.last_error,
                "connected_at": state.connected_at,
                "last_ping_at": state.last_ping_at,
            }
        return result

    # ── 重连 ──

    def reconnect_server(self, server_name: str) -> bool:
        """同步接口：重连指定 Server。

        线程安全：使用 _reconnect_lock 防止并发重连同一 Server。
        由 MCPTool 的 reconnect_hook 触发，也可手动调用。

        Returns:
            是否重连成功。
        """
        with self._reconnect_lock:
            state = self._servers.get(server_name)
            if not state:
                logger.warning("重连失败：未知 Server '{}'", server_name)
                return False

            if state.status == ConnectionStatus.RECONNECTING:
                logger.debug("Server '{}' 正在重连中，跳过", server_name)
                return False

            if not self._loop or not self._loop.is_running():
                logger.error("MCP 事件循环未运行，无法重连")
                return False

            future = asyncio.run_coroutine_threadsafe(
                self._async_reconnect(state), self._loop
            )
            try:
                return future.result(timeout=MCPDefaults.CONNECT_TIMEOUT_S + 5)
            except Exception as e:
                logger.error("Server '{}' 重连失败: {}", server_name, e)
                state.status = ConnectionStatus.FAILED
                state.last_error = str(e)
                return False

    async def _async_reconnect(self, state: ServerState) -> bool:
        """异步重连单个 Server。

        流程：关闭旧连接 → 建立新连接 → 更新 session。
        工具无需重新注册（MCPTool 通过 session_resolver 自动获取新 session）。
        """
        server_name = state.config.name
        state.status = ConnectionStatus.RECONNECTING
        state.retry_count += 1
        logger.info(
            "MCP Server '{}' 开始重连（第 {} 次）",
            server_name, state.retry_count,
        )

        # 先关闭旧连接
        if state.exit_stack:
            try:
                await state.exit_stack.aclose()
            except Exception as e:
                logger.debug("旧连接关闭: {}", e)
            state.exit_stack = None
        state.session = None

        # 建立新连接
        try:
            stack = AsyncExitStack()
            await stack.__aenter__()

            session = await asyncio.wait_for(
                self._establish_session(state.config, stack),
                timeout=MCPDefaults.CONNECT_TIMEOUT_S,
            )

            state.session = session
            state.exit_stack = stack
            state.status = ConnectionStatus.CONNECTED
            state.connected_at = time.time()
            state.last_error = None

            # 重新发现工具（Server 可能更新了工具列表，但我们不重新注册，
            # 因为 ToolRegistry 中的 MCPTool 实例通过 session_resolver 自动使用新 session）
            result = await session.list_tools()
            new_count = len(result.tools)
            if new_count != state.tool_count:
                logger.warning(
                    "MCP Server '{}' 重连后工具数量变化: {} → {}（需重启服务以更新注册）",
                    server_name, state.tool_count, new_count,
                )
            state.tool_defs = list(result.tools)

            logger.info("MCP Server '{}' 重连成功", server_name)
            return True

        except Exception as e:
            state.status = ConnectionStatus.FAILED
            state.last_error = str(e)
            logger.error("MCP Server '{}' 重连失败: {}", server_name, e)
            return False

    # ── 发现与注册 ──

    async def _async_discover_and_register(self, registry: ToolRegistry) -> int:
        """异步核心：连接所有 enabled Server 并注册工具。"""
        configs = load_mcp_config(self._config_path)
        enabled = [c for c in configs if not c.disabled]

        if not enabled:
            logger.debug("没有 enabled 的 MCP Server，跳过")
            return 0

        # 初始化 ServerState（先注册 state，_create_mcp_tools 需要引用）
        for config in enabled:
            self._servers[config.name] = ServerState(config=config)

        total = 0
        for config in enabled:
            state = self._servers[config.name]
            try:
                tools = await self._connect_server(state)
                for tool in tools:
                    try:
                        registry.register(tool)
                        total += 1
                    except ValueError as e:
                        logger.warning("MCP 工具注册冲突: {}", e)
            except asyncio.TimeoutError as e:
                state.status = ConnectionStatus.FAILED
                state.last_error = str(e)
                logger.error("MCP Server '{}' 连接超时: {}", config.name, e)
            except Exception as e:
                state.status = ConnectionStatus.FAILED
                state.last_error = str(e)
                logger.error("MCP Server '{}' 连接失败: {}", config.name, e)
                # 单个 Server 失败不阻塞其他 Server

        return total

    def discover_and_register(self, registry: ToolRegistry) -> int:
        """连接所有 enabled MCP Server，发现工具并注册到 ToolRegistry。

        同步接口，内部通过 MCP 专用事件循环执行异步逻辑。

        Args:
            registry: 工具注册中心。

        Returns:
            成功注册的工具总数。
        """
        loop = self._ensure_event_loop()
        future = asyncio.run_coroutine_threadsafe(
            self._async_discover_and_register(registry),
            loop,
        )
        try:
            count = future.result(timeout=MCPDefaults.DISCOVER_TIMEOUT_S)
            if count:
                logger.info("MCP 工具注册完成 | 共注册 {} 个外部工具", count)
            return count
        except Exception as e:
            logger.error("MCP 工具发现与注册失败: {}", e)
            return 0

    # ── 关闭 ──

    async def _async_shutdown(self) -> None:
        """异步关闭所有 MCP Server 连接。"""
        for name, state in self._servers.items():
            if state.exit_stack:
                try:
                    await state.exit_stack.aclose()
                except Exception as e:
                    # anyio cancel scope 跨任务关闭是已知行为，不影响资源释放
                    logger.debug("MCP Server '{}' 关闭: {}", name, e)
                state.exit_stack = None
            state.session = None
            state.status = ConnectionStatus.DISCONNECTED
        self._servers.clear()

    def shutdown(self) -> None:
        """优雅关闭所有 MCP Server 连接和专用事件循环。"""
        if self._loop and self._loop.is_running():
            # 关闭连接
            future = asyncio.run_coroutine_threadsafe(
                self._async_shutdown(), self._loop
            )
            try:
                future.result(timeout=MCPDefaults.SHUTDOWN_TIMEOUT_S)
            except Exception as e:
                logger.warning("MCP shutdown 超时或出错: {}", e)

            # 停止事件循环
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._loop_thread:
                self._loop_thread.join(timeout=5)

        self._loop = None
        self._loop_thread = None
        logger.info("MCP 资源已清理")

    # ── 查询接口 ──

    @property
    def connected_servers(self) -> list[str]:
        """返回已连接的 MCP Server 名称列表。"""
        return [
            name
            for name, state in self._servers.items()
            if state.status == ConnectionStatus.CONNECTED
        ]

    @property
    def server_states(self) -> dict[str, ServerState]:
        """返回所有 Server 状态（只读副本引用）。"""
        return dict(self._servers)
