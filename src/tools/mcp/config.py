"""MCP Server 配置解析。

从 .mcp.json 读取 MCP Server 声明，支持两种传输方式：
- stdio：本地子进程通信（有 command 字段）
- streamable-http：远程 HTTP 通信（有 url 字段）

配置格式与 Cursor / Claude Desktop 的 .mcp.json 完全兼容。
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from src.utils.logger import logger


class TransportType(Enum):
    """MCP 传输类型。"""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable-http"


class MCPDefaults:
    """MCP 相关默认常量，集中管理避免魔法数字。"""

    # 连接阶段超时（秒）：单个 Server 建立连接 + initialize + list_tools 的总时限
    CONNECT_TIMEOUT_S: float = 30.0

    # call_tool 调用超时（秒）：单次工具调用的时限
    CALL_TOOL_TIMEOUT_S: float = 60.0

    # 整体发现超时（秒）：discover_and_register 的总时限
    DISCOVER_TIMEOUT_S: float = 60.0

    # 重连相关
    MAX_RETRIES: int = 2  # 单次 call_tool 失败后的最大重连重试次数
    RECONNECT_BACKOFF_S: float = 1.0  # 重连前等待（秒）

    # HTTP 传输默认超时（毫秒）
    HTTP_TIMEOUT_MS: int = 30000

    # shutdown 超时（秒）
    SHUTDOWN_TIMEOUT_S: float = 10.0

    # 事件循环线程启动超时（秒）
    EVENT_LOOP_READY_TIMEOUT_S: float = 5.0


@dataclass(frozen=True)
class MCPServerConfig:
    """单个 MCP Server 的配置。

    传输类型由字段自动推断：
    - 有 command → stdio
    - 有 url → streamable-http
    """

    name: str
    transport: TransportType
    description: str = ""
    disabled: bool = False

    # stdio 传输
    command: Optional[str] = None
    args: tuple[str, ...] = field(default_factory=tuple)
    env: dict[str, str] = field(default_factory=dict)

    # streamable-http 传输
    url: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_ms: int = MCPDefaults.HTTP_TIMEOUT_MS

    # 健壮性配置
    max_retries: int = MCPDefaults.MAX_RETRIES

    def __post_init__(self):
        if self.transport == TransportType.STDIO and not self.command:
            raise ValueError(f"MCP Server '{self.name}': stdio 传输必须指定 command")
        if self.transport == TransportType.STREAMABLE_HTTP and not self.url:
            raise ValueError(f"MCP Server '{self.name}': streamable-http 传输必须指定 url")

    @property
    def call_timeout_s(self) -> float:
        """call_tool 超时（秒），由 timeout_ms 转换而来。"""
        return self.timeout_ms / 1000


def _parse_server_config(name: str, raw: dict) -> MCPServerConfig:
    """从 JSON 字典解析单个 Server 配置。"""
    # 自动推断传输类型
    if "command" in raw:
        transport = TransportType.STDIO
    elif "url" in raw:
        transport = TransportType.STREAMABLE_HTTP
    else:
        raise ValueError(
            f"MCP Server '{name}' 配置无效：需要 'command'（stdio）或 'url'（streamable-http）"
        )

    return MCPServerConfig(
        name=name,
        transport=transport,
        description=raw.get("description", ""),
        disabled=raw.get("disabled", False),
        # stdio
        command=raw.get("command"),
        args=tuple(raw.get("args", [])),
        env=dict(raw.get("env", {})),
        # streamable-http
        url=raw.get("url"),
        headers=dict(raw.get("headers", {})),
        timeout_ms=raw.get("timeout", MCPDefaults.HTTP_TIMEOUT_MS),
        # 健壮性
        max_retries=raw.get("maxRetries", MCPDefaults.MAX_RETRIES),
    )


def load_mcp_config(config_path: str = ".mcp.json") -> list[MCPServerConfig]:
    """加载 .mcp.json 配置文件，返回所有 Server 配置列表。

    Args:
        config_path: 配置文件路径，默认为项目根目录的 .mcp.json。

    Returns:
        解析后的 MCPServerConfig 列表（包含 disabled 的，由调用方过滤）。
        配置文件不存在或为空时返回空列表。
    """
    path = Path(config_path)
    if not path.exists():
        logger.debug("MCP 配置文件 {} 不存在，跳过", config_path)
        return []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("MCP 配置文件 {} 解析失败: {}", config_path, e)
        return []

    servers_raw = raw.get("mcpServers", {})
    if not servers_raw:
        return []

    configs: list[MCPServerConfig] = []
    for name, server_raw in servers_raw.items():
        try:
            configs.append(_parse_server_config(name, server_raw))
        except (ValueError, TypeError) as e:
            logger.warning("MCP Server '{}' 配置解析失败: {}", name, e)

    logger.info(
        "MCP 配置加载完成 | 共 {} 个 Server（enabled: {}）",
        len(configs),
        sum(1 for c in configs if not c.disabled),
    )
    return configs
