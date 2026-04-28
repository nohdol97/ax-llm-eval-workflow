"""ExperimentQuery 단위 테스트.

검증 범위:
- 목록 조회 (페이지네이션, 상태 필터, 검색, 정렬)
- 상세 조회 (runs + config_snapshot 포함)
- 미존재 → 404
- 본인 외 사용자 (project_id 격리) → 404 (정보 노출 방지)
- admin은 본인 외 실험도 접근 가능
- progress 계산 (processed/total/percentage/eta_sec)
- Lazy cleanup: 만료된 ZSet 멤버 제거
"""

from __future__ import annotations

import gzip
import json
import time
from typing import Any

import pytest

from app.models.experiment import ExperimentDetail, ExperimentListResponse
from app.services.experiment_query import (
    ExperimentNotFoundError,
    ExperimentQuery,
)
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
async def _seed_experiment(
    redis: MockRedisClient,
    experiment_id: str,
    *,
    name: str = "exp",
    status: str = "running",
    project_id: str = "proj-a",
    started_by: str = "user-1",
    total_items: int = 100,
    completed_items: int = 25,
    failed_items: int = 5,
    total_runs: int = 1,
    total_cost_usd: float = 0.42,
    runs: list[dict[str, Any]] | None = None,
    created_at_iso: str | None = None,
    config: dict[str, Any] | None = None,
    config_blob_gzip: bytes | None = None,
) -> None:
    """``ax:experiment:{id}`` Hash + 보조 키 시드."""
    created_at = created_at_iso or "2026-04-12T00:00:00.000000Z"
    payload: dict[str, Any] = {
        "name": name,
        "description": f"desc-{experiment_id}",
        "status": status,
        "project_id": project_id,
        "started_by": started_by,
        "owner_user_id": started_by,
        "total_items": str(total_items),
        "completed_items": str(completed_items),
        "failed_items": str(failed_items),
        "total_cost_usd": str(total_cost_usd),
        "total_runs": str(total_runs),
        "created_at": created_at,
        "updated_at": created_at,
    }
    if config is not None:
        payload["config"] = json.dumps(config, ensure_ascii=False)

    await redis.hset(f"ax:experiment:{experiment_id}", mapping=payload)

    if config_blob_gzip is not None:
        # decode_responses=True 환경의 fakeredis는 str을 요구 — latin-1로 안전 패스스루
        await redis._client.set(
            f"ax:experiment:{experiment_id}:config_blob",
            config_blob_gzip.decode("latin-1"),
        )

    # ZSet 인덱스 — score=created_at_unix (테스트는 시간 단조 증가 가정)
    score = time.time()
    await redis._client.zadd(f"ax:project:{project_id}:experiments", {experiment_id: score})

    runs = runs or []
    runs_set_key = f"ax:experiment:{experiment_id}:runs"
    for run in runs:
        await redis._client.sadd(runs_set_key, run["run_name"])
        run_key = f"ax:run:{experiment_id}:{run['run_name']}"
        await redis.hset(
            run_key,
            mapping={
                "status": run.get("status", "completed"),
                "model": run.get("model", "gpt-4o"),
                "prompt_version": str(run.get("prompt_version", 1)),
                "total_items": str(run.get("total_items", 50)),
                "completed_items": str(run.get("completed_items", 50)),
                "failed_items": str(run.get("failed_items", 0)),
                "total_cost_usd": str(run.get("total_cost_usd", 0.21)),
                "total_latency_ms": str(run.get("total_latency_ms", 5000.0)),
                "total_score_sum": str(run.get("total_score_sum", 42.0)),
                "scored_count": str(run.get("scored_count", 50)),
            },
        )


@pytest.fixture
def make_query(redis_client: MockRedisClient) -> Any:
    """``ExperimentQuery(redis_client, langfuse=stub)`` 팩토리."""

    def _factory() -> ExperimentQuery:
        # langfuse는 본 단위 테스트 범위에서 호출되지 않음 — None 인자 회피용 stub.
        return ExperimentQuery(
            redis=redis_client,
            langfuse=object(),  # type: ignore[arg-type]
        )

    return _factory


# ---------- 상세 조회 ----------
@pytest.mark.unit
class TestGetExperiment:
    async def test_get_experiment_basic(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(
            redis_client,
            "e1",
            name="감성분석 v3 vs v4",
            runs=[
                {"run_name": "v3-gpt", "model": "gpt-4o", "prompt_version": 3},
                {"run_name": "v4-gpt", "model": "gpt-4o", "prompt_version": 4},
            ],
            config={"prompt_configs": [{"name": "x", "version": 3}]},
        )
        query = make_query()
        detail = await query.get_experiment("e1", user_id="user-1", user_role="user")

        assert isinstance(detail, ExperimentDetail)
        assert detail.experiment_id == "e1"
        assert detail.name == "감성분석 v3 vs v4"
        assert detail.status == "running"
        assert detail.project_id == "proj-a"
        assert detail.owner == "user-1"
        # progress
        assert detail.progress["total"] == 100
        assert detail.progress["completed"] == 25
        assert detail.progress["failed"] == 5
        assert detail.progress["processed"] == 30
        assert detail.progress["percentage"] == 30.0
        # runs (정렬은 _read_run_names에서 sorted())
        assert len(detail.runs) == 2
        run_names = {r.run_name for r in detail.runs}
        assert run_names == {"v3-gpt", "v4-gpt"}
        # avg_score = 42 / 50 = 0.84
        for run in detail.runs:
            assert run.avg_score == pytest.approx(0.84)
            assert run.avg_latency_ms == pytest.approx(100.0)  # 5000/50
        # config_snapshot
        assert detail.config_snapshot == {"prompt_configs": [{"name": "x", "version": 3}]}
        assert detail.evaluator_summary == {}

    async def test_get_experiment_not_found(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        query = make_query()
        with pytest.raises(ExperimentNotFoundError):
            await query.get_experiment("nope", user_id="user-1", user_role="user")

    async def test_get_experiment_other_user_returns_404(
        self,
        redis_client: MockRedisClient,
        make_query: Any,
    ) -> None:
        await _seed_experiment(redis_client, "e2", started_by="alice")
        query = make_query()
        with pytest.raises(ExperimentNotFoundError):
            await query.get_experiment("e2", user_id="bob", user_role="user")

    async def test_admin_can_see_other_users_experiment(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e3", started_by="alice")
        query = make_query()
        detail = await query.get_experiment("e3", user_id="admin-1", user_role="admin")
        assert detail.owner == "alice"

    async def test_get_experiment_project_filter_mismatch(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e4", project_id="proj-a", started_by="user-1")
        query = make_query()
        with pytest.raises(ExperimentNotFoundError):
            await query.get_experiment(
                "e4", user_id="user-1", user_role="user", project_id="proj-b"
            )

    async def test_config_snapshot_blob_fallback(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        """``config`` 미존재 + ``config_blob`` (gzip) 폴백 동작."""
        large = {"big": "x" * 100}
        gz = gzip.compress(json.dumps(large).encode("utf-8"))
        await _seed_experiment(
            redis_client,
            "e5",
            config=None,
            config_blob_gzip=gz,
        )
        query = make_query()
        detail = await query.get_experiment("e5", user_id="user-1", user_role="user")
        assert detail.config_snapshot == large

    async def test_progress_calculates_eta(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        # created_at을 충분히 과거로 두어 elapsed > 0 보장
        await _seed_experiment(
            redis_client,
            "e6",
            total_items=100,
            completed_items=20,
            failed_items=0,
            created_at_iso="2026-04-12T00:00:00.000000Z",
        )
        query = make_query()
        detail = await query.get_experiment("e6", user_id="user-1", user_role="user")
        # processed=20, total=100 — eta_sec는 양수여야 함 (시간 경과 기반)
        assert detail.progress["processed"] == 20
        assert detail.progress["percentage"] == 20.0
        assert detail.progress["eta_sec"] is not None
        assert detail.progress["eta_sec"] > 0


# ---------- 목록 조회 ----------
@pytest.mark.unit
class TestListExperiments:
    async def test_list_basic_returns_only_owners_experiments(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e1", started_by="user-1")
        await _seed_experiment(redis_client, "e2", started_by="user-1")
        await _seed_experiment(redis_client, "e3", started_by="alice")
        query = make_query()
        resp = await query.list_experiments(project_id="proj-a", user_id="user-1", user_role="user")
        assert isinstance(resp, ExperimentListResponse)
        assert resp.total == 2
        ids = {item.experiment_id for item in resp.items}
        assert ids == {"e1", "e2"}

    async def test_list_admin_sees_all(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e1", started_by="user-1")
        await _seed_experiment(redis_client, "e2", started_by="alice")
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-a", user_id="admin-1", user_role="admin"
        )
        assert resp.total == 2

    async def test_list_status_filter(self, redis_client: MockRedisClient, make_query: Any) -> None:
        await _seed_experiment(redis_client, "e1", status="running")
        await _seed_experiment(redis_client, "e2", status="completed")
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-a", user_id="user-1", user_role="user", status="completed"
        )
        assert resp.total == 1
        assert resp.items[0].experiment_id == "e2"

    async def test_list_search_by_name(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e1", name="감성분석 v3")
        await _seed_experiment(redis_client, "e2", name="요약 모델 비교")
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-a", user_id="user-1", user_role="user", search="감성"
        )
        assert resp.total == 1
        assert resp.items[0].experiment_id == "e1"

    async def test_list_pagination(self, redis_client: MockRedisClient, make_query: Any) -> None:
        for i in range(5):
            await _seed_experiment(redis_client, f"e{i}", name=f"exp-{i}")
        query = make_query()

        page1 = await query.list_experiments(
            project_id="proj-a",
            user_id="user-1",
            user_role="user",
            page=1,
            page_size=2,
        )
        assert page1.total == 5
        assert len(page1.items) == 2

        page2 = await query.list_experiments(
            project_id="proj-a",
            user_id="user-1",
            user_role="user",
            page=2,
            page_size=2,
        )
        assert len(page2.items) == 2
        # 페이지 1과 2의 교집합은 없어야 함
        ids1 = {item.experiment_id for item in page1.items}
        ids2 = {item.experiment_id for item in page2.items}
        assert ids1.isdisjoint(ids2)

    async def test_list_empty(self, redis_client: MockRedisClient, make_query: Any) -> None:
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-empty", user_id="user-1", user_role="user"
        )
        assert resp.total == 0
        assert resp.items == []

    async def test_list_lazy_cleanup_removes_expired(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        """ZSet에 expired_id가 있고 Hash가 없으면 ZREM으로 정리된다."""
        # 정상 1건 + 만료된 1건 (Hash 없음, ZSet에만 존재)
        await _seed_experiment(redis_client, "live-1")
        await redis_client._client.zadd("ax:project:proj-a:experiments", {"expired-1": time.time()})

        query = make_query()
        resp = await query.list_experiments(project_id="proj-a", user_id="user-1", user_role="user")
        assert resp.total == 1
        assert resp.items[0].experiment_id == "live-1"
        # ZSet에서 expired-1이 제거되었는지 확인
        score = await redis_client._client.zscore("ax:project:proj-a:experiments", "expired-1")
        assert score is None

    async def test_list_sort_order_desc_default(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        # 시간 단조 증가하도록 시드 — ZSet score가 시간순으로 정렬
        await _seed_experiment(redis_client, "first")
        await _seed_experiment(redis_client, "second")
        await _seed_experiment(redis_client, "third")
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-a", user_id="user-1", user_role="user", sort_order="desc"
        )
        # desc → 가장 최근(third)이 첫 번째
        assert resp.items[0].experiment_id == "third"
        assert resp.items[-1].experiment_id == "first"

    async def test_list_sort_order_asc(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "first")
        await _seed_experiment(redis_client, "second")
        query = make_query()
        resp = await query.list_experiments(
            project_id="proj-a", user_id="user-1", user_role="user", sort_order="asc"
        )
        assert resp.items[0].experiment_id == "first"


# ---------- progress 계산 단위 ----------
@pytest.mark.unit
class TestProgressCalculation:
    async def test_progress_zero_total(
        self, redis_client: MockRedisClient, make_query: Any
    ) -> None:
        await _seed_experiment(redis_client, "e0", total_items=0, completed_items=0, failed_items=0)
        query = make_query()
        detail = await query.get_experiment("e0", user_id="user-1", user_role="user")
        assert detail.progress["total"] == 0
        assert detail.progress["percentage"] == 0.0
        assert detail.progress["eta_sec"] is None

    async def test_progress_completed(self, redis_client: MockRedisClient, make_query: Any) -> None:
        await _seed_experiment(
            redis_client,
            "e1",
            status="completed",
            total_items=100,
            completed_items=95,
            failed_items=5,
        )
        query = make_query()
        detail = await query.get_experiment("e1", user_id="user-1", user_role="user")
        assert detail.progress["processed"] == 100
        assert detail.progress["percentage"] == 100.0
        # 100% 처리 → eta_sec는 None (total > processed 조건 미충족)
        assert detail.progress["eta_sec"] is None
