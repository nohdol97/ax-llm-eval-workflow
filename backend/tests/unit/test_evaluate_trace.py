"""EvaluationPipeline.evaluate_trace 단위 테스트 (Phase 8-A-2).

검증 항목
- 다중 evaluator 병렬 (trace_builtin + builtin + judge 혼합)
- weighted_score 계산 (null 제외 재정규화)
- Langfuse score 기록 (mock_langfuse._get_scores())
- timeout 처리
- evaluator 실패 → None 처리
- LLM Judge evaluator (litellm 주입)
- Custom code runner trace 분기
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.evaluators.pipeline import (
    WEIGHTED_SCORE_NAME,
    EvaluationPipeline,
)
from app.models.experiment import EvaluatorConfig
from tests.fixtures.mock_langfuse import MockLangfuseClient
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.trace_helper import make_trace

pytestmark = pytest.mark.unit


def _ev(
    name: str,
    weight: float = 1.0,
    type_: str = "trace_builtin",
    **config: Any,
) -> EvaluatorConfig:
    return EvaluatorConfig(type=type_, name=name, weight=weight, config=config)


# --------------------------------------------------------------------------- #
# 기본 동작
# --------------------------------------------------------------------------- #
class TestEvaluateTraceBasic:
    @pytest.mark.asyncio
    async def test_empty_evaluators_returns_empty_dict(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace()
        scores = await pipeline.evaluate_trace([], trace, None)
        assert scores == {}

    @pytest.mark.asyncio
    async def test_single_trace_builtin(self, langfuse_client: MockLangfuseClient) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace(tool_calls=[("web_search", {}, "ok")])
        evs = [_ev("tool_called", tool_name="web_search")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["tool_called"] == 1.0
        assert scores[WEIGHTED_SCORE_NAME] == 1.0

    @pytest.mark.asyncio
    async def test_unknown_trace_builtin_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace()
        evs = [_ev("nonexistent_trace_eval")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["nonexistent_trace_eval"] is None
        assert scores[WEIGHTED_SCORE_NAME] is None


# --------------------------------------------------------------------------- #
# 혼합 evaluator (trace_builtin + builtin)
# --------------------------------------------------------------------------- #
class TestEvaluateTraceMixedTypes:
    @pytest.mark.asyncio
    async def test_trace_builtin_and_builtin_via_adapter(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace(
            output="hello",
            tool_calls=[("web_search", {}, "ok")],
        )
        evs = [
            _ev("tool_called", type_="trace_builtin", tool_name="web_search"),
            _ev("exact_match", type_="builtin"),
        ]
        scores = await pipeline.evaluate_trace(evs, trace, {"expected_output": "hello"})
        assert scores["tool_called"] == 1.0
        assert scores["exact_match"] == 1.0
        assert scores[WEIGHTED_SCORE_NAME] == 1.0

    @pytest.mark.asyncio
    async def test_partial_None_excluded_from_weighted(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace(error_spans=[])  # error 없음 → recovery=None
        evs = [
            _ev("agent_loop_bounded", max_generations=10),
            _ev("error_recovery_attempted"),  # → None
        ]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["agent_loop_bounded"] == 1.0
        assert scores["error_recovery_attempted"] is None
        # weighted = 1.0 (None 제외 재정규화)
        assert scores[WEIGHTED_SCORE_NAME] == 1.0

    @pytest.mark.asyncio
    async def test_explicit_weights_used(self, langfuse_client: MockLangfuseClient) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace(
            output="hello",
            tool_calls=[("a", {}, "")],
        )
        # tool_called=1.0 (weight 0.7), no_error_spans=1.0 (weight 0.3)
        evs = [
            _ev("tool_called", weight=0.7, tool_name="a"),
            _ev("no_error_spans", weight=0.3),
        ]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores[WEIGHTED_SCORE_NAME] == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Langfuse 기록
# --------------------------------------------------------------------------- #
class TestEvaluateTraceLangfuseRecording:
    @pytest.mark.asyncio
    async def test_records_scores_to_langfuse(self, langfuse_client: MockLangfuseClient) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace_id = langfuse_client.create_trace(name="t")
        trace = make_trace(
            trace_id=trace_id,
            tool_calls=[("a", {}, "")],
        )
        evs = [_ev("tool_called", tool_name="a")]
        await pipeline.evaluate_trace(evs, trace, None)

        recorded = langfuse_client._get_scores()
        names = {s.name for s in recorded}
        assert "tool_called" in names
        assert WEIGHTED_SCORE_NAME in names

    @pytest.mark.asyncio
    async def test_skips_None_scores_in_recording(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace_id = langfuse_client.create_trace(name="t")
        trace = make_trace(trace_id=trace_id)
        # error_recovery_attempted: error 없음 → None
        evs = [
            _ev("error_recovery_attempted"),
            _ev("agent_loop_bounded"),
        ]
        await pipeline.evaluate_trace(evs, trace, None)
        recorded = langfuse_client._get_scores()
        names = {s.name for s in recorded}
        assert "error_recovery_attempted" not in names  # None은 미기록
        assert "agent_loop_bounded" in names

    @pytest.mark.asyncio
    async def test_no_trace_id_no_recording(self, langfuse_client: MockLangfuseClient) -> None:
        """trace.id가 빈 문자열이면 langfuse 기록 안 함."""
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        trace = make_trace(trace_id="")
        evs = [_ev("agent_loop_bounded")]
        await pipeline.evaluate_trace(evs, trace, None)
        assert langfuse_client._get_scores() == []

    @pytest.mark.asyncio
    async def test_langfuse_failure_does_not_break_evaluation(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        """trace_id가 mock에 없으면 score 호출이 LangfuseNotFoundError를 raise.

        이 경우 pipeline은 catch하여 평가 결과는 정상 반환해야 한다.
        """
        pipeline = EvaluationPipeline(langfuse=langfuse_client)
        # trace_id 가 mock에 없는 임의 값
        trace = make_trace(trace_id="nonexistent-trace-id")
        evs = [_ev("agent_loop_bounded")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["agent_loop_bounded"] == 1.0  # 평가 자체는 성공


# --------------------------------------------------------------------------- #
# Timeout / 실패
# --------------------------------------------------------------------------- #
class _SlowTraceEvaluator:
    """timeout 시뮬레이션 — 매 호출마다 sleep 후 반환.

    pipeline 이 ``cls()`` 로 인스턴스화하므로 인자 없는 생성자 필요.
    ``SLEEP_SEC`` 클래스 속성으로 sleep 시간 제어.
    """

    name = "slow_trace_eval"
    SLEEP_SEC: float = 0.5

    async def evaluate_trace(self, trace: Any, expected: Any, config: Any) -> float | None:
        await asyncio.sleep(self.SLEEP_SEC)
        return 1.0


class TestEvaluateTraceTimeout:
    @pytest.mark.asyncio
    async def test_evaluator_timeout_returns_None(
        self, langfuse_client: MockLangfuseClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 강제로 registry에 slow evaluator 등록 (pipeline 모듈의 import 별칭 기준).
        from app.evaluators import pipeline as pipeline_mod

        registry = dict(pipeline_mod.TRACE_BUILT_IN_REGISTRY)
        registry["slow_trace_eval"] = _SlowTraceEvaluator
        monkeypatch.setattr(pipeline_mod, "TRACE_BUILT_IN_REGISTRY", registry)

        pipeline = EvaluationPipeline(langfuse=langfuse_client, timeout_sec=0.05)
        trace = make_trace()
        evs = [_ev("slow_trace_eval")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["slow_trace_eval"] is None
        assert scores[WEIGHTED_SCORE_NAME] is None


# --------------------------------------------------------------------------- #
# LLM Judge 의존 evaluator (litellm 주입)
# --------------------------------------------------------------------------- #
class TestEvaluateTraceLLMJudge:
    @pytest.mark.asyncio
    async def test_grounding_with_litellm_returns_score(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_response('{"score": 7, "reasoning": "ok"}')
        pipeline = EvaluationPipeline(langfuse=langfuse_client, litellm_client=litellm)
        trace = make_trace(
            tool_calls=[("web", {}, "fact")],
            output="based on the web result",
        )
        evs = [_ev("tool_result_grounding", judge_model="gpt-4o-mini")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["tool_result_grounding"] == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_grounding_without_litellm_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        # litellm 미주입 → grounding evaluator는 None
        pipeline = EvaluationPipeline(langfuse=langfuse_client, litellm_client=None)
        trace = make_trace(tool_calls=[("web", {}, "fact")], output="answer")
        evs = [_ev("tool_result_grounding")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["tool_result_grounding"] is None


# --------------------------------------------------------------------------- #
# Custom Code / Judge runner trace 분기
# --------------------------------------------------------------------------- #
class _CustomRunner:
    """custom_code_runner 시뮬레이션 — output을 받아 정규화 점수 반환."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> float:
        self.calls.append(kwargs)
        return self.value


class TestEvaluateTraceCustomCode:
    @pytest.mark.asyncio
    async def test_approved_custom_code_runner_invoked_with_trace_output(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        runner = _CustomRunner(value=0.6)
        pipeline = EvaluationPipeline(langfuse=langfuse_client, custom_code_runner=runner)
        trace = make_trace(output="hello", tool_calls=[("a", {}, "")])
        evs = [_ev("custom_eval_1", type_="approved")]
        scores = await pipeline.evaluate_trace(evs, trace, {"expected_output": "ref"})
        assert scores["custom_eval_1"] == 0.6
        assert len(runner.calls) == 1
        # output은 trace.output, expected는 expected_output, metadata에 trace 통계
        kwargs = runner.calls[0]
        assert kwargs["output"] == "hello"
        assert kwargs["expected"] == "ref"
        assert kwargs["metadata"]["tool_call_count"] == 1

    @pytest.mark.asyncio
    async def test_judge_runner_invoked_for_judge_type(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        runner = _CustomRunner(value=0.9)
        pipeline = EvaluationPipeline(langfuse=langfuse_client, judge_runner=runner)
        trace = make_trace(output="hi")
        evs = [_ev("relevance", type_="judge")]
        scores = await pipeline.evaluate_trace(evs, trace, {"expected_output": "hi"})
        assert scores["relevance"] == 0.9
        assert len(runner.calls) == 1

    @pytest.mark.asyncio
    async def test_custom_runner_missing_returns_None(
        self, langfuse_client: MockLangfuseClient
    ) -> None:
        pipeline = EvaluationPipeline(langfuse=langfuse_client, custom_code_runner=None)
        # 자동 import 도 우회
        pipeline._custom_runner = None  # type: ignore[assignment]
        trace = make_trace()
        evs = [_ev("c1", type_="approved")]
        scores = await pipeline.evaluate_trace(evs, trace, None)
        assert scores["c1"] is None
