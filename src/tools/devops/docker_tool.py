"""Docker 容器管理工具。

通过 docker CLI 提供容器和镜像的查询、运维与诊断能力。
所有子命令均可用，写操作和危险操作通过 Human-in-the-loop 确认机制保障安全。

安全机制（三层防线）：
- L0 只读子命令：直接执行，无需确认
- L1 写操作子命令：smart 模式下触发用户确认
- L2 危险/不可逆子命令：始终触发用户确认
- 命令注入防御（CommandSandbox）
- 危险参数拦截（--privileged 等）
"""

from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.tools.devops.command_sandbox import CommandSandbox


# ── 三级子命令分级 ──

# L0: 只读查询（直接执行，无需确认）
L0_READONLY = frozenset({
    "ps", "images", "logs", "inspect", "stats",
    "network", "volume", "system", "version", "info",
    "compose", "top",
})

# L1: 运维写操作（smart 模式下需确认）
L1_WRITE = frozenset({
    "start", "stop", "restart", "pull", "build",
    "exec", "run", "cp", "tag", "create",
})

# L2: 危险/不可逆操作（始终需确认）
L2_DANGEROUS = frozenset({
    "rm", "rmi", "kill", "prune", "push",
})

# 所有子命令
ALL_SUBCOMMANDS = L0_READONLY | L1_WRITE | L2_DANGEROUS

# 禁止的参数（安全红线，即使确认也不允许）
BLOCKED_FLAGS = frozenset({
    "--privileged",
})


class DockerTool(BaseTool):
    """Docker 容器管理工具。

    Args:
        sandbox: CommandSandbox 实例，负责安全执行。
    """

    def __init__(self, sandbox: CommandSandbox):
        self._sandbox = sandbox

    @property
    def name(self) -> str:
        return "docker"

    @property
    def description(self) -> str:
        return (
            "Docker 容器管理工具，查看和诊断本地 Docker 容器与镜像。\n"
            "支持的查询操作：\n"
            "- ps: 查看容器列表（默认只显示运行中的，加 --all 显示所有）\n"
            "- images: 查看本地镜像列表\n"
            "- logs: 查看容器日志（支持 --tail 限制行数，--since 按时间过滤）\n"
            "- inspect: 查看容器或镜像的详细 JSON 配置\n"
            "- stats: 查看容器资源使用（CPU/内存/网络），必须加 --no-stream\n"
            "- top: 查看容器内运行的进程\n"
            "- network ls/inspect: 查看 Docker 网络\n"
            "- volume ls/inspect: 查看 Docker 卷\n"
            "- system df: 查看 Docker 磁盘使用\n"
            "- version: 查看 Docker 版本\n"
            "- info: 查看 Docker 系统信息\n"
            "- compose ps: 查看 Compose 服务状态（需在项目目录下）\n\n"
            "支持的运维操作（需确认后执行）：\n"
            "- start/stop/restart: 容器生命周期管理\n"
            "- exec: 在运行中的容器内执行命令\n"
            "- run: 创建并运行新容器\n"
            "- cp: 在容器与宿主机之间复制文件\n"
            "- pull/build/tag/create: 镜像与容器构建\n\n"
            "危险操作（需确认）：\n"
            "- rm/rmi: 删除容器或镜像\n"
            "- kill: 强制终止容器\n"
            "- prune: 清理未使用的资源\n"
            "- push: 推送镜像到远程仓库"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": sorted(ALL_SUBCOMMANDS),
                    "description": (
                        "docker 子命令。对于复合命令用空格连接，"
                        "如 'network ls'、'compose ps'、'system df'"
                    ),
                },
                "target": {
                    "type": "string",
                    "description": (
                        "目标容器名/ID 或镜像名（部分命令需要，"
                        "如 logs、inspect、start、stop、exec 等）"
                    ),
                },
                "flags": {
                    "type": "string",
                    "description": (
                        "附加参数，空格分隔。常用：\n"
                        "  --all（显示所有容器）、--tail=50（限制日志行数）\n"
                        "  --since=1h（最近 1 小时日志）、--no-stream（stats 单次快照）\n"
                        "  --format 'table {{.Names}}\\t{{.Status}}'（自定义输出格式）\n"
                        "  --filter name=xxx（按名称过滤）\n"
                        "  exec 场景: 直接写要执行的命令，如 'ls -la /app'"
                    ),
                },
            },
            "required": ["subcommand"],
        }

    def should_confirm(self, **kwargs) -> bool:
        """判断是否需要用户确认。

        - L0 只读：不需要确认
        - L1 写操作：smart 模式下需确认
        - L2 危险操作：始终需确认
        """
        raw = kwargs.get("subcommand", "")
        subcommand = raw.strip().split()[0] if raw.strip() else ""
        return subcommand in L1_WRITE or subcommand in L2_DANGEROUS

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
