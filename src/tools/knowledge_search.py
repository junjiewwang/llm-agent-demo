"""知识库检索工具。

让 Agent 能够从导入的文档知识库中检索信息，实现 RAG（检索增强生成）。
"""

from typing import Any, Dict, Optional

from src.rag.knowledge_base import KnowledgeBase
from src.tools.base_tool import BaseTool


class KnowledgeSearchTool(BaseTool):
    """知识库检索工具。

    Agent 可以使用此工具从已导入的文档中检索相关信息，
    用于回答基于特定文档的问题。
    """

    def __init__(self, knowledge_base: Optional[KnowledgeBase] = None):
        self._kb = knowledge_base

    def set_knowledge_base(self, kb: KnowledgeBase) -> None:
        """设置知识库实例。"""
        self._kb = kb

    @property
    def name(self) -> str:
        return "knowledge_search"

    @property
    def description(self) -> str:
        return (
            "从已导入的文档知识库中检索相关信息（基于语义相似度）。"
            "适用场景：用户的问题可能涉及已导入的文档内容时使用（如公司制度、技术文档等）。"
            "注意：系统会在每次对话前自动进行知识库预检索，结果已注入上下文。"
            "仅当你需要用不同的关键词重新检索、或需要更多结果时，才主动调用此工具。"
            "如果知识库为空，请告知用户先导入文档。"
        )

    @property
    def parameters(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索关键词或问题，用于在知识库中查找相关内容",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回最相关的结果数量，默认 3",
                },
            },
            "required": ["query"],
        }

    def execute(self, query: str, top_k: int = 3, **kwargs) -> str:
        if not self._kb:
            return "知识库未初始化，请先导入文档（使用 /import 命令）。"

        if self._kb.count() == 0:
            return "知识库为空，请先导入文档（使用 /import 命令）。"

        results = self._kb.search(query, top_k=top_k)
        if not results:
            return f"在知识库中未找到与 '{query}' 相关的内容。"

        output_parts = [f"找到 {len(results)} 条相关内容：\n"]
        for i, r in enumerate(results, 1):
            source = r["metadata"].get("filename", "未知来源")
            output_parts.append(f"[{i}] 来源: {source}")
            output_parts.append(f"{r['text']}\n")

        return "\n".join(output_parts)
