"""可观测性插桩工具。

提供跨线程 Context 传播、常用指标定义、Span 内容记录等辅助功能。
"""

import json
import threading
from contextlib import contextmanager
from typing import Callable, List, Optional

from opentelemetry import context as otel_context, trace
from opentelemetry.trace import StatusCode

from src.observability import get_meter

# ── 跨线程 Context 传播 ──


def propagate_context(fn: Callable) -> Callable:
    """装饰器：捕获当前线程的 OTel Context，在子线程中自动恢复。

    用于 threading.Thread(target=propagate_context(fn))，
    确保子线程中创建的 span 关联到父线程的 trace。
    """
    ctx = otel_context.get_current()

    def wrapper(*args, **kwargs):
        token = otel_context.attach(ctx)
        try:
            return fn(*args, **kwargs)
        finally:
            otel_context.detach(token)

    return wrapper


def start_thread_with_context(target: Callable, *, daemon: bool = True,
                              name: Optional[str] = None) -> threading.Thread:
    """创建并启动线程，自动传播 OTel Context。"""
    thread = threading.Thread(target=propagate_context(target), daemon=daemon, name=name)
    thread.start()
    return thread


# ── Metrics 定义（懒初始化） ──

_llm_token_counter = None
_llm_duration_histogram = None
_agent_duration_histogram = None


def _ensure_metrics():
    """延迟初始化 metrics instruments（避免在 import 时就需要 MeterProvider）。"""
    global _llm_token_counter, _llm_duration_histogram, _agent_duration_histogram
    if _llm_token_counter is not None:
        return

    meter = get_meter("llm-react-agent")

    _llm_token_counter = meter.create_counter(
        name="llm.token.usage",
        description="LLM token usage (input/output)",
        unit="token",
    )
    _llm_duration_histogram = meter.create_histogram(
        name="llm.request.duration",
        description="LLM API request duration",
        unit="ms",
    )
    _agent_duration_histogram = meter.create_histogram(
        name="agent.run.duration",
        description="Agent run total duration",
        unit="ms",
    )


def record_llm_metrics(
    *,
    model: str,
    call_type: str,
    prompt_tokens: int,
    completion_tokens: int,
    duration_ms: float,
) -> None:
    """记录一次 LLM 调用的 metrics。"""
    _ensure_metrics()
    attrs = {"model": model, "call_type": call_type}

    _llm_token_counter.add(prompt_tokens, {**attrs, "direction": "input"})
    _llm_token_counter.add(completion_tokens, {**attrs, "direction": "output"})
    _llm_duration_histogram.record(duration_ms, attrs)


def record_agent_run_metrics(*, duration_ms: float, hit_max_iterations: bool) -> None:
    """记录一次 Agent.run() 的 metrics。"""
    _ensure_metrics()
    _agent_duration_histogram.record(duration_ms, {"hit_max_iterations": str(hit_max_iterations)})


# ── Span 辅助 ──


@contextmanager
def trace_span(tracer: trace.Tracer, name: str, attributes: Optional[dict] = None):
    """便捷的 span 上下文管理器，异常时自动标记 ERROR 状态。"""
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        try:
            yield span
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            raise


# ── Span 内容记录（受 OTEL_LOG_CONTENT 开关控制） ──


def _truncate(text: str, max_length: int) -> str:
    """截断文本以适配 Span attribute 大小限制。"""
    if len(text) <= max_length:
        return text
    return text[:max_length] + f"...[truncated, total {len(text)} chars]"


def set_span_content(span: trace.Span, key: str, content: str) -> None:
    """按配置决定是否将文本内容写入 Span attribute。

    仅当 OTEL_LOG_CONTENT=true 时写入，自动截断超长文本。
    """
    from src.config import settings
    otel_conf = settings.otel
    if not otel_conf.log_content:
        return
    span.set_attribute(key, _truncate(content, otel_conf.log_content_max_length))


def set_span_messages(span: trace.Span, key: str, messages: List[dict]) -> None:
    """将 messages 列表序列化为 JSON 后写入 Span attribute。

    仅当 OTEL_LOG_CONTENT=true 时写入。

    除了记录完整内容（per-message 截断 + 整体兜底截断），还额外写入：
    - {key}.summary: 每条消息的 role/name/chars 分布，用于快速定位"大消息"
    - {key}.total_chars: 截断前的真实总字符数
    - {key}.count: 消息条数
    """
    from src.config import settings
    otel_conf = settings.otel
    if not otel_conf.log_content:
        return

    max_length = otel_conf.log_content_max_length
    # 单条消息 content 截断阈值：总限制的 1/4，至少 512，确保每条消息不会独占整个 attribute
    per_msg_limit = max(max_length // 4, 512)

    # 1. 构建 per-message 摘要（轻量，不受截断影响）
    summary = _build_messages_summary(messages)
    span.set_attribute(f"{key}.summary", json.dumps(summary, ensure_ascii=False))
    span.set_attribute(f"{key}.count", len(messages))

    # 2. 计算截断前的真实总字符数
    total_chars = sum(len(m.get("content") or "") for m in messages)
    span.set_attribute(f"{key}.total_chars", total_chars)

    # 3. per-message 截断后序列化（使每条消息都有机会出现在 attribute 中）
    truncated_messages = _truncate_messages(messages, per_msg_limit)
    content = json.dumps(truncated_messages, ensure_ascii=False)
    span.set_attribute(key, _truncate(content, max_length))


def _build_messages_summary(messages: List[dict]) -> List[dict]:
    """构建每条消息的轻量摘要，用于快速定位 token 消耗来源。

    每条摘要包含 role、name（如有）、chars（content 字符数）、tool_calls 数量（如有），
    通常总大小只有几百字符，不会被截断。
    """
    summary = []
    for m in messages:
        item: dict = {"role": m.get("role", "unknown")}
        if m.get("name"):
            item["name"] = m["name"]
        item["chars"] = len(m.get("content") or "")
        if m.get("tool_calls"):
            item["tool_calls"] = len(m["tool_calls"])
        summary.append(item)
    return summary


def _truncate_messages(messages: List[dict], per_msg_limit: int) -> List[dict]:
    """对每条消息的 content 字段独立截断，避免单条大消息独占整个 attribute。

    返回截断后的消息副本列表，不修改原始数据。
    """
    result = []
    for m in messages:
        content = m.get("content") or ""
        if len(content) > per_msg_limit:
            truncated = dict(m)
            truncated["content"] = (
                content[:per_msg_limit]
                + f"...[truncated, original {len(content)} chars]"
            )
            result.append(truncated)
        else:
            result.append(m)
    return result


def set_span_distances(
    key: str,
    candidates: List[dict],
    threshold: float,
    injected_count: int = 0,
) -> None:
    """将检索 distance 分数记录到当前 Span，用于相关性阈值调优。

    记录所有候选的 distance（含被过滤的），以及阈值和注入数量，
    便于在 Jaeger/Console 中观察检索质量并调整阈值。

    Args:
        key: Span attribute 前缀（如 "kb.distances" / "memory.distances"）。
        candidates: 所有检索候选结果（含 distance 字段）。
        threshold: 当前使用的阈值。
        injected_count: 实际注入的数量（通过阈值过滤后）。
    """
    span = trace.get_current_span()
    if not span or not span.is_recording():
        return

    distances = [round(r.get("distance", -1), 4) for r in candidates]
    span.set_attribute(f"{key}", json.dumps(distances))
    span.set_attribute(f"{key}.threshold", threshold)
    span.set_attribute(f"{key}.candidates", len(candidates))
    span.set_attribute(f"{key}.injected", injected_count)
