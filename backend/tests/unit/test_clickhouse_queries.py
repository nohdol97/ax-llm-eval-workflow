"""``app.services.clickhouse_queries`` 정적 검증.

ClickHouse parameterized query 정책 준수 확인:
- f-string ``{var}`` / ``%(name)s`` 보간 패턴이 잔재하지 않는다
- LIMIT 절을 명시한다 (auto-LIMIT 의존 금지)
- ClickHouse parameterized 문법 ``{name:Type}`` 만 사용
- 쓰기 SQL 차단 (INSERT/UPDATE/DELETE 등)
"""

from __future__ import annotations

import re

import pytest

from app.services.clickhouse_client import (
    ClickHouseSecurityError,
    _ensure_limit,
    _validate_sql,
)
from app.services.clickhouse_queries import ALL_QUERIES

# ---------- 위험 패턴 (MockClickHouse + 실코드 둘 다 차단) ----------
_FSTRING_VAR = re.compile(r"\{[a-zA-Z_]\w*\}")  # f-string {var} (no `:type`)
_PERCENT_S = re.compile(r"%\(.*?\)s")  # %(name)s 보간
# LIMIT 검증용: 정수 리터럴 또는 파라미터화된 ``{limit:UInt32}`` 형태 모두 허용
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_LIMIT_OR_PARAM_RE = re.compile(r"\bLIMIT\s+(\d+|\{[a-zA-Z_]\w*:[A-Za-z0-9()]+\})", re.IGNORECASE)
_PARAM_RE = re.compile(r"\{[a-zA-Z_]\w*:[A-Za-z0-9()]+\}")
_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


@pytest.mark.unit
class TestQuerySafety:
    """모든 쿼리에 대한 정적 보안 검증."""

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_no_fstring_variable(self, name: str, sql: str) -> None:
        """``{var}`` 단독 형태(f-string 잔재)가 없다."""
        match = _FSTRING_VAR.search(sql)
        assert match is None, (
            f"{name}: f-string variable interpolation detected: {match.group(0) if match else ''}"
        )

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_no_percent_s_interpolation(self, name: str, sql: str) -> None:
        """``%(name)s`` 보간이 없다."""
        assert _PERCENT_S.search(sql) is None, f"{name}: %(...)s pattern detected"

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_has_limit_clause(self, name: str, sql: str) -> None:
        """모든 쿼리에 LIMIT 절(정수 또는 파라미터)이 명시되어 있다."""
        assert _LIMIT_OR_PARAM_RE.search(sql) is not None, f"{name}: LIMIT clause missing"

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_has_parameterized_syntax(self, name: str, sql: str) -> None:
        """ClickHouse 파라미터화 문법 ``{name:Type}`` 이 적어도 1회 존재."""
        # LATENCY_STATS_QUERY 등은 다수 파라미터를 가진다.
        assert _PARAM_RE.search(sql), f"{name}: parameterized syntax {{name:Type}} not found"

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_no_write_verbs(self, name: str, sql: str) -> None:
        """SELECT 외 쓰기 동사가 없다."""
        # JSONExtract* 같은 내장 함수는 쓰기 동사가 아니므로 단어 경계 검사로 OK
        # 단, 'CREATED_AT' 컬럼명에 'CREATE'가 부분 매치되지 않도록 경계로 매치.
        match = _WRITE_RE.search(sql)
        if match:
            # `CREATE TABLE` 등 실제 쓰기 키워드만 차단 — 컬럼명에는 일반적으로 등장하지 않음.
            # 본 프로젝트 쿼리에는 등장하지 않는다.
            pytest.fail(f"{name}: write verb detected: {match.group(0)}")


@pytest.mark.unit
class TestValidatorIntegration:
    """``ClickHouseClient._validate_sql`` 가 모든 쿼리를 통과시킨다."""

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_validate_sql_passes(self, name: str, sql: str) -> None:
        """프로덕션 검증기를 통과해야 한다."""
        try:
            _validate_sql(sql)
        except ClickHouseSecurityError as exc:
            pytest.fail(f"{name}: validator rejected query — {exc}")

    @pytest.mark.parametrize("name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES])
    def test_ensure_limit_idempotent(self, name: str, sql: str) -> None:
        """이미 LIMIT가 있으면 자동 LIMIT가 추가되지 않는다 (정수/파라미터 모두)."""
        result = _ensure_limit(sql)
        # LIMIT가 1번만 등장 (정수 또는 파라미터 형태)
        assert len(_LIMIT_OR_PARAM_RE.findall(result)) == 1, f"{name}: LIMIT가 중복 추가됨"


@pytest.mark.unit
class TestSpecificQueryShape:
    """주요 쿼리의 핵심 컬럼/조인 존재 검증."""

    def test_compare_runs_has_required_columns(self) -> None:
        from app.services.clickhouse_queries import COMPARE_RUNS_QUERY

        for col in (
            "avg_latency_ms",
            "p50_latency_ms",
            "p90_latency_ms",
            "p99_latency_ms",
            "total_cost_usd",
            "avg_total_tokens",
            "avg_score",
            "items_completed",
        ):
            assert col in COMPARE_RUNS_QUERY, f"missing column: {col}"

    def test_compare_runs_uses_project_filter(self) -> None:
        from app.services.clickhouse_queries import COMPARE_RUNS_QUERY

        assert "{project_id:String}" in COMPARE_RUNS_QUERY
        assert "{run_names:Array(String)}" in COMPARE_RUNS_QUERY

    def test_score_distribution_uses_bin_param(self) -> None:
        from app.services.clickhouse_queries import SCORE_DISTRIBUTION_QUERY

        assert "{bins:UInt32}" in SCORE_DISTRIBUTION_QUERY
        assert "{score_name:String}" in SCORE_DISTRIBUTION_QUERY

    def test_latency_distribution_uses_bin_width(self) -> None:
        from app.services.clickhouse_queries import LATENCY_DISTRIBUTION_QUERY

        assert "{bin_width:Float64}" in LATENCY_DISTRIBUTION_QUERY
        assert "{run_name:String}" in LATENCY_DISTRIBUTION_QUERY

    def test_cost_distribution_separates_costs(self) -> None:
        from app.services.clickhouse_queries import COST_DISTRIBUTION_QUERY

        assert "model_cost" in COST_DISTRIBUTION_QUERY
        assert "eval_cost" in COST_DISTRIBUTION_QUERY
        assert "total_cost" in COST_DISTRIBUTION_QUERY


@pytest.mark.unit
class TestTraceQueries:
    """Phase 8-A-1 trace 쿼리 정적 검증."""

    def test_trace_search_uses_required_params(self) -> None:
        from app.services.clickhouse_queries import TRACE_SEARCH_QUERY

        for param in (
            "{project_id:String}",
            "{name:String}",
            "{tags:Array(String)}",
            "{tags_count:UInt32}",
            "{user_ids:Array(String)}",
            "{user_ids_count:UInt32}",
            "{session_ids:Array(String)}",
            "{session_ids_count:UInt32}",
            "{has_from:UInt8}",
            "{from_timestamp:DateTime64(3)}",
            "{has_to:UInt8}",
            "{to_timestamp:DateTime64(3)}",
            "{limit:UInt32}",
            "{offset:UInt32}",
        ):
            assert param in TRACE_SEARCH_QUERY, f"missing param: {param}"

    def test_trace_search_returns_required_columns(self) -> None:
        from app.services.clickhouse_queries import TRACE_SEARCH_QUERY

        for col in (
            "AS id",
            "AS name",
            "AS user_id",
            "AS session_id",
            "AS tags",
            "AS metadata",
            "AS timestamp",
            "AS total_cost_usd",
            "AS total_latency_ms",
            "AS observation_count",
        ):
            assert col in TRACE_SEARCH_QUERY, f"missing column alias: {col}"

    def test_trace_count_only_returns_total(self) -> None:
        from app.services.clickhouse_queries import TRACE_COUNT_QUERY

        assert "count(DISTINCT t.id) AS total" in TRACE_COUNT_QUERY
        # COUNT 쿼리도 필터 파라미터 동일하게 사용해야 한다
        assert "{project_id:String}" in TRACE_COUNT_QUERY
        assert "{tags:Array(String)}" in TRACE_COUNT_QUERY

    def test_trace_detail_filters_by_id_and_project(self) -> None:
        from app.services.clickhouse_queries import TRACE_DETAIL_QUERY

        assert "{trace_id:String}" in TRACE_DETAIL_QUERY
        assert "{project_id:String}" in TRACE_DETAIL_QUERY
        assert "AS input" in TRACE_DETAIL_QUERY
        assert "AS output" in TRACE_DETAIL_QUERY
        assert "AS timestamp" in TRACE_DETAIL_QUERY

    def test_trace_observations_orders_by_start_time(self) -> None:
        from app.services.clickhouse_queries import TRACE_OBSERVATIONS_QUERY

        assert "{trace_id:String}" in TRACE_OBSERVATIONS_QUERY
        assert "ORDER BY o.start_time ASC" in TRACE_OBSERVATIONS_QUERY
        # 핵심 컬럼 alias 검증
        for col in (
            "AS type",
            "AS parent_observation_id",
            "AS level",
            "AS status_message",
            "AS latency_ms",
            "AS model",
            "AS usage",
            "AS cost_usd",
        ):
            assert col in TRACE_OBSERVATIONS_QUERY, f"missing column alias: {col}"

    def test_trace_scores_query(self) -> None:
        from app.services.clickhouse_queries import TRACE_SCORES_QUERY

        assert "{trace_id:String}" in TRACE_SCORES_QUERY
        assert "AS created_at" in TRACE_SCORES_QUERY
        assert "AS value" in TRACE_SCORES_QUERY
