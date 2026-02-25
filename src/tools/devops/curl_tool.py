"""HTTP 请求工具（基于 curl CLI）。

为 Agent 提供安全的 HTTP 请求能力，用于 API 调试、健康检查、接口探测等场景。

安全机制：
- URL scheme 白名单：只允许 http/https，拦截 file:// gopher:// 等 SSRF 攻击
- Host 校验：黑名单（云 metadata 端点）+ 可选白名单
- 危险参数拦截：禁止文件写入（-o）、文件上传（-F）、代理（--proxy）等
- 读写分级：只读模式仅允许 GET/HEAD/OPTIONS
- 命令注入防御：shell=False + shell 元字符检测
- 资源保护：超时控制 + 响应大小限制 + 输出截断
"""

import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.tools.base_tool import BaseTool
from src.utils.logger import logger

# Shell 元字符检测正则（与 command_sandbox 一致）
_SHELL_META_PATTERN = re.compile(r"[;|&`$><\n\r]")

# HTTP 状态码分隔标记
_STATUS_MARKER = "---HTTP_STATUS:"


@dataclass(frozen=True)
class HttpRequestPolicy:
    """HTTP 请求工具的安全策略。

    Attributes:
        binary: curl 二进制名称。
        allowed_methods: 允许的 HTTP 方法。
        allowed_schemes: 允许的 URL scheme（防 SSRF）。
        blocked_flags: 禁止的 curl 参数。
        allowed_hosts: Host 白名单（为空不限制）。
        blocked_hosts: Host 黑名单（默认拦截云 metadata 端点）。
        timeout: 请求超时（秒）。
        max_response_bytes: 响应体大小上限（字节）。
        max_output_chars: 输出文本截断阈值（字符）。
    """

    binary: str = "curl"

    allowed_methods: frozenset[str] = frozenset({
        "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS",
    })

    allowed_schemes: frozenset[str] = frozenset({"http", "https"})

    blocked_flags: frozenset[str] = frozenset({
        # 文件写入
        "-o", "--output",
        "-O", "--remote-name",
        # 文件上传
        "-T", "--upload-file",
        "-F", "--form",
        # 配置文件（可绕过安全检查）
        "-K", "--config",
        # 代理（可绕过网络策略）
        "--proxy", "-x",
        "--socks4", "--socks5",
        # DNS / 网络绕过
        "--dns-servers", "--resolve",
        # Unix socket（可访问本机服务）
        "--unix-socket", "--abstract-unix-socket",
        # 凭据相关
        "-b", "--cookie",
        "-c", "--cookie-jar",
        "--cert", "--key",
        "--netrc",
    })

    allowed_hosts: frozenset[str] = field(default_factory=frozenset)

    blocked_hosts: frozenset[str] = field(default_factory=lambda: frozenset({
        "169.254.169.254",
        "metadata.google.internal",
    }))

    timeout: int = 30
    max_response_bytes: int = 1_048_576  # 1MB
    max_output_chars: int = 8000


class HttpSandbox:
    """HTTP 请求安全执行沙箱。

    安全检查链路：
    1. curl 二进制可用性检查
    2. URL scheme 校验（只允许 http/https）
    3. Host 黑名单 + 可选白名单
    4. Blocked flags 检测
    5. Shell 注入检测
    6. subprocess 安全执行（shell=False）
    7. 输出截断
    """

    def __init__(self, policy: HttpRequestPolicy):
        self._policy = policy
        self._binary_path: Optional[str] = shutil.which(policy.binary)
        if not self._binary_path:
            logger.warning(
                "CLI 工具 '{}' 未找到，请确认已安装并在 PATH 中", policy.binary
            )

    @property
    def is_available(self) -> bool:
        return self._binary_path is not None

    def execute(
        self,
        url: str,
        method: str,
        headers: list[str],
        data: Optional[str],
        extra_flags: list[str],
    ) -> str:
        """安全执行 HTTP 请求。

        Args:
            url: 请求 URL。
            method: HTTP 方法。
            headers: 请求头列表，每项格式 "Header: value"。
            data: 请求体（POST/PUT/PATCH 时使用）。
            extra_flags: 附加 curl 参数。

        Returns:
            格式化的响应内容（包含 HTTP 状态码）。

        Raises:
            RuntimeError: 安全检查失败或执行异常时。
        """
        # 1. 二进制可用性
        if not self._binary_path:
            raise RuntimeError(
                f"'{self._policy.binary}' 未安装或不在 PATH 中，请先安装并确认可执行"
            )

        # 2. URL 安全校验
        self._validate_url(url)

        # 3. HTTP 方法校验
        if method not in self._policy.allowed_methods:
            raise RuntimeError(
                f"HTTP 方法 '{method}' 不被允许。"
                f"允许: {sorted(self._policy.allowed_methods)}"
            )

        # 4. Blocked flags 检查
        self._check_blocked_flags(extra_flags)

        # 5. Shell 注入检测（检查所有用户可控参数）
        self._check_injection(url, headers, data, extra_flags)

        # 6. 组装命令
        cmd = [
            self._binary_path,
            "-s", "-S",  # silent + show errors
            "--max-time", str(self._policy.timeout),
            "--max-filesize", str(self._policy.max_response_bytes),
            "-X", method,
            "-w", f"\n{_STATUS_MARKER}%{{http_code}}---",
        ]
        for h in headers:
            cmd.extend(["-H", h])
        if data:
            cmd.extend(["-d", data])
        cmd.extend(extra_flags)
        cmd.append(url)

        logger.info(
            "执行 HTTP 请求: {} {} (超时 {}s)", method, url, self._policy.timeout
        )

        # 7. 安全执行
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._policy.timeout + 5,  # 留 5s 余量给进程清理
                shell=False,
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(
                f"HTTP 请求超时（{self._policy.timeout}秒）。"
                "请检查目标地址是否可达，或尝试缩短超时时间。"
            )
        except OSError as e:
            raise RuntimeError(f"命令执行失败: {e}")

        # 8. 处理 stderr（curl 错误信息）
        if result.returncode != 0:
            stderr = result.stderr.strip()
            if len(stderr) > 1000:
                stderr = stderr[:1000] + "... (输出截断)"
            raise RuntimeError(f"curl 请求失败 (exit={result.returncode}): {stderr}")

        # 9. 解析输出，提取 HTTP 状态码
        output = result.stdout
        return self._format_output(output)

    def _validate_url(self, url: str) -> None:
        """URL 安全校验：scheme → host 非空 → 黑名单 → 可选白名单。"""
        parsed = urllib.parse.urlparse(url)

        # scheme 校验
        scheme = parsed.scheme.lower()
        if scheme not in self._policy.allowed_schemes:
            raise RuntimeError(
                f"URL scheme '{scheme}' 不被允许。"
                f"只允许: {sorted(self._policy.allowed_schemes)}"
            )

        # host 不能为空
        if not parsed.hostname:
            raise RuntimeError("URL 缺少 host")

        host = parsed.hostname.lower()

        # host 黑名单（云 metadata 端点等，无条件拦截）
        if host in self._policy.blocked_hosts:
            raise RuntimeError(f"Host '{host}' 被安全策略禁止访问")

        # 可选白名单：配置后仅允许白名单内的 host
        if self._policy.allowed_hosts and host not in self._policy.allowed_hosts:
            raise RuntimeError(
                f"Host '{host}' 不在允许列表中。"
                f"允许: {sorted(self._policy.allowed_hosts)}"
            )

    def _check_blocked_flags(self, flags: list[str]) -> None:
        """检查是否包含被禁止的 curl 参数。"""
        for flag in flags:
            flag_part = flag.split("=")[0] if "=" in flag else flag
            if flag_part in self._policy.blocked_flags:
                raise RuntimeError(
                    f"参数 '{flag_part}' 被安全策略禁止。"
                    f"被禁止的参数: {sorted(self._policy.blocked_flags)}"
                )

    @staticmethod
    def _check_injection(
        url: str,
        headers: list[str],
        data: Optional[str],
        flags: list[str],
    ) -> None:
        """Shell 注入检测（所有用户可控参数不得含 shell 元字符）。

        注意：data 中的 $ 和 & 在 JSON / form 数据中是合法字符，
        但由于使用 shell=False 执行，这些字符不会被 shell 解析，
        因此只对 url/headers/flags 做元字符检测，data 豁免。
        """
        parts_to_check = [url] + headers + flags
        for part in parts_to_check:
            if _SHELL_META_PATTERN.search(part):
                raise RuntimeError(
                    f"参数 '{part}' 包含不允许的特殊字符。"
                    "不允许使用 ; | & ` $ > < 等 shell 元字符。"
                )

    def _format_output(self, raw: str) -> str:
        """解析 curl 输出，提取 HTTP 状态码，截断过长响应。"""
        # 分离响应体和状态码
        status_code = ""
        body = raw
        marker_pos = raw.rfind(_STATUS_MARKER)
        if marker_pos != -1:
            body = raw[:marker_pos]
            # 提取 "---HTTP_STATUS:200---" 中的 200
            status_part = raw[marker_pos + len(_STATUS_MARKER):]
            status_code = status_part.rstrip("-").strip()

        body = body.strip()
        if not body:
            body = "(无响应体)"

        # 截断过长输出
        body = self._truncate(body)

        # 拼装最终输出
        if status_code:
            return f"[HTTP {status_code}]\n{body}"
        return body

    def _truncate(self, output: str) -> str:
        """截断过长输出，保留头尾。"""
        max_chars = self._policy.max_output_chars
        if len(output) <= max_chars:
            return output

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
        separator = f"\n\n... [已省略 {omitted} 字符] ...\n\n"

        return head + separator + tail


# ── 只读 / 写操作方法分级 ──

_READONLY_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_WRITE_METHODS = frozenset({"POST", "PUT", "DELETE", "PATCH"})


class CurlTool(BaseTool):
    """HTTP 请求工具（基于 curl）。

    通过 curl CLI 发送 HTTP 请求，用于 API 调试、健康检查、接口探测。
    默认只读模式（仅 GET/HEAD/OPTIONS），可配置开启写操作。

    Args:
        sandbox: HttpSandbox 实例，负责安全执行。
        enable_write: 是否启用写操作方法（POST/PUT/DELETE/PATCH）。
    """

    def __init__(self, sandbox: HttpSandbox, enable_write: bool = False):
        self._sandbox = sandbox
        self._enable_write = enable_write

    @property
    def name(self) -> str:
        return "curl"

    @property
    def description(self) -> str:
        base = (
            "HTTP 请求工具，通过 curl 发送 HTTP 请求。\n"
            "适用场景：API 调试、健康检查、接口探测、获取远程数据。\n\n"
            "支持的只读操作：\n"
            "- GET: 获取资源（默认方法）\n"
            "- HEAD: 只获取响应头（检查可达性）\n"
            "- OPTIONS: 查询支持的方法（CORS 预检）"
        )
        if self._enable_write:
            base += (
                "\n\n支持的写操作（已启用）：\n"
                "- POST: 创建资源 / 提交数据\n"
                "- PUT: 更新资源（全量替换）\n"
                "- PATCH: 更新资源（部分修改）\n"
                "- DELETE: 删除资源"
            )
        base += (
            "\n\n安全限制：\n"
            "- 只允许 http/https 协议\n"
            "- 不允许访问内网地址和云 metadata 端点\n"
            "- 不支持文件上传/下载到本地"
        )
        return base

    @property
    def parameters(self) -> Dict[str, Any]:
        methods = sorted(_READONLY_METHODS)
        if self._enable_write:
            methods = sorted(_READONLY_METHODS | _WRITE_METHODS)

        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "请求的完整 URL（必须 http:// 或 https:// 开头）",
                },
                "method": {
                    "type": "string",
                    "enum": methods,
                    "description": "HTTP 方法，默认 GET",
                },
                "headers": {
                    "type": "string",
                    "description": (
                        "自定义请求头，每行一个，格式: Header-Name: value\n"
                        "示例:\n"
                        "  Content-Type: application/json\n"
                        "  Authorization: Bearer token123"
                    ),
                },
                "data": {
                    "type": "string",
                    "description": (
                        "请求体数据（POST/PUT/PATCH 时使用）。\n"
                        'JSON 示例: {"key": "value"}'
                    ),
                },
                "flags": {
                    "type": "string",
                    "description": (
                        "附加 curl 参数，空格分隔。常用：\n"
                        "  -L（跟随重定向）、-k（忽略 SSL 校验，仅调试）\n"
                        "  -v（详细输出，用于调试）、--compressed（接受压缩）\n"
                        "  -I（只获取响应头）"
                    ),
                },
            },
            "required": ["url"],
        }

    def should_confirm(self, **kwargs) -> bool:
        """写操作方法（POST/PUT/DELETE/PATCH）需要用户确认。"""
        method = kwargs.get("method", "GET").upper()
        return method in _WRITE_METHODS

    def execute(self, **kwargs) -> str:
        url: str = kwargs.get("url", "")
        method: str = kwargs.get("method", "GET").upper()
        headers_str: str = kwargs.get("headers", "")
        data: str = kwargs.get("data", "")
        flags_str: str = kwargs.get("flags", "")

        if not url:
            raise RuntimeError("url 参数不能为空")

        # 读写权限检查
        if not self._enable_write and method not in _READONLY_METHODS:
            raise RuntimeError(
                f"当前为只读模式，不允许 {method} 请求。"
                f"只允许: {sorted(_READONLY_METHODS)}"
            )

        # 解析 headers（按行分割）
        headers = [h.strip() for h in headers_str.splitlines() if h.strip()]

        # 解析 extra_flags
        extra_flags = flags_str.strip().split() if flags_str.strip() else []

        return self._sandbox.execute(
            url=url,
            method=method,
            headers=headers,
            data=data or None,
            extra_flags=extra_flags,
        )
