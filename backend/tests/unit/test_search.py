"""검색 라우터 + 서비스 단위 테스트.

검증:
- ``validate_query`` 길이/문자 검증
- ``search`` 도메인별 매칭 (prompts/datasets/experiments)
- 점수 가중치 (exact > name > description)
- ``snippet`` 생성 (±40자)
- ``GET /api/v1/search`` 라우터 query 파라미터 검증
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_langfuse_client, get_redis_client
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.services.search_service import (
    _make_snippet,
    _score_match,
    search,
    validate_query,
)
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_redis import MockRedisClient


# ---------- validate_query ----------
@pytest.mark.unit
class TestValidateQuery:
    """``q`` 표현식 검증 — API_DESIGN.md §10.1."""

    def test_valid_simple_query(self) -> None:
        assert validate_query("sentiment") == "sentiment"

    def test_strips_whitespace(self) -> None:
        assert validate_query("  hello  world  ") == "hello world"

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_query("a")

    def test_too_long_raises(self) -> None:
        with pytest.raises(ValueError):
            validate_query("a" * 201)

    def test_wildcard_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_query("test*")

    def test_sql_quote_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_query("test'OR")

    def test_unicode_korean_allowed(self) -> None:
        """한국어는 허용."""
        assert validate_query("감성분석") == "감성분석"

    def test_dash_underscore_allowed(self) -> None:
        assert validate_query("model-v3_test") == "model-v3_test"


# ---------- 점수/스니펫 ----------
@pytest.mark.unit
class TestScoreMatch:
    """``_score_match`` 가중치."""

    def test_exact_match(self) -> None:
        assert _score_match("sentiment", None, "sentiment") == 1.0

    def test_name_substring(self) -> None:
        assert _score_match("sentiment-v3", None, "sentiment") == 0.8

    def test_description_match(self) -> None:
        assert _score_match("foo", "this is sentiment analysis", "sentiment") == 0.6

    def test_no_match(self) -> None:
        assert _score_match("foo", "bar", "sentiment") == 0.0

    def test_case_insensitive(self) -> None:
        assert _score_match("SENTIMENT", None, "sentiment") == 1.0


@pytest.mark.unit
class TestMakeSnippet:
    """``_make_snippet`` 생성."""

    def test_match_in_middle(self) -> None:
        text = "a" * 60 + "MATCH" + "b" * 60
        snippet = _make_snippet(text, "match")
        assert snippet is not None
        assert "MATCH" in snippet
        # 양 끝 ellipsis
        assert snippet.startswith("…")
        assert snippet.endswith("…")

    def test_no_match_returns_none(self) -> None:
        assert _make_snippet("hello world", "absent") is None

    def test_none_text(self) -> None:
        assert _make_snippet(None, "x") is None


# ---------- search 통합 (mock) ----------
@pytest.mark.unit
class TestSearchService:
    """``search`` 서비스 통합."""

    @pytest.fixture
    def seeded_langfuse(self) -> MockLangfuseClient:
        client = MockLangfuseClient()
        client._seed(
            prompts=[
                {
                    "name": "sentiment-analysis",
                    "body": "Analyze sentiment of {{text}}",
                    "tags": ["감성분석"],
                },
                {
                    "name": "summarize",
                    "body": "Summarize {{document}}",
                    "tags": [],
                },
            ],
            datasets=[
                {
                    "name": "sentiment-golden-100",
                    "description": "골든셋 sentiment dataset",
                    "items": [],
                },
                {
                    "name": "qa-set",
                    "description": "QA pairs",
                    "items": [],
                },
            ],
        )
        return client

    async def test_search_prompts_only(
        self,
        seeded_langfuse: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        result = await search(
            query="sentiment",
            type_="prompts",
            project_id=None,
            limit=20,
            langfuse=seeded_langfuse,
            redis=redis_client,
        )
        assert result.query == "sentiment"
        assert len(result.results["prompts"]) == 1
        assert result.results["datasets"] == []
        assert result.results["experiments"] == []
        assert result.results["prompts"][0].name == "sentiment-analysis"

    async def test_search_all_returns_multiple_domains(
        self,
        seeded_langfuse: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        result = await search(
            query="sentiment",
            type_="all",
            project_id=None,
            limit=20,
            langfuse=seeded_langfuse,
            redis=redis_client,
        )
        assert len(result.results["prompts"]) >= 1
        assert len(result.results["datasets"]) >= 1
        assert result.total >= 2

    async def test_search_no_match(
        self,
        seeded_langfuse: MockLangfuseClient,
        redis_client: MockRedisClient,
    ) -> None:
        result = await search(
            query="nonexistent-keyword",
            type_="all",
            project_id=None,
            limit=20,
            langfuse=seeded_langfuse,
            redis=redis_client,
        )
        assert result.total == 0

    async def test_search_limit_respected(
        self,
        redis_client: MockRedisClient,
    ) -> None:
        client = MockLangfuseClient()
        # 30개 데이터셋 시드 — 모두 'test'를 이름에 포함
        client._seed(
            datasets=[
                {"name": f"test-ds-{i:03d}", "description": "test data", "items": []}
                for i in range(30)
            ],
        )
        result = await search(
            query="test",
            type_="datasets",
            project_id=None,
            limit=5,
            langfuse=client,
            redis=redis_client,
        )
        assert len(result.results["datasets"]) == 5

    async def test_search_results_sorted_by_score(
        self,
        redis_client: MockRedisClient,
    ) -> None:
        """exact match가 partial match보다 먼저."""
        client = MockLangfuseClient()
        client._seed(
            datasets=[
                {"name": "sentiment-extra", "description": None, "items": []},
                {"name": "sentiment", "description": None, "items": []},
            ],
        )
        result = await search(
            query="sentiment",
            type_="datasets",
            project_id=None,
            limit=20,
            langfuse=client,
            redis=redis_client,
        )
        names = [r.name for r in result.results["datasets"]]
        assert names[0] == "sentiment"


# ---------- 라우터 통합 ----------
@pytest.fixture
def viewer_user() -> User:
    return User(id="user-1", email="v@x.com", role="viewer")


@pytest.fixture
def search_app(viewer_user: User, redis_client: MockRedisClient) -> TestClient:
    """search 라우터 + mock 의존성."""
    langfuse = MockLangfuseClient()
    langfuse._seed(
        prompts=[{"name": "sentiment-test", "body": "Analyze sentiment"}],
    )
    app = create_app()
    app.dependency_overrides[get_langfuse_client] = lambda: langfuse
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    return TestClient(app)


@pytest.mark.unit
class TestSearchEndpoint:
    """``GET /api/v1/search``."""

    def test_basic_search(self, search_app: TestClient) -> None:
        resp = search_app.get("/api/v1/search?q=sentiment")
        assert resp.status_code == 200
        body = resp.json()
        assert body["query"] == "sentiment"
        assert "prompts" in body["results"]

    def test_query_too_short_returns_422(self, search_app: TestClient) -> None:
        """``q``가 1자면 FastAPI Query validator가 422."""
        resp = search_app.get("/api/v1/search?q=a")
        assert resp.status_code == 422

    def test_invalid_type_returns_422(self, search_app: TestClient) -> None:
        resp = search_app.get("/api/v1/search?q=test&type=invalid")
        assert resp.status_code == 422

    def test_wildcard_returns_422(self, search_app: TestClient) -> None:
        resp = search_app.get("/api/v1/search?q=test%2A")  # *
        assert resp.status_code == 422

    def test_unauthenticated_request_rejected(self, redis_client: MockRedisClient) -> None:
        app = create_app()
        app.dependency_overrides[get_langfuse_client] = lambda: MockLangfuseClient()
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        client = TestClient(app)
        resp = client.get("/api/v1/search?q=test")
        assert resp.status_code == 401
