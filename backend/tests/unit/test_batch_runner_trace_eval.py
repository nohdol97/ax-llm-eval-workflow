"""BatchExperimentRunner trace_eval 모드 단위 테스트 (Phase 8-A-3).

검증 범위
- ``ExperimentCreate`` 모드별 검증 (mode=live는 prompt_configs 등 필수,
  mode=trace_eval은 trace_filter 필수)
- ``create_experiment`` trace_eval 분기 → Redis 초기 상태 + 백그라운드 실행 시작
- ``_run_trace_eval`` 흐름 (mock TraceFetcher + EvaluationPipeline)
- expected dataset 매칭 (있음/없음/매칭 실패)
- 빈 trace 결과 → LabsError
- 알림 (best-effort) 발송
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from app.core.errors import LabsError
from app.models.experiment import (
    EvaluatorConfig,
    ExperimentCreate,
    ModelConfig,
    PromptConfig,
)
from app.models.trace import TraceFilter, TraceSummary, TraceTree
from app.services.batch_runner import (
    BatchExperimentRunner,
    _exp_key,
    _full_key,
    _underlying,
)
from app.services.context_engine import ContextEngine
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
def _make_summary(trace_id: str, *, name: str = "qa-agent") -> TraceSummary:
    """간단한 TraceSummary 생성 — 검색 결과로 사용."""
    return TraceSummary(
        id=trace_id,
        name=name,
        user_id=None,
        session_id=None,
        tags=[],
        total_cost_usd=0.01,
        total_latency_ms=120.0,
        timestamp=datetime.now(UTC),
        observation_count=0,
    )


def _make_trace(
    trace_id: str,
    *,
    project_id: str = "proj-1",
    name: str = "qa-agent",
    input_data: dict[str, Any] | None = None,
    output_data: str | None = None,
) -> TraceTree:
    """간단한 TraceTree 생성 — get() 응답으로 사용."""
    return TraceTree(
        id=trace_id,
        project_id=project_id,
        name=name,
        input=input_data or {"question": f"q-{trace_id}"},
        output=output_data or f"answer-{trace_id}",
        user_id=None,
        session_id=None,
        tags=[],
        metadata={},
        observations=[],
        scores=[],
        total_cost_usd=0.01,
        total_latency_ms=120.0,
        timestamp=datetime.now(UTC),
    )


class StubTraceFetcher:
    """TraceFetcher 인터페이스 stub — search/get 동작."""

    def __init__(
        self,
        summaries: list[TraceSummary],
        traces: dict[str, TraceTree] | None = None,
        total: int | None = None,
    ) -> None:
        self.summaries = summaries
        self.total = total if total is not None else len(summaries)
        self.traces = traces or {s.id: _make_trace(s.id) for s in summaries}
        self.search_calls: list[TraceFilter] = []
        self.get_calls: list[tuple[str, str]] = []

    async def search(self, filter: TraceFilter) -> tuple[list[TraceSummary], int]:  # noqa: A002
        self.search_calls.append(filter)
        return self.summaries, self.total

    async def get(self, trace_id: str, project_id: str) -> TraceTree:
        self.get_calls.append((trace_id, project_id))
        if trace_id not in self.traces:
            raise KeyError(trace_id)
        return self.traces[trace_id]


class StubPipeline:
    """EvaluationPipeline.evaluate_trace stub."""

    def __init__(self, scores: dict[str, float | None] | None = None) -> None:
        # 호출 순서별 다른 결과를 주입할 수도 있도록 인덱스 기반
        self._scores = scores or {"weighted_score": 0.85, "tool_called": 1.0}
        self.calls: list[dict[str, Any]] = []

    async def evaluate_trace(
        self,
        evaluators: list[EvaluatorConfig],
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> dict[str, float | None]:
        self.calls.append({"trace_id": trace.id, "expected": expected})
        return dict(self._scores)


def _make_trace_eval_request(
    *,
    project_id: str = "proj-1",
    sample_size: int | None = None,
    expected_dataset: str | None = None,
    evaluators: list[EvaluatorConfig] | None = None,
) -> ExperimentCreate:
    return ExperimentCreate(
        project_id=project_id,
        name="trace-eval-exp",
        mode="trace_eval",
        trace_filter=TraceFilter(
            project_id=project_id,
            name="qa-agent",
            sample_size=sample_size,
        ),
        expected_dataset_name=expected_dataset,
        evaluators=evaluators
        or [
            EvaluatorConfig(type="trace_builtin", name="tool_called", weight=1.0),
        ],
        concurrency=2,
        metadata={},
    )


@pytest.fixture
def stub_pipeline() -> StubPipeline:
    return StubPipeline()


@pytest.fixture
def stub_fetcher() -> StubTraceFetcher:
    summaries = [_make_summary(f"t-{i}") for i in range(3)]
    return StubTraceFetcher(summaries=summaries)


@pytest.fixture
def runner(
    langfuse_client: MockLangfuseClient,
    litellm_client: MockLiteLLMProxy,
    redis_client: MockRedisClient,
    stub_fetcher: StubTraceFetcher,
    stub_pipeline: StubPipeline,
) -> BatchExperimentRunner:
    return BatchExperimentRunner(
        langfuse=langfuse_client,
        litellm=litellm_client,
        redis=redis_client,
        context_engine=ContextEngine(),
        evaluation_pipeline=stub_pipeline,
        trace_fetcher=stub_fetcher,
    )


# ---------- 1. ExperimentCreate validation ----------
@pytest.mark.unit
class TestExperimentCreateValidation:
    """모드별 필수 필드 검증."""

    def test_mode_live_default_requires_prompt_configs(self) -> None:
        """mode 미지정 (default=live)에서 prompt_configs 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                dataset_name="d",
                model_configs=[ModelConfig(model="m")],
            )

    def test_mode_live_requires_dataset_name(self) -> None:
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                mode="live",
                prompt_configs=[PromptConfig(name="p")],
                model_configs=[ModelConfig(model="m")],
            )

    def test_mode_live_requires_model_configs(self) -> None:
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                mode="live",
                prompt_configs=[PromptConfig(name="p")],
                dataset_name="d",
            )

    def test_mode_trace_eval_requires_trace_filter(self) -> None:
        """mode=trace_eval에서 trace_filter 누락 시 ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                mode="trace_eval",
                evaluators=[EvaluatorConfig(type="trace_builtin", name="tool_called")],
            )

    def test_mode_trace_eval_requires_evaluators(self) -> None:
        """mode=trace_eval에서 evaluators 비어 있으면 ValidationError."""
        with pytest.raises(ValidationError):
            ExperimentCreate(
                project_id="p",
                name="n",
                mode="trace_eval",
                trace_filter=TraceFilter(project_id="p"),
                evaluators=[],
            )

    def test_mode_trace_eval_minimal_valid(self) -> None:
        """mode=trace_eval 최소 필수 필드 통과."""
        req = ExperimentCreate(
            project_id="p",
            name="n",
            mode="trace_eval",
            trace_filter=TraceFilter(project_id="p"),
            evaluators=[EvaluatorConfig(type="trace_builtin", name="tool_called")],
        )
        assert req.mode == "trace_eval"
        assert req.trace_filter is not None
        assert req.prompt_configs is None

    def test_evaluator_type_trace_builtin_accepted(self) -> None:
        """``trace_builtin`` 타입이 EvaluatorConfig에서 허용된다."""
        cfg = EvaluatorConfig(
            type="trace_builtin", name="tool_called", config={"tool_name": "search"}
        )
        assert cfg.type == "trace_builtin"
        assert cfg.config == {"tool_name": "search"}


# ---------- 2. create_experiment trace_eval 분기 ----------
@pytest.mark.unit
class TestCreateTraceEvalExperiment:
    """trace_eval 모드 create_experiment."""

    async def test_creates_experiment_with_trace_eval_state(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        stub_fetcher: StubTraceFetcher,
    ) -> None:
        """Redis Hash에 mode=trace_eval로 초기화된다."""
        request = _make_trace_eval_request()
        # 백그라운드 task가 즉시 실행되지 않도록 차단
        runner._run_trace_eval = AsyncMock()  # type: ignore[method-assign]
        response = await runner.create_experiment(request=request, user_id="u-1")

        assert response.experiment_id
        assert response.status == "running"
        assert response.total_runs == 1
        assert response.total_items == 3  # stub_fetcher.summaries length
        assert response.runs == []

        # search가 호출되어 사전 매칭 수 확인
        assert len(stub_fetcher.search_calls) == 1
        assert stub_fetcher.search_calls[0].project_id == "proj-1"

        # Redis에 mode=trace_eval 기록
        underlying = _underlying(redis_client)
        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["mode"] == "trace_eval"
        assert raw["status"] == "running"
        assert int(raw["total_runs"]) == 1
        assert int(raw["total_items"]) == 3
        # config snapshot에 mode=trace_eval, trace_filter 포함
        snap = json.loads(raw["config"])
        assert snap["mode"] == "trace_eval"
        assert snap["trace_filter"]["project_id"] == "proj-1"

    async def test_empty_trace_match_raises(
        self,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
        redis_client: MockRedisClient,
    ) -> None:
        """매칭 trace 0개면 LabsError."""
        empty_fetcher = StubTraceFetcher(summaries=[], total=0)
        runner = BatchExperimentRunner(
            langfuse=langfuse_client,
            litellm=litellm_client,
            redis=redis_client,
            context_engine=ContextEngine(),
            evaluation_pipeline=StubPipeline(),
            trace_fetcher=empty_fetcher,
        )
        request = _make_trace_eval_request()
        with pytest.raises(LabsError, match="매칭되는 trace가 없습니다"):
            await runner.create_experiment(request=request, user_id="u-1")

    async def test_no_trace_fetcher_raises(
        self,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
        redis_client: MockRedisClient,
    ) -> None:
        """trace_fetcher 미주입 시 LabsError."""
        runner = BatchExperimentRunner(
            langfuse=langfuse_client,
            litellm=litellm_client,
            redis=redis_client,
            context_engine=ContextEngine(),
            evaluation_pipeline=StubPipeline(),
            trace_fetcher=None,
        )
        request = _make_trace_eval_request()
        with pytest.raises(LabsError, match="TraceFetcher"):
            await runner.create_experiment(request=request, user_id="u-1")

    async def test_sample_size_caps_total_items(
        self,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
        redis_client: MockRedisClient,
    ) -> None:
        """sample_size가 total보다 작으면 evaluated_target = sample_size."""
        # 5개 summaries인데 sample_size=2
        summaries = [_make_summary(f"t-{i}") for i in range(5)]
        fetcher = StubTraceFetcher(summaries=summaries, total=5)
        runner = BatchExperimentRunner(
            langfuse=langfuse_client,
            litellm=litellm_client,
            redis=redis_client,
            context_engine=ContextEngine(),
            evaluation_pipeline=StubPipeline(),
            trace_fetcher=fetcher,
        )
        request = _make_trace_eval_request(sample_size=2)
        runner._run_trace_eval = AsyncMock()  # type: ignore[method-assign]
        response = await runner.create_experiment(request=request, user_id="u-1")
        assert response.total_items == 2

    async def test_invalid_evaluator_weights_raises(
        self,
        runner: BatchExperimentRunner,
    ) -> None:
        """가중치 합 > 1.0이면 LabsError."""
        request = _make_trace_eval_request(
            evaluators=[
                EvaluatorConfig(type="trace_builtin", name="a", weight=0.7),
                EvaluatorConfig(type="trace_builtin", name="b", weight=0.7),
            ],
        )
        with pytest.raises(LabsError, match="가중치"):
            await runner.create_experiment(request=request, user_id="u-1")


# ---------- 3. _run_trace_eval 백그라운드 흐름 ----------
@pytest.mark.unit
class TestRunTraceEval:
    """trace_eval 백그라운드 실행."""

    async def test_runs_through_all_traces(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
        stub_fetcher: StubTraceFetcher,
        stub_pipeline: StubPipeline,
    ) -> None:
        """모든 trace가 fetch + evaluate되고 status=completed."""
        request = _make_trace_eval_request()
        response = await runner.create_experiment(request=request, user_id="u-1")
        # 백그라운드 task 완료 대기
        for task in list(runner._tasks.values()):
            await task

        # 각 trace에 대해 get() 호출됨 (3건)
        assert len(stub_fetcher.get_calls) == 3
        # 각 trace에 대해 evaluate_trace 호출됨 (3건)
        assert len(stub_pipeline.calls) == 3

        # 최종 상태 completed
        underlying = _underlying(redis_client)
        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["status"] == "completed"
        assert int(raw["completed_items"]) == 3
        assert int(raw["traces_evaluated"]) == 3
        # weighted_score 합산 (0.85 × 3) — float 포멧 차이로 근사 비교
        assert abs(float(raw["total_score_sum"]) - 0.85 * 3) < 1e-6
        assert int(raw["scored_count"]) == 3

    async def test_with_expected_dataset_matches_input(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
        stub_fetcher: StubTraceFetcher,
        stub_pipeline: StubPipeline,
    ) -> None:
        """expected_dataset_name이 있으면 trace.input과 매칭하여 expected 전달."""
        # 데이터셋 시드 — t-0, t-1 trace의 input과 동일 매칭
        # _make_trace는 input={"question": f"q-{trace_id}"} 사용
        langfuse_client._seed(
            datasets=[
                {
                    "name": "golden",
                    "items": [
                        {
                            "input": {"question": "q-t-0"},
                            "expected_output": "expected-0",
                        },
                        {
                            "input": {"question": "q-t-1"},
                            "expected_output": "expected-1",
                        },
                    ],
                }
            ],
        )
        request = _make_trace_eval_request(expected_dataset="golden")
        await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        # evaluate_trace 호출 시 일부는 expected가 dict, 일부는 None
        with_expected = [c for c in stub_pipeline.calls if c["expected"] is not None]
        without_expected = [c for c in stub_pipeline.calls if c["expected"] is None]
        # t-0, t-1는 매칭, t-2는 미매칭
        assert len(with_expected) == 2
        assert len(without_expected) == 1
        # expected 페이로드 검증
        for c in with_expected:
            assert "expected_output" in c["expected"]

    async def test_without_expected_dataset_passes_none(
        self,
        runner: BatchExperimentRunner,
        stub_pipeline: StubPipeline,
    ) -> None:
        """expected_dataset_name 미지정 시 모든 trace에 expected=None."""
        request = _make_trace_eval_request(expected_dataset=None)
        await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task
        assert all(c["expected"] is None for c in stub_pipeline.calls)

    async def test_pipeline_failure_per_trace_does_not_abort(
        self,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
        redis_client: MockRedisClient,
        stub_fetcher: StubTraceFetcher,
    ) -> None:
        """개별 trace 평가가 실패해도 전체 실험은 completed."""

        class FailingPipeline:
            def __init__(self) -> None:
                self.count = 0

            async def evaluate_trace(
                self,
                evaluators: list[EvaluatorConfig],
                trace: TraceTree,
                expected: dict[str, Any] | None,
            ) -> dict[str, float | None]:
                self.count += 1
                if self.count == 2:
                    raise RuntimeError("evaluator boom")
                return {"weighted_score": 0.5}

        pipeline = FailingPipeline()
        runner = BatchExperimentRunner(
            langfuse=langfuse_client,
            litellm=litellm_client,
            redis=redis_client,
            context_engine=ContextEngine(),
            evaluation_pipeline=pipeline,
            trace_fetcher=stub_fetcher,
        )
        request = _make_trace_eval_request()
        response = await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        underlying = _underlying(redis_client)
        raw = await underlying.hgetall(_full_key(redis_client, _exp_key(response.experiment_id)))
        assert raw["status"] == "completed"
        # 3개 fetch + 3개 평가, 1개는 빈 dict 반환되므로 scored_count=2
        assert int(raw["scored_count"]) == 2

    async def test_notification_sent_on_complete(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
    ) -> None:
        """완료 시 owner에게 알림 1건 생성."""
        request = _make_trace_eval_request()
        await runner.create_experiment(request=request, user_id="u-99")
        for task in list(runner._tasks.values()):
            await task

        # notification_service의 알림 키가 생성됐는지 확인
        underlying = _underlying(redis_client)
        # Sorted Set 인덱스: ax:notification:u-99:index
        index_count = await underlying.zcard("ax:notification:u-99:index")
        assert index_count >= 1

    async def test_concurrency_counter_decremented_on_complete(
        self,
        runner: BatchExperimentRunner,
        redis_client: MockRedisClient,
    ) -> None:
        """완료 후 워크스페이스 동시 카운터가 0으로 회복."""
        request = _make_trace_eval_request()
        await runner.create_experiment(request=request, user_id="u-1")
        for task in list(runner._tasks.values()):
            await task

        counter = await redis_client.get("concurrency:experiments")
        # 진행 중에 +1 → 종료 후 -1로 0이 되어야 함
        assert counter in (None, "0", 0)


# ---------- 4. _match_expected 단위 ----------
@pytest.mark.unit
class TestMatchExpected:
    """_match_expected 단위 테스트."""

    async def test_returns_empty_when_no_dataset_name(
        self,
        runner: BatchExperimentRunner,
    ) -> None:
        traces = [_make_trace("t-1")]
        result = await runner._match_expected(traces, None)
        assert result == {}

    async def test_returns_empty_when_no_traces(
        self,
        runner: BatchExperimentRunner,
    ) -> None:
        result = await runner._match_expected([], "golden")
        assert result == {}

    async def test_matches_by_input_signature(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """trace.input과 dataset.input이 동일하면 expected_output 매칭된다."""
        langfuse_client._seed(
            datasets=[
                {
                    "name": "g1",
                    "items": [
                        {
                            "input": {"question": "qx"},
                            "expected_output": "ans-1",
                        },
                    ],
                }
            ]
        )
        traces = [_make_trace("t-x", input_data={"question": "qx"})]
        result = await runner._match_expected(traces, "g1")
        assert "t-x" in result
        assert result["t-x"]["expected_output"] == "ans-1"

    async def test_no_match_returns_no_entry(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        langfuse_client._seed(
            datasets=[
                {
                    "name": "g2",
                    "items": [
                        {
                            "input": {"question": "different"},
                            "expected_output": "x",
                        },
                    ],
                }
            ]
        )
        traces = [_make_trace("t-y", input_data={"question": "not-matched"})]
        result = await runner._match_expected(traces, "g2")
        assert result == {}

    async def test_dataset_fetch_failure_returns_empty(
        self,
        runner: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        """dataset 조회 실패도 빈 dict를 반환 (실험은 expected 없이 진행)."""
        # 존재하지 않는 dataset
        traces = [_make_trace("t-1")]
        result = await runner._match_expected(traces, "non-existent")
        assert result == {}
