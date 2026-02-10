"""CLI 命令执行安全沙箱。

为 kubectl / docker 等外部 CLI 工具提供统一的安全执行层：
- 子命令白名单：只允许预定义的子命令
- 危险参数拦截：禁止 --force、--rm、--privileged 等高危参数
- 命令注入防御：shell=False + shell 元字符检测
- 超时控制：防止 `kubectl logs -f` 等挂起
- 输出截断：防止大量日志耗尽上下文窗口
- 敏感信息过滤：屏蔽 Secret 等敏感资源的 data 字段
"""

import re
import shutil
import subprocess
from dataclasses import dataclass, field

from src.utils.logger import logger


@dataclass(frozen=True)
class CommandPolicy:
    """单个 CLI 工具的安全策略。

    Attributes:
        binary: CLI 二进制名称（如 "kubectl"），会自动通过 shutil.which 解析。
        allowed_subcommands: 允许的子命令白名单（如 {"get", "describe", "logs"}）。
        blocked_flags: 禁止的参数（如 {"--force", "--rm"}），大小写敏感。
        sensitive_resources: 需要屏蔽输出的资源类型（如 {"secret", "secrets"}）。
        timeout: 命令执行超时（秒）。
        max_output_chars: 输出字符数上限，超出则截断。
    """

    binary: str
    allowed_subcommands: frozenset[str] = field(default_factory=frozenset)
    blocked_flags: frozenset[str] = field(default_factory=frozenset)
    sensitive_resources: frozenset[str] = field(default_factory=frozenset)
    timeout: int = 30
    max_output_chars: int = 5000


# Shell 元字符检测正则：; | && || ` $( > < 换行
_SHELL_META_PATTERN = re.compile(r"[;|&`$><\n\r]")

# Secret data 字段屏蔽正则（匹配 base64 值）
_SECRET_DATA_PATTERN = re.compile(
    r"^(\s+\S+:\s*).+$",
    re.MULTILINE,
)


class CommandSandbox:
    """CLI 命令安全执行沙箱。

    职责：
    1. 验证命令是否在策略白名单内
    2. 检测并拦截危险参数和注入攻击
    3. 通过 subprocess 安全执行命令
    4. 对敏感资源输出进行脱敏
    5. 输出超长时自动截断
    """

    def __init__(self, policy: CommandPolicy):
        self._policy: CommandPolicy = policy
        self._binary_path: str | None = self._resolve_binary(policy.binary)

    @property
    def policy(self) -> CommandPolicy:
        return self._policy

    @property
    def is_available(self) -> bool:
        """检查 CLI 工具是否可用。"""
        return self._binary_path is not None

    @staticmethod
    def _resolve_binary(binary: str) -> str | None:
        """解析二进制路径，不存在则返回 None。"""
        path = shutil.which(binary)
        if not path:
            logger.warning("CLI 工具 '{}' 未找到，请确认已安装并在 PATH 中", binary)
        return path

    def execute(self, subcommand: str, args: list[str]) -> str:
        """安全执行 CLI 命令。

        Args:
            subcommand: 子命令（如 "get"、"ps"）。
            args: 子命令之后的参数列表。

        Returns:
            命令的 stdout 输出。

        Raises:
            RuntimeError: 二进制不可用、安全检查失败、或命令执行异常时。
        """
        # 1. 二进制可用性检查
        if not self._binary_path:
            raise RuntimeError(
                f"'{self._policy.binary}' 未安装或不在 PATH 中，请先安装并确认可执行"
            )

        # 2. 子命令白名单检查
        if subcommand not in self._policy.allowed_subcommands:
            raise RuntimeError(
                f"子命令 '{subcommand}' 不被允许。可用子命令: {sorted(self._policy.allowed_subcommands)}"
            )

        # 3. 危险参数检查（逐个参数检测）
        self._check_blocked_flags(args)

        # 4. 命令注入检测（所有参数不得含 shell 元字符）
        self._check_injection(subcommand, args)

        # 5. 组装并执行命令
        cmd = [self._binary_path, subcommand] + args
        logger.info("执行命令: {} (超时 {}s)", " ".join(cmd), self._policy.timeout)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._policy.timeout,
                shell=False,  # 安全：参数作为独立列表传递，不经过 shell 解析
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"命令执行超时（{self._policy.timeout}秒）。如果是日志查看，请使用 --tail 限制行数。"
            )
        except OSError as e:
            raise RuntimeError(f"命令执行失败: {e}")

        # 6. 处理输出
        if result.returncode != 0:
            stderr = result.stderr.strip()
            # stderr 也可能很长，截断到合理长度
            if len(stderr) > 1000:
                stderr = stderr[:1000] + "... (输出截断)"
            raise RuntimeError(f"命令返回错误 (exit={result.returncode}): {stderr}")

        output = result.stdout.strip()
        if not output:
            return "(无输出)"

        # 7. 敏感信息过滤
        output = self._mask_sensitive(output, subcommand, args)

        # 8. 截断过长输出
        output = self._truncate(output)

        return output

    def _check_blocked_flags(self, args: list[str]) -> None:
        """检查参数中是否包含被禁止的 flag。"""
        for arg in args:
            # 拆分 --flag=value 形式，只检查 flag 部分
            flag_part = arg.split("=")[0] if "=" in arg else arg
            if flag_part in self._policy.blocked_flags:
                raise RuntimeError(
                    f"参数 '{flag_part}' 被安全策略禁止。被禁止的参数: {sorted(self._policy.blocked_flags)}"
                )

    @staticmethod
    def _check_injection(subcommand: str, args: list[str]) -> None:
        """检测所有参数是否包含 shell 元字符（命令注入防御）。"""
        all_parts = [subcommand] + args
        for part in all_parts:
            if _SHELL_META_PATTERN.search(part):
                raise RuntimeError(
                    f"参数 '{part}' 包含不允许的特殊字符。不允许使用 ; | & ` $ > < 等 shell 元字符。"
                )

    def _mask_sensitive(self, output: str, _subcommand: str, args: list[str]) -> str:
        """对敏感资源的输出进行脱敏。

        当查看 Secret 类资源时，将 data 字段中的 base64 值替换为 ***。
        """
        if not self._policy.sensitive_resources:
            return output

        # 检查参数中是否涉及敏感资源
        args_lower = [a.lower() for a in args]
        is_sensitive = any(
            res in args_lower for res in self._policy.sensitive_resources
        )

        if not is_sensitive:
            return output

        # YAML/JSON 输出中的 base64 值脱敏
        if any(f in args for f in ["-o", "yaml", "json", "-o=yaml", "-o=json"]):
            output = _SECRET_DATA_PATTERN.sub(r"\1***REDACTED***", output)
            output += "\n\n[注意: 敏感数据已脱敏]"

        return output

    def _truncate(self, output: str) -> str:
        """截断过长输出，保留头尾。"""
        max_chars = self._policy.max_output_chars
        if len(output) <= max_chars:
            return output

        original_lines = output.count("\n") + 1
        head_chars = int(max_chars * 0.6)
        tail_chars = int(max_chars * 0.2)

        head = output[:head_chars]
        tail = output[-tail_chars:] if tail_chars > 0 else ""

        # 尽量在行边界截断
        head_end = head.rfind("\n")
        if head_end > head_chars * 0.5:
            head = head[:head_end]

        tail_start = tail.find("\n")
        if 0 < tail_start < len(tail) * 0.5:
            tail = tail[tail_start + 1:]

        omitted = len(output) - len(head) - len(tail)
        separator = (
            f"\n\n... [已省略 {omitted} 字符 / 原始共 {original_lines} 行] ...\n\n"
        )

        return head + separator + tail
