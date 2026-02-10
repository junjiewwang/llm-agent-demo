"""网页搜索工具（模拟实现）。

当前为模拟实现，后续可替换为真实搜索 API（如 SerpAPI、Bing Search 等）。
"""

import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Any, Dict

from src.tools.base_tool import BaseTool
from src.utils.logger import logger


class WebSearchTool(BaseTool):
    """网页搜索工具。

    当前使用 DuckDuckGo Instant Answer API（免费、无需 API Key）。
    如果请求失败则返回模拟结果提示。
    """

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "搜索互联网获取最新信息（基于 DuckDuckGo）。"
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
        logger.info("执行网页搜索: {}", query)
        try:
            return self._search_duckduckgo(query)
        except Exception as e:
            logger.warning("搜索请求失败: {}, 返回提示信息", e)
            return f"搜索 '{query}' 时网络请求失败（{e}）。建议：请根据你已有的知识回答用户问题，并说明信息可能不是最新的。"

    @staticmethod
    def _search_duckduckgo(query: str) -> str:
        """使用 DuckDuckGo Instant Answer API 进行搜索。"""
        params = urllib.parse.urlencode({
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
        })
        url = f"https://api.duckduckgo.com/?{params}"

        req = urllib.request.Request(url, headers={"User-Agent": "LLM-Agent/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        results = []

        # 摘要结果
        if data.get("Abstract"):
            results.append(f"摘要: {data['Abstract']}")
            if data.get("AbstractSource"):
                results.append(f"来源: {data['AbstractSource']}")

        # 相关主题
        for topic in data.get("RelatedTopics", [])[:3]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(f"- {topic['Text']}")

        if not results:
            return f"搜索 '{query}' 未找到即时结果。建议：请根据你已有的知识回答用户问题。"

        return "\n".join(results)
