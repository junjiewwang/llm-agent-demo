"""网页搜索工具。

通过策略模式支持多个搜索后端（DuckDuckGo、Tavily 等），
根据配置自动选择最佳后端，对 Agent 侧接口保持不变。
"""

from typing import Any, Dict, Optional

from src.config import settings
from src.tools.base_tool import BaseTool
from src.tools.search_backends import (
    SearchBackend, DuckDuckGoBackend, TavilyBackend,
)
from src.utils.logger import logger


def _try_create_tavily(api_key: str) -> Optional[SearchBackend]:
    """尝试创建 Tavily 后端，如果依赖未安装则返回 None。"""
    try:
        import tavily  # noqa: F401
        return TavilyBackend(api_key=api_key)
    except ImportError:
        logger.warning("tavily-python 未安装，无法使用 Tavily 后端（pip install tavily-python）")
        return None


def create_search_backend() -> SearchBackend:
    """根据配置创建搜索后端。

    优先级：
    1. 配置明确指定 backend="tavily" → TavilyBackend（依赖未装则回退）
    2. 配置明确指定 backend="duckduckgo" → DuckDuckGoBackend
    3. backend="auto"（默认）→ 有 Tavily Key 且依赖可用则用 Tavily，否则 DuckDuckGo
    """
    backend_name = settings.search.backend.lower()

    if backend_name == "tavily":
        if not settings.search.tavily_api_key:
            logger.warning("SEARCH_BACKEND=tavily 但未配置 SEARCH_TAVILY_API_KEY，回退到 DuckDuckGo")
            return DuckDuckGoBackend()
        backend = _try_create_tavily(settings.search.tavily_api_key)
        return backend or DuckDuckGoBackend()

    if backend_name == "duckduckgo":
        return DuckDuckGoBackend()

    # auto: 有 Tavily Key 且依赖可用就用 Tavily，否则 DuckDuckGo
    if settings.search.tavily_api_key:
        backend = _try_create_tavily(settings.search.tavily_api_key)
        if backend:
            logger.info("搜索后端: Tavily（auto 模式，检测到 API Key）")
            return backend

    logger.info("搜索后端: DuckDuckGo（auto 模式，免费方案）")
    return DuckDuckGoBackend()


class WebSearchTool(BaseTool):
    """网页搜索工具。

    支持多个搜索后端，通过配置或构造参数切换：
    - DuckDuckGo: 免费，无需 API Key（默认）
    - Tavily: 专为 AI Agent 优化（需配置 SEARCH_TAVILY_API_KEY）

    对 Agent 侧接口完全不变，内部通过 SearchBackend 策略执行实际搜索。
    """

    def __init__(self, backend: Optional[SearchBackend] = None):
        self._backend = backend or create_search_backend()
        logger.info("WebSearchTool 初始化 | 后端: {}", self._backend.name)

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            f"搜索互联网获取最新信息（当前后端: {self._backend.name}）。"
            "适用场景：需要查询实时信息、最新新闻、你不确定的事实、或用户明确要求搜索时使用。"
            "不适用：如果已有知识库检索结果能回答问题，无需再搜索互联网。"
            "限制：返回结果为摘要形式，可能不够详尽；网络请求可能超时。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词",
                }
            },
            "required": ["query"],
        }

    def execute(self, query: str, **kwargs) -> str:
        logger.info("执行网页搜索: {} (后端: {})", query, self._backend.name)
        try:
            results = self._backend.search(query, max_results=5)
        except Exception as e:
            logger.warning("搜索请求失败 ({}): {}", self._backend.name, e)
            return (
                f"搜索 '{query}' 时请求失败（{e}）。"
                "建议：请根据你已有的知识回答用户问题，并说明信息可能不是最新的。"
            )

        if not results:
            return f"搜索 '{query}' 未找到结果。建议：请根据你已有的知识回答用户问题。"

        # 格式化为 LLM 友好的文本
        formatted = []
        for i, r in enumerate(results, 1):
            parts = [f"[{i}] {r.title}"]
            if r.snippet:
                parts.append(r.snippet)
            if r.url:
                parts.append(f"链接: {r.url}")
            formatted.append("\n".join(parts))

        return "\n\n".join(formatted)
