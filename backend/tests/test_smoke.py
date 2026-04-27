"""Phase 0 Smoke Test.

Mock fixture 6종 + JWT helper가 정상적으로 로딩되고 핵심 동작이 가능한지 검증한다.
모든 테스트는 사내 의존성 없이 통과해야 한다.
"""

from __future__ import annotations

import jwt as pyjwt
import pytest

from tests.fixtures.jwt_helper import JWTTestHelper
from tests.fixtures.mock_clickhouse import (
    ClickHouseSecurityError,
    MockClickHouseClient,
)
from tests.fixtures.mock_langfuse import (
    LangfuseNotFoundError,
    MockLangfuseClient,
)
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.mock_loki import MockLokiSink
from tests.fixtures.mock_otel import MockOTelExporter
from tests.fixtures.mock_redis import MockRedisClient

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Langfuse
# ---------------------------------------------------------------------------
def test_langfuse_mock_get_prompt(langfuse_client: MockLangfuseClient) -> None:
    """seed된 프롬프트를 get_prompt로 조회하면 본문과 variables를 자동 파싱해야 한다."""
    langfuse_client._seed(
        prompts=[{"name": "greet", "body": "Hello {{name}}, age={{age}}", "version": 1}]
    )
    p = langfuse_client.get_prompt("greet")
    assert p.body == "Hello {{name}}, age={{age}}"
    assert "name" in p.variables
    assert "age" in p.variables


def test_langfuse_mock_get_prompt_not_found_raises(
    langfuse_client: MockLangfuseClient,
) -> None:
    """미존재 프롬프트 조회 시 LangfuseNotFoundError가 발생해야 한다."""
    with pytest.raises(LangfuseNotFoundError):
        langfuse_client.get_prompt("does-not-exist")


def test_langfuse_create_trace_and_score(langfuse_client: MockLangfuseClient) -> None:
    """trace + generation + score 흐름이 동작하고 검증 헬퍼로 조회 가능해야 한다."""
    trace_id = langfuse_client.create_trace(name="test-trace")
    langfuse_client.create_generation(
        trace_id=trace_id,
        name="gen-1",
        model="gpt-4o",
        input=[{"role": "user", "content": "hi"}],
        output="hello",
        usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    )
    langfuse_client.score(trace_id=trace_id, name="quality", value=0.95)

    assert len(langfuse_client._get_traces()) == 1
    assert len(langfuse_client._get_scores()) == 1
    assert len(langfuse_client._get_generations()) == 1


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_redis_mock_set_get(redis_client: MockRedisClient) -> None:
    """SET → GET이 정상 동작하고 prefix 조회 헬퍼가 작동해야 한다."""
    await redis_client.set("ax:test:key", "value", ex=60)
    got = await redis_client.get("ax:test:key")
    assert got == "value"

    keys = await redis_client._get_keys_with_prefix("ax:")
    assert "ax:test:key" in keys


@pytest.mark.asyncio
async def test_redis_mock_incr(redis_client: MockRedisClient) -> None:
    """INCRBY 동작 검증."""
    await redis_client.set("ax:counter", 0)
    v1 = await redis_client.incr("ax:counter")
    v2 = await redis_client.incr("ax:counter", amount=5)
    assert v1 == 1
    assert v2 == 6


# ---------------------------------------------------------------------------
# LiteLLM
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_litellm_mock_completion(litellm_client: MockLiteLLMProxy) -> None:
    """set_response()로 지정한 content가 응답에 포함되어야 한다."""
    litellm_client.set_response("hello world")
    res = await litellm_client.completion(
        model="gpt-4o",
        messages=[{"role": "user", "content": "hi"}],
    )
    assert isinstance(res, dict)
    assert "hello world" in res["choices"][0]["message"]["content"]
    assert res["usage"]["total_tokens"] > 0


@pytest.mark.asyncio
async def test_litellm_mock_health_and_models(
    litellm_client: MockLiteLLMProxy,
) -> None:
    """health() / model_info() 응답 형태 검증."""
    h = await litellm_client.health()
    assert h["status"] == "healthy"

    info = await litellm_client.model_info()
    assert "data" in info
    assert len(info["data"]) >= 8


# ---------------------------------------------------------------------------
# ClickHouse
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clickhouse_mock_register_and_query(
    clickhouse_client: MockClickHouseClient,
) -> None:
    """등록된 패턴 매치 시 rows 반환, 호출 이력 기록 확인."""
    clickhouse_client.register_response(
        sql_pattern=r"SELECT\s+count\(\*\)",
        rows=[{"count": 42}],
    )
    rows = await clickhouse_client.query(
        "SELECT count(*) FROM traces WHERE name = %(n)s",
        parameters={"n": "test"},
    )
    assert rows == [{"count": 42}]
    executed = clickhouse_client._get_executed_queries()
    assert len(executed) == 1
    assert executed[0][1] == {"n": "test"}


@pytest.mark.asyncio
async def test_clickhouse_mock_rejects_unsafe_sql(
    clickhouse_client: MockClickHouseClient,
) -> None:
    """f-string 패턴 (``{var}`` 잔재) 감지 시 ClickHouseSecurityError raise."""
    with pytest.raises(ClickHouseSecurityError):
        await clickhouse_client.query("SELECT * FROM t WHERE id = {user_id}")


# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
def test_otel_mock_record_and_clear(otel_exporter: MockOTelExporter) -> None:
    """record_span 후 get_finished_spans로 조회되며 clear로 초기화 가능."""
    otel_exporter.record_span(
        name="test-span",
        attributes={"http.method": "GET"},
        status="OK",
        duration_ns=1000,
    )
    spans = otel_exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0]["name"] == "test-span"
    assert spans[0]["attributes"]["http.method"] == "GET"

    otel_exporter.clear()
    assert otel_exporter.get_finished_spans() == []


# ---------------------------------------------------------------------------
# Loki
# ---------------------------------------------------------------------------
def test_loki_mock_record_and_query(loki_sink: MockLokiSink) -> None:
    """record로 로그 기록 후 label 조회 가능."""
    loki_sink.record(level="info", event="user.login", user_id="user-1")
    loki_sink.record(level="error", event="db.fail", reason="timeout")
    assert len(loki_sink.get_logs()) == 2
    assert len(loki_sink.get_logs_with_label("user_id", "user-1")) == 1
    assert len(loki_sink.get_logs_by_level("error")) == 1


def test_loki_mock_pii_check_passes_for_safe_logs(loki_sink: MockLokiSink) -> None:
    """더미 데이터만 있는 로그는 PII 검증 통과."""
    loki_sink.record(level="info", event="user.action", user_id="user-1", action="click")
    loki_sink.assert_no_pii_leaked()


def test_loki_mock_pii_check_fails_for_email_leak(loki_sink: MockLokiSink) -> None:
    """이메일 패턴이 로그에 들어가면 AssertionError가 발생해야 한다."""
    loki_sink.record(level="info", event="leak", message="contact me at user@example.com")
    with pytest.raises(AssertionError):
        loki_sink.assert_no_pii_leaked()


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------
def test_jwt_admin_signs_correctly(
    jwt_helper: JWTTestHelper,
    test_jwt_admin: str,
) -> None:
    """발행된 JWT가 공개키로 검증 가능하고, roles=admin claim을 가져야 한다."""
    decoded = pyjwt.decode(
        test_jwt_admin,
        jwt_helper.get_public_pem(),
        algorithms=["RS256"],
        audience="labs",
        issuer="https://auth.test.local",
    )
    assert decoded["roles"] == ["admin"]
    assert decoded["sub"] == "user-admin-1"


def test_jwt_jwks_format(jwt_helper: JWTTestHelper) -> None:
    """JWKS 응답이 RSA 키 형식을 갖추어야 한다."""
    jwks = jwt_helper.get_public_jwks()
    assert "keys" in jwks
    assert len(jwks["keys"]) == 1
    key = jwks["keys"][0]
    assert key["kty"] == "RSA"
    assert key["use"] == "sig"
    assert key["alg"] == "RS256"
    assert "n" in key
    assert "e" in key


def test_jwt_expired_token_rejected(jwt_helper: JWTTestHelper) -> None:
    """만료 토큰은 PyJWT가 ExpiredSignatureError를 발생시켜야 한다."""
    expired = jwt_helper.create_expired_token(role="user")
    with pytest.raises(pyjwt.ExpiredSignatureError):
        pyjwt.decode(
            expired,
            jwt_helper.get_public_pem(),
            algorithms=["RS256"],
            audience="labs",
            issuer="https://auth.test.local",
        )
