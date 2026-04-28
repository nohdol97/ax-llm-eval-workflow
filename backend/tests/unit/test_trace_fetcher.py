"""``app.services.trace_fetcher`` 단위 테스트.

검증 대상:
- ClickHouse 직접 모드: search/get/get_many → row → TraceTree 변환
- observations 시간순 정렬 보장
- TraceNotFoundError 처리
- 필터 파라미터 빌드 (project_id, name, tags, user_ids, sessions, from/to)
- 샘플링 (random / first / stratified)
- TraceTree helper: find_observations / tool_calls / llm_calls
- 폴백 모드: list_traces / get_trace SDK 위임 (Mock Langfuse)
- 폴백 모드 SDK 미지원 시 TraceFetcherError
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.trace import TraceFilter, TraceObservation, TraceSummary, TraceTree
from app.services.trace_fetcher import (
    TraceFetcher,
    TraceFetcherError,
    TraceNotFoundError,
)
from tests.fixtures.mock_clickhouse import MockClickHouseClient
from tests.fixtures.mock_langfuse import MockLangfuseClient

# ---------- 공통 helper ----------

_BASE_TIME = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _trace_row(
    trace_id: str = "t1",
    name: str = "qa-agent",
    user_id: str | None = "u1",
    session_id: str | None = "s1",
    timestamp: datetime | None = None,
    cost: float = 0.0,
    latency_ms: float | None = None,
    obs_count: int = 0,
    tags: Any = None,
) -> dict[str, Any]:
    return {
        "id": trace_id,
        "name": name,
        "user_id": user_id,
        "session_id": session_id,
        "tags": tags if tags is not None else ["alpha"],
        "metadata": {"env": "dev"},
        "timestamp": timestamp or _BASE_TIME,
        "total_cost_usd": cost,
        "total_latency_ms": latency_ms,
        "observation_count": obs_count,
    }


def _trace_detail_row(
    trace_id: str = "t1",
    project_id: str = "proj-1",
    name: str = "qa-agent",
) -> dict[str, Any]:
    return {
        "id": trace_id,
        "name": name,
        "project_id": project_id,
        "input": '{"q": "hi"}',
        "output": "answer",
        "user_id": "u1",
        "session_id": "s1",
        "tags": ["alpha"],
        "metadata": '{"env": "dev"}',
        "timestamp": _BASE_TIME,
    }


def _obs_row(
    obs_id: str,
    obs_type: str,
    name: str,
    start_offset_ms: int,
    end_offset_ms: int | None,
    *,
    level: str = "DEFAULT",
    status: str | None = None,
    cost: float | None = None,
    model: str | None = None,
    usage: dict[str, int] | None = None,
    parent_id: str | None = None,
    obs_input: Any = None,
    obs_output: Any = None,
) -> dict[str, Any]:
    start = _BASE_TIME + timedelta(milliseconds=start_offset_ms)
    end = _BASE_TIME + timedelta(milliseconds=end_offset_ms) if end_offset_ms is not None else None
    latency = (end_offset_ms - start_offset_ms) if end_offset_ms is not None else None
    return {
        "id": obs_id,
        "type": obs_type,
        "name": name,
        "parent_observation_id": parent_id,
        "input": obs_input,
        "output": obs_output,
        "level": level,
        "status_message": status,
        "start_time": start,
        "end_time": end,
        "latency_ms": float(latency) if latency is not None else None,
        "model": model,
        "usage": usage,
        "cost_usd": cost,
        "metadata": {},
    }


@pytest.fixture
def fetcher_direct(
    clickhouse_client: MockClickHouseClient,
    langfuse_client: MockLangfuseClient,
) -> TraceFetcher:
    """ClickHouse 직접 모드 fetcher."""
    return TraceFetcher(
        clickhouse=clickhouse_client,  # type: ignore[arg-type]
        langfuse=langfuse_client,  # type: ignore[arg-type]
        use_fallback=False,
    )


@pytest.fixture
def fetcher_fallback(
    langfuse_client: MockLangfuseClient,
) -> TraceFetcher:
    """폴백 모드 fetcher (clickhouse=None)."""
    return TraceFetcher(
        clickhouse=None,
        langfuse=langfuse_client,  # type: ignore[arg-type]
        use_fallback=True,
    )


# ===================================================================
# 1) ClickHouse 직접 모드 — search
# ===================================================================
@pytest.mark.unit
class TestSearchViaClickHouse:
    """``TraceFetcher.search`` 직접 모드."""

    async def test_basic_search_returns_summaries_and_total(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(
            r"count\(DISTINCT\s+t\.id\)\s+AS\s+total",
            [{"total": 42}],
        )
        clickhouse_client.register_response(
            r"sum\(coalesce\(o\.calculated_total_cost",
            [
                _trace_row(trace_id="t1", obs_count=3, cost=0.01),
                _trace_row(trace_id="t2", obs_count=5, cost=0.02),
            ],
        )

        filter_ = TraceFilter(project_id="proj-1")
        summaries, total = await fetcher_direct.search(filter_)

        assert total == 42
        assert len(summaries) == 2
        assert summaries[0].id == "t1"
        assert summaries[0].observation_count == 3
        assert summaries[0].total_cost_usd == pytest.approx(0.01)
        assert summaries[1].id == "t2"

    async def test_filter_params_build(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(r"count\(DISTINCT", [{"total": 0}])
        clickhouse_client.register_response(r"sum\(coalesce", [])

        filter_ = TraceFilter(
            project_id="proj-1",
            name="qa-agent",
            tags=["t1", "t2"],
            user_ids=["u1"],
            session_ids=["s1", "s2"],
            from_timestamp=_BASE_TIME,
            to_timestamp=_BASE_TIME + timedelta(hours=1),
        )
        await fetcher_direct.search(filter_)

        executed = clickhouse_client._get_executed_queries()
        # 첫 번째 호출은 count 또는 search (gather 순서 보장 X) — 두 개 다 검사
        assert len(executed) == 2
        for _sql, params in executed:
            assert params["project_id"] == "proj-1"
            assert params["name"] == "qa-agent"
            assert params["tags"] == ["t1", "t2"]
            assert params["tags_count"] == 2
            assert params["user_ids"] == ["u1"]
            assert params["user_ids_count"] == 1
            assert params["session_ids_count"] == 2
            assert params["has_from"] == 1
            assert params["has_to"] == 1
            assert params["from_timestamp"] == _BASE_TIME

    async def test_empty_filter_uses_zero_flags(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(r"count\(DISTINCT", [{"total": 0}])
        clickhouse_client.register_response(r"sum\(coalesce", [])

        await fetcher_direct.search(TraceFilter(project_id="proj-1"))
        executed = clickhouse_client._get_executed_queries()
        for _sql, params in executed:
            assert params["name"] == ""
            assert params["tags"] == []
            assert params["tags_count"] == 0
            assert params["has_from"] == 0
            assert params["has_to"] == 0


# ===================================================================
# 2) ClickHouse 직접 모드 — 단건 조회
# ===================================================================
@pytest.mark.unit
class TestGetViaClickHouse:
    """``TraceFetcher.get`` 직접 모드."""

    async def test_get_returns_tree_with_sorted_observations(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(
            r"FROM\s+traces\s+AS\s+t\s+WHERE\s+t\.id\s*=",
            [_trace_detail_row()],
        )
        # 의도적으로 시간 역순으로 등록 — fetcher가 정렬해야 한다
        clickhouse_client.register_response(
            r"FROM\s+observations\s+AS\s+o\s+WHERE\s+o\.trace_id",
            [
                _obs_row("o2", "GENERATION", "llm_call", 200, 400, model="gpt-4o"),
                _obs_row("o1", "SPAN", "retrieve_context", 0, 100, cost=0.001),
                _obs_row("o3", "EVENT", "logged", 500, None),
            ],
        )
        clickhouse_client.register_response(
            r"FROM\s+scores\s+AS\s+s\s+WHERE\s+s\.trace_id",
            [
                {
                    "id": "sc1",
                    "name": "accuracy",
                    "value": 0.9,
                    "comment": "ok",
                    "created_at": _BASE_TIME,
                }
            ],
        )

        tree = await fetcher_direct.get("t1", "proj-1")

        assert isinstance(tree, TraceTree)
        assert tree.id == "t1"
        assert tree.project_id == "proj-1"
        assert tree.name == "qa-agent"
        # 정렬 확인 — start_time 오름차순
        assert [o.id for o in tree.observations] == ["o1", "o2", "o3"]
        # 타입 정규화
        assert tree.observations[0].type == "span"
        assert tree.observations[1].type == "generation"
        assert tree.observations[2].type == "event"
        # JSON input 파싱
        assert tree.input == {"q": "hi"}
        # metadata JSON 파싱
        assert tree.metadata == {"env": "dev"}
        # scores
        assert len(tree.scores) == 1
        assert tree.scores[0]["name"] == "accuracy"
        # aggregates
        assert tree.total_cost_usd == pytest.approx(0.001)
        # o1: 0~100, o2: 200~400, o3: 500~None — end 누락이라 max(end)=400
        assert tree.total_latency_ms == pytest.approx(400.0)

    async def test_get_raises_not_found(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        # 어느 것도 register 하지 않으면 빈 list 반환 → not found
        with pytest.raises(TraceNotFoundError):
            await fetcher_direct.get("missing", "proj-1")

    async def test_get_many_parallel(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(
            r"FROM\s+traces\s+AS\s+t\s+WHERE\s+t\.id",
            [_trace_detail_row()],  # 모든 trace_id에 동일 응답
        )
        clickhouse_client.register_response(r"FROM\s+observations", [])
        clickhouse_client.register_response(r"FROM\s+scores", [])

        trees = await fetcher_direct.get_many(["a", "b", "c"], "proj-1")
        assert len(trees) == 3
        # 모두 동일 mock 응답
        assert all(t.name == "qa-agent" for t in trees)


# ===================================================================
# 3) TraceTree helper
# ===================================================================
@pytest.mark.unit
class TestTraceTreeHelpers:
    """``TraceTree.find_observations`` / ``tool_calls`` / ``llm_calls``."""

    @pytest.fixture
    def sample_tree(self) -> TraceTree:
        observations = [
            TraceObservation(id="o1", type="span", name="retrieve", start_time=_BASE_TIME),
            TraceObservation(
                id="o2",
                type="generation",
                name="llm_call",
                start_time=_BASE_TIME + timedelta(seconds=1),
            ),
            TraceObservation(
                id="o3",
                type="span",
                name="retrieve",
                start_time=_BASE_TIME + timedelta(seconds=2),
            ),
            TraceObservation(
                id="o4",
                type="event",
                name="logged",
                start_time=_BASE_TIME + timedelta(seconds=3),
            ),
        ]
        return TraceTree(
            id="t1",
            project_id="p",
            name="agent",
            observations=observations,
            timestamp=_BASE_TIME,
        )

    def test_find_observations_by_name(self, sample_tree: TraceTree) -> None:
        result = sample_tree.find_observations(name="retrieve")
        assert [o.id for o in result] == ["o1", "o3"]

    def test_find_observations_by_type(self, sample_tree: TraceTree) -> None:
        result = sample_tree.find_observations(type="generation")
        assert [o.id for o in result] == ["o2"]

    def test_find_observations_by_name_and_type(self, sample_tree: TraceTree) -> None:
        result = sample_tree.find_observations(name="retrieve", type="span")
        assert [o.id for o in result] == ["o1", "o3"]

    def test_find_observations_no_filter_returns_empty(self, sample_tree: TraceTree) -> None:
        # 의도하지 않은 전체 노출 방지
        assert sample_tree.find_observations() == []

    def test_tool_calls_returns_only_spans(self, sample_tree: TraceTree) -> None:
        result = sample_tree.tool_calls()
        assert [o.id for o in result] == ["o1", "o3"]

    def test_llm_calls_returns_only_generations(self, sample_tree: TraceTree) -> None:
        result = sample_tree.llm_calls()
        assert [o.id for o in result] == ["o2"]


# ===================================================================
# 4) 샘플링
# ===================================================================
@pytest.mark.unit
class TestSampling:
    """``TraceFetcher._sample`` 전략별 동작."""

    @pytest.fixture
    def items(self) -> list[TraceSummary]:
        return [
            TraceSummary(
                id=f"t{i}",
                name="agent",
                tags=["A" if i < 5 else "B"],
                timestamp=_BASE_TIME + timedelta(seconds=i),
            )
            for i in range(10)
        ]

    def test_first_strategy(self, items: list[TraceSummary]) -> None:
        result = TraceFetcher._sample(items, 3, "first")
        assert [it.id for it in result] == ["t0", "t1", "t2"]

    def test_random_strategy_size(self, items: list[TraceSummary]) -> None:
        random.seed(42)
        result = TraceFetcher._sample(items, 4, "random")
        assert len(result) == 4
        # 모두 원본 items에 속함
        ids = {it.id for it in items}
        assert all(r.id in ids for r in result)

    def test_random_strategy_no_dupes(self, items: list[TraceSummary]) -> None:
        random.seed(7)
        result = TraceFetcher._sample(items, 5, "random")
        assert len({r.id for r in result}) == 5

    def test_stratified_balances_groups(self, items: list[TraceSummary]) -> None:
        random.seed(123)
        result = TraceFetcher._sample(items, 4, "stratified")
        assert len(result) == 4
        tags_a = sum(1 for r in result if r.tags == ["A"])
        tags_b = sum(1 for r in result if r.tags == ["B"])
        # 균등 분포 → 각 그룹 1개 이상
        assert tags_a >= 1
        assert tags_b >= 1

    def test_sample_size_larger_than_items(self, items: list[TraceSummary]) -> None:
        result = TraceFetcher._sample(items, 100, "random")
        assert len(result) == len(items)

    def test_search_applies_sampling(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        # 5건 응답하지만 sample_size=2
        clickhouse_client.register_response(r"count\(DISTINCT", [{"total": 5}])
        clickhouse_client.register_response(
            r"sum\(coalesce",
            [_trace_row(trace_id=f"t{i}", obs_count=1) for i in range(5)],
        )
        random.seed(0)
        filter_ = TraceFilter(
            project_id="proj-1",
            sample_size=2,
            sample_strategy="first",
        )

        async def _run() -> tuple[list[TraceSummary], int]:
            return await fetcher_direct.search(filter_)

        import asyncio

        summaries, total = asyncio.run(_run())
        assert len(summaries) == 2
        assert total == 2
        assert [s.id for s in summaries] == ["t0", "t1"]


# ===================================================================
# 5) 폴백 모드 (Langfuse SDK)
# ===================================================================
@pytest.mark.unit
class TestFallbackMode:
    """``USE_LANGFUSE_PUBLIC_API_FALLBACK=true`` 또는 clickhouse=None 인 경우."""

    async def test_fallback_search_uses_langfuse_list_traces(
        self,
        fetcher_fallback: TraceFetcher,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        # MockLangfuseClient에 list_traces 추가 (monkey patch — 폴백 검증)
        captured: dict[str, Any] = {}

        def fake_list_traces(**kwargs: Any) -> list[dict[str, Any]]:
            captured.update(kwargs)
            return [
                {
                    "id": "t1",
                    "name": "agent",
                    "userId": "u1",
                    "tags": ["alpha"],
                    "timestamp": _BASE_TIME.isoformat(),
                    "totalCost": 0.05,
                },
            ]

        langfuse_client.list_traces = fake_list_traces  # type: ignore[attr-defined]

        filter_ = TraceFilter(project_id="proj-1", name="agent")
        summaries, total = await fetcher_fallback.search(filter_)

        assert captured["project_id"] == "proj-1"
        assert captured["name"] == "agent"
        assert total == 1
        assert summaries[0].id == "t1"
        assert summaries[0].user_id == "u1"
        assert summaries[0].total_cost_usd == pytest.approx(0.05)

    async def test_fallback_get_uses_langfuse_get_trace(
        self,
        fetcher_fallback: TraceFetcher,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        def fake_get_trace(trace_id: str) -> dict[str, Any]:
            return {
                "id": trace_id,
                "name": "agent",
                "projectId": "proj-1",
                "input": {"q": "hi"},
                "output": "answer",
                "tags": ["alpha"],
                "metadata": {"env": "dev"},
                "timestamp": _BASE_TIME.isoformat(),
                "observations": [
                    {
                        "id": "o1",
                        "type": "SPAN",
                        "name": "retrieve",
                        "start_time": _BASE_TIME.isoformat(),
                        "end_time": (_BASE_TIME + timedelta(seconds=1)).isoformat(),
                        "level": "DEFAULT",
                    },
                    {
                        "id": "o2",
                        "type": "GENERATION",
                        "name": "llm",
                        "start_time": (_BASE_TIME + timedelta(seconds=2)).isoformat(),
                        "end_time": (_BASE_TIME + timedelta(seconds=3)).isoformat(),
                        "model": "gpt-4o",
                        "cost_usd": 0.001,
                    },
                ],
                "scores": [
                    {
                        "id": "s1",
                        "name": "acc",
                        "value": 0.9,
                        "created_at": _BASE_TIME.isoformat(),
                    }
                ],
            }

        langfuse_client.get_trace = fake_get_trace  # type: ignore[attr-defined]

        tree = await fetcher_fallback.get("t1", "proj-1")
        assert tree.id == "t1"
        assert tree.project_id == "proj-1"
        assert len(tree.observations) == 2
        # 정렬 검증
        assert [o.id for o in tree.observations] == ["o1", "o2"]
        assert tree.observations[1].type == "generation"
        assert tree.observations[1].model == "gpt-4o"
        assert len(tree.scores) == 1

    async def test_fallback_get_returns_404(
        self,
        fetcher_fallback: TraceFetcher,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        langfuse_client.get_trace = lambda _tid: None  # type: ignore[attr-defined]
        with pytest.raises(TraceNotFoundError):
            await fetcher_fallback.get("missing", "proj-1")

    async def test_fallback_search_without_sdk_method_raises(
        self,
        fetcher_fallback: TraceFetcher,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        # MockLangfuseClient 에는 list_traces 가 없다 — 명시 에러
        if hasattr(langfuse_client, "list_traces"):
            delattr(langfuse_client, "list_traces")
        with pytest.raises(TraceFetcherError):
            await fetcher_fallback.search(TraceFilter(project_id="proj-1"))

    async def test_fallback_get_without_sdk_method_raises(
        self,
        fetcher_fallback: TraceFetcher,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        if hasattr(langfuse_client, "get_trace"):
            delattr(langfuse_client, "get_trace")
        with pytest.raises(TraceFetcherError):
            await fetcher_fallback.get("t1", "proj-1")


# ===================================================================
# 6) 변환 helper edge cases
# ===================================================================
@pytest.mark.unit
class TestConversionEdgeCases:
    """row → 모델 변환의 비정상 입력 처리."""

    async def test_observation_with_unknown_level_defaults(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        clickhouse_client.register_response(r"FROM\s+traces", [_trace_detail_row()])
        clickhouse_client.register_response(
            r"FROM\s+observations",
            [
                _obs_row(
                    "o1",
                    "weird_type",
                    "thing",
                    0,
                    100,
                    level="UNKNOWN",
                )
            ],
        )
        clickhouse_client.register_response(r"FROM\s+scores", [])

        tree = await fetcher_direct.get("t1", "proj-1")
        # weird type → 기본 span / weird level → DEFAULT
        assert tree.observations[0].type == "span"
        assert tree.observations[0].level == "DEFAULT"

    async def test_string_input_remains_string(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        row = _trace_detail_row()
        row["input"] = "raw text query"
        clickhouse_client.register_response(r"FROM\s+traces", [row])
        clickhouse_client.register_response(r"FROM\s+observations", [])
        clickhouse_client.register_response(r"FROM\s+scores", [])

        tree = await fetcher_direct.get("t1", "proj-1")
        assert tree.input == "raw text query"

    async def test_metadata_invalid_json_defaults_to_empty(
        self,
        fetcher_direct: TraceFetcher,
        clickhouse_client: MockClickHouseClient,
    ) -> None:
        row = _trace_detail_row()
        row["metadata"] = "not-json {broken"
        clickhouse_client.register_response(r"FROM\s+traces", [row])
        clickhouse_client.register_response(r"FROM\s+observations", [])
        clickhouse_client.register_response(r"FROM\s+scores", [])

        tree = await fetcher_direct.get("t1", "proj-1")
        assert tree.metadata == {}
