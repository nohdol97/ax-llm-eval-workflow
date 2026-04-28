"""AutoEvalRepo 단위 테스트 (Phase 8-B-1).

검증:
- create_policy / get_policy / list_policies (페이지네이션, status 필터)
- update_policy (schedule 변경 시 next_run_at 재계산)
- delete_policy (모든 인덱스 정리)
- pause_policy / resume_policy (active ZSet 추가/제거)
- create_run / get_run / update_run / list_runs
- get_latest_completed_run
- fetch_due_policies (시각 기반)
- record_cost / get_daily_cost (TTL 검증)
- get_cost_usage (기간 합산)
- _compute_next_run (cron / interval / event)
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.models.auto_eval import (
    AutoEvalPolicyCreate,
    AutoEvalPolicyUpdate,
    AutoEvalRun,
    AutoEvalSchedule,
)
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter
from app.services.auto_eval_repo import (
    AutoEvalPolicyNotFoundError,
    AutoEvalRepo,
    AutoEvalRunNotFoundError,
)
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
def make_filter(project_id: str = "proj-1") -> TraceFilter:
    return TraceFilter(project_id=project_id)


def make_evaluator(name: str = "ev1") -> EvaluatorConfig:
    return EvaluatorConfig(type="builtin", name=name, weight=1.0)


def make_create(
    name: str = "test-policy",
    project_id: str = "proj-1",
    schedule: AutoEvalSchedule | None = None,
) -> AutoEvalPolicyCreate:
    return AutoEvalPolicyCreate(
        name=name,
        project_id=project_id,
        trace_filter=make_filter(project_id),
        evaluators=[make_evaluator()],
        schedule=schedule or AutoEvalSchedule(type="interval", interval_seconds=3600),
    )


@pytest.fixture
def repo(redis_client: MockRedisClient) -> AutoEvalRepo:
    return AutoEvalRepo(redis_client)


# ---------- Policy CRUD ----------
@pytest.mark.unit
class TestPolicyCRUD:
    """create / get / list / update / delete."""

    async def test_create_policy_assigns_id_and_timestamps(self, repo: AutoEvalRepo) -> None:
        now = datetime(2026, 4, 26, tzinfo=UTC)
        policy = await repo.create_policy(make_create(), owner="user-1", now=now)
        assert policy.id.startswith("policy_")
        assert policy.owner == "user-1"
        assert policy.created_at == now
        assert policy.updated_at == now
        assert policy.next_run_at is not None
        # interval 3600 → 1시간 뒤
        assert policy.next_run_at == now + timedelta(seconds=3600)

    async def test_create_paused_has_no_next_run(self, repo: AutoEvalRepo) -> None:
        c = make_create()
        c2 = AutoEvalPolicyCreate(**{**c.model_dump(), "status": "paused"})
        policy = await repo.create_policy(c2, owner="user-1")
        assert policy.status == "paused"
        assert policy.next_run_at is None

    async def test_get_policy_round_trip(self, repo: AutoEvalRepo) -> None:
        created = await repo.create_policy(make_create(), owner="user-1")
        fetched = await repo.get_policy(created.id)
        assert fetched.id == created.id
        assert fetched.name == created.name
        assert fetched.owner == "user-1"

    async def test_get_policy_not_found(self, repo: AutoEvalRepo) -> None:
        with pytest.raises(AutoEvalPolicyNotFoundError):
            await repo.get_policy("policy_does_not_exist")

    async def test_list_policies_filtered_by_project(self, repo: AutoEvalRepo) -> None:
        await repo.create_policy(make_create(name="p1", project_id="proj-A"), owner="user-1")
        await repo.create_policy(make_create(name="p2", project_id="proj-A"), owner="user-1")
        await repo.create_policy(make_create(name="p3", project_id="proj-B"), owner="user-2")

        items, total = await repo.list_policies(project_id="proj-A")
        assert total == 2
        names = {p.name for p in items}
        assert names == {"p1", "p2"}

    async def test_list_policies_filtered_by_status(self, repo: AutoEvalRepo) -> None:
        active = await repo.create_policy(make_create(name="active"), owner="u1")
        paused = await repo.create_policy(make_create(name="paused"), owner="u1")
        await repo.pause_policy(paused.id)

        items, total = await repo.list_policies(status="active")
        assert total == 1
        assert items[0].id == active.id

        items, total = await repo.list_policies(status="paused")
        assert total == 1
        assert items[0].id == paused.id

    async def test_list_policies_pagination(self, repo: AutoEvalRepo) -> None:
        for i in range(5):
            await repo.create_policy(make_create(name=f"p{i}", project_id="proj-X"), owner="u1")
        items, total = await repo.list_policies(project_id="proj-X", page=1, page_size=2)
        assert total == 5
        assert len(items) == 2

        items_p2, _ = await repo.list_policies(project_id="proj-X", page=2, page_size=2)
        assert len(items_p2) == 2

        # page 3 — 1개 잔여
        items_p3, _ = await repo.list_policies(project_id="proj-X", page=3, page_size=2)
        assert len(items_p3) == 1

    async def test_update_policy_schedule_recomputes_next_run(self, repo: AutoEvalRepo) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        old_next = policy.next_run_at
        # schedule 변경: interval 60초로
        new_schedule = AutoEvalSchedule(type="interval", interval_seconds=60)
        updated = await repo.update_policy(policy.id, AutoEvalPolicyUpdate(schedule=new_schedule))
        assert updated.schedule.interval_seconds == 60
        assert updated.next_run_at is not None
        assert updated.next_run_at != old_next

    async def test_update_policy_partial_field(self, repo: AutoEvalRepo) -> None:
        policy = await repo.create_policy(make_create(name="orig"), owner="u1")
        updated = await repo.update_policy(policy.id, AutoEvalPolicyUpdate(name="renamed"))
        assert updated.name == "renamed"
        # 변경되지 않은 필드는 보존
        assert updated.owner == "u1"
        assert updated.schedule == policy.schedule

    async def test_update_policy_status_to_paused_clears_next_run(self, repo: AutoEvalRepo) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        assert policy.next_run_at is not None
        updated = await repo.update_policy(policy.id, AutoEvalPolicyUpdate(status="paused"))
        assert updated.status == "paused"
        assert updated.next_run_at is None

    async def test_delete_policy_removes_indexes(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        await repo.delete_policy(policy.id)

        # get 시 NotFound
        with pytest.raises(AutoEvalPolicyNotFoundError):
            await repo.get_policy(policy.id)

        # active ZSet 미포함
        active = await redis_client._client.zrange("ax:auto_eval_policies:active", 0, -1)
        assert policy.id not in [
            (r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in active
        ]


# ---------- Pause / Resume ----------
@pytest.mark.unit
class TestPauseResume:
    """pause → active ZSet 제거 / resume → 재등록."""

    async def test_pause_removes_from_active_zset(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        active_before = await redis_client._client.zrange("ax:auto_eval_policies:active", 0, -1)
        ids_before = [
            (r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in active_before
        ]
        assert policy.id in ids_before

        paused = await repo.pause_policy(policy.id)
        assert paused.status == "paused"

        active_after = await redis_client._client.zrange("ax:auto_eval_policies:active", 0, -1)
        ids_after = [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in active_after]
        assert policy.id not in ids_after

    async def test_resume_re_adds_to_active_zset(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        await repo.pause_policy(policy.id)
        resumed = await repo.resume_policy(policy.id)
        assert resumed.status == "active"
        assert resumed.next_run_at is not None

        active = await redis_client._client.zrange("ax:auto_eval_policies:active", 0, -1)
        ids = [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in active]
        assert policy.id in ids


# ---------- Run CRUD ----------
@pytest.mark.unit
class TestRunCRUD:
    """run create / get / update / list."""

    async def test_create_and_get_run(self, repo: AutoEvalRepo) -> None:
        run = AutoEvalRun(
            id="run_test1",
            policy_id="policy_p1",
            started_at=datetime.now(UTC),
            status="running",
        )
        await repo.create_run(run)
        fetched = await repo.get_run("run_test1")
        assert fetched.id == "run_test1"
        assert fetched.status == "running"

    async def test_get_run_not_found(self, repo: AutoEvalRepo) -> None:
        with pytest.raises(AutoEvalRunNotFoundError):
            await repo.get_run("run_nope")

    async def test_update_run_changes_status(self, repo: AutoEvalRepo) -> None:
        run = AutoEvalRun(
            id="run_test1",
            policy_id="policy_p1",
            started_at=datetime.now(UTC),
            status="running",
        )
        await repo.create_run(run)
        run.status = "completed"
        run.completed_at = datetime.now(UTC)
        run.avg_score = 0.85
        await repo.update_run(run)

        fetched = await repo.get_run("run_test1")
        assert fetched.status == "completed"
        assert fetched.avg_score == 0.85

    async def test_list_runs_by_policy_desc(self, repo: AutoEvalRepo) -> None:
        # 3개 run — 시각 다르게
        base = datetime(2026, 4, 26, 10, 0, tzinfo=UTC)
        for i in range(3):
            r = AutoEvalRun(
                id=f"run_{i}",
                policy_id="policy_p1",
                started_at=base + timedelta(minutes=i),
                status="completed",
            )
            await repo.create_run(r)

        items, total = await repo.list_runs("policy_p1")
        assert total == 3
        # desc order
        assert items[0].id == "run_2"
        assert items[2].id == "run_0"

    async def test_list_runs_status_filter(self, repo: AutoEvalRepo) -> None:
        await repo.create_run(
            AutoEvalRun(
                id="r1",
                policy_id="p1",
                started_at=datetime.now(UTC),
                status="completed",
            )
        )
        await repo.create_run(
            AutoEvalRun(
                id="r2",
                policy_id="p1",
                started_at=datetime.now(UTC) + timedelta(minutes=1),
                status="failed",
            )
        )
        completed_items, c_total = await repo.list_runs("p1", status="completed")
        assert c_total == 1
        assert completed_items[0].id == "r1"

        failed_items, f_total = await repo.list_runs("p1", status="failed")
        assert f_total == 1
        assert failed_items[0].id == "r2"

    async def test_get_latest_completed_run(self, repo: AutoEvalRepo) -> None:
        # 1) failed run
        await repo.create_run(
            AutoEvalRun(
                id="r1",
                policy_id="p1",
                started_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
                status="failed",
            )
        )
        # 2) completed run (older)
        await repo.create_run(
            AutoEvalRun(
                id="r2",
                policy_id="p1",
                started_at=datetime(2026, 4, 26, 9, 0, tzinfo=UTC),
                status="completed",
                avg_score=0.9,
            )
        )
        # 3) completed run (newer)
        await repo.create_run(
            AutoEvalRun(
                id="r3",
                policy_id="p1",
                started_at=datetime(2026, 4, 26, 11, 0, tzinfo=UTC),
                status="completed",
                avg_score=0.7,
            )
        )

        latest = await repo.get_latest_completed_run("p1")
        assert latest is not None
        assert latest.id == "r3"

    async def test_get_latest_completed_run_none(self, repo: AutoEvalRepo) -> None:
        latest = await repo.get_latest_completed_run("p1_empty")
        assert latest is None


# ---------- Schedule ----------
@pytest.mark.unit
class TestSchedule:
    """fetch_due_policies / reschedule / _compute_next_run."""

    async def test_fetch_due_policies_returns_only_overdue(self, repo: AutoEvalRepo) -> None:
        now = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        # overdue: 1시간 전 next_run
        p_due = await repo.create_policy(
            make_create(name="due"), owner="u1", now=now - timedelta(hours=2)
        )
        # interval 3600 → next_run = (now-2h)+1h = now-1h <= now (overdue)

        # not due: 30분 후 schedule
        p_not_due = await repo.create_policy(
            make_create(name="future"), owner="u1", now=now + timedelta(hours=10)
        )
        # interval 3600 → next = now+11h > now

        due_ids = await repo.fetch_due_policies(now)
        assert p_due.id in due_ids
        assert p_not_due.id not in due_ids

    async def test_reschedule_updates_next_and_last(self, repo: AutoEvalRepo) -> None:
        now0 = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        policy = await repo.create_policy(make_create(), owner="u1", now=now0)
        old_next = policy.next_run_at

        now1 = now0 + timedelta(hours=2)
        rescheduled = await repo.reschedule(policy, now=now1)
        assert rescheduled.last_run_at == now1
        assert rescheduled.next_run_at is not None
        assert rescheduled.next_run_at > old_next

    async def test_compute_next_run_cron(self) -> None:
        """매시간 실행 cron — base 다음 정시."""
        s = AutoEvalSchedule(type="cron", cron_expression="0 * * * *")
        base = datetime(2026, 4, 26, 12, 30, tzinfo=UTC)
        nxt = AutoEvalRepo._compute_next_run(s, base)
        assert nxt == datetime(2026, 4, 26, 13, 0, tzinfo=UTC)

    async def test_compute_next_run_interval(self) -> None:
        s = AutoEvalSchedule(type="interval", interval_seconds=120)
        base = datetime(2026, 4, 26, 12, 0, tzinfo=UTC)
        nxt = AutoEvalRepo._compute_next_run(s, base)
        assert nxt == base + timedelta(seconds=120)

    async def test_compute_next_run_event_far_future(self) -> None:
        s = AutoEvalSchedule(type="event", event_trigger="new_traces", event_threshold=10)
        base = datetime(2026, 4, 26, tzinfo=UTC)
        nxt = AutoEvalRepo._compute_next_run(s, base)
        # 365일 뒤
        assert nxt == base + timedelta(days=365)


# ---------- Cost tracking ----------
@pytest.mark.unit
class TestCost:
    """record_cost / get_daily_cost / get_cost_usage."""

    async def test_record_cost_accumulates(self, repo: AutoEvalRepo) -> None:
        d = date(2026, 4, 26)
        v1 = await repo.record_cost("p1", 0.01, day=d)
        assert pytest.approx(v1, abs=1e-6) == 0.01
        v2 = await repo.record_cost("p1", 0.02, day=d)
        assert pytest.approx(v2, abs=1e-6) == 0.03

    async def test_get_daily_cost_default_zero(self, repo: AutoEvalRepo) -> None:
        cost = await repo.get_daily_cost("p1_no_cost", day=date(2026, 4, 26))
        assert cost == 0.0

    async def test_record_cost_sets_ttl(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        d = date(2026, 4, 26)
        await repo.record_cost("p1", 0.01, day=d)
        ttl = await redis_client._client.ttl(f"ax:auto_eval_cost:p1:{d.isoformat()}")
        assert 0 < ttl <= 48 * 3600

    async def test_record_negative_cost_raises(self, repo: AutoEvalRepo) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            await repo.record_cost("p1", -1.0)

    async def test_get_cost_usage_aggregates(self, repo: AutoEvalRepo) -> None:
        d1 = date(2026, 4, 25)
        d2 = date(2026, 4, 26)
        d3 = date(2026, 4, 27)
        await repo.record_cost("p1", 0.10, day=d1)
        await repo.record_cost("p1", 0.20, day=d2)
        await repo.record_cost("p1", 0.05, day=d3)

        usage = await repo.get_cost_usage("p1", d1, d3)
        assert usage["policy_id"] == "p1"
        assert usage["date_range"] == "2026-04-25:2026-04-27"
        assert pytest.approx(usage["total_cost_usd"], abs=1e-6) == 0.35
        assert len(usage["daily_breakdown"]) == 3

    async def test_get_cost_usage_invalid_range(self, repo: AutoEvalRepo) -> None:
        with pytest.raises(ValueError, match="from_date"):
            await repo.get_cost_usage("p1", date(2026, 4, 27), date(2026, 4, 25))
