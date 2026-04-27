"""관측성 (OpenTelemetry + Prometheus + structlog) 초기화.

- structlog: ``configure_logging`` 호출
- OpenTelemetry: TracerProvider + OTLP/HTTP exporter
  - ``OTEL_EXPORTER_OTLP_ENDPOINT`` 미설정 시 NoOp tracer
  - FastAPI / HTTPX / Redis 자동 instrumentation
- Prometheus: ``prometheus-fastapi-instrumentator``로 ``/metrics`` 노출
  - 본 프로젝트 커스텀 메트릭(``ax_*``) 일부 placeholder 등록
- 미들웨어: 요청별 ``request_id`` 생성 + structlog context binding
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import Counter, Gauge, Histogram

from app.core.config import Settings
from app.core.logging import configure_logging, get_logger

# ---------- Prometheus 커스텀 메트릭 ----------
# 메트릭 이름 prefix: ``ax_``
ax_experiments_in_progress = Gauge(
    "ax_experiments_in_progress",
    "현재 진행 중인 실험 수",
    labelnames=("environment",),
)

ax_llm_first_token_latency_seconds = Histogram(
    "ax_llm_first_token_latency_seconds",
    "LLM 첫 토큰 응답 latency (스트리밍 시작 시간)",
    labelnames=("model",),
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

ax_evaluator_runs_total = Counter(
    "ax_evaluator_runs_total",
    "Evaluator 실행 카운트",
    labelnames=("evaluator", "status"),
)

ax_redis_operation_seconds = Histogram(
    "ax_redis_operation_seconds",
    "Redis 작업 latency",
    labelnames=("op",),
    buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
)


def _setup_otel(settings: Settings) -> None:
    """OpenTelemetry SDK 초기화.

    ``OTEL_EXPORTER_OTLP_ENDPOINT`` 미설정 시 NoOp tracer(기본 OTel api)만 사용.
    """
    logger = get_logger(__name__)

    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        logger.info(
            "otel_disabled",
            reason="OTEL_EXPORTER_OTLP_ENDPOINT not configured",
        )
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        logger.warning("otel_sdk_not_available", error=str(exc))
        return

    # 헤더 파싱 (k=v,k=v 형식)
    headers: dict[str, str] = {}
    if settings.OTEL_EXPORTER_OTLP_HEADERS:
        for part in settings.OTEL_EXPORTER_OTLP_HEADERS.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                headers[k.strip()] = v.strip()

    # 리소스 속성 파싱 + service.name override
    resource_attrs: dict[str, str] = {"service.name": settings.OTEL_SERVICE_NAME}
    if settings.OTEL_RESOURCE_ATTRIBUTES:
        for part in settings.OTEL_RESOURCE_ATTRIBUTES.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                resource_attrs[k.strip()] = v.strip()

    resource = Resource.create(resource_attrs)
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        headers=headers or None,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    logger.info(
        "otel_initialized",
        endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT,
        service_name=settings.OTEL_SERVICE_NAME,
    )


def _instrument_fastapi(app: FastAPI) -> None:
    """FastAPI auto-instrumentation."""
    logger = get_logger(__name__)
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception as exc:  # pragma: no cover — 의존성 누락 가능
        logger.warning("otel_fastapi_instrument_failed", error=str(exc))


def _instrument_httpx() -> None:
    """HTTPX auto-instrumentation."""
    logger = get_logger(__name__)
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.warning("otel_httpx_instrument_failed", error=str(exc))


def _instrument_redis() -> None:
    """Redis auto-instrumentation."""
    logger = get_logger(__name__)
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument()
    except Exception as exc:  # pragma: no cover
        logger.warning("otel_redis_instrument_failed", error=str(exc))


def _setup_prometheus(app: FastAPI, settings: Settings) -> None:
    """Prometheus FastAPI Instrumentator + ``/metrics`` 노출."""
    logger = get_logger(__name__)
    if not settings.LABS_METRICS_ENABLED:
        logger.info("prometheus_disabled")
        return
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=False,
            should_ignore_untemplated=True,
            should_respect_env_var=False,
        )
        instrumentator.instrument(app).expose(
            app, endpoint=settings.LABS_METRICS_PATH, include_in_schema=False
        )
        logger.info("prometheus_initialized", path=settings.LABS_METRICS_PATH)
    except Exception as exc:  # pragma: no cover
        logger.warning("prometheus_init_failed", error=str(exc))


def _add_request_id_middleware(app: FastAPI) -> None:
    """요청별 request_id 생성 + structlog context binding."""

    @app.middleware("http")
    async def request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # 클라이언트가 보낸 X-Request-ID 우선 사용
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id

        # structlog 컨텍스트에 binding (이번 요청 내내 모든 로그에 자동 첨부)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            path=request.url.path,
            method=request.method,
        )
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.clear_contextvars()

        response.headers["X-Request-ID"] = request_id
        return response


def setup_observability(app: FastAPI, settings: Settings) -> None:
    """애플리케이션 관측성 초기화 진입점.

    호출 순서:
      1. structlog 설정
      2. OTel TracerProvider + auto-instrumentation
      3. Prometheus instrumentator + ``/metrics`` 노출
      4. request_id 미들웨어
    """
    configure_logging(
        log_level=settings.LABS_LOG_LEVEL,
        log_format=settings.LABS_LOG_FORMAT,
    )

    _setup_otel(settings)
    _instrument_fastapi(app)
    _instrument_httpx()
    _instrument_redis()

    _setup_prometheus(app, settings)
    _add_request_id_middleware(app)


# 외부에서 logger 접근 편의
__all__ = [
    "ax_evaluator_runs_total",
    "ax_experiments_in_progress",
    "ax_llm_first_token_latency_seconds",
    "ax_redis_operation_seconds",
    "get_logger",
    "setup_observability",
]


def _module_get_logger(name: str | None = None) -> Any:
    """모듈 외부에 노출되는 logger getter (re-export)."""
    return get_logger(name)
