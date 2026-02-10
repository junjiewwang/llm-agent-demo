"""Token 计数工具模块。

基于 tiktoken 估算消息的 Token 数量，用于对话历史的智能截断。
支持 OpenAI 模型的精确计数，对其他模型做近似估算。
"""

from typing import List

from src.llm.base_client import Message
from src.utils.logger import logger

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    logger.warning("tiktoken 未安装，将使用字符数估算 Token（不够精确）")


class TokenCounter:
    """Token 计数器。

    优先使用 tiktoken 精确计数，不可用时按字符数估算。
    """

    def __init__(self, model: str = "gpt-4o"):
        self._model = model
        self._encoder = None

        if _TIKTOKEN_AVAILABLE:
            try:
                self._encoder = tiktoken.encoding_for_model(model)
            except KeyError:
                # 未知模型，使用 cl100k_base（GPT-4/3.5 使用的编码）
                self._encoder = tiktoken.get_encoding("cl100k_base")
                logger.debug("模型 {} 无专用编码器，使用 cl100k_base", model)

    def count_text(self, text: str) -> int:
        """计算文本的 Token 数。"""
        if self._encoder:
            return len(self._encoder.encode(text))
        # 回退：中文约 1 字 ≈ 1.5 token，英文约 1 词 ≈ 1.3 token，粗略按 字符数/2 估算
        return max(1, len(text) // 2)

    def count_message(self, message: Message) -> int:
        """计算单条消息的 Token 数（含角色和格式开销）。"""
        # OpenAI 每条消息有 ~4 token 的格式开销
        tokens = 4
        if message.content:
            tokens += self.count_text(message.content)
        if message.name:
            tokens += self.count_text(message.name)
        if message.tool_calls:
            # tool_calls 的 JSON 也消耗 token
            import json
            tokens += self.count_text(json.dumps(message.tool_calls))
        return tokens

    def count_messages(self, messages: List[Message]) -> int:
        """计算消息列表的总 Token 数。"""
        # 每次对话有 ~3 token 的 reply 开销
        return sum(self.count_message(m) for m in messages) + 3
