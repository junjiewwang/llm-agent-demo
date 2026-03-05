"""统一 CLI 命令执行工具。

LLM 只需要调用一个 execute_command 工具，传入完整命令字符串即可。
安全校验由 BashExecutor 统一处理，无需 LLM 理解多套参数 schema。

替代原来的 KubectlTool + DockerTool + CurlTool（3 个工具 → 1 个工具）。
"""

from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.tools.devops.bash_executor import BashExecutor


class ExecuteCommandTool(BaseTool):
    """统一 CLI 命令执行工具。

    LLM 直接写完整命令（如 "kubectl get pods -n default"），
    BashExecutor 负责二进制白名单、子命令分级、安全校验和执行。

    Args:
        executor: BashExecutor 实例，负责安全执行。
        allowed_binaries: 当前允许的二进制列表（用于生成 description）。
    """

    # 禁用结构化调用契约：本工具接收自然命令字符串，不是结构化参数
    _enable_structured_contract: bool = False

    def __init__(self, executor: BashExecutor, allowed_binaries: list[str]):
        self._executor = executor
        self._allowed_binaries = allowed_binaries

    @property
    def name(self) -> str:
        return "execute_command"

    @property
    def description(self) -> str:
        binaries = ", ".join(sorted(self._allowed_binaries))
        return (
            f"在服务器上安全执行 CLI 命令，支持完整 bash 语法（管道、heredoc、重定向等）。\n"
            f"支持的工具: {binaries}\n\n"
            f"基本用法:\n"
            f"  - kubectl get pods -n default\n"
            f"  - kubectl describe pod my-pod -n production\n"
            f"  - docker ps --all\n"
            f"  - curl -s https://api.example.com/health\n\n"
            f"管道用法（推荐用于数据过滤和格式化）:\n"
            f"  - kubectl get pods -A | grep Error\n"
            f"  - kubectl get pods -o json | jq '.items[].metadata.name'\n"
            f"  - kubectl get pods -A | grep -v Running | wc -l\n"
            f"  - docker ps | grep redis | awk '{{print $1}}'\n\n"
            f"Heredoc 用法（适合多行 YAML/JSON 输入）:\n"
            f"  - cat << EOF | kubectl apply -f -\n"
            f"    apiVersion: v1\n"
            f"    kind: ConfigMap\n"
            f"    ...\n"
            f"    EOF\n\n"
            f"管道中可使用的工具: grep, awk, sed, sort, uniq, wc, head, tail, jq, yq, cut, tr, column, xargs 等\n\n"
            f"安全限制:\n"
            f"  - 仅允许已配置的二进制程序\n"
            f"  - 危险操作需要用户确认\n"
            f"  - 命令超时限制\n"
            f"  - curl 只允许 http/https 协议"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "要执行的完整命令（如 'kubectl get pods -n default'）",
                },
            },
            "required": ["command"],
        }

    def should_confirm(self, **kwargs) -> bool:
        """写操作需要用户确认。"""
        command = kwargs.get("command", "")
        return self._executor.classify(command) == "write"

    def execute(self, **kwargs) -> str:
        command: str = kwargs.get("command", "")
        if not command:
            raise RuntimeError("command 参数不能为空")

        return self._executor.execute(command)
