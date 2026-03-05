"""统一 Bash 命令安全执行器。

安全模型：校验前置 + PATH 隔离 + bash -c 原生执行。

安全层级（由外到内）：
1. PATH 隔离：构建受限 symlink 目录，bash 只能发现白名单二进制
2. 绝对路径拦截：正则检测 /usr/bin/xxx 等绝对路径调用，防止绕过 PATH
3. bash 内建黑名单：拦截 eval / exec / source 等危险内建命令
4. BinaryPolicy 校验：首段二进制白名单 + 子命令分级 + blocked_flags
5. curl 专属校验：URL scheme / host 黑名单 / host 白名单
6. 超时控制 + 输出截断 + 敏感信息脱敏
"""

import os
import re
import shlex
import shutil
import subprocess
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from src.utils.logger import logger

# ── 安全常量 ──

# 绝对路径检测：拦截直接使用绝对路径调用二进制（绕过 PATH 隔离）
_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(?:^|[|;&\s])\s*/"       # 命令起始或管道/分隔符后紧跟 /
    r"(?:usr|bin|sbin|opt|etc"  # 常见系统目录
    r"|home|tmp|var|dev|proc"
    r"|sys|root|snap|nix)"
    r"/",
)

# bash 危险内建命令黑名单（不受 PATH 限制，必须在命令层面拦截）
_BUILTIN_BLACKLIST = frozenset({
    "eval", "exec", "source", ".", "export", "unset",
    "alias", "unalias", "enable", "builtin", "declare",
    "typeset", "readonly", "set", "shopt", "trap",
    "ulimit", "umask", "hash", "type", "command",
    "bg", "fg", "jobs", "kill", "wait",
    "compgen", "complete", "compopt",
})

# Secret data 字段屏蔽正则（匹配 base64 值）
_SECRET_DATA_PATTERN = re.compile(r"^(\s+\S+:\s*).+$", re.MULTILINE)

# 云 metadata 端点黑名单（无条件拦截，防 SSRF）
_BLOCKED_HOSTS = frozenset({"169.254.169.254", "metadata.google.internal"})

# HTTP 状态码分隔标记（curl 专用）
_STATUS_MARKER = "---HTTP_STATUS:"


def _split_pipe_segments(command: str) -> list[str]:
    """按管道符分割命令，但跳过引号内的管道符。

    bash 中 `|` 在单引号 / 双引号内是普通字符，不是管道分隔符。
    例如: `grep -E "kube-dns|coredns"` 中的 `|` 不应作为管道分割。

    Returns:
        管道各段的列表（不含前后空白）。
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0

    while i < len(command):
        ch = command[i]

        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif ch == "\\" and in_double and i + 1 < len(command):
            # 双引号内的转义
            current.append(ch)
            current.append(command[i + 1])
            i += 1
        elif ch == "|" and not in_single and not in_double:
            segments.append("".join(current).strip())
            current = []
        else:
            current.append(ch)

        i += 1

    # 最后一段
    tail = "".join(current).strip()
    if tail:
        segments.append(tail)

    return segments


@dataclass(frozen=True)
class BinaryPolicy:
    """单个二进制程序的安全策略。

    Attributes:
        name: 二进制名称（如 "kubectl"）。
        read_subcommands: 只读子命令（无需确认）。
        write_subcommands: 写操作子命令（需确认）。
        blocked_flags: 禁止的参数。
        sensitive_resources: 需脱敏输出的资源类型（如 kubectl secret）。
        default_args: 自动注入的默认参数（如 docker stats --no-stream）。
        inject_rules: 子命令 → 默认注入参数的映射（如 {"stats": ["--no-stream"]}）。
    """

    name: str
    read_subcommands: frozenset[str] = field(default_factory=frozenset)
    write_subcommands: frozenset[str] = field(default_factory=frozenset)
    blocked_flags: frozenset[str] = field(default_factory=frozenset)
    sensitive_resources: frozenset[str] = field(default_factory=frozenset)
    default_args: tuple[str, ...] = ()
    inject_rules: dict[str, list[str]] = field(default_factory=dict)


class BashExecutor:
    """统一安全命令执行器（PATH 隔离 + bash -c 原生执行）。

    安全执行流程：
    1. 安全预检（3 层）：绝对路径拦截 → bash 内建黑名单 → 二进制白名单校验
    2. 特殊校验：BinaryPolicy blocked_flags + curl URL 安全 + namespace 白名单
    3. 执行：bash -c command（受限 PATH 环境）
    4. 后处理：截断 + 脱敏

    PATH 隔离机制：
    - 启动时创建临时 symlink 目录，只链接白名单二进制
    - 执行时 env={"PATH": symlink_dir}，bash 只能发现白名单中的命令
    - 未授权的二进制（如 rm, python）在 PATH 中找不到，bash 直接报 command not found
    """

    def __init__(
        self,
        policies: dict[str, BinaryPolicy],
        pipe_tools: frozenset[str],
        timeout: int = 30,
        max_output_chars: int = 5000,
        namespace_whitelist: Optional[frozenset[str]] = None,
        curl_allowed_hosts: Optional[frozenset[str]] = None,
    ):
        self._policies = policies
        self._pipe_tools = pipe_tools
        self._timeout = timeout
        self._max_output = max_output_chars
        self._namespace_whitelist = namespace_whitelist
        self._curl_allowed_hosts = curl_allowed_hosts

        # 合并所有白名单二进制名称（policies + pipe_tools）
        self._all_allowed = frozenset(policies.keys()) | pipe_tools

        # 预解析二进制路径 + 构建受限 PATH 目录
        self._binary_paths: dict[str, Optional[str]] = {}
        for name in self._all_allowed:
            path = shutil.which(name)
            if not path:
                logger.warning("CLI 工具 '{}' 未找到，请确认已安装并在 PATH 中", name)
            self._binary_paths[name] = path

        # 预解析 bash 绝对路径（执行器本身需要调用 bash -c）
        self._bash_path = shutil.which("bash")
        if not self._bash_path:
            raise RuntimeError("bash 未找到，BashExecutor 无法工作")

        self._restricted_path = self._setup_restricted_path()

    def _setup_restricted_path(self) -> str:
        """构建受限 PATH 目录：只包含白名单二进制的 symlink。

        在临时目录中为每个白名单二进制创建软链接，
        执行时将 PATH 设为此目录，bash 只能发现这些命令。

        Returns:
            受限 symlink 目录的路径。
        """
        restricted_dir = os.path.join(tempfile.gettempdir(), "agent-restricted-bin")
        os.makedirs(restricted_dir, exist_ok=True)

        # 清理旧链接（防止残留过期 symlink）
        for existing in Path(restricted_dir).iterdir():
            existing.unlink()

        linked = []
        for name, path in self._binary_paths.items():
            if not path:
                continue
            link = os.path.join(restricted_dir, name)
            try:
                os.symlink(path, link)
                linked.append(name)
            except OSError as e:
                logger.warning("创建 symlink 失败: {} → {}: {}", name, path, e)

        logger.info(
            "受限 PATH 目录已构建: {} | 已链接: {}",
            restricted_dir, sorted(linked),
        )
        return restricted_dir

    def execute(self, command: str) -> str:
        """安全执行一条 bash 命令。

        支持完整 bash 语法：管道、heredoc、重定向、子 shell 等。
        安全由 PATH 隔离 + 预检校验保障，而非限制 bash 语法。

        Args:
            command: 完整命令字符串（如 "kubectl get pods -A | grep Error"）。

        Returns:
            命令输出。

        Raises:
            RuntimeError: 安全检查失败或执行异常时。
        """
        if not command or not command.strip():
            raise RuntimeError("命令不能为空")

        # 1. 安全预检（3 层）
        self._check_absolute_path(command)
        self._check_builtin_blacklist(command)
        primary_binary, primary_args = self._validate_binaries(command)

        # 2. 首段 BinaryPolicy 特殊校验
        if primary_binary:
            policy = self._policies.get(primary_binary)
            if policy:
                self._check_blocked_flags(primary_args, policy)

                if primary_binary == "curl":
                    self._validate_curl(primary_args)
                elif primary_binary == "kubectl" and self._namespace_whitelist:
                    self._check_namespace(primary_args)

        # 3. 通过 bash -c 原生执行（受限 PATH）
        restricted_env = self._build_restricted_env()
        logger.info("执行命令: bash -c '{}' (超时 {}s)", command, self._timeout)

        try:
            result = subprocess.run(
                [self._bash_path, "-c", command],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=restricted_env,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"命令执行超时（{self._timeout}秒）。"
                "如果是日志查看，请使用 --tail 限制行数。"
            )
        except OSError as e:
            raise RuntimeError(f"命令执行失败: {e}")

        # 4. 处理输出
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if len(stderr) > 1000:
                stderr = stderr[:1000] + "... (输出截断)"
            raise RuntimeError(
                f"命令返回错误 (exit={result.returncode}): {stderr}"
            )

        output = result.stdout.strip()
        if not output:
            output = "(无输出)"

        # 5. 敏感信息脱敏（首段有 policy 时）
        if primary_binary:
            policy = self._policies.get(primary_binary)
            if policy:
                output = self._mask_sensitive(output, primary_args, policy)

        # 6. 截断
        return self._truncate(output)

    def classify(self, command: str) -> str:
        """分类命令危险等级（基于管道首段的主命令）。

        Returns:
            "read" / "write" / "blocked"
        """
        # 提取管道首段
        first_segment = _split_pipe_segments(command)[0]

        try:
            tokens = shlex.split(first_segment)
        except ValueError:
            return "blocked"

        if not tokens:
            return "blocked"

        binary = tokens[0]
        if binary not in self._policies:
            return "blocked"

        policy = self._policies[binary]

        # curl: 根据 HTTP 方法判断
        if binary == "curl":
            return self._classify_curl(tokens[1:])

        # 其他工具: 根据子命令分级
        subcmd = tokens[1] if len(tokens) > 1 else ""
        if subcmd in policy.read_subcommands:
            return "read"
        if subcmd in policy.write_subcommands:
            return "write"

        # 未知子命令默认为 write（安全侧）
        return "write"

    def is_available(self, binary: str) -> bool:
        """检查二进制是否可用。"""
        return self._binary_paths.get(binary) is not None

    # ── 安全预检方法 ──

    @staticmethod
    def _check_absolute_path(command: str) -> None:
        """拦截绝对路径调用（防止绕过 PATH 隔离）。"""
        if _ABSOLUTE_PATH_PATTERN.search(command):
            raise RuntimeError(
                "不允许使用绝对路径执行命令。"
                "请直接使用命令名称（如 kubectl、docker），不要使用 /usr/bin/xxx 形式。"
            )

    @staticmethod
    def _check_builtin_blacklist(command: str) -> None:
        """拦截 bash 危险内建命令（不受 PATH 限制）。"""
        # 提取管道各段的首个 token（即各段的命令名）
        for segment in _split_pipe_segments(command):
            segment = segment.strip()
            if not segment:
                continue
            try:
                tokens = shlex.split(segment)
            except ValueError:
                continue
            if tokens and tokens[0] in _BUILTIN_BLACKLIST:
                raise RuntimeError(
                    f"不允许使用 bash 内建命令 '{tokens[0]}'。"
                    f"被禁止的内建命令: eval, exec, source, export 等。"
                )

    def _validate_binaries(self, command: str) -> tuple[Optional[str], list[str]]:
        """校验命令中所有二进制是否在白名单内。

        按管道拆分后，逐段提取命令名并校验：
        - 首段：必须在 BinaryPolicy 白名单中
        - 后续段：必须在 BinaryPolicy 或 PIPE_TOOLS 白名单中

        Returns:
            (首段二进制名, 首段参数列表) 用于后续 Policy 校验。
        """
        segments = _split_pipe_segments(command)
        primary_binary = None
        primary_args: list[str] = []

        for i, segment in enumerate(segments):
            segment = segment.strip()
            if not segment:
                continue

            try:
                tokens = shlex.split(segment)
            except ValueError as e:
                raise RuntimeError(f"命令段解析失败: '{segment}' → {e}")

            if not tokens:
                continue

            binary = tokens[0]

            if i == 0:
                # 首段：必须有 BinaryPolicy
                if binary not in self._policies:
                    raise RuntimeError(
                        f"不允许执行 '{binary}'。"
                        f"允许的命令: {sorted(self._policies.keys())}"
                    )
                if not self._binary_paths.get(binary):
                    raise RuntimeError(
                        f"'{binary}' 未安装或不在 PATH 中，请先安装并确认可执行"
                    )
                primary_binary = binary
                primary_args = tokens[1:]
            else:
                # 管道后续段：BinaryPolicy 或 PIPE_TOOLS 均可
                if binary not in self._all_allowed:
                    raise RuntimeError(
                        f"管道中不允许使用 '{binary}'。"
                        f"允许的管道工具: {sorted(self._pipe_tools)}"
                    )

        return primary_binary, primary_args

    def _build_restricted_env(self) -> dict[str, str]:
        """构建受限执行环境变量。

        关键：PATH 仅指向 symlink 目录，bash 只能发现白名单二进制。
        保留必要的环境变量（HOME, LANG 等），防止命令执行异常。
        """
        return {
            "PATH": self._restricted_path,
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
            "TERM": os.environ.get("TERM", "xterm"),
            # kubectl / docker 可能需要的配置路径
            "KUBECONFIG": os.environ.get("KUBECONFIG", ""),
            "DOCKER_HOST": os.environ.get("DOCKER_HOST", ""),
        }

    # ── BinaryPolicy 校验方法 ──

    @staticmethod
    def _check_blocked_flags(args: list[str], policy: BinaryPolicy) -> None:
        """检查参数中是否包含被禁止的 flag。

        同时检查完整参数（如 --grace-period=0）和 flag 前缀（如 --privileged），
        确保 --key=value 形式的黑名单项能被两种匹配方式命中。
        """
        for arg in args:
            if arg in policy.blocked_flags:
                raise RuntimeError(
                    f"参数 '{arg}' 被安全策略禁止。"
                    f"被禁止的参数: {sorted(policy.blocked_flags)}"
                )
            flag_part = arg.split("=")[0] if "=" in arg else arg
            if flag_part in policy.blocked_flags:
                raise RuntimeError(
                    f"参数 '{flag_part}' 被安全策略禁止。"
                    f"被禁止的参数: {sorted(policy.blocked_flags)}"
                )

    def _validate_curl(self, args: list[str]) -> None:
        """curl 专属安全校验：URL scheme + host 黑名单 + 可选白名单。"""
        url = self._extract_url(args)
        if not url:
            raise RuntimeError("curl 命令需要指定 URL")

        parsed = urllib.parse.urlparse(url)

        if parsed.scheme.lower() not in ("http", "https"):
            raise RuntimeError(
                f"URL scheme '{parsed.scheme}' 不被允许。只允许 http/https"
            )

        if not parsed.hostname:
            raise RuntimeError("URL 缺少 host")

        host = parsed.hostname.lower()

        if host in _BLOCKED_HOSTS:
            raise RuntimeError(f"Host '{host}' 被安全策略禁止访问")

        if self._curl_allowed_hosts and host not in self._curl_allowed_hosts:
            raise RuntimeError(
                f"Host '{host}' 不在允许列表中。"
                f"允许: {sorted(self._curl_allowed_hosts)}"
            )

    @staticmethod
    def _extract_url(args: list[str]) -> Optional[str]:
        """从 curl 参数中提取 URL（非 flag 参数中的第一个 http(s):// 开头的）。"""
        skip_next = False
        for arg in args:
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-") and not arg.startswith("--") and len(arg) == 2:
                if arg in ("-X", "-H", "-d", "-o", "-b", "-c", "-w", "-e", "-A", "-u"):
                    skip_next = True
                continue
            if arg.startswith("--"):
                if "=" not in arg:
                    if arg in ("--request", "--header", "--data", "--data-raw",
                               "--data-urlencode", "--output", "--cookie",
                               "--cookie-jar", "--user-agent", "--referer",
                               "--max-time", "--max-filesize", "--connect-timeout"):
                        skip_next = True
                continue
            if arg.startswith(("http://", "https://")):
                return arg
        return None

    def _check_namespace(self, args: list[str]) -> None:
        """校验 kubectl namespace 是否在白名单内。"""
        ns = None
        for i, arg in enumerate(args):
            if arg == "-n" and i + 1 < len(args):
                ns = args[i + 1]
                break
            if arg.startswith("--namespace="):
                ns = arg.split("=", 1)[1]
                break
            if arg.startswith("-n") and len(arg) > 2:
                ns = arg[2:]
                break

        if ns and self._namespace_whitelist and ns not in self._namespace_whitelist:
            raise RuntimeError(
                f"命名空间 '{ns}' 不在允许列表中。"
                f"允许的命名空间: {sorted(self._namespace_whitelist)}"
            )

    def _classify_curl(self, args: list[str]) -> str:
        """根据 curl 的 -X 方法判断读写级别。"""
        method = "GET"
        for i, arg in enumerate(args):
            if arg in ("-X", "--request") and i + 1 < len(args):
                method = args[i + 1].upper()
                break
            if arg.startswith("-X") and len(arg) > 2:
                method = arg[2:].upper()
                break
            if arg.startswith("--request="):
                method = arg.split("=", 1)[1].upper()
                break
            if arg in ("-d", "--data", "--data-raw", "--data-urlencode"):
                if method == "GET":
                    method = "POST"

        if method in ("GET", "HEAD", "OPTIONS"):
            return "read"
        return "write"

    # ── 输出处理方法 ──

    @staticmethod
    def _mask_sensitive(
        output: str, args: list[str], policy: BinaryPolicy
    ) -> str:
        """对敏感资源的输出进行脱敏（如 kubectl secret）。"""
        if not policy.sensitive_resources:
            return output

        args_lower = [a.lower() for a in args]
        is_sensitive = any(
            res in args_lower for res in policy.sensitive_resources
        )
        if not is_sensitive:
            return output

        if any(f in args for f in ["-o", "yaml", "json", "-o=yaml", "-o=json"]):
            output = _SECRET_DATA_PATTERN.sub(r"\1***REDACTED***", output)
            output += "\n\n[注意: 敏感数据已脱敏]"

        return output

    def _truncate(self, output: str) -> str:
        """截断过长输出，保留头尾。"""
        max_chars = self._max_output
        if len(output) <= max_chars:
            return output

        original_lines = output.count("\n") + 1
        head_chars = int(max_chars * 0.6)
        tail_chars = int(max_chars * 0.2)

        head = output[:head_chars]
        tail = output[-tail_chars:] if tail_chars > 0 else ""

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
