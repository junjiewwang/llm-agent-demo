"""搜索后端策略 — 为 WebSearchTool 提供可插拔的搜索引擎实现。

通过策略模式将搜索引擎的选择与工具本身解耦：
- WebSearchTool 只负责"作为 Agent 工具"的职责（名称、描述、参数、格式化输出）
- SearchBackend 负责实际的搜索执行逻辑

支持的后端：
- DuckDuckGoBackend: 免费，无需 API Key，基于 ddgs 库，默认方案
- TavilyBackend: 专为 LLM/RAG 优化，需 API Key（免费 1000 次/月）

扩展新后端只需实现 SearchBackend.search() 方法。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

from src.utils.logger import logger


@dataclass
class SearchResult:
    """统一的搜索结果模型。

    所有后端返回相同结构，使 WebSearchTool 的格式化逻辑与后端无关。
    """

    title: str
    snippet: str
    url: str = ""
    source: str = ""


class SearchBackend(ABC):
    """搜索后端抽象基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """后端名称，用于日志和调试。"""

    @abstractmethod
    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        """执行搜索。

        Args:
            query: 搜索关键词。
            max_results: 最大返回结果数。

        Returns:
            搜索结果列表。

        Raises:
            Exception: 搜索失败时抛出（由调用方处理）。
        """


class DuckDuckGoBackend(SearchBackend):
    """基于 ddgs 库的搜索后端。

    免费、无需 API Key，适合 Demo 和个人项目。
    使用 ddgs 库（DuckDuckGo Search 的新版包名），能返回真正的搜索结果。
    """

    @property
    def name(self) -> str:
        return "DuckDuckGo"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        from ddgs import DDGS

        raw_results = DDGS().text(query, max_results=max_results)
        results = [
            SearchResult(
                title=r.get("title", ""),
                snippet=r.get("body", ""),
                url=r.get("href", ""),
                source="DuckDuckGo",
            )
            for r in raw_results
        ]
        logger.debug("DuckDuckGo 搜索 '{}' 返回 {} 条结果", query, len(results))
        return results


class TavilyBackend(SearchBackend):
    """基于 Tavily API 的搜索后端。

    专为 LLM Agent 和 RAG 场景优化，返回结构化摘要和内容。
    需要 API Key（免费额度 1000 次/月，https://tavily.com）。
    """

    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("Tavily API Key 不能为空")
        self._api_key = api_key

    @property
    def name(self) -> str:
        return "Tavily"

    def search(self, query: str, max_results: int = 5) -> List[SearchResult]:
        from tavily import TavilyClient

        client = TavilyClient(api_key=self._api_key)
        response = client.search(query, max_results=max_results)
        results = [
            SearchResult(
                title=r.get("title", ""),
                snippet=r.get("content", ""),
                url=r.get("url", ""),
                source="Tavily",
            )
            for r in response.get("results", [])
        ]
        logger.debug("Tavily 搜索 '{}' 返回 {} 条结果", query, len(results))
        return results
