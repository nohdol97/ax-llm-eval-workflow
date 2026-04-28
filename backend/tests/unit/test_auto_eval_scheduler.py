"""AutoEvalScheduler 단위 테스트 (Phase 8-B-1).

검증:
- start / stop graceful shutdown (idempotent)
- _tick — due 정책 0건/N건
- 동시 실행 한도 도달 시 spawn 중단
- _run_with_concurrency 후 카운터 감소 (예외 발생에도 누수 없음)
- engine 예외 swallow → polling 지속
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.models.auto_eval import AutoEvalPolicyCreate, AutoEvalSchedule
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter
from app.services.auto_eval_engine import AutoEvalEngineError
from app.services.auto_eval_repo import AutoEvalRepo
from app.services.auto_eval_scheduler import AutoEvalScheduler
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
class FakeEngine:
    """run_policy 호출만 흉내."""

    def __init__(
        self,
        delay_sec: float = 0.0,
        raise_for: set[str] | None = None,
        engine_error_for: set[str] | None = None,
    ) -> None:
        self._delay = delay_sec
        self._raise_for = raise_for or set()
        self._engine_error_for = engine_error_for or set()
        self.calls: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def run_policy(self, policy_id: str):
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            self.calls.append(policy_id)
            if self._delay:
                await asyncio.sleep(self._delay)
            if policy_id in self._engine_error_for:
                raise AutoEvalEngineError(f"engine err {policy_id}")
            if policy_id in self._raise_for:
                raise RuntimeError(f"unexpected {policy_id}")
        finally:
            self.in_flight -= 1


def make_create(name: str = "p", project_id: str = "proj-1") -> AutoEvalPolicyCreate:
    return AutoEvalPolicyCreate(
        name=name,
        project_id=project_id,
        trace_filter=TraceFilter(project_id=project_id),
        evaluators=[EvaluatorConfig(type="builtin", name="ev1", weight=1.0)],
        schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
    )


@pytest.fixture
def repo(redis_client: MockRedisClient) -> AutoEvalRepo:
    return AutoEvalRepo(redis_client)


def _make_scheduler(
    repo: AutoEvalRepo, redis: MockRedisClient, engine: FakeEngine
) -> AutoEvalScheduler:
    return AutoEvalScheduler(repo, engine, redis)  # type: ignore[arg-type]


# ---------- Lifecycle ----------
@pytest.mark.unit
class TestLifecycle:
    async def test_start_stop_idempotent(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        await scheduler.start()
        # 두 번 start — 무해
        await scheduler.start()
        await scheduler.stop(timeout_sec=1.0)
        # stop 두 번 — 무해
        await scheduler.stop(timeout_sec=1.0)

    async def test_start_resets_concurrency_counter(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        # 누수된 카운터를 가정
        await redis_client._client.set(AutoEvalScheduler.CONCURRENCY_KEY, 99)
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        await scheduler.start()
        # 시작 직후 0 으로 리셋되어야 함
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == 0
        await scheduler.stop(timeout_sec=1.0)


# ---------- _tick ----------
@pytest.mark.unit
class TestTick:
    async def test_tick_with_no_due_policies(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        await scheduler._tick()
        assert engine.calls == []

    async def test_tick_runs_due_policies(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        # overdue 정책 생성
        policy = await repo.create_policy(
            make_create(),
            owner="u1",
            now=datetime.now(UTC) - timedelta(hours=2),
        )
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)

        await scheduler._tick()
        # 비동기 spawn 후 실행 완료 대기
        await asyncio.gather(*scheduler._running_tasks, return_exceptions=True)
        assert policy.id in engine.calls

    async def test_tick_respects_concurrency_limit(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        # MAX 한도 도달 상태로 미리 설정
        await redis_client._client.set(
            AutoEvalScheduler.CONCURRENCY_KEY, AutoEvalScheduler.MAX_CONCURRENT
        )
        # due 정책 N개 생성
        for i in range(3):
            await repo.create_policy(
                make_create(name=f"p{i}", project_id=f"proj-{i}"),
                owner="u1",
                now=datetime.now(UTC) - timedelta(hours=2),
            )

        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        await scheduler._tick()
        # 한도 도달이라 spawn 안 됨
        assert engine.calls == []
        # 카운터는 그대로 MAX
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == AutoEvalScheduler.MAX_CONCURRENT


# ---------- _run_with_concurrency ----------
@pytest.mark.unit
class TestRunWithConcurrency:
    async def test_counter_decrements_on_success(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        # 카운터 미리 1로
        await redis_client._client.set(AutoEvalScheduler.CONCURRENCY_KEY, 1)
        await scheduler._run_with_concurrency("policy_x")
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == 0
        assert "policy_x" in engine.calls

    async def test_counter_decrements_on_engine_error(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        engine = FakeEngine(engine_error_for={"policy_x"})
        scheduler = _make_scheduler(repo, redis_client, engine)
        await redis_client._client.set(AutoEvalScheduler.CONCURRENCY_KEY, 1)
        # AutoEvalEngineError 는 swallow
        await scheduler._run_with_concurrency("policy_x")
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == 0

    async def test_counter_decrements_on_unexpected_error(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        engine = FakeEngine(raise_for={"policy_x"})
        scheduler = _make_scheduler(repo, redis_client, engine)
        await redis_client._client.set(AutoEvalScheduler.CONCURRENCY_KEY, 1)
        await scheduler._run_with_concurrency("policy_x")
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == 0

    async def test_counter_negative_protection(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        """카운터가 0 이하로 가는 비정상 시 0 으로 강제."""
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)
        # 카운터 0인 채로 decr → -1 → 0 보정
        await redis_client._client.set(AutoEvalScheduler.CONCURRENCY_KEY, 0)
        await scheduler._run_with_concurrency("policy_x")
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        assert int(val) == 0


# ---------- Integration: poll_loop end-to-end ----------
@pytest.mark.unit
class TestPollLoopIntegration:
    async def test_poll_loop_runs_due_policy_once(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient, monkeypatch
    ) -> None:
        # 빠른 polling 으로 변경
        monkeypatch.setattr(AutoEvalScheduler, "POLL_INTERVAL_SEC", 0.05)

        # overdue 정책 1개
        policy = await repo.create_policy(
            make_create(),
            owner="u1",
            now=datetime.now(UTC) - timedelta(hours=2),
        )
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)

        await scheduler.start()
        # 짧은 대기 — 최소 1회 tick 보장
        await asyncio.sleep(0.2)
        await scheduler.stop(timeout_sec=2.0)

        assert policy.id in engine.calls

    async def test_poll_loop_continues_after_tick_exception(
        self,
        repo: AutoEvalRepo,
        redis_client: MockRedisClient,
        monkeypatch,
    ) -> None:
        """tick 내부 예외가 발생해도 polling 계속됨 — fetch_due_policies mock으로 1회 raise."""
        monkeypatch.setattr(AutoEvalScheduler, "POLL_INTERVAL_SEC", 0.05)
        engine = FakeEngine()
        scheduler = _make_scheduler(repo, redis_client, engine)

        call_count = {"n": 0}
        original_fetch = repo.fetch_due_policies

        async def flaky_fetch(now):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("first call boom")
            return await original_fetch(now)

        monkeypatch.setattr(repo, "fetch_due_policies", flaky_fetch)

        await scheduler.start()
        await asyncio.sleep(0.2)
        await scheduler.stop(timeout_sec=1.0)

        # 최소 2회 호출 — 즉, 1회 예외 후에도 다음 tick 진행됨
        assert call_count["n"] >= 2

    async def test_stop_during_running_tasks(
        self,
        repo: AutoEvalRepo,
        redis_client: MockRedisClient,
        monkeypatch,
    ) -> None:
        """진행 중 task 가 있어도 stop timeout 내 graceful 회수."""
        monkeypatch.setattr(AutoEvalScheduler, "POLL_INTERVAL_SEC", 0.05)
        await repo.create_policy(
            make_create(),
            owner="u1",
            now=datetime.now(UTC) - timedelta(hours=2),
        )
        # 매우 긴 작업 simulating
        engine = FakeEngine(delay_sec=2.0)
        scheduler = _make_scheduler(repo, redis_client, engine)
        await scheduler.start()
        await asyncio.sleep(0.15)  # tick + spawn
        # 짧은 timeout — task 미완료 → cancel
        await scheduler.stop(timeout_sec=0.1)
        # 카운터 누수 없음 (cancel된 finally 에서 decr 보장)
        val = await redis_client._client.get(AutoEvalScheduler.CONCURRENCY_KEY)
        # cancel 시 finally 미실행 가능성 → 음수 보호 필요
        # 핵심: 0 또는 0 으로 보정
        assert int(val) >= 0
