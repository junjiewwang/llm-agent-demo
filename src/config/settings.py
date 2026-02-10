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


class Settings:
    """全局配置聚合，各子配置独立加载 .env。"""

    def __init__(self):
        self.llm = LLMSettings()
        self.agent = AgentSettings()


settings = Settings()
