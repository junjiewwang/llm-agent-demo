"""工具执行 Mixin —— 抽取 ReActAgent 和 PlanExecuteAgent 共用的工具执行逻辑。

提供：
- 单个工具串行执行（含确认拦截）
- 多工具并发执行（无需确认时）
- 参数解析 + 事件发送 + 结果记录

宿主类需满足的协议（通过实例属性）：
- self._tools: ToolRegistry
- self._memory: ConversationMemory
- self._loop_detector: LoopDetector（可选，无则跳过循环记录）
"""

import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.agent.events import AgentEvent, EventType
from src.agent.metrics import RunMetrics
from src.config import settings
from src.observability.instruments import propagate_context
from src.tools.result import ToolResult
from src.utils.logger import logger

# 工具并发执行的最大线程数
_TOOL_MAX_WORKERS = 5


@dataclass
class ParsedToolCall:
    """工具调用解析结果。"""
    func_name: str
    func_args: dict
    func_args_str: str
    start_time: float


@dataclass
class ToolExecResult:
    """工具执行结果包装。"""
    result: ToolResult
    duration_ms: int


class ToolExecutorMixin:
    """工具执行 Mixin，提供并发/串行工具执行、确认拦截等能力。

    宿主类通过多继承方式使用：
        class ReActAgent(BaseAgent, ToolExecutorMixin): ...
        class PlanExecuteAgent(BaseAgent, ToolExecutorMixin): ...

    宿主类需提供以下实例属性：
    - _tools: ToolRegistry
    - _memory: ConversationMemory
    - _loop_detector: LoopDetector（可选）
    """

    def execute_tool_calls(
        self,
        tool_calls: list,
        metrics: RunMetrics,
        emit=None,
        wait_for_confirmation=None,
    ) -> None:
        """执行 LLM 请求的所有工具调用。

        单个 tool_call 时串行执行；多个 tool_call 时并发执行以减少总耗时。
        如果并发批次中有需要确认的工具，退化为串行以保证确认体验。
        无论并发还是串行，结果都按原始顺序写入 Memory（保证上下文一致性）。
        """
        if len(tool_calls) == 1:
            self._execute_single_tool(tool_calls[0], metrics, emit, wait_for_confirmation)
            return

        # 多个 tool_calls：检查是否有需要确认的工具
        # 如果有，退化为串行执行（V1 简化策略，避免并发确认的 UX 复杂度）
        if wait_for_confirmation and self._has_confirmable_tool(tool_calls):
            logger.info("并发批次中有需要确认的工具，退化为串行执行")
            for tc in tool_calls:
                self._execute_single_tool(tc, metrics, emit, wait_for_confirmation)
            return

        # 多个 tool_calls 且无需确认：并发执行
        total = len(tool_calls)
        logger.info("并发执行 {} 个工具调用", total)

        # 先发送所有 TOOL_CALL 事件 + 解析参数
        parsed: List[Optional[ParsedToolCall]] = []
        for idx, tc in enumerate(tool_calls):
            p = self._parse_and_emit_tool_call(
                tc, metrics, emit,
                parallel_total=total, parallel_index=idx + 1,
            )
            parsed.append(p)

        # 并发执行所有已成功解析的工具（propagate_context 确保子线程 span 关联到父 trace）
        results: Dict[int, ToolExecResult] = {}
        with ThreadPoolExecutor(max_workers=min(len(tool_calls), _TOOL_MAX_WORKERS)) as pool:
            future_to_idx = {}
            for i, p in enumerate(parsed):
                if p is not None:
                    future = pool.submit(
                        propagate_context(self._tools.execute),
                        p.func_name, **p.func_args,
                    )
                    future_to_idx[future] = i

            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                p = parsed[idx]
                assert p is not None
                start_time = p.start_time
                try:
                    result = future.result()
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    results[idx] = ToolExecResult(
                        result=result, duration_ms=duration_ms,
                    )
                except Exception as e:
                    duration_ms = int((time.monotonic() - start_time) * 1000)
                    results[idx] = ToolExecResult(
                        result=ToolResult.fail(f"工具执行异常: {e}"),
                        duration_ms=duration_ms,
                    )

        # 按原始顺序写入 Memory 和发送事件（保证上下文一致性）
        for i, tc in enumerate(tool_calls):
            p = parsed[i]
            if p is None:
                continue  # 解析失败的已经在 _parse_and_emit_tool_call 中处理

            exec_result = results.get(i)
            if exec_result is None:
                continue

            self._record_tool_result(
                tc, p, exec_result.result, exec_result.duration_ms, metrics, emit,
                parallel_total=total, parallel_index=i + 1,
            )

    def _execute_single_tool(
        self, tc, metrics: RunMetrics, emit=None,
        wait_for_confirmation=None,
    ) -> None:
        """串行执行单个工具调用，支持确认拦截。"""
        p = self._parse_and_emit_tool_call(tc, metrics, emit)
        if p is None:
            return

        # 确认拦截：检查工具是否需要用户确认
        result = self._maybe_confirm_and_execute(p, metrics, emit, wait_for_confirmation)
        duration_ms = int((time.monotonic() - p.start_time) * 1000)
        self._record_tool_result(tc, p, result, duration_ms, metrics, emit)

    def _maybe_confirm_and_execute(
        self, parsed: ParsedToolCall, metrics: RunMetrics, emit=None,
        wait_for_confirmation=None,
    ) -> ToolResult:
        """确认拦截 + 执行工具。

        如果工具需要确认且有确认回调，发送 TOOL_CONFIRM 事件并阻塞等待。
        用户批准后执行，拒绝或超时则返回失败结果。
        """
        confirm_mode = settings.agent.tool_confirm_mode

        # 判断是否需要确认
        needs_confirm = False
        if confirm_mode == "always":
            needs_confirm = True
        elif confirm_mode == "smart":
            needs_confirm = self._should_confirm_tool(parsed.func_name, parsed.func_args)

        if needs_confirm and wait_for_confirmation:
            confirm_id = str(uuid.uuid4())
            logger.info("工具 {} 需要用户确认 | confirm_id={}", parsed.func_name, confirm_id[:8])

            if emit:
                emit(AgentEvent(
                    type=EventType.TOOL_CONFIRM,
                    iteration=metrics.iterations,
                    max_iterations=metrics.max_iterations,
                    tool_name=parsed.func_name,
                    tool_args=parsed.func_args,
                    confirm_id=confirm_id,
                ))

            # 阻塞等待用户决策
            approved = wait_for_confirmation(confirm_id)

            if approved is None:
                logger.info("工具确认超时或被停止 | {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])
                return ToolResult.fail("用户确认超时或对话已停止，已跳过执行")
            elif not approved:
                logger.info("用户拒绝执行工具 {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])
                return ToolResult.fail("用户已拒绝执行此操作")
            else:
                logger.info("用户批准执行工具 {} | confirm_id={}",
                           parsed.func_name, confirm_id[:8])

        return self._tools.execute(parsed.func_name, **parsed.func_args)

    def _should_confirm_tool(self, tool_name: str, tool_args: dict) -> bool:
        """根据工具的 should_confirm 方法判断是否需要确认。"""
        try:
            tool = self._tools.get(tool_name)
            return tool.should_confirm(**tool_args)
        except (KeyError, Exception):
            return False

    def _has_confirmable_tool(self, tool_calls: list) -> bool:
        """检查 tool_calls 批次中是否有需要确认的工具。"""
        for tc in tool_calls:
            try:
                func_name = tc["function"]["name"]
                func_args_str = tc["function"]["arguments"]
                func_args = json.loads(func_args_str) if func_args_str else {}
                if self._should_confirm_tool(func_name, func_args):
                    return True
            except (json.JSONDecodeError, KeyError):
                continue
        return False

    def _parse_and_emit_tool_call(
        self, tc, metrics: RunMetrics, emit=None,
        parallel_total: int = 0, parallel_index: int = 0,
    ) -> Optional[ParsedToolCall]:
        """解析工具调用参数，发送 TOOL_CALL 事件。

        Returns:
            解析成功返回 ParsedToolCall，失败返回 None（已记录错误到 Memory）。
        """
        func_name = tc["function"]["name"]
        func_args_str = tc["function"]["arguments"]
        tool_call_id = tc["id"]

        logger.info("调用工具: {} | 参数: {}", func_name, func_args_str)

        try:
            func_args = json.loads(func_args_str) if func_args_str else {}
        except json.JSONDecodeError as e:
            error_msg = f"参数解析失败: {e}"
            logger.error("工具参数解析失败: {} | 原始参数: {}", e, func_args_str)
            self._memory.add_tool_result(tool_call_id, func_name, error_msg)
            metrics.record_tool_call(func_name, success=False, duration_ms=0, error=str(e))
            self._record_loop(func_name, func_args_str)
            if emit:
                emit(AgentEvent(
                    type=EventType.TOOL_RESULT,
                    tool_name=func_name,
                    tool_args={},
                    tool_result_preview=error_msg[:100],
                    success=False,
                    message=error_msg,
                ))
            return None

        if emit:
            emit(AgentEvent(
                type=EventType.TOOL_CALL,
                iteration=metrics.iterations,
                max_iterations=metrics.max_iterations,
                tool_name=func_name,
                tool_args=func_args,
                parallel_total=parallel_total,
                parallel_index=parallel_index,
            ))

        return ParsedToolCall(
            func_name=func_name,
            func_args=func_args,
            func_args_str=func_args_str,
            start_time=time.monotonic(),
        )

    def _record_tool_result(
        self, tc, parsed: ParsedToolCall, result: ToolResult, duration_ms: int,
        metrics: RunMetrics, emit=None,
        parallel_total: int = 0, parallel_index: int = 0,
    ) -> None:
        """记录工具执行结果到 Memory、Metrics、LoopDetector，并发送事件。"""
        tool_call_id = tc["id"]

        metrics.record_tool_call(
            parsed.func_name,
            success=result.success,
            duration_ms=duration_ms,
            error=result.error,
        )

        message_content = result.to_message()

        self._record_loop(parsed.func_name, parsed.func_args_str)
        self._record_loop_result(parsed.func_name, message_content)
        truncated_info = " (已截断)" if result.truncated else ""
        logger.info("工具 {} 执行完成 | 耗时: {:.0f}ms{} | 结果: {}",
                    parsed.func_name, duration_ms, truncated_info, message_content[:200])

        self._memory.add_tool_result(tool_call_id, parsed.func_name, message_content)

        if emit:
            emit(AgentEvent(
                type=EventType.TOOL_RESULT,
                iteration=metrics.iterations,
                max_iterations=metrics.max_iterations,
                tool_name=parsed.func_name,
                tool_args=parsed.func_args,
                tool_result_preview=message_content[:150],
                duration_ms=duration_ms,
                success=result.success,
                parallel_total=parallel_total,
                parallel_index=parallel_index,
            ))

    def _record_loop(self, func_name: str, func_args_str: str) -> None:
        """记录工具调用到循环检测器（L1 精确匹配）。"""
        detector = getattr(self, "_loop_detector", None)
        if detector is not None:
            detector.record(func_name, func_args_str)

    def _record_loop_result(self, func_name: str, result_content: str) -> None:
        """记录工具结果到循环检测器（L2 语义匹配）。"""
        detector = getattr(self, "_loop_detector", None)
        if detector is not None:
            detector.record_result(func_name, result_content)
