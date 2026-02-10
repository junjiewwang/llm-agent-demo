"""文档分块模块。

将长文档切分为适合 Embedding 和检索的小块（chunk），支持：
- 基于字符数的滑动窗口分块
- 段落感知分块（优先在段落边界切分）
- 可配置的 chunk 大小和重叠长度
"""

from typing import List, Dict, Any, Optional

from src.utils.logger import logger


class TextChunker:
    """文本分块器。

    采用段落感知 + 滑动窗口策略：
    1. 先按段落（双换行）拆分
    2. 将小段落合并直到接近 chunk_size
    3. 超长段落按 chunk_size 滑动窗口切分
    4. 相邻 chunk 之间有 overlap 重叠，保证语义连贯
    """

    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 100):
        """
        Args:
            chunk_size: 每个 chunk 的目标字符数。
            chunk_overlap: 相邻 chunk 之间的重叠字符数。
        """
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap 必须小于 chunk_size")

        self._chunk_size = chunk_size
        self._chunk_overlap = chunk_overlap

    def chunk(
        self,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """将文本分块。

        Args:
            text: 待分块的文本。
            metadata: 附加到每个 chunk 的元数据。

        Returns:
            chunk 列表，每项包含 text 和 metadata。
        """
        if not text.strip():
            return []

        base_meta = metadata or {}
        paragraphs = self._split_paragraphs(text)
        chunks = self._merge_and_split(paragraphs)

        result = []
        for i, chunk_text in enumerate(chunks):
            chunk_meta = {
                **base_meta,
                "chunk_index": i,
                "chunk_total": len(chunks),
            }
            result.append({"text": chunk_text, "metadata": chunk_meta})

        logger.debug(
            "文档分块完成 | {} 字符 -> {} 个 chunk (size={}, overlap={})",
            len(text), len(result), self._chunk_size, self._chunk_overlap,
        )
        return result

    def _split_paragraphs(self, text: str) -> List[str]:
        """按段落拆分文本（双换行分隔）。"""
        # 按双换行分段，保留非空段落
        raw_paragraphs = text.split("\n\n")
        paragraphs = [p.strip() for p in raw_paragraphs if p.strip()]
        return paragraphs

    def _merge_and_split(self, paragraphs: List[str]) -> List[str]:
        """将段落合并/切分为合适大小的 chunk。"""
        chunks = []
        current_chunk = ""

        for para in paragraphs:
            # 如果当前段落本身就超过 chunk_size，需要强制切分
            if len(para) > self._chunk_size:
                # 先把累积的 current_chunk 存下来
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                    current_chunk = ""
                # 滑动窗口切分超长段落
                chunks.extend(self._sliding_window_split(para))
                continue

            # 尝试将段落追加到当前 chunk
            candidate = (current_chunk + "\n\n" + para).strip() if current_chunk else para
            if len(candidate) <= self._chunk_size:
                current_chunk = candidate
            else:
                # 当前 chunk 已满，保存并开始新 chunk（带重叠）
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                # 新 chunk 从重叠部分开始
                overlap_text = current_chunk[-self._chunk_overlap:] if len(current_chunk) > self._chunk_overlap else ""
                current_chunk = (overlap_text + "\n\n" + para).strip() if overlap_text else para

        # 最后一个 chunk
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        return chunks

    def _sliding_window_split(self, text: str) -> List[str]:
        """对超长文本进行滑动窗口切分。"""
        chunks = []
        step = self._chunk_size - self._chunk_overlap
        for start in range(0, len(text), step):
            end = start + self._chunk_size
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
        return chunks
