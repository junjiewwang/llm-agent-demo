"""工具执行结果 - 结构化返回 + 智能截断。

解决的问题：
1. 工具返回纯字符串，无法区分成功/失败，Agent 只能通过字符串前缀匹配判断
2. 长工具输出（如网页搜索返回大量内容）可能耗尽上下文窗口
3. 缺乏统一的错误信息格式

ToolResult 提供：
- 明确的 success/error 状态
- 智能截断（保留 head + tail + 统计摘要）
- 统一的 to_message() 输出（传给 LLM）
"""

from dataclasses import dataclass


# 工具结果默认最大字符数（约 1000 token）
DEFAULT_MAX_CHARS = 3000


@dataclass
class ToolResult:
    """单次工具执行的结构化结果。"""

    output: str
    success: bool = True
    error: str = ""
    truncated: bool = False

    def to_message(self) -> str:
        """转换为传给 LLM 的 tool message 内容。"""
        if not self.success:
            return f"[工具执行失败] {self.error}"
        if self.truncated:
            return f"{self.output}\n\n[注意: 结果已截断，原始长度超过限制]"
        return self.output

    @staticmethod
    def ok(output: str, max_chars: int = DEFAULT_MAX_CHARS) -> "ToolResult":
        """创建成功结果，自动执行智能截断。"""
        truncated_output, was_truncated = _smart_truncate(output, max_chars)
        return ToolResult(output=truncated_output, success=True, truncated=was_truncated)

    @staticmethod
    def fail(error: str) -> "ToolResult":
        """创建失败结果。"""
        return ToolResult(output="", success=False, error=error)


def _smart_truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """智能截断长文本，保留 head + tail + 统计信息。

    策略：
    - 短于阈值：原样返回
    - 超出阈值：保留前 60% + 后 20%，中间用省略摘要替代

    Returns:
        (截断后文本, 是否发生截断)
    """
    if len(text) <= max_chars:
        return text, False

    original_len = len(text)
    original_lines = text.count('\n') + 1

    # head 60%, tail 20%, 中间留给省略信息
    head_chars = int(max_chars * 0.6)
    tail_chars = int(max_chars * 0.2)

    head = text[:head_chars]
    tail = text[-tail_chars:] if tail_chars > 0 else ""

    # 尽量在行边界截断，避免截断到一半
    head_end = head.rfind('\n')
    if head_end > head_chars * 0.5:  # 至少保留一半
        head = head[:head_end]

    tail_start = tail.find('\n')
    if tail_start > 0 and tail_start < len(tail) * 0.5:
        tail = tail[tail_start + 1:]

    omitted_chars = original_len - len(head) - len(tail)
    separator = (
        f"\n\n... [已省略 {omitted_chars} 字符 / 原始共 {original_lines} 行] ...\n\n"
    )

    return head + separator + tail, True
