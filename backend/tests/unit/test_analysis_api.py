"""분석 API 라우터 단위 테스트.

검증:
- 5개 엔드포인트 200 OK
- viewer 권한 통과 (모든 엔드포인트가 viewer+)
- 요청 검증 (run_names 2~5개, bins 2~50)
- ClickHouse 미설정 시 503 응답
- POST 본문 / GET query 파라미터 처리
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_analysis_service
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.services.analysis_service import AnalysisService
from tests.fixtures.mock_clickhouse import MockClickHouseClient


# ---------- 공통 픽스처 ----------
@pytest.fixture
def viewer_user() -> User:
    return User(id="user-viewer-1", email="v@x.com", role="viewer")


@pytest.fixture
def admin_user() -> User:
    return User(id="user-admin-1", email="a@x.com", role="admin")


@pytest.fixture
def mock_clickhouse() -> MockClickHouseClient:
    return MockClickHouseClient()


@pytest.fixture
def analysis_app(
    viewer_user: User, mock_clickhouse: MockClickHouseClient
) -> Iterator[TestClient]:
    """분석 라우터 + mock ClickHouse + viewer 토큰."""
    app = create_app()
    service = AnalysisService(clickhouse=mock_clickhouse)  # type: ignore[arg-type]
    app.dependency_overrides[get_analysis_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def _seed_compare_runs(mock: MockClickHouseClient) -> None:
    mock.register_response(
        r"FROM\s+traces\s+AS\s+t\s+LEFT\s+JOIN\s+observations[\s\S]*GROUP\s+BY\s+t\.name\s+ORDER",
        [
            {
                "run_name": "run_a",
                "avg_latency_ms": 100.0,
                "p50_latency_ms": 95.0,
                "p90_latency_ms": 180.0,
                "p99_latency_ms": 220.0,
                "total_cost_usd": 1.0,
                "avg_total_tokens": 500.0,
                "avg_score": 0.85,
                "items_completed": 30,
            },
            {
                "run_name": "run_b",
                "avg_latency_ms": 80.0,
                "p50_latency_ms": 75.0,
                "p90_latency_ms": 150.0,
                "p99_latency_ms": 180.0,
                "total_cost_usd": 0.7,
                "avg_total_tokens": 480.0,
                "avg_score": 0.78,
                "items_completed": 30,
            },
        ],
    )
    mock.register_response(
        r"INNER\s+JOIN\s+scores\s+AS\s+s[\s\S]*GROUP\s+BY\s+t\.name,\s+s\.name",
        [
            {"run_name": "run_a", "score_name": "accuracy", "avg_value": 0.85},
            {"run_name": "run_b", "score_name": "accuracy", "avg_value": 0.78},
        ],
    )


# ===================================================================
# 1) POST /analysis/compare
# ===================================================================
@pytest.mark.unit
class TestCompareRunsEndpoint:
    """``POST /api/v1/analysis/compare``."""

    def test_basic_200(
        self,
        analysis_app: TestClient,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        _seed_compare_runs(mock_clickhouse)
        resp = analysis_app.post(
            "/api/v1/analysis/compare",
            json={"project_id": "proj-1", "run_names": ["run_a", "run_b"]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["project_id"] == "proj-1"
        assert len(body["runs"]) == 2
        assert "accuracy" in body["scores"]
        assert body["scores"]["accuracy"]["run_a"] == pytest.approx(0.85)

    def test_run_names_too_few(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare",
            json={"project_id": "p", "run_names": ["only_one"]},
        )
        assert resp.status_code == 422

    def test_run_names_too_many(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare",
            json={
                "project_id": "p",
                "run_names": ["a", "b", "c", "d", "e", "f"],
            },
        )
        assert resp.status_code == 422

    def test_missing_project_id(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare",
            json={"run_names": ["a", "b"]},
        )
        assert resp.status_code == 422

    def test_extra_field_rejected(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare",
            json={
                "project_id": "p",
                "run_names": ["a", "b"],
                "unknown_field": True,
            },
        )
        assert resp.status_code == 422


# ===================================================================
# 2) POST /analysis/compare/items
# ===================================================================
def _seed_compare_items(mock: MockClickHouseClient) -> None:
    mock.register_response(
        r"LEFT\s+JOIN\s+observations\s+AS\s+o[\s\S]*GROUP\s+BY\s+dri\.dataset_item_id",
        [
            {
                "dataset_item_id": "i1",
                "run_name": "run_a",
                "trace_id": "t1",
                "input": '{"q":"x"}',
                "expected": "ok",
                "output": "ok!",
                "latency_ms": 100.0,
                "cost_usd": 0.01,
            },
            {
                "dataset_item_id": "i1",
                "run_name": "run_b",
                "trace_id": "t2",
                "input": '{"q":"x"}',
                "expected": "ok",
                "output": "no",
                "latency_ms": 200.0,
                "cost_usd": 0.02,
            },
        ],
    )
    mock.register_response(
        r"INNER\s+JOIN\s+scores\s+AS\s+s[\s\S]*GROUP\s+BY\s+dri\.dataset_item_id",
        [
            {"dataset_item_id": "i1", "run_name": "run_a", "score_name": "acc", "value": 0.9},
            {"dataset_item_id": "i1", "run_name": "run_b", "score_name": "acc", "value": 0.3},
        ],
    )


@pytest.mark.unit
class TestCompareItemsEndpoint:
    """``POST /api/v1/analysis/compare/items``."""

    def test_basic_200(
        self,
        analysis_app: TestClient,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        _seed_compare_items(mock_clickhouse)
        resp = analysis_app.post(
            "/api/v1/analysis/compare/items",
            json={
                "project_id": "p",
                "run_names": ["run_a", "run_b"],
                "score_name": "acc",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["dataset_item_id"] == "i1"
        assert body["items"][0]["score_range"] == pytest.approx(0.6)

    def test_invalid_sort_by(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare/items",
            json={
                "project_id": "p",
                "run_names": ["a", "b"],
                "sort_by": "INVALID",
            },
        )
        assert resp.status_code == 422

    def test_page_size_limit(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.post(
            "/api/v1/analysis/compare/items",
            json={
                "project_id": "p",
                "run_names": ["a", "b"],
                "page_size": 9999,
            },
        )
        assert resp.status_code == 422


# ===================================================================
# 3) GET /analysis/scores/distribution
# ===================================================================
@pytest.mark.unit
class TestScoreDistributionEndpoint:
    """``GET /api/v1/analysis/scores/distribution``."""

    def test_basic_200(
        self,
        analysis_app: TestClient,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        mock_clickhouse.register_response(
            r"GROUP\s+BY\s+t\.name,\s+bin_index",
            [{"run_name": "run_a", "bin_index": 5, "sample_count": 10}],
        )
        mock_clickhouse.register_response(
            r"avg\(s\.value\)\s+AS\s+avg_value",
            [
                {
                    "run_name": "run_a",
                    "avg_value": 0.5,
                    "stddev_value": 0.1,
                    "min_value": 0.4,
                    "max_value": 0.6,
                    "sample_count": 10,
                }
            ],
        )

        resp = analysis_app.get(
            "/api/v1/analysis/scores/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "run_a"),
                ("run_names", "run_b"),
                ("score_name", "acc"),
                ("bins", 10),
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert len(body["bins"]) == 10
        assert body["bins"][5]["count"] == 10
        assert "run_a" in body["statistics"]

    def test_bins_too_low(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.get(
            "/api/v1/analysis/scores/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "a"),
                ("run_names", "b"),
                ("score_name", "acc"),
                ("bins", 1),
            ],
        )
        assert resp.status_code == 422

    def test_bins_too_high(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.get(
            "/api/v1/analysis/scores/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "a"),
                ("run_names", "b"),
                ("score_name", "acc"),
                ("bins", 100),
            ],
        )
        assert resp.status_code == 422

    def test_too_few_run_names(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.get(
            "/api/v1/analysis/scores/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "only_one"),
                ("score_name", "acc"),
            ],
        )
        assert resp.status_code == 422

    def test_too_many_run_names(
        self, analysis_app: TestClient
    ) -> None:
        resp = analysis_app.get(
            "/api/v1/analysis/scores/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "a"),
                ("run_names", "b"),
                ("run_names", "c"),
                ("run_names", "d"),
                ("run_names", "e"),
                ("run_names", "f"),
                ("score_name", "acc"),
            ],
        )
        assert resp.status_code == 422


# ===================================================================
# 4) GET /analysis/latency/distribution
# ===================================================================
@pytest.mark.unit
class TestLatencyDistributionEndpoint:
    """``GET /api/v1/analysis/latency/distribution``."""

    def test_basic_200(
        self,
        analysis_app: TestClient,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        mock_clickhouse.register_response(
            r"avg\(o\.latency\)\s+AS\s+avg_latency_ms",
            [
                {
                    "avg_latency_ms": 100.0,
                    "stddev_ms": 20.0,
                    "p50_ms": 95.0,
                    "p90_ms": 150.0,
                    "p99_ms": 200.0,
                    "max_ms": 250.0,
                    "sample_count": 50,
                }
            ],
        )
        mock_clickhouse.register_response(
            r"GROUP\s+BY\s+bin_index",
            [{"bin_index": 0, "sample_count": 20}],
        )

        resp = analysis_app.get(
            "/api/v1/analysis/latency/distribution",
            params={"project_id": "p", "run_name": "run_a", "bins": 10},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["run_name"] == "run_a"
        assert body["p50"] == 95.0
        assert body["p99"] == 200.0
        assert len(body["bins"]) == 10

    def test_missing_run_name(self, analysis_app: TestClient) -> None:
        resp = analysis_app.get(
            "/api/v1/analysis/latency/distribution",
            params={"project_id": "p", "bins": 10},
        )
        assert resp.status_code == 422


# ===================================================================
# 5) GET /analysis/cost/distribution
# ===================================================================
@pytest.mark.unit
class TestCostDistributionEndpoint:
    """``GET /api/v1/analysis/cost/distribution``."""

    def test_basic_200(
        self,
        analysis_app: TestClient,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        mock_clickhouse.register_response(
            r"sumIf",
            [
                {
                    "run_name": "run_a",
                    "model_cost": 0.8,
                    "eval_cost": 0.2,
                    "total_cost": 1.0,
                },
                {
                    "run_name": "run_b",
                    "model_cost": 0.5,
                    "eval_cost": 0.05,
                    "total_cost": 0.55,
                },
            ],
        )
        resp = analysis_app.get(
            "/api/v1/analysis/cost/distribution",
            params=[
                ("project_id", "p"),
                ("run_names", "run_a"),
                ("run_names", "run_b"),
            ],
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "run_a" in body["runs"]
        assert body["runs"]["run_a"]["model_cost"] == pytest.approx(0.8)
        assert body["runs"]["run_a"]["eval_cost"] == pytest.approx(0.2)
        assert body["runs"]["run_a"]["total_cost"] == pytest.approx(1.0)


# ===================================================================
# 6) ClickHouse 미설정 → 503
# ===================================================================
@pytest.mark.unit
class TestClickHouseUnavailable:
    """``app.state.clickhouse`` 가 None 일 때 503."""

    def test_503_when_clickhouse_none(
        self, viewer_user: User
    ) -> None:
        """``app.state.clickhouse=None`` 시 ``get_analysis_service`` 가 503 raise."""
        app = create_app()
        app.dependency_overrides[get_current_user] = lambda: viewer_user
        with TestClient(app) as client:
            # lifespan 진입 후 강제로 None 으로 덮어씌워 미설정 상황 시뮬레이션
            app.state.clickhouse = None
            resp = client.post(
                "/api/v1/analysis/compare",
                json={"project_id": "p", "run_names": ["a", "b"]},
            )
            assert resp.status_code == 503, resp.text
            body = resp.json()
            assert (
                "ClickHouse" in (body.get("detail") or "")
                or "ClickHouse" in (body.get("title") or "")
            )


# ===================================================================
# 7) 권한 — admin 도 통과
# ===================================================================
@pytest.mark.unit
class TestPermissions:
    """모든 엔드포인트는 viewer+ 이므로 admin/user/viewer 모두 통과."""

    def test_admin_passes(
        self,
        admin_user: User,
        mock_clickhouse: MockClickHouseClient,
    ) -> None:
        _seed_compare_runs(mock_clickhouse)
        app = create_app()
        service = AnalysisService(clickhouse=mock_clickhouse)  # type: ignore[arg-type]
        app.dependency_overrides[get_analysis_service] = lambda: service
        app.dependency_overrides[get_current_user] = lambda: admin_user
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/analysis/compare",
                json={"project_id": "p", "run_names": ["run_a", "run_b"]},
            )
            assert resp.status_code == 200

    def test_unauthenticated_blocked(
        self, mock_clickhouse: MockClickHouseClient
    ) -> None:
        """토큰 없으면 401."""
        _seed_compare_runs(mock_clickhouse)
        app = create_app()
        service = AnalysisService(clickhouse=mock_clickhouse)  # type: ignore[arg-type]
        app.dependency_overrides[get_analysis_service] = lambda: service
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/analysis/compare",
                json={"project_id": "p", "run_names": ["a", "b"]},
            )
            # 401 (Authorization 헤더 없음)
            assert resp.status_code == 401


# ===================================================================
# 8) 라우터 경로 등록 확인
# ===================================================================
@pytest.mark.unit
class TestRouterRegistration:
    """5개 엔드포인트가 모두 등록되어야 한다."""

    def test_all_paths_registered(self) -> None:
        from app.main import app

        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        expected = {
            "/api/v1/analysis/compare",
            "/api/v1/analysis/compare/items",
            "/api/v1/analysis/scores/distribution",
            "/api/v1/analysis/latency/distribution",
            "/api/v1/analysis/cost/distribution",
        }
        missing = expected - paths
        assert not missing, f"missing paths: {missing}"


# ---------- 미사용 import 차단 ----------
__all__: list[str] = []
_ = Any
