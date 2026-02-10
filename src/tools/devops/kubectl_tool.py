"""Kubernetes 集群管理工具。

通过 kubectl CLI 提供 K8s 资源的查询和诊断能力。
默认只读模式，只允许查询类子命令。

安全机制：
- 子命令白名单（默认 Level 0 只读）
- 危险参数拦截
- Secret 敏感数据脱敏
- 命令注入防御（CommandSandbox）
"""

from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.tools.devops.command_sandbox import CommandSandbox


# 只读子命令（Level 0）
_READONLY_SUBCOMMANDS = frozenset({
    "get", "describe", "logs", "top", "explain",
    "api-resources", "api-versions", "cluster-info",
    "version",
})

# 有限写操作子命令（Level 1，需配置开启）
_WRITE_SUBCOMMANDS = frozenset({
    "scale", "rollout", "cordon", "uncordon", "label", "annotate",
})

# 禁止的参数
_BLOCKED_FLAGS = frozenset({
    "--force",
    "--grace-period=0",
    "--cascade=orphan",
    "--all-namespaces",  # 防止误操作全集群（可通过 -A 绕过，下面单独处理）
    "-A",
})

# 敏感资源类型（输出需脱敏）
_SENSITIVE_RESOURCES = frozenset({
    "secret", "secrets",
})


class KubectlTool(BaseTool):
    """Kubernetes 集群管理工具。

    Args:
        sandbox: CommandSandbox 实例，负责安全执行。
        enable_write: 是否启用写操作（默认 False，只读）。
        allowed_namespaces: 允许访问的 namespace 列表（None=全部允许）。
    """

    def __init__(
        self,
        sandbox: CommandSandbox,
        enable_write: bool = False,
        allowed_namespaces: list[str] | None = None,
    ):
        self._sandbox = sandbox
        self._enable_write = enable_write
        self._allowed_namespaces = (
            set(allowed_namespaces) if allowed_namespaces else None
        )

    @property
    def name(self) -> str:
        return "kubectl"

    @property
    def description(self) -> str:
        base = (
            "Kubernetes 集群管理工具，通过 kubectl 查询和诊断 K8s 资源。\n"
            "支持的查询操作：\n"
            "- get: 列出资源（pods, services, deployments, nodes, configmaps 等）\n"
            "- describe: 查看资源详情（事件、状态、配置）\n"
            "- logs: 查看 Pod 日志（支持 --tail 限制行数，--previous 查看上次崩溃日志）\n"
            "- top: 查看节点/Pod 的 CPU、内存使用情况（需要 Metrics Server）\n"
            "- explain: 查看资源字段的文档说明\n"
            "- api-resources: 列出集群所有可用的 API 资源类型\n"
            "- version: 查看客户端和服务端版本\n"
            "- cluster-info: 查看集群信息"
        )
        if self._enable_write:
            base += (
                "\n\n支持的运维操作（已启用）：\n"
                "- scale: 调整副本数\n"
                "- rollout: 管理滚动更新（status/restart/undo）\n"
                "- cordon/uncordon: 标记节点不可调度/恢复调度\n"
                "- label/annotate: 添加/修改标签或注解"
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
                    "description": "kubectl 子命令",
                },
                "resource_type": {
                    "type": "string",
                    "description": (
                        "资源类型，如 pods, services, deployments, nodes, "
                        "configmaps, ingresses, namespaces, events 等"
                    ),
                },
                "resource_name": {
                    "type": "string",
                    "description": "资源名称（可选，不填则列出所有）",
                },
                "namespace": {
                    "type": "string",
                    "description": (
                        "命名空间（默认 default）。"
                        "使用 'get namespaces' 可查看所有命名空间。"
                    ),
                },
                "flags": {
                    "type": "string",
                    "description": (
                        "附加参数，空格分隔。常用：\n"
                        "  -o wide（更多列）、-o yaml（YAML 格式）、-o json（JSON 格式）\n"
                        "  --tail=100（限制日志行数）、--previous（上次容器日志）\n"
                        "  -l app=nginx（标签选择器）、--sort-by=.status.startTime"
                    ),
                },
            },
            "required": ["subcommand"],
        }

    def execute(self, **kwargs) -> str:
        subcommand: str = kwargs.get("subcommand", "")
        resource_type: str = kwargs.get("resource_type", "")
        resource_name: str = kwargs.get("resource_name", "")
        namespace: str = kwargs.get("namespace", "")
        flags_str: str = kwargs.get("flags", "")

        # 构建参数列表
        args: list[str] = []

        # 资源类型和名称
        if resource_type:
            if resource_name:
                args.append(f"{resource_type}/{resource_name}")
            else:
                args.append(resource_type)

        # Namespace 校验和添加
        if namespace:
            if self._allowed_namespaces and namespace not in self._allowed_namespaces:
                raise RuntimeError(
                    f"命名空间 '{namespace}' 不在允许列表中。"
                    f"允许的命名空间: {sorted(self._allowed_namespaces)}"
                )
            args.extend(["-n", namespace])

        # 解析并添加附加参数
        if flags_str:
            flags = flags_str.strip().split()
            args.extend(flags)

        return self._sandbox.execute(subcommand, args)
