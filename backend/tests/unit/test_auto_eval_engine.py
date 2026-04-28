"""AutoEvalEngine 단위 테스트 (Phase 8-B-1).

검증:
- run_policy 정상 흐름 (mock TraceFetcher + Pipeline)
- 일일 비용 한도 초과 → status=skipped + 알림
- traces 0건 → status=completed
- alert threshold 매칭 (lt/lte/gt/gte + drop_pct)
- baseline 기반 회귀 감지
- expected dataset 매칭
- 실패 시 status=failed + error_message
- 비활성 정책 → AutoEvalEngineError
- 알림 발송 검증 (notification_service.create_notification 호출 확인)
- review_queue 통합 (placeholder)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from app.evaluators.pipeline import WEIGHTED_SCORE_NAME
from app.models.auto_eval import (
    AlertThreshold,
    AutoEvalPolicyCreate,
    AutoEvalRun,
    AutoEvalSchedule,
)
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter, TraceSummary
from app.services.auto_eval_engine import (
    LLM_JUDGE_COST_PER_TRACE_USD,
    AutoEvalEngine,
    AutoEvalEngineError,
)
from app.services.auto_eval_repo import AutoEvalRepo
from tests.fixtures.mock_redis import MockRedisClient
from tests.fixtures.trace_helper import make_trace


# ---------- Mock 컴포넌트 ----------
class FakeTraceFetcher:
    """TraceFetcher 흉내 — search/get 만 노출."""

    def __init__(
        self,
        summaries: list[TraceSummary] | None = None,
        traces: dict[str, Any] | None = None,
        get_raises: dict[str, Exception] | None = None,
        search_raises: Exception | None = None,
    ) -> None:
        self._summaries = summaries or []
        self._traces = traces or {}
        self._get_raises = get_raises or {}
        self._search_raises = search_raises
        self.search_calls = 0
        self.get_calls = 0

    async def search(self, filter):  # noqa: A002
        self.search_calls += 1
        if self._search_raises:
            raise self._search_raises
        return list(self._summaries), len(self._summaries)

    async def get(self, trace_id: str, project_id: str):
        self.get_calls += 1
        if trace_id in self._get_raises:
            raise self._get_raises[trace_id]
        return self._traces[trace_id]


class FakePipeline:
    """EvaluationPipeline 흉내 — evaluate_trace 만 노출."""

    def __init__(
        self,
        results_by_trace: dict[str, dict[str, float | None]] | None = None,
        raise_on: set[str] | None = None,
        delay_sec: float = 0.0,
    ) -> None:
        self._results = results_by_trace or {}
        self._raise_on = raise_on or set()
        self._delay = delay_sec
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    async def evaluate_trace(
        self,
        evaluators,
        trace,
        expected: dict[str, Any] | None = None,
    ):
        if self._delay:
            await asyncio.sleep(self._delay)
        self.calls.append((trace.id, expected))
        if trace.id in self._raise_on:
            raise RuntimeError(f"forced fail on {trace.id}")
        return dict(self._results.get(trace.id, {WEIGHTED_SCORE_NAME: 0.85}))


def make_summary(tid: str) -> TraceSummary:
    return TraceSummary(
        id=tid,
        name="agent",
        timestamp=datetime.now(UTC),
        observation_count=0,
    )


def make_create(
    *,
    project_id: str = "proj-1",
    daily_cost_limit_usd: float | None = None,
    alert_thresholds: list[AlertThreshold] | None = None,
    notification_targets: list[str] | None = None,
    evaluators: list[EvaluatorConfig] | None = None,
    expected_dataset_name: str | None = None,
) -> AutoEvalPolicyCreate:
    return AutoEvalPolicyCreate(
        name="qa-policy",
        project_id=project_id,
        trace_filter=TraceFilter(project_id=project_id),
        evaluators=evaluators or [EvaluatorConfig(type="builtin", name="ev1", weight=1.0)],
        schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
        daily_cost_limit_usd=daily_cost_limit_usd,
        alert_thresholds=alert_thresholds or [],
        notification_targets=notification_targets or [],
        expected_dataset_name=expected_dataset_name,
    )


@pytest.fixture
def repo(redis_client: MockRedisClient) -> AutoEvalRepo:
    return AutoEvalRepo(redis_client)


def make_engine(
    repo: AutoEvalRepo,
    fetcher: FakeTraceFetcher,
    pipeline: FakePipeline,
    redis: MockRedisClient,
    *,
    langfuse: Any | None = None,
    review_queue: Any | None = None,
) -> AutoEvalEngine:
    return AutoEvalEngine(
        repo=repo,
        trace_fetcher=fetcher,  # type: ignore[arg-type]
        pipeline=pipeline,  # type: ignore[arg-type]
        langfuse=langfuse or _DummyLangfuse(),
        redis=redis,
        review_queue=review_queue,
    )


class _DummyLangfuse:
    """list_dataset_items_via_client 호출 시 langfuse 객체로 전달된다."""

    def __init__(self) -> None:
        self._datasets: dict[str, Any] = {}

    def add_dataset(self, name: str, items: list[dict[str, Any]]) -> None:
        class _DS:
            pass

        ds = _DS()
        ds.items = items
        self._datasets[name] = ds


# ---------- 정상 흐름 ----------
@pytest.mark.unit
class TestRunPolicyHappyPath:
    """정상 흐름 — search → fetch → eval → 집계 → reschedule."""

    async def test_run_policy_completes_with_metrics(
        self,
        repo: AutoEvalRepo,
        redis_client: MockRedisClient,
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")

        t1 = make_trace(trace_id="t1", project_id="proj-1", output="out1")
        t2 = make_trace(trace_id="t2", project_id="proj-1", output="out2")
        fetcher = FakeTraceFetcher(
            summaries=[make_summary("t1"), make_summary("t2")],
            traces={"t1": t1, "t2": t2},
        )
        pipeline = FakePipeline(
            results_by_trace={
                "t1": {"ev1": 0.9, WEIGHTED_SCORE_NAME: 0.9},
                "t2": {"ev1": 0.5, WEIGHTED_SCORE_NAME: 0.5},
            }
        )
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        run = await engine.run_policy(policy.id)
        assert run.status == "completed"
        assert run.traces_evaluated == 2
        assert run.traces_total == 2
        assert pytest.approx(run.avg_score, abs=1e-6) == 0.7
        # weighted_score >= 0.7 비율: 1/2 = 0.5
        assert pytest.approx(run.pass_rate, abs=1e-6) == 0.5
        assert "ev1" in run.scores_by_evaluator
        assert WEIGHTED_SCORE_NAME in run.scores_by_evaluator
        assert run.duration_ms is not None
        assert run.completed_at is not None

    async def test_run_policy_reschedules_after_completion(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        old_next = policy.next_run_at

        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        await engine.run_policy(policy.id)

        refreshed = await repo.get_policy(policy.id)
        assert refreshed.last_run_at is not None
        assert refreshed.next_run_at is not None
        assert refreshed.next_run_at > old_next  # type: ignore[operator]

    async def test_run_policy_zero_traces_completes(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        fetcher = FakeTraceFetcher(summaries=[])
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        run = await engine.run_policy(policy.id)
        assert run.status == "completed"
        assert run.traces_evaluated == 0
        assert run.avg_score is None
        assert run.pass_rate is None


# ---------- 비용 한도 ----------
@pytest.mark.unit
class TestCostLimit:
    async def test_cost_limit_exceeded_skips_run(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                daily_cost_limit_usd=0.10,
                notification_targets=["user-1"],
            ),
            owner="u1",
        )
        # 한도 초과 시키기
        await repo.record_cost(policy.id, 0.20)

        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")])
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        run = await engine.run_policy(policy.id)
        assert run.status == "skipped"
        assert run.skip_reason == "daily_cost_limit_exceeded"
        # search 호출 안 됨 (cost limit early return)
        assert fetcher.search_calls == 0

        # 알림 1건 생성 확인 (in-app)
        keys = await redis_client._get_keys_with_prefix("ax:notification:user-1:")
        assert any(k for k in keys)

    async def test_cost_limit_not_set_runs(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        # 누적 비용 있어도 한도 미설정 → 정상 실행
        await repo.record_cost(policy.id, 100.0)
        fetcher = FakeTraceFetcher(summaries=[])
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        assert run.status == "completed"

    async def test_cost_estimate_records_to_repo(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        # judge evaluator 1개 + 2 trace → 비용 = 2 * 1 * 0.005 = 0.01
        policy = await repo.create_policy(
            make_create(evaluators=[EvaluatorConfig(type="judge", name="judge1", weight=1.0)]),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        t2 = make_trace(trace_id="t2")
        fetcher = FakeTraceFetcher(
            summaries=[make_summary("t1"), make_summary("t2")],
            traces={"t1": t1, "t2": t2},
        )
        pipeline = FakePipeline(
            results_by_trace={
                "t1": {"judge1": 0.8, WEIGHTED_SCORE_NAME: 0.8},
                "t2": {"judge1": 0.7, WEIGHTED_SCORE_NAME: 0.7},
            }
        )
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        expected_cost = 2 * 1 * LLM_JUDGE_COST_PER_TRACE_USD
        assert pytest.approx(run.cost_usd, abs=1e-6) == expected_cost
        # repo 누적
        accum = await repo.get_daily_cost(policy.id)
        assert pytest.approx(accum, abs=1e-6) == expected_cost


# ---------- Alert thresholds ----------
@pytest.mark.unit
class TestAlertThresholds:
    """절대값 + drop_pct 기반 회귀 감지."""

    async def test_lt_threshold_triggers(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[AlertThreshold(metric="avg_score", operator="lt", value=0.8)],
                notification_targets=["u1"],
            ),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.5, WEIGHTED_SCORE_NAME: 0.5}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        # avg_score=0.5 < 0.8 → 발화
        assert "avg_score" in run.triggered_alerts

    async def test_gte_threshold_triggers(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[AlertThreshold(metric="pass_rate", operator="gte", value=0.5)]
            ),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.9, WEIGHTED_SCORE_NAME: 0.9}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        # pass_rate=1.0 >= 0.5 → 발화
        assert "pass_rate" in run.triggered_alerts

    async def test_threshold_not_triggered(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[AlertThreshold(metric="avg_score", operator="lt", value=0.5)]
            ),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.9, WEIGHTED_SCORE_NAME: 0.9}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        # avg_score=0.9 < 0.5 거짓 → 미발화
        assert run.triggered_alerts == []

    async def test_drop_pct_baseline_triggers(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[
                    AlertThreshold(
                        metric="avg_score",
                        operator="lt",
                        value=0.0,  # 절대값 비활성
                        drop_pct=0.20,
                    )
                ]
            ),
            owner="u1",
        )
        # baseline: avg_score=1.0
        baseline = AutoEvalRun(
            id="run_baseline",
            policy_id=policy.id,
            started_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            completed_at=datetime(2026, 4, 26, 10, 1, tzinfo=UTC),
            status="completed",
            avg_score=1.0,
        )
        await repo.create_run(baseline)

        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(
            results_by_trace={"t1": {"ev1": 0.7, WEIGHTED_SCORE_NAME: 0.7}}  # avg=0.7 → drop=30%
        )
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        assert "avg_score" in run.triggered_alerts

    async def test_evaluator_score_threshold(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[
                    AlertThreshold(
                        metric="evaluator_score",
                        evaluator_name="ev1",
                        operator="lt",
                        value=0.5,
                    )
                ]
            ),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.3, WEIGHTED_SCORE_NAME: 0.3}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        assert "evaluator_score:ev1" in run.triggered_alerts


# ---------- Notifications ----------
@pytest.mark.unit
class TestNotifications:
    """알림 발송 — regression / cost_limit."""

    async def test_regression_notification_created(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(
            make_create(
                alert_thresholds=[AlertThreshold(metric="avg_score", operator="lt", value=1.0)],
                notification_targets=["user-A", "user-B"],
            ),
            owner="u1",
        )
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.5, WEIGHTED_SCORE_NAME: 0.5}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        await engine.run_policy(policy.id)

        # 두 사용자 모두 알림 1건
        for uid in ("user-A", "user-B"):
            keys = await redis_client._get_keys_with_prefix(f"ax:notification:{uid}:")
            assert len(keys) >= 1


# ---------- Expected dataset matching ----------
@pytest.mark.unit
class TestExpectedDataset:
    async def test_match_by_input_signature(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        langfuse = _DummyLangfuse()
        # dataset 등록: input={"q": "hi"} → expected_output="bye"
        langfuse.add_dataset(
            "golden",
            [
                {
                    "id": "i1",
                    "input": {"q": "hi"},
                    "expected_output": "bye",
                    "metadata": {},
                }
            ],
        )
        policy = await repo.create_policy(make_create(expected_dataset_name="golden"), owner="u1")

        t1 = make_trace(
            trace_id="t1",
            project_id="proj-1",
            input_value={"q": "hi"},
            output="actual",
        )
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline(results_by_trace={"t1": {"ev1": 0.9, WEIGHTED_SCORE_NAME: 0.9}})
        engine = make_engine(repo, fetcher, pipeline, redis_client, langfuse=langfuse)
        await engine.run_policy(policy.id)

        # pipeline 호출 시 expected 가 전달되었는지
        assert pipeline.calls
        _, expected = pipeline.calls[0]
        assert expected is not None
        assert expected["expected_output"] == "bye"


# ---------- Failure modes ----------
@pytest.mark.unit
class TestFailureModes:
    async def test_inactive_policy_raises(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        await repo.pause_policy(policy.id)
        fetcher = FakeTraceFetcher()
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        with pytest.raises(AutoEvalEngineError, match="not active"):
            await engine.run_policy(policy.id)

    async def test_search_failure_marks_run_failed(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        fetcher = FakeTraceFetcher(search_raises=RuntimeError("boom"))
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        with pytest.raises(AutoEvalEngineError):
            await engine.run_policy(policy.id)

        # 마지막 run 조회 — failed + error_message
        items, _ = await repo.list_runs(policy.id)
        assert items
        latest = items[0]
        assert latest.status == "failed"
        assert latest.error_message and "boom" in latest.error_message

    async def test_individual_trace_eval_failure_continues(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        t1 = make_trace(trace_id="t1")
        t2 = make_trace(trace_id="t2")
        fetcher = FakeTraceFetcher(
            summaries=[make_summary("t1"), make_summary("t2")],
            traces={"t1": t1, "t2": t2},
        )
        # t1 만 평가 실패
        pipeline = FakePipeline(
            results_by_trace={
                "t2": {"ev1": 0.8, WEIGHTED_SCORE_NAME: 0.8},
            },
            raise_on={"t1"},
        )
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        # 1건 평가 성공만 반영
        assert run.status == "completed"
        assert run.traces_evaluated == 2  # 두 trace fetch는 됨
        # avg_score 는 t2 의 0.8
        assert pytest.approx(run.avg_score, abs=1e-6) == 0.8

    async def test_individual_trace_fetch_failure_excludes(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        t2 = make_trace(trace_id="t2")
        fetcher = FakeTraceFetcher(
            summaries=[make_summary("t1"), make_summary("t2")],
            traces={"t2": t2},
            get_raises={"t1": RuntimeError("fetch failed")},
        )
        pipeline = FakePipeline(results_by_trace={"t2": {"ev1": 0.7, WEIGHTED_SCORE_NAME: 0.7}})
        engine = make_engine(repo, fetcher, pipeline, redis_client)
        run = await engine.run_policy(policy.id)
        assert run.status == "completed"
        # t1 fetch 실패 → 1건만 평가
        assert run.traces_evaluated == 1


# ---------- Review queue 통합 ----------
@pytest.mark.unit
class TestReviewQueue:
    async def test_review_queue_none_returns_zero(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client, review_queue=None)
        run = await engine.run_policy(policy.id)
        assert run.review_items_created == 0

    async def test_review_queue_enqueues(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        class _Queue:
            def __init__(self) -> None:
                self.calls: list[Any] = []

            async def enqueue(self, policy, run, trace, scores):
                self.calls.append(trace.id)
                return True

        q = _Queue()
        policy = await repo.create_policy(make_create(), owner="u1")
        t1 = make_trace(trace_id="t1")
        t2 = make_trace(trace_id="t2")
        fetcher = FakeTraceFetcher(
            summaries=[make_summary("t1"), make_summary("t2")],
            traces={"t1": t1, "t2": t2},
        )
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client, review_queue=q)
        run = await engine.run_policy(policy.id)
        assert run.review_items_created == 2
        assert set(q.calls) == {"t1", "t2"}


# ---------- Concurrency simulation ----------
@pytest.mark.unit
class TestConcurrency:
    async def test_multiple_runs_in_parallel(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        """3개 정책 병렬 실행 — 카운터 누수 없이 모두 완료."""
        policies = []
        for i in range(3):
            p = await repo.create_policy(make_create(project_id=f"proj-{i}"), owner="u1")
            policies.append(p)

        t1 = make_trace(trace_id="t1")
        fetcher = FakeTraceFetcher(summaries=[make_summary("t1")], traces={"t1": t1})
        pipeline = FakePipeline()
        engine = make_engine(repo, fetcher, pipeline, redis_client)

        runs = await asyncio.gather(*[engine.run_policy(p.id) for p in policies])
        assert all(r.status == "completed" for r in runs)
