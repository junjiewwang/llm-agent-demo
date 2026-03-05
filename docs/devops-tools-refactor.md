# DevOps 工具集重构方案：统一 Bash 执行器

## 一、现状分析

### 1.1 当前架构

```
LLM 看到 3 个独立的 Function Calling 工具：
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   kubectl    │  │   docker     │  │    curl      │
│ subcommand   │  │ subcommand   │  │ url          │
│ resource_type│  │ target       │  │ method       │
│ resource_name│  │ flags        │  │ headers      │
│ namespace    │  │              │  │ data         │
│ flags        │  │              │  │ flags        │
└──────┬───────┘  └──────┬───────┘  └──────┬───────┘
       │                 │                 │
  CommandSandbox    CommandSandbox    HttpSandbox (独立实现)
  CommandPolicy     CommandPolicy    HttpRequestPolicy
       │                 │                 │
       └────────── subprocess.run ─────────┘
```

### 1.2 核心问题

| # | 问题 | 具体表现 |
|---|------|---------|
| 1 | **LLM 认知负担重** | 3 个工具 = 3 套参数 schema，LLM 需要理解每个工具的独立参数语义（`resource_type` vs `target` vs `url`）；增加新工具（如 helm、terraform）需要教 LLM 理解又一套新 schema |
| 2 | **大量重复代码** | `_SHELL_META_PATTERN`、`_check_injection`、`_check_blocked_flags`、`_truncate`、二进制检查 等在 `CommandSandbox` 和 `HttpSandbox` 中几乎完全重复 |
| 3 | **扩展成本高** | 新增一个 DevOps 工具（如 helm）需要：新建 tool 文件 → 定义子命令分级 → 定义 JSON Schema → 在 factory 注册 → 在 settings 加开关。至少 200+ 行代码 |
| 4 | **参数中间层多余** | LLM 本来就"会写命令"，但当前架构强迫 LLM 把命令拆成结构化参数（`subcommand`, `resource_type`, `flags`），然后工具再拼回命令行。这个拆→拼的往返是浪费 |
| 5 | **curl 完全独立** | CurlTool 自己实现了一整套沙箱（HttpSandbox），与 CommandSandbox 高度重复但不可复用 |
| 6 | **双重截断** | 沙箱层截断 5000/8000 字符 + ToolResult 层截断 3000 字符，两层逻辑重叠 |

### 1.3 重复代码统计

| 重复项 | 位置 A | 位置 B |
|--------|--------|--------|
| `_SHELL_META_PATTERN` | `command_sandbox.py:43` | `curl_tool.py:25` |
| `_check_injection()` | `CommandSandbox` | `HttpSandbox` |
| `_check_blocked_flags()` | `CommandSandbox` | `HttpSandbox` |
| `_truncate()` | `CommandSandbox` | `HttpSandbox` |
| 二进制 `shutil.which()` | `CommandSandbox.__init__` | `HttpSandbox.__init__` |
| 超时/错误处理 | `CommandSandbox.execute` | `HttpSandbox.execute` |
| L0/L1/L2 分级模式 | `kubectl_tool.py` | `docker_tool.py` |
| `should_confirm` 判断 | 3 个 tool 各自实现 |
| `flags` 字符串解析 | `kubectl_tool.py` | `docker_tool.py` |

---

## 二、重构目标

1. **LLM 只看到 1 个工具**：`execute_command`，参数只有 `command`（字符串）+ `confirm`（布尔）
2. **消除重复代码**：统一安全执行层，删除 `HttpSandbox`
3. **零成本扩展**：新增 DevOps 工具只需在配置中添加二进制名到白名单
4. **保持安全能力不降级**：命令注入防御、白名单、黑名单参数、超时、截断、脱敏全部保留

---

## 三、重构方案

### 3.1 新架构

```
LLM 只看到 1 个 Function Calling 工具：
┌──────────────────────────────────────────┐
│           execute_command                │
│  command: "kubectl get pods -n default"  │
│  (一个字符串参数，LLM 直接写命令)          │
└─────────────────┬────────────────────────┘
                  │
        ┌─────────▼─────────┐
        │   BashExecutor    │  ← 统一安全执行器
        │                   │
        │ 1. 解析命令       │  shlex.split()
        │ 2. 二进制白名单   │  binary ∈ allowed_binaries?
        │ 3. 子命令分级     │  → 判断 confirm 级别
        │ 4. 黑名单参数    │  → 拦截危险 flags
        │ 5. 注入检测      │  → shell 元字符检测
        │ 6. 安全执行      │  subprocess.run(shell=False)
        │ 7. 输出处理      │  截断 + 脱敏
        └─────────┬─────────┘
                  │
          subprocess.run
```

### 3.2 核心设计

#### 3.2.1 LLM 工具定义（极简）

```python
name = "execute_command"
description = """在服务器上安全执行 CLI 命令。

支持的工具: kubectl, docker, curl, helm 等（由管理员配置）。
直接输入完整命令即可，例如:
  - kubectl get pods -n default
  - docker ps --format '{{.Names}}'
  - curl -s https://api.example.com/health

安全限制:
  - 仅允许配置的二进制程序
  - 危险操作需要用户确认
  - 命令超时 30 秒
"""

parameters = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "要执行的完整命令（如 'kubectl get pods -n default'）"
        }
    },
    "required": ["command"]
}
```

**对比当前：**

| 维度 | 当前（3 个工具） | 重构后（1 个工具） |
|------|----------------|------------------|
| LLM 看到的工具数 | 3 | 1 |
| 参数总数 | ~15 个（跨 3 个 schema） | 1 个（`command` 字符串） |
| LLM 要学的 schema | 3 套不同参数 | 1 个直觉参数 |
| 新增工具 | 新建文件 + schema + 注册 | 配置里加一个二进制名 |

#### 3.2.2 BashExecutor（统一安全执行器）

```python
@dataclass(frozen=True)
class BinaryPolicy:
    """单个二进制程序的安全策略"""
    name: str                                    # 如 "kubectl"
    read_subcommands: frozenset[str]             # L0 只读（无需确认）
    write_subcommands: frozenset[str]            # L1 写（需确认）
    blocked_flags: frozenset[str]                # 黑名单参数
    sensitive_resources: frozenset[str] = frozenset()  # 需脱敏的资源类型
    default_args: tuple[str, ...] = ()           # 默认注入的参数（如 docker stats --no-stream）
    namespace_whitelist: frozenset[str] | None = None  # 可选 namespace 限制（kubectl 专用）

class BashExecutor:
    """统一安全命令执行器"""

    def __init__(self, policies: dict[str, BinaryPolicy], timeout: int = 30, max_output_chars: int = 5000):
        self._policies = policies    # binary_name → BinaryPolicy
        self._timeout = timeout
        self._max_output = max_output_chars

    def execute(self, command: str) -> str:
        """
        安全执行一条命令。

        流程:
        1. shlex.split(command) 解析为 token 列表
        2. tokens[0] 查 _policies 白名单
        3. tokens[1] 查子命令分级（read / write / unknown）
        4. 检查 blocked_flags
        5. 检查 shell 注入
        6. 特殊处理（namespace 校验、default_args 注入等）
        7. subprocess.run(tokens, shell=False, timeout=...)
        8. 截断 + 脱敏
        """

    def classify(self, command: str) -> str:
        """分类命令危险等级: "read" / "write" / "blocked" """
```

#### 3.2.3 BinaryPolicy 预置集

```python
# kubectl 策略
KUBECTL_POLICY = BinaryPolicy(
    name="kubectl",
    read_subcommands=frozenset({"get", "describe", "logs", "top", "api-resources", "explain", "version", "cluster-info", "config"}),
    write_subcommands=frozenset({"apply", "create", "delete", "edit", "patch", "scale", "rollout", "label", "annotate", "cordon", "uncordon", "drain", "taint", "expose", "run", "set", "autoscale", "cp", "exec", "port-forward", "replace"}),
    blocked_flags=frozenset({"--force", "--grace-period=0", "--all", "-A", "--all-namespaces"}),
    sensitive_resources=frozenset({"secret", "secrets"}),
)

# docker 策略
DOCKER_POLICY = BinaryPolicy(
    name="docker",
    read_subcommands=frozenset({"ps", "images", "logs", "inspect", "stats", "top", "port", "diff", "history", "version", "info", "network ls", "volume ls", "system df"}),
    write_subcommands=frozenset({"run", "exec", "start", "stop", "restart", "rm", "rmi", "pull", "push", "build", "tag", "rename", "update", "pause", "unpause", "kill", "commit", "cp", "create", "network create", "network rm", "volume create", "volume rm", "system prune"}),
    blocked_flags=frozenset({"--privileged", "--rm", "--force", "-f", "--cap-add", "--security-opt", "--pid=host", "--network=host", "--ipc=host"}),
)

# curl 策略
CURL_POLICY = BinaryPolicy(
    name="curl",
    read_subcommands=frozenset(),  # curl 没有子命令概念
    write_subcommands=frozenset(),
    blocked_flags=frozenset({"-o", "--output", "-O", "-F", "--form", "--proxy", "--socks5", "--cookie", "--cookie-jar", "--cert", "--key", "--upload-file", "-T", "--data-binary"}),
)
```

#### 3.2.4 配置简化

```python
# 当前配置（每个工具独立开关 + 独立超时）
class DevOpsSettings(BaseSettings):
    kubectl_enabled: bool = False
    kubectl_timeout: int = 30
    kubectl_allowed_namespaces: str = ""
    docker_enabled: bool = False
    docker_timeout: int = 30
    curl_enabled: bool = False
    curl_timeout: int = 30
    curl_allowed_hosts: str = ""
    curl_max_response_bytes: int = 1_048_576

# 重构后（统一配置）
class CommandSettings(BaseSettings):
    enabled: bool = False                    # COMMAND_ENABLED 总开关
    allowed_binaries: str = "kubectl,docker,curl"  # COMMAND_ALLOWED_BINARIES 白名单
    timeout: int = 30                        # COMMAND_TIMEOUT 统一超时
    max_output_chars: int = 5000             # COMMAND_MAX_OUTPUT_CHARS
    kubectl_allowed_namespaces: str = ""     # COMMAND_KUBECTL_ALLOWED_NAMESPACES（保留特殊配置）
    curl_allowed_hosts: str = ""             # COMMAND_CURL_ALLOWED_HOSTS（保留特殊配置）
    confirm_writes: bool = True              # COMMAND_CONFIRM_WRITES（写操作是否需确认）

    model_config = SettingsConfigDict(env_prefix="COMMAND_", ...)
```

---

## 四、安全能力对照

确保重构后安全能力**不降级**：

| 安全能力 | 当前实现 | 重构后 |
|---------|---------|--------|
| 二进制白名单 | 按工具开关控制 | `allowed_binaries` 配置白名单 ✅ |
| 子命令分级 | 各 tool 内 L0/L1/L2 集合 | `BinaryPolicy.read/write_subcommands` ✅ |
| 黑名单参数拦截 | `blocked_flags` | `BinaryPolicy.blocked_flags` ✅ |
| 命令注入防御 | `shell=False` + 正则检测 | 相同 ✅ |
| 超时控制 | 各工具独立超时 | 统一超时（可按需扩展为 per-binary）✅ |
| 输出截断 | 各沙箱独立截断 | 统一截断 ✅ |
| 敏感信息脱敏 | kubectl secret 脱敏 | `BinaryPolicy.sensitive_resources` ✅ |
| Namespace 限制 | kubectl 独有 | `BinaryPolicy.namespace_whitelist` ✅ |
| URL 安全校验 | curl 独有 | 在 execute 中对 curl 做特殊 URL 校验 ✅ |
| 写操作确认 | `should_confirm()` | `classify()` 返回 "write" → confirm ✅ |
| `--no-stream` 注入 | docker stats 独有 | `BinaryPolicy.default_args` 或 execute 内特殊处理 ✅ |

---

## 五、curl 特殊处理

curl 与 kubectl/docker 的差异需要在 `BashExecutor.execute()` 中做特殊分支：

```python
def _validate_curl(self, tokens: list[str]) -> None:
    """curl 专属安全校验"""
    # 1. 提取 URL（非 flag 参数中的第一个）
    url = self._extract_url(tokens)
    if not url:
        raise ValueError("curl 命令需要指定 URL")

    # 2. Scheme 校验（仅允许 http/https）
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"不允许的协议: {parsed.scheme}")

    # 3. Host 黑名单（云 metadata 端点）
    if parsed.hostname in ("169.254.169.254", "metadata.google.internal"):
        raise ValueError("不允许访问云 metadata 端点")

    # 4. Host 白名单（如果配置了）
    if self._curl_allowed_hosts and parsed.hostname not in self._curl_allowed_hosts:
        raise ValueError(f"Host {parsed.hostname} 不在允许列表中")
```

---

## 六、文件改动范围

### 新增

| 文件 | 说明 |
|------|------|
| `src/tools/devops/bash_executor.py` | 统一安全执行器（`BashExecutor` + `BinaryPolicy`） |
| `src/tools/devops/policies.py` | 预置策略集（`KUBECTL_POLICY`, `DOCKER_POLICY`, `CURL_POLICY`） |
| `src/tools/devops/execute_command_tool.py` | 统一工具（`ExecuteCommandTool`，继承 `BaseTool`） |

### 修改

| 文件 | 改动 |
|------|------|
| `src/config/settings.py` | `DevOpsSettings` → `CommandSettings`（简化配置） |
| `src/factory.py` | `_register_devops_tools` 简化为注册单个 `ExecuteCommandTool` |
| `src/tools/devops/__init__.py` | 更新导出 |
| `.env.example` | 更新 DevOps 配置说明 |

### 删除

| 文件 | 说明 |
|------|------|
| `src/tools/devops/kubectl_tool.py` | 被 `execute_command_tool.py` 替代 |
| `src/tools/devops/docker_tool.py` | 被 `execute_command_tool.py` 替代 |
| `src/tools/devops/curl_tool.py` | 被 `execute_command_tool.py` 替代 |
| `src/tools/devops/command_sandbox.py` | 被 `bash_executor.py` 替代 |

### 代码量预估

| 类别 | 当前 | 重构后 | 变化 |
|------|------|--------|------|
| kubectl_tool.py | 217 行 | 0（删除）| -217 |
| docker_tool.py | 169 行 | 0（删除）| -169 |
| curl_tool.py | 433 行 | 0（删除）| -433 |
| command_sandbox.py | 226 行 | 0（删除）| -226 |
| bash_executor.py | — | ~200 行 | +200 |
| policies.py | — | ~80 行 | +80 |
| execute_command_tool.py | — | ~80 行 | +80 |
| **合计** | **1045 行** | **~360 行** | **-685 行（-66%）** |

---

## 七、向后兼容

### 配置兼容

| 旧配置 | 新配置 | 迁移 |
|--------|--------|------|
| `DEVOPS_KUBECTL_ENABLED=true` | `COMMAND_ENABLED=true` + `COMMAND_ALLOWED_BINARIES=kubectl,...` | `.env` 修改 |
| `DEVOPS_DOCKER_ENABLED=true` | 同上，binaries 加 `docker` | `.env` 修改 |
| `DEVOPS_CURL_ENABLED=true` | 同上，binaries 加 `curl` | `.env` 修改 |
| `DEVOPS_KUBECTL_TIMEOUT=30` | `COMMAND_TIMEOUT=30`（统一） | 合并 |
| `DEVOPS_KUBECTL_ALLOWED_NAMESPACES` | `COMMAND_KUBECTL_ALLOWED_NAMESPACES` | 重命名 |
| `DEVOPS_CURL_ALLOWED_HOSTS` | `COMMAND_CURL_ALLOWED_HOSTS` | 重命名 |

### LLM 行为变化

| 维度 | 当前 | 重构后 |
|------|------|--------|
| LLM 调用方式 | `tool_call: kubectl, args: {subcommand: "get", resource_type: "pods", namespace: "default"}` | `tool_call: execute_command, args: {command: "kubectl get pods -n default"}` |
| LLM 学习成本 | 理解 3 套参数 schema | 直接写命令（LLM 天生擅长） |
| 新工具适配 | 需要新 schema | 自动支持（只要 binary 在白名单） |

---

## 八、扩展能力

重构后，新增工具极其简单：

```python
# 新增 helm 支持：只需添加一个 BinaryPolicy
HELM_POLICY = BinaryPolicy(
    name="helm",
    read_subcommands=frozenset({"list", "status", "get", "history", "show", "search", "repo list", "version"}),
    write_subcommands=frozenset({"install", "upgrade", "uninstall", "rollback", "repo add", "repo remove"}),
    blocked_flags=frozenset({"--force", "--no-hooks"}),
)

# 然后在配置中添加:
# COMMAND_ALLOWED_BINARIES=kubectl,docker,curl,helm
```

**零新文件、零 schema 定义、零 factory 注册代码。**

---

## 九、实施计划

### Sprint 1: 核心执行器

1. 新建 `bash_executor.py`（BashExecutor + BinaryPolicy）
2. 新建 `policies.py`（预置 kubectl/docker/curl 策略）
3. 新建 `execute_command_tool.py`（ExecuteCommandTool）
4. 修改 `settings.py`（CommandSettings）
5. 修改 `factory.py`（注册新工具）
6. 单元测试验证安全能力不降级

### Sprint 2: 清理

7. 删除旧文件（kubectl_tool.py, docker_tool.py, curl_tool.py, command_sandbox.py）
8. 更新 `__init__.py` 和 `.env.example`
9. 端到端测试（LLM 实际调用验证）

### Sprint 3: 扩展（可选）

10. 添加 helm 策略
11. 添加命令执行审计日志
12. 添加 per-binary 超时配置
