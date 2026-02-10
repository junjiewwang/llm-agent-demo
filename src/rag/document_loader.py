"""文档加载器模块。

支持多种格式的文档加载，统一输出为纯文本：
- .txt  纯文本
- .md   Markdown
- .pdf  PDF（基于 PyMuPDF）
"""

import os
from typing import Dict, Any

from src.utils.logger import logger


class Document:
    """加载后的文档模型。"""

    def __init__(self, content: str, metadata: Dict[str, Any]):
        self.content = content
        self.metadata = metadata

    def __repr__(self) -> str:
        source = self.metadata.get("source", "unknown")
        return f"Document(source={source}, length={len(self.content)})"


class DocumentLoader:
    """文档加载器，根据文件扩展名自动选择解析方式。"""

    # 扩展名 -> 加载方法名 的映射
    _LOADERS = {
        ".txt": "_load_text",
        ".md": "_load_text",
        ".pdf": "_load_pdf",
    }

    @classmethod
    def supported_extensions(cls):
        """返回支持的文件扩展名列表。"""
        return list(cls._LOADERS.keys())

    @classmethod
    def load(cls, file_path: str) -> Document:
        """加载文档文件。

        Args:
            file_path: 文件路径。

        Returns:
            Document 对象。

        Raises:
            ValueError: 不支持的文件格式。
            FileNotFoundError: 文件不存在。
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        loader_name = cls._LOADERS.get(ext)
        if not loader_name:
            raise ValueError(
                f"不支持的文件格式: {ext}，支持: {cls.supported_extensions()}"
            )

        loader_method = getattr(cls, loader_name)
        content = loader_method(file_path)

        metadata = {
            "source": os.path.abspath(file_path),
            "filename": os.path.basename(file_path),
            "extension": ext,
            "size_bytes": os.path.getsize(file_path),
        }

        logger.info(
            "文档加载完成 | {} | {} 字符",
            metadata["filename"], len(content),
        )
        return Document(content=content, metadata=metadata)

    @classmethod
    def load_directory(cls, dir_path: str):
        """加载目录下所有支持的文档。

        Args:
            dir_path: 目录路径。

        Returns:
            Document 列表。
        """
        if not os.path.isdir(dir_path):
            raise NotADirectoryError(f"不是有效目录: {dir_path}")

        documents = []
        for root, _, files in os.walk(dir_path):
            for filename in sorted(files):
                ext = os.path.splitext(filename)[1].lower()
                if ext in cls._LOADERS:
                    file_path = os.path.join(root, filename)
                    try:
                        doc = cls.load(file_path)
                        documents.append(doc)
                    except Exception as e:
                        logger.warning("跳过文件 {} | 错误: {}", file_path, e)

        logger.info("目录加载完成 | {} | {} 个文档", dir_path, len(documents))
        return documents

    @staticmethod
    def _load_text(file_path: str) -> str:
        """加载纯文本/Markdown 文件。"""
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _load_pdf(file_path: str) -> str:
        """加载 PDF 文件（基于 PyMuPDF）。"""
        try:
            import fitz  # PyMuPDF
        except ImportError:
            raise RuntimeError("PDF 解析需要 pymupdf，请执行: pip install pymupdf")

        doc = fitz.open(file_path)
        pages = []
        for page in doc:
            text = page.get_text()
            if text.strip():
                pages.append(text)
        doc.close()

        return "\n\n".join(pages)
