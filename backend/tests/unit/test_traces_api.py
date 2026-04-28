"""``app/api/v1/traces.py`` 라우터 단위 테스트.

검증:
- POST /api/v1/traces/search (페이지네이션, 필터, viewer+)
- GET  /api/v1/traces/{id} (200 / 404 / project_id query 검증)
- POST /api/v1/traces/{id}/score (user+ 권한, 201)
- 401 (토큰 없음), 403 (viewer 가 score 부여 시도)
- include_observations=True 거부
- 라우터 등록 확인
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_langfuse_client, get_trace_fetcher
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.models.trace import (
    TraceObservation,
    TraceSummary,
    TraceTree,
)
from app.services.trace_fetcher import TraceNotFoundError
from tests.fixtures.mock_langfuse import MockLangfuseClient

_BASE_TIME = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


# ---------- 가짜 fetcher ----------
class FakeTraceFetcher:
    """``TraceFetcher`` interface-compatible mock — DI 주입용."""

    def __init__(self) -> None:
        self.search_result: tuple[list[TraceSummary], int] = ([], 0)
        self.get_result: TraceTree | None = None
        self.search_calls: list[Any] = []
        self.get_calls: list[tuple[str, str]] = []
        self.raise_not_found: bool = False

    async def search(self, filter_: Any) -> tuple[list[TraceSummary], int]:
        self.search_calls.append(filter_)
        return self.search_result

    async def get(self, trace_id: str, project_id: str) -> TraceTree:
        self.get_calls.append((trace_id, project_id))
        if self.raise_not_found:
            raise TraceNotFoundError(detail=f"trace {trace_id!r} not found")
        if self.get_result is None:
            raise TraceNotFoundError(detail=f"trace {trace_id!r} not found")
        return self.get_result


# ---------- 공통 fixture ----------
@pytest.fixture
def viewer_user() -> User:
    return User(id="viewer-1", email="v@x.com", role="viewer")


@pytest.fixture
def regular_user() -> User:
    return User(id="user-1", email="u@x.com", role="user")


@pytest.fixture
def fake_fetcher() -> FakeTraceFetcher:
    return FakeTraceFetcher()


@pytest.fixture
def fake_langfuse() -> MockLangfuseClient:
    return MockLangfuseClient()


def _make_app(
    user: User,
    fetcher: FakeTraceFetcher,
    langfuse: MockLangfuseClient,
) -> Iterator[TestClient]:
    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_trace_fetcher] = lambda: fetcher
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
def viewer_app(
    viewer_user: User,
    fake_fetcher: FakeTraceFetcher,
    fake_langfuse: MockLangfuseClient,
) -> Iterator[TestClient]:
    yield from _make_app(viewer_user, fake_fetcher, fake_langfuse)


@pytest.fixture
def user_app(
    regular_user: User,
    fake_fetcher: FakeTraceFetcher,
    fake_langfuse: MockLangfuseClient,
) -> Iterator[TestClient]:
    yield from _make_app(regular_user, fake_fetcher, fake_langfuse)


# ===================================================================
# 1) POST /api/v1/traces/search
# ===================================================================
@pytest.mark.unit
class TestSearchEndpoint:
    """``POST /api/v1/traces/search``."""

    def _summaries(self, n: int) -> list[TraceSummary]:
        return [
            TraceSummary(
                id=f"t{i}",
                name="agent",
                tags=["alpha"],
                timestamp=_BASE_TIME + timedelta(seconds=i),
                observation_count=2,
            )
            for i in range(n)
        ]

    def test_basic_200(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.search_result = (self._summaries(5), 5)
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {"project_id": "proj-1"},
                "page": 1,
                "page_size": 10,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 5
        assert body["page"] == 1
        assert body["page_size"] == 10
        assert len(body["items"]) == 5

    def test_pagination_slices(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.search_result = (self._summaries(25), 25)
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {"project_id": "proj-1"},
                "page": 2,
                "page_size": 10,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["page"] == 2
        assert len(body["items"]) == 10
        assert body["items"][0]["id"] == "t10"

    def test_filter_passes_through(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.search_result = ([], 0)
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {
                    "project_id": "proj-1",
                    "name": "qa-agent",
                    "tags": ["alpha", "beta"],
                    "user_ids": ["u1"],
                    "session_ids": ["s1"],
                    "from_timestamp": _BASE_TIME.isoformat(),
                    "to_timestamp": (_BASE_TIME + timedelta(hours=1)).isoformat(),
                    "sample_size": 100,
                    "sample_strategy": "first",
                },
            },
        )
        assert resp.status_code == 200, resp.text
        assert len(fake_fetcher.search_calls) == 1
        f = fake_fetcher.search_calls[0]
        assert f.project_id == "proj-1"
        assert f.name == "qa-agent"
        assert f.tags == ["alpha", "beta"]
        assert f.sample_size == 100
        assert f.sample_strategy == "first"

    def test_page_size_too_high_422(self, viewer_app: TestClient) -> None:
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {"project_id": "proj-1"},
                "page_size": 500,
            },
        )
        assert resp.status_code == 422

    def test_missing_project_id_422(self, viewer_app: TestClient) -> None:
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={"filter": {}},
        )
        assert resp.status_code == 422

    def test_extra_field_rejected(self, viewer_app: TestClient) -> None:
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {"project_id": "proj-1"},
                "unknown_field": True,
            },
        )
        assert resp.status_code == 422

    def test_include_observations_rejected(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.search_result = ([], 0)
        resp = viewer_app.post(
            "/api/v1/traces/search",
            json={
                "filter": {"project_id": "proj-1"},
                "include_observations": True,
            },
        )
        assert resp.status_code == 400


# ===================================================================
# 2) GET /api/v1/traces/{id}
# ===================================================================
@pytest.mark.unit
class TestGetTraceEndpoint:
    """``GET /api/v1/traces/{id}``."""

    def _make_tree(self, trace_id: str = "t1") -> TraceTree:
        return TraceTree(
            id=trace_id,
            project_id="proj-1",
            name="qa-agent",
            input={"q": "hi"},
            output="answer",
            timestamp=_BASE_TIME,
            observations=[
                TraceObservation(
                    id="o1",
                    type="span",
                    name="retrieve",
                    start_time=_BASE_TIME,
                ),
                TraceObservation(
                    id="o2",
                    type="generation",
                    name="llm",
                    start_time=_BASE_TIME + timedelta(seconds=1),
                ),
            ],
            scores=[{"id": "sc1", "name": "acc", "value": 0.9}],
            total_cost_usd=0.001,
            total_latency_ms=1000.0,
        )

    def test_get_200(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.get_result = self._make_tree()
        resp = viewer_app.get(
            "/api/v1/traces/t1",
            params={"project_id": "proj-1"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["id"] == "t1"
        assert body["name"] == "qa-agent"
        assert len(body["observations"]) == 2
        assert body["observations"][0]["type"] == "span"
        assert body["scores"][0]["name"] == "acc"
        assert fake_fetcher.get_calls == [("t1", "proj-1")]

    def test_get_404(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.raise_not_found = True
        resp = viewer_app.get(
            "/api/v1/traces/missing",
            params={"project_id": "proj-1"},
        )
        assert resp.status_code == 404

    def test_get_missing_project_id_422(
        self,
        viewer_app: TestClient,
        fake_fetcher: FakeTraceFetcher,
    ) -> None:
        fake_fetcher.get_result = self._make_tree()
        resp = viewer_app.get("/api/v1/traces/t1")
        assert resp.status_code == 422


# ===================================================================
# 3) POST /api/v1/traces/{id}/score
# ===================================================================
@pytest.mark.unit
class TestAddScoreEndpoint:
    """``POST /api/v1/traces/{id}/score``."""

    def test_score_201_user(
        self,
        user_app: TestClient,
        fake_langfuse: MockLangfuseClient,
    ) -> None:
        # MockLangfuseClient.score 는 trace 가 등록되어 있어야 한다 — 미리 trace 생성
        trace_id = fake_langfuse.create_trace(name="agent")
        resp = user_app.post(
            f"/api/v1/traces/{trace_id}/score",
            json={"name": "manual_review", "value": 0.85, "comment": "ok"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["trace_id"] == trace_id
        assert body["name"] == "manual_review"
        assert body["value"] == pytest.approx(0.85)
        assert body["score_id"]

    def test_score_403_viewer(
        self,
        viewer_app: TestClient,
        fake_langfuse: MockLangfuseClient,
    ) -> None:
        trace_id = fake_langfuse.create_trace(name="agent")
        resp = viewer_app.post(
            f"/api/v1/traces/{trace_id}/score",
            json={"name": "manual_review", "value": 0.5},
        )
        assert resp.status_code == 403

    def test_score_value_out_of_range_422(self, user_app: TestClient) -> None:
        resp = user_app.post(
            "/api/v1/traces/t1/score",
            json={"name": "manual_review", "value": 1.5},
        )
        assert resp.status_code == 422

    def test_score_negative_value_422(self, user_app: TestClient) -> None:
        resp = user_app.post(
            "/api/v1/traces/t1/score",
            json={"name": "manual_review", "value": -0.1},
        )
        assert resp.status_code == 422

    def test_score_missing_name_422(self, user_app: TestClient) -> None:
        resp = user_app.post(
            "/api/v1/traces/t1/score",
            json={"value": 0.5},
        )
        assert resp.status_code == 422

    def test_score_with_if_match_header_passes(
        self,
        user_app: TestClient,
        fake_langfuse: MockLangfuseClient,
    ) -> None:
        # If-Match 헤더는 현재 placeholder — 통과해야 한다
        trace_id = fake_langfuse.create_trace(name="agent")
        resp = user_app.post(
            f"/api/v1/traces/{trace_id}/score",
            json={"name": "manual", "value": 0.5},
            headers={"If-Match": '"some-etag"'},
        )
        assert resp.status_code == 201


# ===================================================================
# 4) 인증 필수 검증
# ===================================================================
@pytest.mark.unit
class TestAuthRequired:
    """토큰 없으면 401."""

    def test_search_unauthenticated(
        self, fake_fetcher: FakeTraceFetcher, fake_langfuse: MockLangfuseClient
    ) -> None:
        app = create_app()
        app.dependency_overrides[get_trace_fetcher] = lambda: fake_fetcher
        app.dependency_overrides[get_langfuse_client] = lambda: fake_langfuse
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/traces/search",
                json={"filter": {"project_id": "proj-1"}},
            )
            assert resp.status_code == 401
        app.dependency_overrides.clear()

    def test_get_unauthenticated(
        self, fake_fetcher: FakeTraceFetcher, fake_langfuse: MockLangfuseClient
    ) -> None:
        app = create_app()
        app.dependency_overrides[get_trace_fetcher] = lambda: fake_fetcher
        app.dependency_overrides[get_langfuse_client] = lambda: fake_langfuse
        with TestClient(app) as client:
            resp = client.get("/api/v1/traces/t1", params={"project_id": "proj-1"})
            assert resp.status_code == 401
        app.dependency_overrides.clear()

    def test_score_unauthenticated(
        self, fake_fetcher: FakeTraceFetcher, fake_langfuse: MockLangfuseClient
    ) -> None:
        app = create_app()
        app.dependency_overrides[get_trace_fetcher] = lambda: fake_fetcher
        app.dependency_overrides[get_langfuse_client] = lambda: fake_langfuse
        with TestClient(app) as client:
            resp = client.post(
                "/api/v1/traces/t1/score",
                json={"name": "x", "value": 0.5},
            )
            assert resp.status_code == 401
        app.dependency_overrides.clear()


# ===================================================================
# 5) 라우터 등록
# ===================================================================
@pytest.mark.unit
class TestRouterRegistration:
    """3개 trace 엔드포인트가 등록되어야 한다."""

    def test_paths_registered(self) -> None:
        from app.main import app

        paths = {r.path for r in app.routes}  # type: ignore[attr-defined]
        expected = {
            "/api/v1/traces/search",
            "/api/v1/traces/{trace_id}",
            "/api/v1/traces/{trace_id}/score",
        }
        missing = expected - paths
        assert not missing, f"missing paths: {missing}"
