"""``GET /api/v1/health`` — 사내 외부 시스템 + 자체 Redis 헬스 체크.

병렬 실행 (asyncio.gather + 각 호출에 ``asyncio.timeout``) — 한 서비스가 느려져도
전체 응답이 ``LABS_HEALTH_CHECK_TIMEOUT_SEC``를 크게 넘기지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

import httpx
from fastapi import APIRouter, Depends

from app import __version__
from app.core.config import Settings
from app.core.deps import (
    get_app_settings,
    get_clickhouse_client,
    get_langfuse_client,
    get_litellm_client,
    get_redis_client,
)
from app.core.logging import is_json_formatter_active
from app.models.health import HealthResponse, OverallStatus, ServiceHealth
from app.services.clickhouse_client import (
    ClickHouseClient,
    LangfusePublicAPIFallbackClient,
)
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient
from app.services.redis_client import RedisClient

router = APIRouter(tags=["health"])


async def _with_timeout(
    coro: object,
    timeout: float,
    *,
    endpoint: str | None = None,
) -> ServiceHealth:
    """헬스 체크 코루틴을 timeout으로 감싸 ``ServiceHealth`` 반환."""
    start = time.perf_counter()
    try:
        async with asyncio.timeout(timeout):
            result = await coro  # type: ignore[misc]
    except TimeoutError:
        latency_ms = (time.perf_counter() - start) * 1000.0
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=f"timeout after {timeout}s",
            checked_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=str(exc),
            checked_at=datetime.now(UTC),
        )

    if isinstance(result, ServiceHealth):
        return result
    return ServiceHealth(
        status="error",
        endpoint=endpoint,
        detail=f"unexpected result type: {type(result).__name__}",
        checked_at=datetime.now(UTC),
    )


async def _check_prometheus(
    settings: Settings, timeout: float
) -> ServiceHealth:
    """Prometheus query URL의 ``/-/ready`` 호출."""
    if not settings.PROMETHEUS_QUERY_URL:
        return ServiceHealth(
            status="warn",
            endpoint=None,
            detail="PROMETHEUS_QUERY_URL not configured",
            checked_at=datetime.now(UTC),
        )
    endpoint = settings.PROMETHEUS_QUERY_URL.rstrip("/") + "/-/ready"
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(endpoint)
        latency_ms = (time.perf_counter() - start) * 1000.0
        if 200 <= resp.status_code < 300:
            return ServiceHealth(
                status="ok",
                latency_ms=latency_ms,
                endpoint=endpoint,
                checked_at=datetime.now(UTC),
            )
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=f"HTTP {resp.status_code}",
            checked_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=str(exc),
            checked_at=datetime.now(UTC),
        )


async def _check_otel(settings: Settings, timeout: float) -> ServiceHealth:
    """OTel collector ``/v1/traces`` 도달성 점검 (HEAD)."""
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return ServiceHealth(
            status="warn",
            endpoint=None,
            detail="OTEL_EXPORTER_OTLP_ENDPOINT not configured",
            checked_at=datetime.now(UTC),
        )
    endpoint = (
        settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/") + "/v1/traces"
    )
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.head(endpoint)
        latency_ms = (time.perf_counter() - start) * 1000.0
        # 4xx 응답도 도달성 OK으로 간주 (OTel collector는 GET/HEAD를 405로 응답하기도 함)
        if 200 <= resp.status_code < 500:
            return ServiceHealth(
                status="ok",
                latency_ms=latency_ms,
                endpoint=endpoint,
                detail=f"HTTP {resp.status_code} (reachable)",
                checked_at=datetime.now(UTC),
            )
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=f"HTTP {resp.status_code}",
            checked_at=datetime.now(UTC),
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = (time.perf_counter() - start) * 1000.0
        return ServiceHealth(
            status="error",
            latency_ms=latency_ms,
            endpoint=endpoint,
            detail=str(exc),
            checked_at=datetime.now(UTC),
        )


def _check_loki(settings: Settings) -> ServiceHealth:
    """Loki 자체 점검 — JSON formatter 활성 여부 확인."""
    json_active = is_json_formatter_active()
    labels = settings.LABS_LOG_LOKI_LABELS or ""
    if json_active:
        return ServiceHealth(
            status="ok",
            endpoint=None,
            detail=f"json formatter active; labels={labels}",
            checked_at=datetime.now(UTC),
        )
    return ServiceHealth(
        status="warn",
        endpoint=None,
        detail="JSON formatter not detected (Loki ingestion may be impaired)",
        checked_at=datetime.now(UTC),
    )


def _aggregate_status(services: dict[str, ServiceHealth]) -> OverallStatus:
    """개별 서비스 상태로부터 전체 상태 결정."""
    has_error = any(s.status == "error" for s in services.values())
    has_warn = any(s.status == "warn" for s in services.values())
    if has_error:
        return "down"
    if has_warn:
        return "degraded"
    return "ok"


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="외부 시스템 + 자체 Redis 헬스 체크",
)
async def get_health(
    settings: Settings = Depends(get_app_settings),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    redis: RedisClient = Depends(get_redis_client),
    clickhouse: ClickHouseClient
    | LangfusePublicAPIFallbackClient
    | None = Depends(get_clickhouse_client),
) -> HealthResponse:
    """7종 외부 시스템(Langfuse / LiteLLM / ClickHouse / Prometheus / OTel /
    Loki / Redis) 헬스 체크 결과를 반환한다.

    - 미설정 endpoint는 ``warn`` 상태
    - 호출 실패는 ``error`` 상태
    - 각 호출은 ``LABS_HEALTH_CHECK_TIMEOUT_SEC`` 타임아웃 내에서 병렬 실행
    """
    timeout = settings.LABS_HEALTH_CHECK_TIMEOUT_SEC

    # ClickHouse 헬스 (직접 또는 폴백, 미설정 시 warn)
    if clickhouse is None:
        clickhouse_coro = _identity(
            _make_warn_health("ClickHouse client not initialized")
        )
    else:
        clickhouse_coro = _with_timeout(clickhouse.health_check(), timeout=timeout)

    results = await asyncio.gather(
        _with_timeout(langfuse.health_check(), timeout=timeout),
        _with_timeout(litellm.health_check(), timeout=timeout),
        clickhouse_coro,
        _with_timeout(redis.health_check(), timeout=timeout),
        _check_prometheus(settings, timeout=timeout),
        _check_otel(settings, timeout=timeout),
        return_exceptions=False,
    )

    services: dict[str, ServiceHealth] = {
        "langfuse": results[0],
        "litellm": results[1],
        "clickhouse": results[2],
        "redis": results[3],
        "prometheus": results[4],
        "otel": results[5],
        "loki": _check_loki(settings),
    }

    overall = _aggregate_status(services)
    return HealthResponse(
        status=overall,
        version=__version__,
        environment=settings.LABS_ENV,
        services=services,
        checked_at=datetime.now(UTC),
    )


def _make_warn_health(detail: str) -> ServiceHealth:
    """간단한 warn 상태 ``ServiceHealth`` 객체 생성."""
    return ServiceHealth(
        status="warn",
        endpoint=None,
        detail=detail,
        checked_at=datetime.now(UTC),
    )


async def _identity(value: ServiceHealth) -> ServiceHealth:
    """이미 동기적으로 만들어진 ``ServiceHealth``를 awaitable로 감싸기."""
    return value
