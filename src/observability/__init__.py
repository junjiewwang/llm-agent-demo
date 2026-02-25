"""OpenTelemetry 可观测性模块。

提供 Tracing + Metrics 的初始化、获取和清理。
通过 OTEL_ENABLED 配置控制总开关，关闭时零开销（所有 API 走 no-op 实现）。

使用方式：
    from src.observability import init_telemetry, shutdown_telemetry, get_tracer, get_meter

    # 应用启动时
    init_telemetry()

    # 业务代码中
    tracer = get_tracer(__name__)
    meter = get_meter(__name__)

    # 应用关闭时
    shutdown_telemetry()
"""

from typing import Any

from opentelemetry import trace, metrics

from src.config import settings
from src.config.settings import OtelSettings
from src.utils.logger import logger

# 模块级缓存，避免重复初始化
_initialized = False
_tracer_provider = None
_meter_provider = None

# 支持的协议 -> 模块路径映射
_EXPORTER_MODULES = {
    "grpc": "opentelemetry.exporter.otlp.proto.grpc",
    "http": "opentelemetry.exporter.otlp.proto.http",
}


def _make_utf8_span_formatter():
    """创建支持中文直出的 Span 格式化器。

    OTel SDK 的 ConsoleSpanExporter 默认使用 span.to_json()，
    其内部 json.dumps() 未设置 ensure_ascii=False，导致中文被转义为 \\uXXXX。
    通过自定义 formatter 解决此问题。
    """
    import json as _json
    from os import linesep

    def formatter(span) -> str:
        json_str = span.to_json()
        obj = _json.loads(json_str)
        return _json.dumps(obj, indent=4, ensure_ascii=False) + linesep

    return formatter


def _parse_headers(raw: str) -> dict[str, str]:
    """解析 OTel exporter headers 配置字符串。

    格式遵循 OTel 规范 OTEL_EXPORTER_OTLP_HEADERS：
        "key1=value1,key2=value2"
    注意 value 中可能包含 '='（如 Bearer token），只按第一个 '=' 分割。
    """
    if not raw.strip():
        return {}
    headers: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        key, value = key.strip().lower(), value.strip()
        if key:
            headers[key] = value
    return headers


def _create_otlp_exporters(otel_conf: OtelSettings) -> tuple[Any, Any]:
    """根据协议配置创建 OTLP Span/Metric Exporter。

    支持：
    - gRPC / HTTP 协议自动选择
    - insecure 根据 endpoint scheme 自动判断（https → 安全，http → insecure）
    - 可选鉴权 headers（如 Authorization: Bearer xxx）

    Returns:
        (span_exporter, metric_exporter) 元组。
    Raises:
        ValueError: 不支持的协议。
        ImportError/Exception: exporter 创建失败。
    """
    import importlib

    protocol = otel_conf.exporter_protocol.lower()
    if protocol not in _EXPORTER_MODULES:
        raise ValueError(
            f"不支持的 OTEL_EXPORTER_PROTOCOL: '{protocol}'，"
            f"可选值: {list(_EXPORTER_MODULES.keys())}"
        )

    base_module = _EXPORTER_MODULES[protocol]
    trace_mod = importlib.import_module(f"{base_module}.trace_exporter")
    metric_mod = importlib.import_module(f"{base_module}.metric_exporter")

    exporter_kwargs: dict[str, Any] = {"endpoint": otel_conf.exporter_endpoint}

    # gRPC: insecure 根据 endpoint scheme 自动判断
    if protocol == "grpc":
        is_https = otel_conf.exporter_endpoint.startswith("https://")
        exporter_kwargs["insecure"] = not is_https

    # 鉴权 headers
    headers = _parse_headers(otel_conf.exporter_headers)
    if headers:
        exporter_kwargs["headers"] = headers

    span_exporter = trace_mod.OTLPSpanExporter(**exporter_kwargs)
    metric_exporter = metric_mod.OTLPMetricExporter(**exporter_kwargs)

    return span_exporter, metric_exporter


def init_telemetry() -> None:
    """初始化 OpenTelemetry SDK。

    根据配置决定：
    - OTEL_ENABLED=false: 不做任何事，trace/metrics API 走默认 no-op
    - OTEL_CONSOLE_EXPORT=true: Span 输出到控制台（开发调试）
    - 否则: 通过 OTLP 导出到 collector/Jaeger/Tempo（支持 gRPC/HTTP 协议）
    """
    global _initialized, _tracer_provider, _meter_provider

    if _initialized:
        return

    otel_conf = settings.otel
    if not otel_conf.enabled:
        logger.info("OpenTelemetry 已禁用 (OTEL_ENABLED=false)")
        _initialized = True
        return

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import (
        ConsoleMetricExporter,
        PeriodicExportingMetricReader,
    )
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME

    resource = Resource.create({SERVICE_NAME: otel_conf.service_name})

    # ── Tracing + Metrics ──
    _tracer_provider = TracerProvider(resource=resource)

    if otel_conf.console_export:
        span_formatter = _make_utf8_span_formatter()
        _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter(formatter=span_formatter)))
        reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=10000)
        logger.info("OpenTelemetry: ConsoleExporter")
    else:
        try:
            span_exporter, metric_exporter = _create_otlp_exporters(otel_conf)
            _tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=10000)
            logger.info(
                "OpenTelemetry: OTLP/{} -> {}{}",
                otel_conf.exporter_protocol, otel_conf.exporter_endpoint,
                f" (headers: {list(_parse_headers(otel_conf.exporter_headers).keys())})"
                if otel_conf.exporter_headers.strip() else "",
            )
        except Exception as e:
            logger.warning("OTLP exporter 初始化失败，回退到 Console: {}", e)
            _tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            reader = PeriodicExportingMetricReader(ConsoleMetricExporter(), export_interval_millis=10000)

    trace.set_tracer_provider(_tracer_provider)

    _meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    metrics.set_meter_provider(_meter_provider)

    # ── FastAPI 自动插桩 ──
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor().instrument()
        logger.info("FastAPI 自动插桩已启用")
    except Exception as e:
        logger.warning("FastAPI 自动插桩失败: {}", e)

    _initialized = True
    logger.info("OpenTelemetry 初始化完成 | service={}", otel_conf.service_name)


def shutdown_telemetry() -> None:
    """清理 OpenTelemetry 资源（flush pending spans/metrics）。"""
    global _tracer_provider, _meter_provider

    if _tracer_provider and hasattr(_tracer_provider, 'shutdown'):
        try:
            _tracer_provider.shutdown()
        except Exception as e:
            logger.warning("TracerProvider shutdown 异常: {}", e)

    if _meter_provider and hasattr(_meter_provider, 'shutdown'):
        try:
            _meter_provider.shutdown()
        except Exception as e:
            logger.warning("MeterProvider shutdown 异常: {}", e)

    logger.info("OpenTelemetry 资源已清理")


def get_tracer(name: str) -> trace.Tracer:
    """获取 Tracer 实例。OTel 未启用时返回 no-op Tracer。"""
    return trace.get_tracer(name)


def get_meter(name: str) -> metrics.Meter:
    """获取 Meter 实例。OTel 未启用时返回 no-op Meter。"""
    return metrics.get_meter(name)
