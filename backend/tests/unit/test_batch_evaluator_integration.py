"""배치 실험 (BatchExperimentRunner) ↔ Evaluator Pipeline 통합 테스트.

검증 범위
- evaluators 포함 실험 → 각 아이템에 evaluator 점수 기록
- Run Hash의 ``total_score_sum`` / ``scored_count`` 누적
- 실험 생성 시점 가중치 검증 (validate_weights) — 합계 != 1.0이면 422
- evaluators 빈 리스트는 기존 동작 유지 (Phase 4 호환)
"""

from __future__ import annotations

import pytest

from app.core.errors import LabsError
from app.evaluators.pipeline import EvaluationPipeline
from app.models.experiment import (
    EvaluatorConfig,
    ExperimentCreate,
    ModelConfig,
    PromptConfig,
)
from app.services.batch_runner import (
    BatchExperimentRunner,
    _exp_key,
    _full_key,
    _run_key,
    _underlying,
)
from app.services.context_engine import ContextEngine
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 헬퍼 ----------
def _seed_dataset(
    langfuse: MockLangfuseClient,
    *,
    name: str = "ds-eval",
    item_count: int = 2,
    expected: str = "exp",
) -> None:
    langfuse._seed(
        prompts=[
            {"name": "prompt-1", "body": "Hello {{topic}}", "version": 1},
        ],
        datasets=[
            {
                "name": name,
                "items": [
                    {
                        "input": {"topic": f"item-{i}"},
                        "expected_output": expected,
                    }
                    for i in range(item_count)
                ],
            }
        ],
    )


def _make_request(
    *,
    evaluators: list[EvaluatorConfig] | None = None,
    dataset_name: str = "ds-eval",
) -> ExperimentCreate:
    return ExperimentCreate(
        project_id="proj-1",
        name="eval-exp",
        prompt_configs=[PromptConfig(name="prompt-1", version=1)],
        dataset_name=dataset_name,
        model_configs=[
            ModelConfig(model="model-a", parameters={"temperature": 0.0})
        ],
        evaluators=evaluators or [],
        concurrency=2,
    )


@pytest.fixture
def runner_with_pipeline(
    langfuse_client: MockLangfuseClient,
    litellm_client: MockLiteLLMProxy,
    redis_client: MockRedisClient,
) -> BatchExperimentRunner:
    pipeline = EvaluationPipeline(
        langfuse=langfuse_client, litellm_client=litellm_client
    )
    return BatchExperimentRunner(
        langfuse=langfuse_client,
        litellm=litellm_client,
        redis=redis_client,
        context_engine=ContextEngine(),
        evaluation_pipeline=pipeline,
    )


# ---------- 1) 평가 점수 누적 ----------
@pytest.mark.unit
class TestBatchExperimentEvaluatorScores:
    """evaluator를 포함한 실험은 Run Hash에 score를 누적한다."""

    async def test_each_item_score_is_recorded_to_run_hash(
        self,
        runner_with_pipeline: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=3, expected="exp")
        litellm_client.set_response("exp")  # 모든 호출에서 정답 일치

        request = _make_request(
            evaluators=[
                EvaluatorConfig(
                    type="builtin",
                    name="exact_match",
                    config={},
                    weight=1.0,
                )
            ]
        )
        response = await runner_with_pipeline.create_experiment(
            request=request, user_id="u-1"
        )
        for task in list(runner_with_pipeline._tasks.values()):
            await task

        underlying = _underlying(redis_client)
        run_name = response.runs[0].run_name
        run_raw = await underlying.hgetall(
            _full_key(redis_client, _run_key(response.experiment_id, run_name))
        )
        # 모두 일치 → 모든 아이템 score=1.0, scored_count=3
        assert int(run_raw["scored_count"]) == 3
        assert float(run_raw["total_score_sum"]) == pytest.approx(3.0)

        # avg_score 계산은 _run_summary에서 수행
        summary = await runner_with_pipeline._run_summary(
            response.experiment_id, run_name
        )
        assert summary["avg_score"] == pytest.approx(1.0)

    async def test_no_evaluators_keeps_phase4_behavior(
        self,
        runner_with_pipeline: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=2)
        litellm_client.set_response("any")

        request = _make_request(evaluators=[])
        response = await runner_with_pipeline.create_experiment(
            request=request, user_id="u-1"
        )
        for task in list(runner_with_pipeline._tasks.values()):
            await task

        underlying = _underlying(redis_client)
        run_name = response.runs[0].run_name
        run_raw = await underlying.hgetall(
            _full_key(redis_client, _run_key(response.experiment_id, run_name))
        )
        assert int(run_raw["scored_count"]) == 0
        assert float(run_raw["total_score_sum"]) == 0.0
        assert int(run_raw["completed_items"]) == 2

    async def test_partial_score_with_multiple_evaluators(
        self,
        runner_with_pipeline: BatchExperimentRunner,
        redis_client: MockRedisClient,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=2, expected="exact")
        # 모든 응답을 다른 값으로 설정 → exact_match=0, contains=1 (조건 충족)
        litellm_client.set_response("partially exact match value")

        request = _make_request(
            evaluators=[
                EvaluatorConfig(
                    type="builtin",
                    name="contains",
                    config={"keywords": ["partially"]},
                    weight=0.5,
                ),
                EvaluatorConfig(
                    type="builtin",
                    name="exact_match",
                    config={},
                    weight=0.5,
                ),
            ]
        )
        response = await runner_with_pipeline.create_experiment(
            request=request, user_id="u-1"
        )
        for task in list(runner_with_pipeline._tasks.values()):
            await task

        underlying = _underlying(redis_client)
        run_name = response.runs[0].run_name
        run_raw = await underlying.hgetall(
            _full_key(redis_client, _run_key(response.experiment_id, run_name))
        )
        # weighted_score = 0.5*1.0 + 0.5*0.0 = 0.5 per item, 2 items
        assert int(run_raw["scored_count"]) == 2
        assert float(run_raw["total_score_sum"]) == pytest.approx(1.0)


# ---------- 2) 가중치 검증 (실험 생성 시) ----------
@pytest.mark.unit
class TestBatchExperimentWeightValidation:
    """``create_experiment`` 단계에서 ``validate_weights``를 호출."""

    async def test_invalid_weights_raises_at_create(
        self,
        runner_with_pipeline: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=1)
        # 명시 weight 합계 1.5 > 1.0 → ValueError → LabsError로 변환
        request = _make_request(
            evaluators=[
                EvaluatorConfig(
                    type="builtin",
                    name="exact_match",
                    config={},
                    weight=0.7,
                ),
                EvaluatorConfig(
                    type="builtin",
                    name="contains",
                    config={"keywords": ["x"]},
                    weight=0.8,
                ),
            ]
        )
        with pytest.raises(LabsError) as ei:
            await runner_with_pipeline.create_experiment(
                request=request, user_id="u-1"
            )
        assert "가중치" in str(ei.value.detail)

    async def test_valid_weights_passes(
        self,
        runner_with_pipeline: BatchExperimentRunner,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
    ) -> None:
        _seed_dataset(langfuse_client, item_count=1)
        litellm_client.set_response("any")
        request = _make_request(
            evaluators=[
                EvaluatorConfig(
                    type="builtin",
                    name="exact_match",
                    config={},
                    weight=0.4,
                ),
                EvaluatorConfig(
                    type="builtin",
                    name="contains",
                    config={"keywords": ["any"]},
                    weight=0.6,
                ),
            ]
        )
        response = await runner_with_pipeline.create_experiment(
            request=request, user_id="u-1"
        )
        for task in list(runner_with_pipeline._tasks.values()):
            await task

        # 정상 종료 — completed
        underlying = _underlying(redis_client := runner_with_pipeline._redis)
        raw = await underlying.hgetall(
            _full_key(redis_client, _exp_key(response.experiment_id))
        )
        assert raw["status"] == "completed"


# ---------- 3) Pipeline lazy 생성 ----------
@pytest.mark.unit
class TestBatchEvaluatorPipelineLazyInit:
    """``evaluation_pipeline`` 미주입 시 lazy 생성."""

    async def test_lazy_pipeline_created_when_needed(
        self,
        langfuse_client: MockLangfuseClient,
        litellm_client: MockLiteLLMProxy,
        redis_client: MockRedisClient,
    ) -> None:
        runner = BatchExperimentRunner(
            langfuse=langfuse_client,
            litellm=litellm_client,
            redis=redis_client,
            context_engine=ContextEngine(),
            evaluation_pipeline=None,  # 미주입
        )
        _seed_dataset(langfuse_client, item_count=1, expected="x")
        litellm_client.set_response("x")
        request = _make_request(
            evaluators=[
                EvaluatorConfig(
                    type="builtin",
                    name="exact_match",
                    config={},
                    weight=1.0,
                )
            ]
        )
        response = await runner.create_experiment(
            request=request, user_id="u-1"
        )
        for task in list(runner._tasks.values()):
            await task

        # lazy 생성 후 평가 동작
        assert runner._eval_pipeline is not None
        underlying = _underlying(redis_client)
        run_raw = await underlying.hgetall(
            _full_key(
                redis_client,
                _run_key(response.experiment_id, response.runs[0].run_name),
            )
        )
        assert int(run_raw["scored_count"]) == 1
        assert float(run_raw["total_score_sum"]) == pytest.approx(1.0)
