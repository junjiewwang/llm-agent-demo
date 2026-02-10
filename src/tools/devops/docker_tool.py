"""Docker 容器管理工具。

通过 docker CLI 提供容器和镜像的查询与诊断能力。
默认只读模式，只允许查询类子命令。

安全机制：
- 子命令白名单（默认 Level 0 只读）
- 禁止 exec、run --privileged 等危险操作
- 命令注入防御（CommandSandbox）
"""

from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.tools.devops.command_sandbox import CommandSandbox


# 只读子命令（Level 0）
_READONLY_SUBCOMMANDS = frozenset({
    "ps", "images", "logs", "inspect", "stats",
    "network", "volume", "system", "version", "info",
    "compose",
})

# 有限写操作子命令（Level 1，需配置开启）
_WRITE_SUBCOMMANDS = frozenset({
    "start", "stop", "restart",
})

# 禁止的参数
_BLOCKED_FLAGS = frozenset({
    "--privileged",
    "--rm",
    "--force",
    "-f",
    "--volumes",
    "--rmi",
})


class DockerTool(BaseTool):
    """Docker 容器管理工具。

    Args:
        sandbox: CommandSandbox 实例，负责安全执行。
        enable_write: 是否启用写操作（默认 False，只读）。
    """

    def __init__(
        self,
        sandbox: CommandSandbox,
        enable_write: bool = False,
    ):
        self._sandbox = sandbox
        self._enable_write = enable_write

    @property
    def name(self) -> str:
        return "docker"

    @property
    def description(self) -> str:
        base = (
            "Docker 容器管理工具，查看和诊断本地 Docker 容器与镜像。\n"
            "支持的查询操作：\n"
            "- ps: 查看容器列表（默认只显示运行中的，加 --all 显示所有）\n"
            "- images: 查看本地镜像列表\n"
            "- logs: 查看容器日志（支持 --tail 限制行数，--since 按时间过滤）\n"
            "- inspect: 查看容器或镜像的详细 JSON 配置\n"
            "- stats: 查看容器资源使用（CPU/内存/网络），必须加 --no-stream\n"
            "- network ls/inspect: 查看 Docker 网络\n"
            "- volume ls/inspect: 查看 Docker 卷\n"
            "- system df: 查看 Docker 磁盘使用\n"
            "- version: 查看 Docker 版本\n"
            "- info: 查看 Docker 系统信息\n"
            "- compose ps: 查看 Compose 服务状态（需在项目目录下）"
        )
        if self._enable_write:
            base += (
                "\n\n支持的运维操作（已启用）：\n"
                "- start: 启动已停止的容器\n"
                "- stop: 优雅停止运行中的容器\n"
                "- restart: 重启容器"
            )
        return base

    @property
    def parameters(self) -> Dict[str, Any]:
        subcommands = sorted(_READONLY_SUBCOMMANDS)
        if self._enable_write:
            subcommands = sorted(_READONLY_SUBCOMMANDS | _WRITE_SUBCOMMANDS)

        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": subcommands,
                    "description": (
                        "docker 子命令。对于复合命令用空格连接，"
                        "如 'network ls'、'compose ps'、'system df'"
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "目标容器名/ID 或镜像名（部分命令需要，"
                        "如 logs、inspect、start、stop 等）"
                    ),
                },
                "flags": {
                    "type": "string",
                    "description": (
                        "附加参数，空格分隔。常用：\n"
                        "  --all（显示所有容器）、--tail=50（限制日志行数）\n"
                        "  --since=1h（最近 1 小时日志）、--no-stream（stats 单次快照）\n"
                        "  --format 'table {{.Names}}\\t{{.Status}}'（自定义输出格式）\n"
                        "  --filter name=xxx（按名称过滤）"
                    ),
                },
            },
            "required": ["subcommand"],
        }

    def execute(self, **kwargs) -> str:
        raw_subcommand: str = kwargs.get("subcommand", "")
        target: str = kwargs.get("target", "")
        flags_str: str = kwargs.get("flags", "")

        # 处理复合子命令（如 "network ls" → subcommand="network", extra=["ls"]）
        parts = raw_subcommand.strip().split()
        if not parts:
            raise RuntimeError("子命令不能为空")

        subcommand = parts[0]
        extra_args = parts[1:]  # 复合命令的第二部分（如 ls, ps, df）

        # 构建参数列表
        args: list[str] = list(extra_args)

        # stats 命令强制加 --no-stream，防止无限输出
        if subcommand == "stats" and "--no-stream" not in flags_str:
            args.append("--no-stream")

        # 目标容器/镜像
        if target:
            args.append(target)

        # 解析并添加附加参数
        if flags_str:
            flags = flags_str.strip().split()
            args.extend(flags)

        return self._sandbox.execute(subcommand, args)
