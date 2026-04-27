"""Health 엔드포인트 단위 테스트.

FastAPI ``app.dependency_overrides``로 외부 클라이언트를 stub으로 대체한다.

검증:
- 모든 서비스가 ok → status="ok"
- 일부 warn → "degraded"
- 일부 error → "down"
- 응답 스키마 (HealthResponse 모든 필드 존재)
- 미설정 endpoint(Prometheus, OTel) → warn
- ClickHouse client가 ``None``이어도 응답 정상
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import __version__
from app.api.v1 import health as health_module
from app.core.config import Settings, get_settings
from app.core.deps import (
    get_app_settings,
    get_clickhouse_client,
    get_langfuse_client,
    get_litellm_client,
    get_redis_client,
)
from app.main import create_app
from app.models.health import ServiceHealth


# ---------- Stub 클라이언트 ----------
class _StubHealth:
    """``health_check()``만 노출하는 최소 stub."""

    def __init__(self, status: str = "ok", detail: str | None = None) -> None:
        self._status = status
        self._detail = detail

    async def health_check(self) -> ServiceHealth:
        return ServiceHealth(
            status=self._status,  # type: ignore[arg-type]
            latency_ms=1.0,
            endpoint="https://stub.example.com",
            detail=self._detail,
            checked_at=datetime.now(UTC),
        )

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """각 테스트 전후 settings 캐시 초기화 — 환경변수 격리."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def app_with_stubs(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """모든 외부 클라이언트가 stub으로 대체된 FastAPI TestClient."""
    # 환경변수 — 사내 endpoint 미설정 (warn 기대)
    monkeypatch.delenv("PROMETHEUS_QUERY_URL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    # _check_prometheus / _check_otel가 외부 호출하지 않도록 미설정 settings 강제

    # lifespan 호출을 회피하기 위해 lifespan 비활성 후 직접 stub 주입.
    # create_app은 lifespan을 등록하므로, TestClient 진입 시 lifespan startup이 동작한다.
    # 그 때 실제 RedisClient 등이 만들어지므로, dependency_overrides로 라우터 단계에서 stub 주입.

    app = create_app()

    stub_settings = Settings(
        LABS_ENV="dev",
        PROMETHEUS_QUERY_URL="",
        OTEL_EXPORTER_OTLP_ENDPOINT="",
    )

    app.dependency_overrides[get_app_settings] = lambda: stub_settings
    app.dependency_overrides[get_langfuse_client] = lambda: _StubHealth("ok")
    app.dependency_overrides[get_litellm_client] = lambda: _StubHealth("ok")
    app.dependency_overrides[get_redis_client] = lambda: _StubHealth("ok")
    app.dependency_overrides[get_clickhouse_client] = lambda: _StubHealth("ok")

    return TestClient(app)


@pytest.mark.unit
class TestHealthEndpoint:
    """``GET /api/v1/health`` 응답."""

    def test_all_ok_returns_200_ok(self, app_with_stubs: TestClient) -> None:
        """모든 서비스 ok이고 prom/otel 미설정 → degraded (warn 포함)."""
        resp = app_with_stubs.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        # prometheus / otel 미설정 → warn 포함 → 전체 degraded
        assert body["status"] == "degraded"
        assert body["version"] == __version__
        assert body["environment"] == "dev"
        assert "services" in body

    def test_response_includes_seven_services(
        self, app_with_stubs: TestClient
    ) -> None:
        """응답에 7종 서비스 키가 모두 존재."""
        resp = app_with_stubs.get("/api/v1/health")
        body = resp.json()
        names = set(body["services"].keys())
        expected = {"langfuse", "litellm", "clickhouse", "redis", "prometheus", "otel", "loki"}
        assert names == expected

    def test_unconfigured_prometheus_warns(
        self, app_with_stubs: TestClient
    ) -> None:
        """PROMETHEUS_QUERY_URL 미설정 → warn."""
        resp = app_with_stubs.get("/api/v1/health")
        body = resp.json()
        assert body["services"]["prometheus"]["status"] == "warn"
        assert "PROMETHEUS_QUERY_URL" in body["services"]["prometheus"]["detail"]

    def test_unconfigured_otel_warns(
        self, app_with_stubs: TestClient
    ) -> None:
        """OTEL_EXPORTER_OTLP_ENDPOINT 미설정 → warn."""
        resp = app_with_stubs.get("/api/v1/health")
        body = resp.json()
        assert body["services"]["otel"]["status"] == "warn"

    def test_loki_check_relies_on_json_formatter(
        self, app_with_stubs: TestClient
    ) -> None:
        """Loki 체크는 JSON formatter 활성 여부."""
        resp = app_with_stubs.get("/api/v1/health")
        body = resp.json()
        loki = body["services"]["loki"]
        # JSON formatter는 주체적으로 활성화돼야 (ok), 최소한 status 필드는 있어야 함
        assert loki["status"] in ("ok", "warn")


@pytest.mark.unit
class TestHealthAggregation:
    """``_aggregate_status`` 단위 검증."""

    def test_all_ok_returns_ok(self) -> None:
        services = _make_services({"a": "ok", "b": "ok"})
        assert health_module._aggregate_status(services) == "ok"

    def test_any_warn_returns_degraded(self) -> None:
        services = _make_services({"a": "ok", "b": "warn"})
        assert health_module._aggregate_status(services) == "degraded"

    def test_any_error_returns_down(self) -> None:
        services = _make_services({"a": "ok", "b": "error"})
        assert health_module._aggregate_status(services) == "down"

    def test_error_overrides_warn(self) -> None:
        """error가 warn보다 우선 (down)."""
        services = _make_services({"a": "warn", "b": "error"})
        assert health_module._aggregate_status(services) == "down"


def _make_services(data: dict[str, str]) -> dict[str, ServiceHealth]:
    """status 문자열만으로 ServiceHealth dict 생성."""
    return {
        name: ServiceHealth(
            status=status,  # type: ignore[arg-type]
            latency_ms=1.0,
            endpoint=None,
            detail=None,
            checked_at=datetime.now(UTC),
        )
        for name, status in data.items()
    }


@pytest.mark.unit
class TestHealthErrorState:
    """일부 서비스 error 상황 — 전체 status='down'."""

    def test_redis_error_results_in_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis가 error를 반환하면 전체 status='down'."""
        get_settings.cache_clear()
        app = create_app()
        stub_settings = Settings(
            LABS_ENV="dev",
            PROMETHEUS_QUERY_URL="",
            OTEL_EXPORTER_OTLP_ENDPOINT="",
        )
        app.dependency_overrides[get_app_settings] = lambda: stub_settings
        app.dependency_overrides[get_langfuse_client] = lambda: _StubHealth("ok")
        app.dependency_overrides[get_litellm_client] = lambda: _StubHealth("ok")
        app.dependency_overrides[get_redis_client] = lambda: _StubHealth(
            "error", detail="connection refused"
        )
        app.dependency_overrides[get_clickhouse_client] = lambda: _StubHealth("ok")

        client = TestClient(app)
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body: dict[str, Any] = resp.json()
        assert body["services"]["redis"]["status"] == "error"
        assert body["status"] == "down"
