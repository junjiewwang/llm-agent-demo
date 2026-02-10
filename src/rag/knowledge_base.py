"""知识库管理器。

串联文档加载、分块、向量化存储和检索的完整 RAG 流程：
    导入文档 → 分块 → 向量化存储 → 用户查询 → 语义检索 → 返回相关片段
"""

from typing import List, Dict, Any, Optional

from src.memory.vector_store import VectorStore
from src.rag.chunker import TextChunker
from src.rag.document_loader import DocumentLoader, Document
from src.utils.logger import logger


class KnowledgeBase:
    """知识库，管理文档的导入、存储和检索。

    内部使用 VectorStore 做向量存储，与 Agent 长期记忆使用不同的 collection，
    避免知识文档和对话记忆混淆。
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        chunk_size: int = 500,
        chunk_overlap: int = 100,
    ):
        """
        Args:
            vector_store: 向量存储实例，为 None 则自动创建。
            chunk_size: 分块大小。
            chunk_overlap: 分块重叠。
        """
        self._store = vector_store or VectorStore(
            collection_name="knowledge_base",
            persist_directory=".agent_data/knowledge",
        )
        self._chunker = TextChunker(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self._loader = DocumentLoader()

    def import_file(self, file_path: str) -> int:
        """导入单个文件到知识库。

        Args:
            file_path: 文件路径。

        Returns:
            导入的 chunk 数量。
        """
        doc = self._loader.load(file_path)
        return self._index_document(doc)

    def import_directory(self, dir_path: str) -> int:
        """导入目录下所有支持的文档。

        Args:
            dir_path: 目录路径。

        Returns:
            导入的 chunk 总数。
        """
        documents = self._loader.load_directory(dir_path)
        total_chunks = 0
        for doc in documents:
            total_chunks += self._index_document(doc)
        return total_chunks

    def import_text(self, text: str, source: str = "direct_input") -> int:
        """直接导入文本到知识库。

        Args:
            text: 文本内容。
            source: 来源标识。

        Returns:
            导入的 chunk 数量。
        """
        doc = Document(content=text, metadata={"source": source, "filename": source})
        return self._index_document(doc)

    def search(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """检索与查询最相关的知识片段。

        Args:
            query: 查询文本。
            top_k: 返回的最大结果数。

        Returns:
            检索结果列表，每项包含 text, metadata, distance。
        """
        results = self._store.search(query, top_k=top_k)

        # 过滤掉相关度太低的结果
        relevant = [r for r in results if r["distance"] < 1.2]

        logger.debug(
            "知识库检索 | query={} | 命中 {}/{} 条",
            query[:50], len(relevant), len(results),
        )
        return relevant

    def count(self) -> int:
        """返回知识库中的 chunk 总数。"""
        return self._store.count()

    def clear(self) -> None:
        """清空知识库。"""
        self._store.clear()
        logger.info("知识库已清空")

    def _index_document(self, doc: Document) -> int:
        """将文档分块并存入向量存储。"""
        chunks = self._chunker.chunk(
            text=doc.content,
            metadata={
                "source": doc.metadata.get("source", ""),
                "filename": doc.metadata.get("filename", ""),
            },
        )

        for chunk in chunks:
            self._store.add(
                text=chunk["text"],
                metadata=chunk["metadata"],
                dedup=True,
            )

        logger.info(
            "文档索引完成 | {} | {} 个 chunk",
            doc.metadata.get("filename", "unknown"), len(chunks),
        )
        return len(chunks)
