"""应用配置管理模块，基于 pydantic-settings 实现类型安全的配置加载。

每个子配置类独立读取 .env 文件，通过 env_prefix 区分不同配置组。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM 相关配置。"""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"
    context_window: int = 0  # 0 = 自动根据模型名推导

    # 内置模型容量映射表（可扩展）
    # context_window = 模型总容量（input + output），单位 token
    # 匹配优先级：精确匹配 → 模糊匹配（key in model_name）→ 前缀族匹配 → 兜底
    MODEL_CONTEXT_WINDOWS: dict[str, int] = {
        # OpenAI
        "gpt-4o": 128_000,
        "gpt-4-turbo": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5-turbo": 16_385,
        "gpt-3.5-turbo-16k": 16_385,
        # Anthropic
        "claude-3-opus-20240229": 200_000,
        "claude-3-sonnet-20240229": 200_000,
        "claude-3-haiku-20240307": 200_000,
        "claude-3.5-sonnet": 200_000,
        "claude-4": 200_000,
        # DeepSeek
        "deepseek-chat": 64_000,
        "deepseek-coder": 64_000,
        "deepseek-v3": 128_000,
        "deepseek-r1": 128_000,
        "deepseek-reasoner": 128_000,
        # Qwen
        "qwen-turbo": 131_072,
        "qwen-plus": 131_072,
        "qwen-max": 131_072,
        # Local / Others
        "llama3-70b-8192": 8_192,
        "mixtral-8x7b-32768": 32_768,
    }

    # 模型族前缀兜底映射：当精确匹配和模糊匹配都失败时，按前缀推导
    # 按前缀长度降序排列，确保 "deepseek-v3" 优先于 "deepseek"
    _MODEL_FAMILY_DEFAULTS: dict[str, int] = {
        "gpt-4o": 128_000,
        "gpt-4": 8_192,
        "gpt-3.5": 16_385,
        "claude-3": 200_000,
        "claude-4": 200_000,
        "deepseek-v3": 128_000,
        "deepseek-r1": 128_000,
        "deepseek": 64_000,
        "qwen": 131_072,
        "llama": 8_192,
    }

    def model_post_init(self, __context):
        """初始化后自动推导 context_window。"""
        super().model_post_init(__context)
        if self.context_window == 0:
            self.context_window = self._resolve_context_window()

    def _resolve_context_window(self) -> int:
        """三级匹配推导 context_window。"""
        model = self.model

        # 1. 精确匹配
        if model in self.MODEL_CONTEXT_WINDOWS:
            return self.MODEL_CONTEXT_WINDOWS[model]

        # 2. 模糊匹配（映射表 key 是 model 的子串，如 gpt-4o-2024-05-13 匹配 gpt-4o）
        for key, val in self.MODEL_CONTEXT_WINDOWS.items():
            if key in model:
                return val

        # 3. 前缀族匹配（model 以族前缀开头，如 deepseek-v3.2 匹配 deepseek-v3）
        for prefix, val in self._MODEL_FAMILY_DEFAULTS.items():
            if model.startswith(prefix):
                return val

        # 4. 兜底
        return 8_192

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AgentSettings(BaseSettings):
    """Agent 相关配置。

    环境变量前缀: AGENT_
    - AGENT_KB_RELEVANCE_THRESHOLD: 知识库检索相关度阈值（cosine distance，默认 0.7）
    - AGENT_MEMORY_RELEVANCE_THRESHOLD: 长期记忆检索相关度阈值（cosine distance，默认 0.7）
    - AGENT_TOOL_CONFIRM_MODE: 工具执行确认模式（"never" | "smart" | "always"，默认 "smart"）
    """

    max_iterations: int = 10
    temperature: float = 0.7
    step_temperature: float = 0.3  # Plan-Execute 步骤执行的独立 temperature（低温 → 工具选择更确定性）
    max_tokens: int = 4096
    kb_relevance_threshold: float = 0.7
    memory_relevance_threshold: float = 0.7
    tool_confirm_mode: str = "smart"

    # ── 3.0 演进开关（默认关闭，不影响现有行为） ──
    message_usage_enabled: bool = True  # 前端展示消息级 token 消耗
    plan_execute_enabled: bool = False  # Plan-and-Execute 模式（复杂任务自动分解）
    policy_enabled: bool = False  # 工具策略重排（Sprint 2）
    memory_governor_enabled: bool = False  # 长期记忆治理（Sprint 3）
    env_adapter_enabled: bool = False  # 环境适配器（Sprint 3）

    # ── 上下文容量管理 ──
    compression_threshold: float = 0.8  # History Zone 水位线（占 history_budget 的比例），超过则触发压缩
    compression_target_ratio: float = 0.6  # 压缩后目标占比（压缩到 history_budget * 此值）

    # ── Zone 预算上限（占 input_budget 的比例）──
    # 可截断 Zone 的弹性上限，实际用量低于上限时不截断，多余空间归 History Zone
    skill_zone_max_ratio: float = 0.15  # Skill Zone 最多占 input_budget 的 15%
    knowledge_zone_max_ratio: float = 0.15  # Knowledge Zone 最多占 input_budget 的 15%
    memory_zone_max_ratio: float = 0.05  # Memory Zone 最多占 input_budget 的 5%

    # ── Skill 匹配配置 ──
    skill_min_match_score: float = 0.15  # Skill 关键词匹配的最低分数阈值（低于此值不激活）

    # ── Memory Governor 配置 ──
    memory_governor_interval: int = 300  # 治理周期（秒），默认 5 分钟
    memory_default_ttl_days: float = 30  # 新记忆默认 TTL（天），0 = 永不过期
    memory_min_value_score: float = 0.1  # 低于此分值的记忆将被驱逐
    memory_merge_threshold: float = 0.15  # cosine distance 低于此值触发归并

    model_config = SettingsConfigDict(
        env_prefix="AGENT_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class SearchSettings(BaseSettings):
    """搜索工具配置。

    环境变量前缀: SEARCH_
    - SEARCH_BACKEND: 搜索后端选择 ("auto" | "duckduckgo" | "tavily")
    - SEARCH_TAVILY_API_KEY: Tavily API Key（选择 tavily 或 auto 时需要）
    """

    backend: str = "auto"
    tavily_api_key: str = ""

    model_config = SettingsConfigDict(
        env_prefix="SEARCH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class FilesystemSettings(BaseSettings):
    """文件系统工具配置。

    环境变量前缀: FILESYSTEM_
    - FILESYSTEM_SANDBOX_DIR: 默认根目录（读写，相对路径的基准，默认当前工作目录）
    - FILESYSTEM_ALLOWED_DIRS: 额外允许访问的目录（逗号分隔，默认只读）
    - FILESYSTEM_WRITABLE_DIRS: 额外允许写入的目录（逗号分隔，须在 ALLOWED_DIRS 中）
    - FILESYSTEM_EXCLUDE: 排除的路径模式，逗号分隔
    - FILESYSTEM_MAX_FILE_SIZE: 单文件读取大小限制（字节），默认 1MB
    - FILESYSTEM_MAX_DEPTH: 搜索最大深度，默认 5
    - FILESYSTEM_MAX_RESULTS: 搜索最大结果数，默认 50
    """

    sandbox_dir: str = ""
    allowed_dirs: str = ""
    writable_dirs: str = ""
    exclude: str = ".env,.git,__pycache__,.agent_data,.venv,venv,node_modules,.idea,.vscode"
    max_file_size: int = 1_048_576
    max_depth: int = 5
    max_results: int = 50

    model_config = SettingsConfigDict(
        env_prefix="FILESYSTEM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class DevOpsSettings(BaseSettings):
    """DevOps 工具配置。

    环境变量前缀: DEVOPS_
    - DEVOPS_KUBECTL_ENABLED: 是否启用 kubectl 工具（默认 False）
    - DEVOPS_KUBECTL_ALLOWED_NAMESPACES: 允许的 namespace（逗号分隔，空=全部）
    - DEVOPS_KUBECTL_TIMEOUT: kubectl 命令超时（秒，默认 30）
    - DEVOPS_DOCKER_ENABLED: 是否启用 docker 工具（默认 False）
    - DEVOPS_DOCKER_TIMEOUT: docker 命令超时（秒，默认 30）
    - DEVOPS_CURL_ENABLED: 是否启用 curl HTTP 请求工具（默认 False）
    - DEVOPS_CURL_TIMEOUT: 请求超时（秒，默认 30）
    - DEVOPS_CURL_ALLOWED_HOSTS: Host 白名单（逗号分隔，空=不限制）
    - DEVOPS_CURL_MAX_RESPONSE_BYTES: 最大响应大小（字节，默认 1MB）

    安全保障：写操作和危险操作通过 tool_confirm_mode（Human-in-the-loop）确认机制控制。
    """

    kubectl_enabled: bool = False
    kubectl_allowed_namespaces: str = ""
    kubectl_timeout: int = 30
    docker_enabled: bool = False
    docker_timeout: int = 30
    curl_enabled: bool = False
    curl_timeout: int = 30
    curl_allowed_hosts: str = ""
    curl_max_response_bytes: int = 1_048_576

    model_config = SettingsConfigDict(
        env_prefix="DEVOPS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class OtelSettings(BaseSettings):
    """OpenTelemetry 可观测性配置。

    环境变量前缀: OTEL_
    - OTEL_ENABLED: 总开关（默认 false），false 时零开销
    - OTEL_SERVICE_NAME: 服务名
    - OTEL_EXPORTER_PROTOCOL: 导出协议 ("grpc" | "http")，默认 grpc
    - OTEL_EXPORTER_ENDPOINT: OTLP 端点（gRPC 默认 4317，HTTP 默认 4318）
    - OTEL_EXPORTER_HEADERS: OTLP 鉴权 Header（格式: key=value，多个逗号分隔，留空=无鉴权）
    - OTEL_CONSOLE_EXPORT: 开发模式将 trace 输出到控制台
    - OTEL_LOG_CONTENT: 是否在 Span 中记录 LLM 输入/输出内容（默认 false，生产安全）
    - OTEL_LOG_CONTENT_MAX_LENGTH: 单字段最大字符数（防止 Span 过大）
    """

    enabled: bool = False
    service_name: str = "llm-react-agent"
    exporter_protocol: str = "grpc"
    exporter_endpoint: str = "http://localhost:4317"
    exporter_headers: str = ""
    console_export: bool = False
    log_content: bool = False
    log_content_max_length: int = 4096

    model_config = SettingsConfigDict(
        env_prefix="OTEL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class SkillSettings(BaseSettings):
    """Skills 系统配置。

    环境变量前缀: SKILLS_
    - SKILLS_DIRS: Skill 扫描目录（逗号分隔，支持多目录），扫描包含 SKILL.md 的子目录，默认 "skills"
    - SKILLS_DISABLED: 禁用的 Skill 名称列表（逗号分隔），默认为空
    """

    dirs: str = "skills"
    disabled: str = ""

    model_config = SettingsConfigDict(
        env_prefix="SKILLS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AuthSettings(BaseSettings):
    """认证配置。

    环境变量前缀: AUTH_
    - AUTH_SECRET_KEY: JWT 签名密钥
    - AUTH_ALGORITHM: 签名算法（默认 HS256）
    - AUTH_ACCESS_TOKEN_EXPIRE_MINUTES: Token 有效期（分钟）
    """
    secret_key: str = "your-secret-key-please-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days

    model_config = SettingsConfigDict(
        env_prefix="AUTH_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class Settings:
    """全局配置聚合，各子配置独立加载 .env。"""

    def __init__(self):
        self.llm = LLMSettings()
        self.agent = AgentSettings()
        self.search = SearchSettings()
        self.filesystem = FilesystemSettings()
        self.devops = DevOpsSettings()
        self.otel = OtelSettings()
        self.skills = SkillSettings()
        self.auth = AuthSettings()
        self._validate_cross_config()

    def _validate_cross_config(self):
        """跨配置组的一致性校验。"""
        ctx = self.llm.context_window
        out = self.agent.max_tokens
        if ctx > 0 and out >= ctx:
            import warnings
            warnings.warn(
                f"AGENT_MAX_TOKENS({out}) >= LLM_CONTEXT_WINDOW({ctx})，"
                f"input_budget 将为 0。请检查模型映射表或 .env 配置。"
                f"当前模型: {self.llm.model}",
                stacklevel=2,
            )


settings = Settings()
