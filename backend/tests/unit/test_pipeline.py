"""EvaluationPipeline 단위 테스트.

검증 항목:
- 단일 evaluator 호출
- 다중 evaluator 병렬 실행
- 일부 실패 / 미존재 → None 처리
- timeout 처리
- weighted_score 계산 (null 제외 재정규화)
- Langfuse score 기록 (mock_langfuse._get_scores())
- judge / custom_code runner 미주입 시 graceful 처리
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.evaluators.pipeline import (
    DEFAULT_EVALUATOR_TIMEOUT_SEC,
    WEIGHTED_SCORE_NAME,
    EvaluationPipeline,
)
from app.models.experiment import EvaluatorConfig
from tests.fixtures.mock_langfuse import MockLangfuseClient

pytestmark = pytest.mark.unit


def _ev(name: str, weight: float = 1.0, type_: str = "builtin", **config: Any) -> EvaluatorConfig:
    return EvaluatorConfig(type=type_, name=name, weight=weight, config=config)


# --------------------------------------------------------------------------- #
# 단일 evaluator
# --------------------------------------------------------------------------- #
class TestPipelineSingleEvaluator:
    @pytest.mark.asyncio
    async def test_evaluate_item_runs_single_exact_match(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        evs = [_ev("exact_match")]
        scores = await pipeline.evaluate_item(
            evaluators=evs,
            output="hello",
            expected="hello",
            metadata={},
        )
        assert scores["exact_match"] == 1.0
        assert scores[WEIGHTED_SCORE_NAME] == 1.0

    @pytest.mark.asyncio
    async def test_evaluate_item_returns_empty_for_no_evaluators(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        scores = await pipeline.evaluate_item(evaluators=[], output="x", expected=None, metadata={})
        assert scores == {}


# --------------------------------------------------------------------------- #
# 다중 evaluator 병렬
# --------------------------------------------------------------------------- #
class TestPipelineMultiple:
    @pytest.mark.asyncio
    async def test_runs_multiple_evaluators_in_parallel(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        evs = [
            _ev("exact_match"),
            _ev("contains", keywords=["hello"]),
            _ev("json_validity"),
        ]
        scores = await pipeline.evaluate_item(
            evaluators=evs,
            output="hello",
            expected="hello",
            metadata={},
        )
        assert scores["exact_match"] == 1.0
        assert scores["contains"] == 1.0
        assert scores["json_validity"] == 0.0
        # weighted = (1+1+0)/3 ≈ 0.6667
        assert scores[WEIGHTED_SCORE_NAME] == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_partial_None_excluded_from_weighted_score(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        # latency_check: threshold_ms 미지정 → None 반환
        evs = [
            _ev("exact_match"),
            _ev("latency_check"),  # threshold 없음 → None
        ]
        scores = await pipeline.evaluate_item(
            evaluators=evs, output="x", expected="x", metadata={"latency_ms": 100}
        )
        assert scores["exact_match"] == 1.0
        assert scores["latency_check"] is None
        # weighted = exact_match만 → 1.0
        assert scores[WEIGHTED_SCORE_NAME] == 1.0

    @pytest.mark.asyncio
    async def test_explicit_weights_used_for_weighted_score(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        evs = [
            _ev("exact_match", weight=0.7),
            _ev("contains", weight=0.3, keywords=["x"]),
        ]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        # exact=1.0, contains=1.0 → 0.7+0.3 = 1.0
        assert scores[WEIGHTED_SCORE_NAME] == 1.0


# --------------------------------------------------------------------------- #
# 실패 / 예외 / timeout
# --------------------------------------------------------------------------- #
class _SlowJudgeRunner:
    """timeout 시뮬레이션용 — 항상 sleep 후 반환."""

    def __init__(self, sleep_sec: float) -> None:
        self.sleep_sec = sleep_sec

    async def __call__(self, **kwargs: Any) -> float:
        await asyncio.sleep(self.sleep_sec)
        return 1.0


class _FailingJudgeRunner:
    async def __call__(self, **kwargs: Any) -> float:
        raise RuntimeError("judge boom")


class _OkJudgeRunner:
    """정상 동작 — 0.8 반환."""

    def __init__(self, value: float = 0.8) -> None:
        self.value = value
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> float:
        self.calls.append(kwargs)
        return self.value


class TestPipelineErrors:
    @pytest.mark.asyncio
    async def test_unknown_builtin_returns_None(self, langfuse_client: MockLangfuseClient) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        evs = [_ev("nonexistent_evaluator")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["nonexistent_evaluator"] is None
        assert scores[WEIGHTED_SCORE_NAME] is None

    @pytest.mark.asyncio
    async def test_judge_runner_missing_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        # judge_runner=None 강제 (자동 import도 우회)
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        pipeline._judge_runner = None  # type: ignore[assignment]

        evs = [_ev("test_judge", type_="judge")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["test_judge"] is None

    @pytest.mark.asyncio
    async def test_custom_runner_missing_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client, custom_code_runner=None)
        pipeline._custom_runner = None  # type: ignore[assignment]
        evs = [_ev("custom1", type_="approved")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["custom1"] is None

    @pytest.mark.asyncio
    async def test_judge_runner_failure_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client, judge_runner=_FailingJudgeRunner())
        evs = [_ev("judgeA", type_="judge")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["judgeA"] is None

    @pytest.mark.asyncio
    async def test_judge_runner_success_returns_clamped_value(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        runner = _OkJudgeRunner(value=0.8)
        pipeline = EvaluationPipeline(langfuse=langfuse_client, judge_runner=runner)
        evs = [_ev("judgeA", type_="judge")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["judgeA"] == 0.8
        assert len(runner.calls) == 1

    @pytest.mark.asyncio
    async def test_evaluator_timeout_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        slow = _SlowJudgeRunner(sleep_sec=0.5)
        pipeline = EvaluationPipeline(
            langfuse=langfuse_client,
            judge_runner=slow,
            timeout_sec=0.05,  # 50ms
        )
        evs = [_ev("slow_judge", type_="judge")]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        assert scores["slow_judge"] is None
        assert scores[WEIGHTED_SCORE_NAME] is None

    @pytest.mark.asyncio
    async def test_invalid_weights_yields_None_weighted_score(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        # 두 개 모두 명시 + 합계 != 1.0 → ValueError → weight_error
        evs = [
            _ev("exact_match", weight=0.4),
            _ev("contains", weight=0.4, keywords=["x"]),
        ]
        scores = await pipeline.evaluate_item(evaluators=evs, output="x", expected="x", metadata={})
        # 개별 score는 정상 계산
        assert scores["exact_match"] == 1.0
        assert scores["contains"] == 1.0
        # 가중 평균은 None (잘못된 weight)
        assert scores[WEIGHTED_SCORE_NAME] is None


# --------------------------------------------------------------------------- #
# Langfuse score 기록
# --------------------------------------------------------------------------- #
class TestPipelineLangfuseRecording:
    @pytest.mark.asyncio
    async def test_records_scores_to_langfuse_when_trace_id_provided(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        # trace 사전 생성
        trace_id = langfuse_client.create_trace(name="test")

        evs = [_ev("exact_match"), _ev("json_validity")]
        await pipeline.evaluate_item(
            evaluators=evs,
            output="hello",
            expected="hello",
            metadata={},
            trace_id=trace_id,
        )

        recorded = langfuse_client._get_scores()
        score_names = {s.name for s in recorded}
        assert "exact_match" in score_names
        assert "json_validity" in score_names
        assert WEIGHTED_SCORE_NAME in score_names

    @pytest.mark.asyncio
    async def test_skips_recording_when_no_trace_id(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        await pipeline.evaluate_item(
            evaluators=[_ev("exact_match")],
            output="x",
            expected="x",
            metadata={},
        )
        # trace 자체가 없으므로 score도 없음
        assert langfuse_client._get_scores() == []

    @pytest.mark.asyncio
    async def test_skips_None_scores_in_recording(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace_id = langfuse_client.create_trace(name="t")
        # latency_check: threshold 미지정 → None
        evs = [_ev("exact_match"), _ev("latency_check")]
        await pipeline.evaluate_item(
            evaluators=evs,
            output="x",
            expected="x",
            metadata={"latency_ms": 100},
            trace_id=trace_id,
        )
        recorded = langfuse_client._get_scores()
        names = {s.name for s in recorded}
        assert "exact_match" in names
        assert "latency_check" not in names  # None은 기록 안 됨
        assert WEIGHTED_SCORE_NAME in names

    @pytest.mark.asyncio
    async def test_langfuse_score_failure_does_not_break_evaluation(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        # trace_id가 잘못된 값이면 mock이 Error raise — pipeline은 catch
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        scores = await pipeline.evaluate_item(
            evaluators=[_ev("exact_match")],
            output="x",
            expected="x",
            metadata={},
            trace_id="nonexistent-trace-id",
        )
        # 평가 자체는 성공
        assert scores["exact_match"] == 1.0


# --------------------------------------------------------------------------- #
# calculate_weighted_score 정적 헬퍼
# --------------------------------------------------------------------------- #
class TestPipelineStaticHelpers:
    def test_calculate_weighted_score_static_method(self) -> None:
        result = EvaluationPipeline.calculate_weighted_score(
            scores={"a": 1.0, "b": 0.5},
            weights={"a": 0.5, "b": 0.5},
        )
        assert result == pytest.approx(0.75)

    def test_default_timeout_constant(self) -> None:
        assert DEFAULT_EVALUATOR_TIMEOUT_SEC == 5.0

    def test_weighted_score_name_constant(self) -> None:
        assert WEIGHTED_SCORE_NAME == "weighted_score"


# --------------------------------------------------------------------------- #
# litellm 자동 주입 (cosine_similarity)
# --------------------------------------------------------------------------- #
class _StubEmbeddingClient:
    async def embedding(
        self,
        model: str,
        input: list[str] | str,  # noqa: A002
    ) -> dict[str, Any]:
        return {
            "data": [
                {"embedding": [1.0, 0.0], "index": 0},
                {"embedding": [1.0, 0.0], "index": 1},
            ]
        }


class TestPipelineLiteLLMInjection:
    @pytest.mark.asyncio
    async def test_cosine_similarity_uses_injected_litellm(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(
            langfuse=langfuse_client,
            litellm_client=_StubEmbeddingClient(),
        )
        evs = [_ev("cosine_similarity")]
        scores = await pipeline.evaluate_item(
            evaluators=evs,
            output="hello",
            expected="hello",
            metadata={},
        )
        assert scores["cosine_similarity"] == 1.0

    @pytest.mark.asyncio
    async def test_cosine_similarity_returns_None_without_litellm(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        evs = [_ev("cosine_similarity")]
        scores = await pipeline.evaluate_item(
            evaluators=evs,
            output="hello",
            expected="hello",
            metadata={},
        )
        assert scores["cosine_similarity"] is None
