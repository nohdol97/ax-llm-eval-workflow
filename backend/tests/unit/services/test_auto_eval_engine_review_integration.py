"""AutoEvalEngine Review Queue 통합 분기 단위 테스트."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from app.evaluators.pipeline import WEIGHTED_SCORE_NAME
from app.models.auto_eval import AutoEvalPolicyCreate, AutoEvalRun, AutoEvalSchedule
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter, TraceSummary
from app.services.auto_eval_engine import AutoEvalEngine
from app.services.auto_eval_repo import AutoEvalRepo
from tests.fixtures.mock_redis import MockRedisClient
from tests.fixtures.trace_helper import make_trace


class FakeTraceFetcher:
    def __init__(self, summaries: list[TraceSummary], traces: dict[str, Any]) -> None:
        self._summaries = summaries
        self._traces = traces

    async def search(self, filter):  # noqa: A002
        return list(self._summaries), len(self._summaries)

    async def get(self, trace_id: str, project_id: str):
        return self._traces[trace_id]


class FakePipeline:
    def __init__(self, results_by_trace: dict[str, dict[str, float | None]] | None = None) -> None:
        self._results = results_by_trace or {}

    async def evaluate_trace(self, evaluators, trace, expected=None):
        return dict(self._results.get(trace.id, {WEIGHTED_SCORE_NAME: 0.85}))


class DummyLangfuse:
    pass


def make_summary(trace_id: str) -> TraceSummary:
    return TraceSummary(
        id=trace_id,
        name="agent",
        timestamp=datetime.now(UTC),
        observation_count=0,
    )


def make_create(project_id: str = "proj-1") -> AutoEvalPolicyCreate:
    return AutoEvalPolicyCreate(
        name="review-policy",
        project_id=project_id,
        trace_filter=TraceFilter(project_id=project_id),
        evaluators=[EvaluatorConfig(type="builtin", name="ev1", weight=1.0)],
        schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
    )


@pytest.fixture
def repo(redis_client: MockRedisClient) -> AutoEvalRepo:
    return AutoEvalRepo(redis_client)


def make_engine(
    repo: AutoEvalRepo,
    redis_client: MockRedisClient,
    *,
    trace_fetcher: Any | None = None,
    pipeline: Any | None = None,
    review_queue: Any | None = None,
) -> AutoEvalEngine:
    return AutoEvalEngine(
        repo=repo,
        trace_fetcher=trace_fetcher or FakeTraceFetcher([], {}),  # type: ignore[arg-type]
        pipeline=pipeline or FakePipeline(),  # type: ignore[arg-type]
        langfuse=DummyLangfuse(),
        redis=redis_client,
        review_queue=review_queue,
    )


def make_run(policy_id: str) -> AutoEvalRun:
    return AutoEvalRun(
        id="run_review_test",
        policy_id=policy_id,
        started_at=datetime.now(UTC),
        status="running",
    )


@pytest.mark.unit
class TestEnqueueForReview:
    async def test_review_queue_none_returns_zero(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        policy = await repo.create_policy(make_create(), owner="u1")
        engine = make_engine(repo, redis_client, review_queue=None)

        count = await engine._enqueue_for_review(policy, make_run(policy.id), [], [])

        assert count == 0

    async def test_enqueues_each_trace_and_counts_only_truthy_results(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        class Queue:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict[str, float | None]]] = []

            async def enqueue(self, policy, run, trace, scores):
                self.calls.append((trace.id, scores))
                return trace.id == "t1"

        queue = Queue()
        policy = await repo.create_policy(make_create(), owner="u1")
        traces = [make_trace(trace_id="t1"), make_trace(trace_id="t2")]
        results = [{"ev1": 0.9}, {"ev1": 0.2}]
        engine = make_engine(repo, redis_client, review_queue=queue)

        count = await engine._enqueue_for_review(policy, make_run(policy.id), traces, results)

        assert count == 1
        assert queue.calls == [("t1", {"ev1": 0.9}), ("t2", {"ev1": 0.2})]

    async def test_false_return_does_not_increment_count(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        class Queue:
            async def enqueue(self, policy, run, trace, scores):
                return False

        policy = await repo.create_policy(make_create(), owner="u1")
        traces = [make_trace(trace_id="t1"), make_trace(trace_id="t2")]
        results = [{"ev1": 0.9}, {"ev1": 0.2}]
        engine = make_engine(repo, redis_client, review_queue=Queue())

        count = await engine._enqueue_for_review(policy, make_run(policy.id), traces, results)

        assert count == 0

    async def test_enqueue_exceptions_are_swallowed_and_next_trace_continues(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        class Queue:
            def __init__(self) -> None:
                self.calls: list[str] = []

            async def enqueue(self, policy, run, trace, scores):
                self.calls.append(trace.id)
                if trace.id == "t1":
                    raise RuntimeError("boom")
                return True

        queue = Queue()
        policy = await repo.create_policy(make_create(), owner="u1")
        traces = [make_trace(trace_id="t1"), make_trace(trace_id="t2")]
        results = [{"ev1": 0.9}, {"ev1": 0.2}]
        engine = make_engine(repo, redis_client, review_queue=queue)

        count = await engine._enqueue_for_review(policy, make_run(policy.id), traces, results)

        assert count == 1
        assert queue.calls == ["t1", "t2"]

    async def test_run_policy_reflects_enqueue_count_on_review_items_created(
        self, repo: AutoEvalRepo, redis_client: MockRedisClient
    ) -> None:
        class Queue:
            async def enqueue(self, policy, run, trace, scores):
                return trace.id != "t2"

        policy = await repo.create_policy(make_create(), owner="u1")
        t1 = make_trace(trace_id="t1", project_id="proj-1")
        t2 = make_trace(trace_id="t2", project_id="proj-1")
        t3 = make_trace(trace_id="t3", project_id="proj-1")
        fetcher = FakeTraceFetcher(
            [make_summary("t1"), make_summary("t2"), make_summary("t3")],
            {"t1": t1, "t2": t2, "t3": t3},
        )
        pipeline = FakePipeline(
            {
                "t1": {"ev1": 0.9, WEIGHTED_SCORE_NAME: 0.9},
                "t2": {"ev1": 0.1, WEIGHTED_SCORE_NAME: 0.1},
                "t3": {"ev1": 0.8, WEIGHTED_SCORE_NAME: 0.8},
            }
        )
        engine = make_engine(
            repo,
            redis_client,
            trace_fetcher=fetcher,
            pipeline=pipeline,
            review_queue=Queue(),
        )

        run = await engine.run_policy(policy.id)

        assert run.status == "completed"
        assert run.review_items_created == 2

