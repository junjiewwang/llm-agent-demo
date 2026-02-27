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
    # 注意：这里配置的是 Input Context Window，不含 Output
    MODEL_CONTEXT_WINDOWS: dict = {
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
        # DeepSeek
        "deepseek-chat": 64_000,
        "deepseek-coder": 64_000,
        # Local / Others
        "llama3-70b-8192": 8_192,
        "mixtral-8x7b-32768": 32_768,
    }

    def model_post_init(self, __context):
        """初始化后自动推导 context_window。"""
        super().model_post_init(__context)
        if self.context_window == 0:
            # 1. 精确匹配
            if self.model in self.MODEL_CONTEXT_WINDOWS:
                self.context_window = self.MODEL_CONTEXT_WINDOWS[self.model]
            # 2. 模糊匹配（如 gpt-4o-2024-05-13 -> gpt-4o）
            else:
                for key, val in self.MODEL_CONTEXT_WINDOWS.items():
                    if key in self.model:
                        self.context_window = val
                        break
                else:
                    # 3. 默认兜底（保守值）
                    self.context_window = 8_192

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
    max_tokens: int = 4096
    kb_relevance_threshold: float = 0.7
    memory_relevance_threshold: float = 0.7
    tool_confirm_mode: str = "smart"

    # ── 3.0 演进开关（默认关闭，不影响现有行为） ──
    message_usage_enabled: bool = True  # 前端展示消息级 token 消耗
    policy_enabled: bool = False  # 工具策略重排（Sprint 2）
    memory_governor_enabled: bool = False  # 长期记忆治理（Sprint 3）
    env_adapter_enabled: bool = False  # 环境适配器（Sprint 3）

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
    - DEVOPS_KUBECTL_READ_ONLY: kubectl 只读模式（默认 True）
    - DEVOPS_KUBECTL_ALLOWED_NAMESPACES: 允许的 namespace（逗号分隔，空=全部）
    - DEVOPS_KUBECTL_TIMEOUT: kubectl 命令超时（秒，默认 30）
    - DEVOPS_DOCKER_ENABLED: 是否启用 docker 工具（默认 False）
    - DEVOPS_DOCKER_READ_ONLY: docker 只读模式（默认 True）
    - DEVOPS_DOCKER_TIMEOUT: docker 命令超时（秒，默认 30）
    - DEVOPS_CURL_ENABLED: 是否启用 curl HTTP 请求工具（默认 False）
    - DEVOPS_CURL_READ_ONLY: curl 只读模式（默认 True，仅 GET/HEAD/OPTIONS）
    - DEVOPS_CURL_TIMEOUT: 请求超时（秒，默认 30）
    - DEVOPS_CURL_ALLOWED_HOSTS: Host 白名单（逗号分隔，空=不限制）
    - DEVOPS_CURL_MAX_RESPONSE_BYTES: 最大响应大小（字节，默认 1MB）
    """

    kubectl_enabled: bool = False
    kubectl_read_only: bool = True
    kubectl_allowed_namespaces: str = ""
    kubectl_timeout: int = 30
    docker_enabled: bool = False
    docker_read_only: bool = True
    docker_timeout: int = 30
    curl_enabled: bool = False
    curl_read_only: bool = True
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


settings = Settings()
