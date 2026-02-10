"""Agent 运行指标 - 可观测性数据。

记录每次 Agent.run() 的关键指标，用于调试、监控和性能分析。
"""

import time
from dataclasses import dataclass, field
from typing import List


@dataclass
class ToolCallRecord:
    """单次工具调用的记录。"""
    name: str
    success: bool
    duration_ms: float
    error: str = ""


@dataclass
class RunMetrics:
    """单次 Agent.run() 的运行指标。

    在 Agent 运行过程中逐步填充，运行结束后可以打印或持久化。
    """
    # 迭代信息
    iterations: int = 0
    max_iterations: int = 0
    hit_max_iterations: bool = False
    loop_detected: bool = False

    # Token 消耗（输入/输出分别统计，便于成本估算）
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    # 上下文注入
    kb_chunks_injected: int = 0
    memory_items_injected: int = 0

    # 工具调用
    tool_calls: List[ToolCallRecord] = field(default_factory=list)

    # 耗时
    start_time: float = field(default_factory=time.monotonic)
    end_time: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def duration_ms(self) -> float:
        end = self.end_time or time.monotonic()
        return (end - self.start_time) * 1000

    @property
    def tool_call_count(self) -> int:
        return len(self.tool_calls)

    @property
    def tool_success_count(self) -> int:
        return sum(1 for tc in self.tool_calls if tc.success)

    @property
    def tool_failure_count(self) -> int:
        return sum(1 for tc in self.tool_calls if not tc.success)

    def record_tool_call(
        self, name: str, success: bool, duration_ms: float, error: str = ""
    ) -> None:
        """记录一次工具调用。"""
        self.tool_calls.append(
            ToolCallRecord(name=name, success=success, duration_ms=duration_ms, error=error)
        )

    def finish(self) -> None:
        """标记运行结束。"""
        self.end_time = time.monotonic()

    def summary(self) -> str:
        """生成可读的运行摘要。"""
        lines = [
            f"迭代: {self.iterations}/{self.max_iterations}"
            + (" (达到上限)" if self.hit_max_iterations else "")
            + (" (检测到循环)" if self.loop_detected else ""),
            f"耗时: {self.duration_ms:.0f}ms",
            f"工具调用: {self.tool_call_count} 次"
            + (f" (成功 {self.tool_success_count}, 失败 {self.tool_failure_count})"
               if self.tool_calls else ""),
        ]
        if self.kb_chunks_injected:
            lines.append(f"知识库注入: {self.kb_chunks_injected} 条")
        if self.memory_items_injected:
            lines.append(f"记忆注入: {self.memory_items_injected} 条")
        return " | ".join(lines)
