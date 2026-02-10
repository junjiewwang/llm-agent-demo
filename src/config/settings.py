"""应用配置管理模块，基于 pydantic-settings 实现类型安全的配置加载。

每个子配置类独立读取 .env 文件，通过 env_prefix 区分不同配置组。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    """LLM 相关配置。"""

    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o"

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


class AgentSettings(BaseSettings):
    """Agent 相关配置。"""

    max_iterations: int = 10
    temperature: float = 0.7
    max_tokens: int = 4096

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
    """

    kubectl_enabled: bool = False
    kubectl_read_only: bool = True
    kubectl_allowed_namespaces: str = ""
    kubectl_timeout: int = 30
    docker_enabled: bool = False
    docker_read_only: bool = True
    docker_timeout: int = 30

    model_config = SettingsConfigDict(
        env_prefix="DEVOPS_",
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


settings = Settings()
