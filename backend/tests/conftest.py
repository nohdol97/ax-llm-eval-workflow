"""pytest 공통 fixture 등록.

모든 Mock fixture를 pytest에 노출하여, ``unit/``, ``integration/`` 디렉터리의 테스트가
사내 의존성 없이 동작할 수 있도록 한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.fixtures.jwt_helper import JWTTestHelper
from tests.fixtures.mock_clickhouse import MockClickHouseClient
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.mock_loki import MockLokiSink
from tests.fixtures.mock_otel import MockOTelExporter
from tests.fixtures.mock_redis import MockRedisClient


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------
@pytest.fixture
def langfuse_client() -> MockLangfuseClient:
    """Langfuse v3 mock 클라이언트 (function scope, 매 테스트마다 fresh)."""
    return MockLangfuseClient()


# ---------------------------------------------------------------------------
# LiteLLM
# ---------------------------------------------------------------------------
@pytest.fixture
def litellm_client() -> MockLiteLLMProxy:
    """LiteLLM Proxy mock (function scope)."""
    return MockLiteLLMProxy()


# ---------------------------------------------------------------------------
# ClickHouse
# ---------------------------------------------------------------------------
@pytest.fixture
def clickhouse_client() -> MockClickHouseClient:
    """ClickHouse async client mock (function scope)."""
    return MockClickHouseClient()


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
@pytest.fixture
async def redis_client() -> AsyncIterator[MockRedisClient]:
    """fakeredis 기반 Redis client mock. 테스트 종료 시 자동 close."""
    client = MockRedisClient(decode_responses=True)
    try:
        yield client
    finally:
        await client.aclose()


# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
@pytest.fixture
def otel_exporter() -> MockOTelExporter:
    """OTel span exporter mock (function scope)."""
    return MockOTelExporter()


@pytest.fixture(autouse=True)
def _reset_otel(otel_exporter: MockOTelExporter) -> None:
    """각 테스트 시작 전 OTel span buffer 초기화 (테스트 격리)."""
    otel_exporter.clear()


# ---------------------------------------------------------------------------
# Loki / 구조화 로그
# ---------------------------------------------------------------------------
@pytest.fixture
def loki_sink() -> MockLokiSink:
    """구조화 로그 캡처 sink (function scope)."""
    return MockLokiSink()


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def jwt_helper() -> JWTTestHelper:
    """RS256 JWT helper (session scope) — RSA 키페어 생성 비용 절감."""
    return JWTTestHelper(issuer="https://auth.test.local", audience="labs")


@pytest.fixture
def test_jwt_admin(jwt_helper: JWTTestHelper) -> str:
    """admin 역할 JWT 토큰 (function scope)."""
    return jwt_helper.create_token(role="admin", sub="user-admin-1")


@pytest.fixture
def test_jwt_user(jwt_helper: JWTTestHelper) -> str:
    """user 역할 JWT 토큰 (function scope)."""
    return jwt_helper.create_token(role="user", sub="user-1")


@pytest.fixture
def test_jwt_viewer(jwt_helper: JWTTestHelper) -> str:
    """viewer 역할 JWT 토큰 (function scope)."""
    return jwt_helper.create_token(role="viewer", sub="user-viewer-1")
