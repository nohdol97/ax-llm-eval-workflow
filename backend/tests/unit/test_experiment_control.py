"""ExperimentControl 단위 테스트.

검증 범위:
- 합법 상태 전이 (pause/resume/cancel/retry_failed)
- 비합법 상태 전이 → ``STATE_CONFLICT`` (409)
- 본인 외 사용자 → ``FORBIDDEN`` (403)
- 미존재 실험 → ``EXPERIMENT_NOT_FOUND`` (404)
- delete: running/paused 거부 / 그 외 OK
- ETag 계산 + If-Match 검증 (412)
- 동시성: 두 번째 동일 액션 → 두 번째 실패
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.experiment_control import (
    ExperimentControl,
    ExperimentETagMismatchError,
    ExperimentForbiddenError,
    ExperimentNotFoundError,
    ExperimentStateConflictError,
)
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
async def _seed_experiment(
    redis: MockRedisClient,
    experiment_id: str,
    *,
    status: str = "running",
    started_by: str = "user-1",
    project_id: str = "proj-a",
    runs: list[str] | None = None,
    extra: dict[str, str] | None = None,
) -> None:
    """``ax:experiment:{id}`` Hash + ``:runs`` Set 시드."""
    exp_key = f"ax:experiment:{experiment_id}"
    payload = {
        "name": f"exp-{experiment_id}",
        "status": status,
        "started_by": started_by,
        "owner_user_id": started_by,
        "project_id": project_id,
        "created_at": "2026-04-12T00:00:00.000000Z",
        "updated_at": "2026-04-12T00:00:00.000000Z",
        "total_items": "100",
        "completed_items": "0",
        "failed_items": "0",
        "total_cost_usd": "0",
        "total_runs": str(len(runs) if runs else 0),
    }
    if extra:
        payload.update(extra)
    await redis.hset(exp_key, mapping=payload)

    if runs:
        runs_key = f"ax:experiment:{experiment_id}:runs"
        for run_name in runs:
            await redis._client.sadd(runs_key, run_name)
            await redis.hset(
                f"ax:run:{experiment_id}:{run_name}",
                mapping={
                    "status": "running",
                    "model": "gpt-4o",
                    "prompt_version": "1",
                    "total_items": "10",
                    "completed_items": "0",
                    "failed_items": "0",
                    "total_cost_usd": "0",
                    "total_latency_ms": "0",
                    "total_score_sum": "0",
                    "scored_count": "0",
                },
            )


@pytest.fixture
def make_control(redis_client: MockRedisClient) -> Any:
    """``ExperimentControl(redis_client)`` 팩토리."""

    def _factory() -> ExperimentControl:
        return ExperimentControl(redis=redis_client)  # type: ignore[arg-type]

    return _factory


# ---------- 합법 전이 ----------
@pytest.mark.unit
class TestExperimentControlLegalTransitions:
    async def test_pause_running_to_paused(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "e1", status="running")
        control = make_control()
        new_status = await control.pause("e1", user_id="user-1")
        assert new_status == "paused"
        # Hash가 paused로 갱신되었는지 확인
        meta = await redis_client._client.hgetall("ax:experiment:e1")
        assert meta["status"] == "paused"
        assert "paused_at" in meta

    async def test_resume_paused_to_running(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(
            redis_client, "e2", status="paused", extra={"paused_at": "2026-04-12T00:01:00Z"}
        )
        control = make_control()
        new_status = await control.resume("e2", user_id="user-1")
        assert new_status == "running"
        meta = await redis_client._client.hgetall("ax:experiment:e2")
        assert meta["status"] == "running"
        # paused_at은 제거되어야 함
        assert "paused_at" not in meta

    async def test_cancel_running(self, redis_client: MockRedisClient, make_control: Any) -> None:
        await _seed_experiment(redis_client, "e3", status="running")
        control = make_control()
        new_status = await control.cancel("e3", user_id="user-1")
        assert new_status == "cancelled"

    async def test_cancel_paused(self, redis_client: MockRedisClient, make_control: Any) -> None:
        await _seed_experiment(redis_client, "e3p", status="paused")
        control = make_control()
        new_status = await control.cancel("e3p", user_id="user-1")
        assert new_status == "cancelled"

    async def test_cancel_pending(self, redis_client: MockRedisClient, make_control: Any) -> None:
        await _seed_experiment(redis_client, "e3pe", status="pending")
        control = make_control()
        new_status = await control.cancel("e3pe", user_id="user-1")
        assert new_status == "cancelled"

    async def test_retry_failed_completed_to_running(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "e4", status="completed")
        control = make_control()
        new_status = await control.retry_failed("e4", user_id="user-1")
        assert new_status == "running"

    async def test_retry_failed_failed_to_running(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(
            redis_client,
            "e5",
            status="failed",
            extra={"error_message": "boom", "completed_at": "2026-04-12T01:00:00Z"},
        )
        control = make_control()
        new_status = await control.retry_failed("e5", user_id="user-1")
        assert new_status == "running"
        meta = await redis_client._client.hgetall("ax:experiment:e5")
        assert "error_message" not in meta

    async def test_retry_failed_degraded_to_running(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "e6", status="degraded")
        control = make_control()
        new_status = await control.retry_failed("e6", user_id="user-1")
        assert new_status == "running"


# ---------- 비합법 전이 → 409 ----------
@pytest.mark.unit
class TestExperimentControlIllegalTransitions:
    @pytest.mark.parametrize(
        "current",
        ["paused", "completed", "failed", "cancelled", "pending", "queued"],
    )
    async def test_pause_only_from_running(
        self, redis_client: MockRedisClient, make_control: Any, current: str
    ) -> None:
        await _seed_experiment(redis_client, f"p-{current}", status=current)
        control = make_control()
        with pytest.raises(ExperimentStateConflictError):
            await control.pause(f"p-{current}", user_id="user-1")

    @pytest.mark.parametrize(
        "current",
        ["running", "completed", "failed", "cancelled", "pending", "queued"],
    )
    async def test_resume_only_from_paused(
        self, redis_client: MockRedisClient, make_control: Any, current: str
    ) -> None:
        await _seed_experiment(redis_client, f"r-{current}", status=current)
        control = make_control()
        with pytest.raises(ExperimentStateConflictError):
            await control.resume(f"r-{current}", user_id="user-1")

    @pytest.mark.parametrize(
        "current",
        ["completed", "failed", "cancelled"],
    )
    async def test_cancel_terminal_state_rejected(
        self, redis_client: MockRedisClient, make_control: Any, current: str
    ) -> None:
        await _seed_experiment(redis_client, f"c-{current}", status=current)
        control = make_control()
        with pytest.raises(ExperimentStateConflictError):
            await control.cancel(f"c-{current}", user_id="user-1")

    @pytest.mark.parametrize(
        "current",
        ["running", "paused", "pending", "queued", "cancelled"],
    )
    async def test_retry_failed_only_from_completed_failed_degraded(
        self, redis_client: MockRedisClient, make_control: Any, current: str
    ) -> None:
        await _seed_experiment(redis_client, f"rf-{current}", status=current)
        control = make_control()
        with pytest.raises(ExperimentStateConflictError):
            await control.retry_failed(f"rf-{current}", user_id="user-1")


# ---------- not found / forbidden ----------
@pytest.mark.unit
class TestExperimentControlAccess:
    async def test_pause_not_found(self, redis_client: MockRedisClient, make_control: Any) -> None:
        control = make_control()
        with pytest.raises(ExperimentNotFoundError):
            await control.pause("nope", user_id="user-1")

    async def test_pause_forbidden_for_other_user(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "e-priv", status="running", started_by="alice")
        control = make_control()
        with pytest.raises(ExperimentForbiddenError):
            await control.pause("e-priv", user_id="bob")


# ---------- delete ----------
@pytest.mark.unit
class TestExperimentControlDelete:
    async def test_delete_completed(self, redis_client: MockRedisClient, make_control: Any) -> None:
        await _seed_experiment(redis_client, "d1", status="completed", runs=["r1", "r2"])
        # ZSet 멤버도 시드
        await redis_client._client.zadd("ax:project:proj-a:experiments", {"d1": 1.0})
        control = make_control()
        await control.delete("d1", user_id="admin-1")
        # 모든 키가 삭제됐는지 검증
        assert await redis_client._client.exists("ax:experiment:d1") == 0
        assert await redis_client._client.exists("ax:experiment:d1:runs") == 0
        assert await redis_client._client.exists("ax:run:d1:r1") == 0
        assert await redis_client._client.exists("ax:run:d1:r2") == 0
        assert await redis_client._client.zscore("ax:project:proj-a:experiments", "d1") is None

    async def test_delete_cancelled_ok(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "d2", status="cancelled")
        control = make_control()
        await control.delete("d2", user_id="admin-1")
        assert await redis_client._client.exists("ax:experiment:d2") == 0

    async def test_delete_failed_ok(self, redis_client: MockRedisClient, make_control: Any) -> None:
        await _seed_experiment(redis_client, "d3", status="failed")
        control = make_control()
        await control.delete("d3", user_id="admin-1")
        assert await redis_client._client.exists("ax:experiment:d3") == 0

    @pytest.mark.parametrize("blocked", ["running", "paused"])
    async def test_delete_blocked_for_active(
        self, redis_client: MockRedisClient, make_control: Any, blocked: str
    ) -> None:
        await _seed_experiment(redis_client, f"d-{blocked}", status=blocked)
        control = make_control()
        with pytest.raises(ExperimentStateConflictError):
            await control.delete(f"d-{blocked}", user_id="admin-1")
        # Hash가 살아있는지 확인 (삭제 거부 후 보존)
        assert await redis_client._client.exists(f"ax:experiment:d-{blocked}") == 1

    async def test_delete_not_found(self, redis_client: MockRedisClient, make_control: Any) -> None:
        control = make_control()
        with pytest.raises(ExperimentNotFoundError):
            await control.delete("nope", user_id="admin-1")


# ---------- ETag / If-Match ----------
@pytest.mark.unit
class TestETagAndIfMatch:
    def test_compute_etag_changes_on_status(
        self,
    ) -> None:
        meta1 = {
            "status": "running",
            "updated_at": "2026-04-12T00:00:00Z",
        }
        meta2 = {
            "status": "paused",
            "updated_at": "2026-04-12T00:00:00Z",
        }
        assert ExperimentControl.compute_etag(meta1) != ExperimentControl.compute_etag(meta2)

    def test_compute_etag_stable_for_same_input(
        self,
    ) -> None:
        meta = {
            "status": "running",
            "updated_at": "2026-04-12T00:00:00Z",
        }
        assert ExperimentControl.compute_etag(meta) == ExperimentControl.compute_etag(meta)

    def test_verify_if_match_pass_on_match(self) -> None:
        etag = '"deadbeef0000cafe"'
        ExperimentControl.verify_if_match(etag, etag)  # 통과

    def test_verify_if_match_pass_on_wildcard(self) -> None:
        etag = '"deadbeef0000cafe"'
        ExperimentControl.verify_if_match("*", etag)

    def test_verify_if_match_skip_when_none(self) -> None:
        etag = '"deadbeef0000cafe"'
        ExperimentControl.verify_if_match(None, etag)

    def test_verify_if_match_raise_on_mismatch(self) -> None:
        with pytest.raises(ExperimentETagMismatchError):
            ExperimentControl.verify_if_match('"oldetag"', '"newetag"')


# ---------- 동시성 시뮬레이션 ----------
@pytest.mark.unit
class TestConcurrencyRace:
    async def test_double_pause_second_fails(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        """첫 번째 pause는 성공, 두 번째는 STATE_CONFLICT.

        Lua의 원자성 덕분에 첫 호출 후 status가 'paused'로 바뀌어 두 번째는
        ``STATE_CONFLICT:paused``가 된다.
        """
        await _seed_experiment(redis_client, "race-1", status="running")
        control = make_control()
        first = await control.pause("race-1", user_id="user-1")
        assert first == "paused"
        with pytest.raises(ExperimentStateConflictError):
            await control.pause("race-1", user_id="user-1")

    async def test_double_cancel_second_fails(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        await _seed_experiment(redis_client, "race-2", status="running")
        control = make_control()
        first = await control.cancel("race-2", user_id="user-1")
        assert first == "cancelled"
        with pytest.raises(ExperimentStateConflictError):
            await control.cancel("race-2", user_id="user-1")


# ---------- 종료 상태 후 TTL 단축 검증 ----------
@pytest.mark.unit
class TestTerminalTTLShortening:
    async def test_cancel_shortens_ttl(
        self, redis_client: MockRedisClient, make_control: Any
    ) -> None:
        """``cancel`` 후 Hash + Run Set + Run Hash TTL이 1시간으로 갱신된다."""
        await _seed_experiment(redis_client, "ttl-1", status="running", runs=["r1"])
        # 사전에 TTL을 24h(86400)으로 박아두기
        await redis_client._client.expire("ax:experiment:ttl-1", 86400)
        await redis_client._client.expire("ax:experiment:ttl-1:runs", 86400)
        await redis_client._client.expire("ax:run:ttl-1:r1", 86400)

        control = make_control()
        await control.cancel("ttl-1", user_id="user-1")

        # 종료 상태 → TTL 1시간(3600)으로 단축
        ttl_exp = await redis_client._client.ttl("ax:experiment:ttl-1")
        ttl_runs = await redis_client._client.ttl("ax:experiment:ttl-1:runs")
        ttl_run = await redis_client._client.ttl("ax:run:ttl-1:r1")
        assert 0 < ttl_exp <= 3600
        assert 0 < ttl_runs <= 3600
        assert 0 < ttl_run <= 3600
