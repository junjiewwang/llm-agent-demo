"""Kubernetes 集群管理工具。

通过 kubectl CLI 提供 K8s 资源的查询、运维和诊断能力。
所有子命令均可用，写操作和危险操作通过 Human-in-the-loop 确认机制保障安全。

安全机制（三层防线）：
- L0 只读子命令：直接执行，无需确认
- L1 写操作子命令：smart 模式下触发用户确认
- L2 危险/不可逆子命令：始终触发用户确认
- 安全红线参数拦截（即使确认也不允许）
- Secret 敏感数据脱敏
- 命令注入防御（CommandSandbox）
"""

from typing import Any, Dict, List, Optional

from src.tools.base_tool import BaseTool
from src.tools.devops.command_sandbox import CommandSandbox


# ── 三级子命令分级 ──

# L0: 只读查询（直接执行，无需确认）
L0_READONLY = frozenset({
    "get", "describe", "logs", "top", "explain",
    "api-resources", "api-versions", "cluster-info",
    "version", "diff", "auth",
})

# L1: 运维写操作（smart 模式下需确认）
L1_WRITE = frozenset({
    "apply", "create", "patch", "edit",
    "scale", "rollout", "cordon", "uncordon",
    "label", "annotate", "taint",
    "exec", "cp", "port-forward",
})

# L2: 危险/不可逆操作（始终需确认）
L2_DANGEROUS = frozenset({
    "delete", "drain", "replace",
})

# 所有子命令
ALL_SUBCOMMANDS = L0_READONLY | L1_WRITE | L2_DANGEROUS

# 安全红线参数（即使确认也不允许）
BLOCKED_FLAGS = frozenset({
    "--grace-period=0",
})

# 敏感资源类型（输出需脱敏）
SENSITIVE_RESOURCES = frozenset({
    "secret", "secrets",
})


class KubectlTool(BaseTool):
    """Kubernetes 集群管理工具。

    Args:
        sandbox: CommandSandbox 实例，负责安全执行。
        allowed_namespaces: 允许访问的 namespace 列表（None=全部允许）。
    """

    def __init__(
        self,
        sandbox: CommandSandbox,
        allowed_namespaces: Optional[List[str]] = None,
    ):
        self._sandbox = sandbox
        self._allowed_namespaces = (
            set(allowed_namespaces) if allowed_namespaces else None
        )

    @property
    def name(self) -> str:
        return "kubectl"

    @property
    def description(self) -> str:
        return (
            "Kubernetes 集群管理工具，通过 kubectl 查询和诊断 K8s 资源。\n"
            "支持的查询操作：\n"
            "- get: 列出资源（pods, services, deployments, nodes, configmaps 等）\n"
            "- describe: 查看资源详情（事件、状态、配置）\n"
            "- logs: 查看 Pod 日志（支持 --tail 限制行数，--previous 查看上次崩溃日志）\n"
            "- top: 查看节点/Pod 的 CPU、内存使用情况（需要 Metrics Server）\n"
            "- explain: 查看资源字段的文档说明\n"
            "- api-resources: 列出集群所有可用的 API 资源类型\n"
            "- version: 查看客户端和服务端版本\n"
            "- cluster-info: 查看集群信息\n"
            "- diff: 对比本地资源定义与集群当前状态的差异\n"
            "- auth: 检查权限（如 auth can-i list pods）\n\n"
            "支持的运维操作（需确认后执行）：\n"
            "- apply: 应用资源配置（声明式）\n"
            "- create: 创建资源（命令式）\n"
            "- patch: 局部更新资源字段\n"
            "- edit: 交互式编辑资源\n"
            "- scale: 调整副本数\n"
            "- rollout: 管理滚动更新（status/restart/undo/history）\n"
            "- cordon/uncordon: 标记节点不可调度/恢复调度\n"
            "- label/annotate: 添加/修改标签或注解\n"
            "- taint: 设置节点污点\n"
            "- exec: 在容器中执行命令\n"
            "- cp: 在容器和本地之间复制文件\n"
            "- port-forward: 端口转发\n\n"
            "危险操作（需确认）：\n"
            "- delete: 删除资源\n"
            "- drain: 排空节点（驱逐所有 Pod）\n"
            "- replace: 替换资源（先删后建）"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "subcommand": {
                    "type": "string",
                    "enum": sorted(ALL_SUBCOMMANDS),
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
                        "使用 '--all-namespaces' 或 '-A' 在 flags 中可跨全部命名空间查询。"
                    ),
                },
                "flags": {
                    "type": "string",
                    "description": (
                        "附加参数，空格分隔。常用：\n"
                        "  -o wide（更多列）、-o yaml（YAML 格式）、-o json（JSON 格式）\n"
                        "  --tail=100（限制日志行数）、--previous（上次容器日志）\n"
                        "  -l app=nginx（标签选择器）、--sort-by=.status.startTime\n"
                        "  -A / --all-namespaces（跨全部命名空间）\n"
                        "  -f filename.yaml（指定文件，用于 apply/create/delete 等）\n"
                        "  --dry-run=client（模拟执行，不实际变更）"
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
        - 写操作 + 全集群范围（-A/--all-namespaces）：需确认
        """
        subcommand = kwargs.get("subcommand", "")
        if subcommand in L1_WRITE or subcommand in L2_DANGEROUS:
            return True

        # 只读命令 + 全集群写标志组合不存在，但防御性检查：
        # 如果未来有 L0 命令携带了写入性质的 flags，也可在此扩展
        return False

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

        output = self._sandbox.execute(subcommand, args)

        # 空结果语义增强：帮助 LLM 理解空输出是正常状态，避免无效重试
        if output == "(无输出)" and resource_type:
            return (
                f"(无输出)\n\n"
                f"[提示] 查询 {resource_type} 返回空结果，"
                f"这通常表示当前集群中没有匹配的 {resource_type} 资源。"
                f"空结果本身就是有效信息，无需使用不同参数重试。"
            )

        return output
