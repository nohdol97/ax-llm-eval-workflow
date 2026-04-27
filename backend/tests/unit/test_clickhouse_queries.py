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
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)
_PARAM_RE = re.compile(r"\{[a-zA-Z_]\w*:[A-Za-z0-9()]+\}")
_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|RENAME|GRANT|REVOKE)\b",
    re.IGNORECASE,
)


@pytest.mark.unit
class TestQuerySafety:
    """모든 쿼리에 대한 정적 보안 검증."""

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_no_fstring_variable(self, name: str, sql: str) -> None:
        """``{var}`` 단독 형태(f-string 잔재)가 없다."""
        match = _FSTRING_VAR.search(sql)
        assert match is None, (
            f"{name}: f-string variable interpolation detected: {match.group(0) if match else ''}"
        )

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_no_percent_s_interpolation(self, name: str, sql: str) -> None:
        """``%(name)s`` 보간이 없다."""
        assert _PERCENT_S.search(sql) is None, f"{name}: %(...)s pattern detected"

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_has_limit_clause(self, name: str, sql: str) -> None:
        """모든 쿼리에 LIMIT 절이 명시되어 있다."""
        assert _LIMIT_RE.search(sql) is not None, f"{name}: LIMIT clause missing"

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_has_parameterized_syntax(self, name: str, sql: str) -> None:
        """ClickHouse 파라미터화 문법 ``{name:Type}`` 이 적어도 1회 존재."""
        # LATENCY_STATS_QUERY 등은 다수 파라미터를 가진다.
        assert _PARAM_RE.search(sql), (
            f"{name}: parameterized syntax {{name:Type}} not found"
        )

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
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

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_validate_sql_passes(self, name: str, sql: str) -> None:
        """프로덕션 검증기를 통과해야 한다."""
        try:
            _validate_sql(sql)
        except ClickHouseSecurityError as exc:
            pytest.fail(f"{name}: validator rejected query — {exc}")

    @pytest.mark.parametrize(
        "name,sql", ALL_QUERIES, ids=[n for n, _ in ALL_QUERIES]
    )
    def test_ensure_limit_idempotent(self, name: str, sql: str) -> None:
        """이미 LIMIT가 있어도 자동 LIMIT가 추가되지 않는다."""
        result = _ensure_limit(sql)
        # LIMIT가 1번만 등장
        assert len(_LIMIT_RE.findall(result)) == 1, (
            f"{name}: LIMIT가 중복 추가됨"
        )


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
