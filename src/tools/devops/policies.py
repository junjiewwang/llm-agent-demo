"""预置二进制安全策略集。

将原来分散在各 tool 文件中的子命令分级、黑名单参数等安全配置，
统一到 BinaryPolicy 数据结构中。新增工具只需在此文件添加一个 Policy 即可。
"""

from src.tools.devops.bash_executor import BinaryPolicy

# ── kubectl 策略 ──

KUBECTL_POLICY = BinaryPolicy(
    name="kubectl",
    # L0 只读（直接执行，无需确认）
    read_subcommands=frozenset({
        "get", "describe", "logs", "top", "explain",
        "api-resources", "api-versions", "cluster-info",
        "version", "diff", "auth",
    }),
    # L1 写操作 + L2 危险操作（需确认）
    write_subcommands=frozenset({
        # L1 运维
        "apply", "create", "patch", "edit",
        "scale", "rollout", "cordon", "uncordon",
        "label", "annotate", "taint",
        "exec", "cp", "port-forward",
        # L2 危险
        "delete", "drain", "replace",
    }),
    blocked_flags=frozenset({
        "--grace-period=0",
    }),
    sensitive_resources=frozenset({
        "secret", "secrets",
    }),
)

# ── docker 策略 ──

DOCKER_POLICY = BinaryPolicy(
    name="docker",
    read_subcommands=frozenset({
        "ps", "images", "logs", "inspect", "stats",
        "network", "volume", "system", "version", "info",
        "compose", "top",
    }),
    write_subcommands=frozenset({
        # L1 运维
        "start", "stop", "restart", "pull", "build",
        "exec", "run", "cp", "tag", "create",
        # L2 危险
        "rm", "rmi", "kill", "prune", "push",
    }),
    blocked_flags=frozenset({
        "--privileged",
    }),
    # docker stats 自动注入 --no-stream，防止无限输出
    inject_rules={"stats": ["--no-stream"]},
)

# ── curl 策略 ──

CURL_POLICY = BinaryPolicy(
    name="curl",
    # curl 没有子命令概念，read/write 由 HTTP 方法决定
    read_subcommands=frozenset(),
    write_subcommands=frozenset(),
    blocked_flags=frozenset({
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
    }),
)

# ── 管道工具白名单 ──
# 管道右侧允许使用的安全文本处理工具（无需 BinaryPolicy，仅做文本过滤/格式化）。
# 这些工具不会修改系统状态，只对 stdin 做变换后输出到 stdout。

PIPE_TOOLS: frozenset[str] = frozenset({
    # 文本搜索 & 过滤
    "grep", "egrep", "fgrep",
    # 文本处理
    "awk", "sed", "tr", "cut", "paste",
    # 排序 & 去重
    "sort", "uniq",
    # 行数 & 统计
    "wc", "head", "tail",
    # 格式化 & 列对齐
    "column", "fmt",
    # 结构化数据处理
    "jq", "yq",
    # 其他常用
    "cat", "tee", "xargs",
    "base64", "md5sum", "sha256sum",
})

# ── 策略注册表 ──

ALL_POLICIES: dict[str, BinaryPolicy] = {
    "kubectl": KUBECTL_POLICY,
    "docker": DOCKER_POLICY,
    "curl": CURL_POLICY,
}
