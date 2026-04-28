"""신규 trace evaluator 10종 단위 테스트 (Phase 8-A-2).

각 evaluator의 happy path / edge case / error path 를 검증한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.evaluators.trace_base import OutputAdapter, TraceEvaluatorError
from app.evaluators.trace_built_in import (
    AgentLoopBoundedEvaluator,
    ErrorRecoveryAttemptedEvaluator,
    HallucinationCheckEvaluator,
    LatencyBreakdownHealthyEvaluator,
    NoErrorSpansEvaluator,
    ToolCallCountInRangeEvaluator,
    ToolCalledEvaluator,
    ToolCalledWithArgsEvaluator,
    ToolCallSequenceEvaluator,
    ToolResultGroundingEvaluator,
)
from tests.fixtures.mock_litellm import MockLiteLLMProxy
from tests.fixtures.trace_helper import make_observation, make_trace

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# 1. ToolCalledEvaluator
# --------------------------------------------------------------------------- #
class TestToolCalled:
    @pytest.mark.asyncio
    async def test_called_returns_1(self) -> None:
        ev = ToolCalledEvaluator()
        trace = make_trace(tool_calls=[("web_search", {}, "result")])
        score = await ev.evaluate_trace(trace, None, {"tool_name": "web_search"})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_not_called_returns_0(self) -> None:
        ev = ToolCalledEvaluator()
        trace = make_trace(tool_calls=[("calc", {}, "1")])
        score = await ev.evaluate_trace(trace, None, {"tool_name": "web_search"})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_no_tools_at_all_returns_0(self) -> None:
        ev = ToolCalledEvaluator()
        trace = make_trace(tool_calls=[])
        score = await ev.evaluate_trace(trace, None, {"tool_name": "web_search"})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_expected_dict_fallback(self) -> None:
        ev = ToolCalledEvaluator()
        trace = make_trace(tool_calls=[("web_search", {}, "ok")])
        score = await ev.evaluate_trace(trace, {"tool_name": "web_search"}, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_missing_tool_name_raises(self) -> None:
        ev = ToolCalledEvaluator()
        trace = make_trace()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(trace, None, {})


# --------------------------------------------------------------------------- #
# 2. ToolCalledWithArgsEvaluator
# --------------------------------------------------------------------------- #
class TestToolCalledWithArgs:
    @pytest.mark.asyncio
    async def test_exact_match_full_score(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[("web_search", {"query": "weather", "lang": "en"}, "ok")])
        score = await ev.evaluate_trace(
            trace,
            None,
            {"tool_name": "web_search", "args_match": {"query": "weather", "lang": "en"}},
        )
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_partial_match_yields_ratio(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[("web_search", {"query": "weather", "lang": "fr"}, "ok")])
        score = await ev.evaluate_trace(
            trace,
            None,
            {"tool_name": "web_search", "args_match": {"query": "weather", "lang": "en"}},
        )
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_regex_pattern_match(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[("web_search", {"query": "today's weather"}, "ok")])
        score = await ev.evaluate_trace(
            trace,
            None,
            {"tool_name": "web_search", "args_match": {"query": ".*weather.*"}},
        )
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_anchor_regex(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[("calc", {"expr": "1+2"}, "3")])
        score = await ev.evaluate_trace(
            trace,
            None,
            {"tool_name": "calc", "args_match": {"expr": r"^\d+\+\d+$"}},
        )
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_wildcard_pattern(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[("web", {"q": "hello world"}, "ok")])
        score = await ev.evaluate_trace(
            trace, None, {"tool_name": "web", "args_match": {"q": "hello*"}}
        )
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_tool_not_called_returns_0(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(tool_calls=[])
        score = await ev.evaluate_trace(trace, None, {"tool_name": "web", "args_match": {"q": "x"}})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_multiple_candidates_takes_best(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        trace = make_trace(
            tool_calls=[
                ("web", {"q": "wrong"}, "ok"),
                ("web", {"q": "right"}, "ok"),
            ]
        )
        score = await ev.evaluate_trace(
            trace, None, {"tool_name": "web", "args_match": {"q": "right"}}
        )
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_missing_tool_name_raises(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"args_match": {"q": "x"}})

    @pytest.mark.asyncio
    async def test_invalid_regex_falls_back_to_equality(self) -> None:
        ev = ToolCalledWithArgsEvaluator()
        # 의도적으로 잘못된 anchor 형태 + literal 값 — 정확 일치 시 매치
        trace = make_trace(tool_calls=[("web", {"q": "^bad("}, "ok")])
        score = await ev.evaluate_trace(
            trace, None, {"tool_name": "web", "args_match": {"q": "^bad("}}
        )
        assert score == 1.0


# --------------------------------------------------------------------------- #
# 3. ToolCallSequenceEvaluator
# --------------------------------------------------------------------------- #
class TestToolCallSequence:
    @pytest.mark.asyncio
    async def test_strict_exact_match(self) -> None:
        ev = ToolCallSequenceEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("b", {}, ""), ("c", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"sequence": ["a", "b", "c"], "strict": True})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_strict_extra_tool_in_middle_fails(self) -> None:
        ev = ToolCallSequenceEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("x", {}, ""), ("b", {}, ""), ("c", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"sequence": ["a", "b", "c"], "strict": True})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_subsequence_with_extra_tools(self) -> None:
        ev = ToolCallSequenceEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("x", {}, ""), ("b", {}, ""), ("c", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"sequence": ["a", "b", "c"]})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_subsequence_missing_returns_0(self) -> None:
        ev = ToolCallSequenceEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("c", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"sequence": ["a", "b", "c"]})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_empty_sequence_raises(self) -> None:
        ev = ToolCallSequenceEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"sequence": []})

    @pytest.mark.asyncio
    async def test_subsequence_wrong_order_returns_0(self) -> None:
        ev = ToolCallSequenceEvaluator()
        trace = make_trace(tool_calls=[("c", {}, ""), ("b", {}, ""), ("a", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"sequence": ["a", "b", "c"]})
        assert score == 0.0


# --------------------------------------------------------------------------- #
# 4. ToolCallCountInRangeEvaluator
# --------------------------------------------------------------------------- #
class TestToolCallCountInRange:
    @pytest.mark.asyncio
    async def test_in_range_returns_1(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("b", {}, ""), ("c", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"min": 1, "max": 5})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_below_min_returns_0(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"min": 2, "max": 5})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_above_max_returns_0(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "")] * 10)
        score = await ev.evaluate_trace(trace, None, {"min": 1, "max": 5})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_boundary_min(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "")])
        score = await ev.evaluate_trace(trace, None, {"min": 1, "max": 1})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_filters_by_tool_name(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        trace = make_trace(tool_calls=[("a", {}, ""), ("a", {}, ""), ("b", {}, "")])
        # a만 카운트 → 2
        score = await ev.evaluate_trace(trace, None, {"min": 2, "max": 2, "tool_name": "a"})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_missing_min_max_raises(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"min": 1})

    @pytest.mark.asyncio
    async def test_min_greater_than_max_raises(self) -> None:
        ev = ToolCallCountInRangeEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"min": 5, "max": 3})


# --------------------------------------------------------------------------- #
# 5. NoErrorSpansEvaluator
# --------------------------------------------------------------------------- #
class TestNoErrorSpans:
    @pytest.mark.asyncio
    async def test_no_errors_returns_1(self) -> None:
        ev = NoErrorSpansEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "ok")])
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_with_error_returns_0(self) -> None:
        ev = NoErrorSpansEvaluator()
        trace = make_trace(error_spans=[("a", "boom")])
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_ignored_errors_dont_count(self) -> None:
        ev = NoErrorSpansEvaluator()
        trace = make_trace(error_spans=[("retryable", "transient")])
        score = await ev.evaluate_trace(trace, None, {"ignore_names": ["retryable"]})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_mixed_some_ignored(self) -> None:
        ev = NoErrorSpansEvaluator()
        trace = make_trace(error_spans=[("retryable", "x"), ("real_error", "boom")])
        score = await ev.evaluate_trace(trace, None, {"ignore_names": ["retryable"]})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_invalid_ignore_names_raises(self) -> None:
        ev = NoErrorSpansEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"ignore_names": "not-a-list"})


# --------------------------------------------------------------------------- #
# 6. ErrorRecoveryAttemptedEvaluator
# --------------------------------------------------------------------------- #
class TestErrorRecoveryAttempted:
    @pytest.mark.asyncio
    async def test_no_errors_returns_None(self) -> None:
        ev = ErrorRecoveryAttemptedEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "ok")])
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_recovered_returns_1(self) -> None:
        ev = ErrorRecoveryAttemptedEvaluator()
        # 시간 순: error span "search" → 이후 "search" success
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        err = make_observation(
            name="search",
            type="span",
            level="ERROR",
            latency_ms=10.0,
            start_time=base,
        )
        retry = make_observation(
            name="search",
            type="span",
            level="DEFAULT",
            latency_ms=10.0,
            start_time=base + timedelta(seconds=1),
        )
        trace = make_trace(
            tool_calls=[],
            extra_observations=[err, retry],
            base_time=base,
        )
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_no_recovery_returns_0(self) -> None:
        ev = ErrorRecoveryAttemptedEvaluator()
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        err = make_observation(
            name="search",
            type="span",
            level="ERROR",
            start_time=base,
        )
        trace = make_trace(extra_observations=[err], base_time=base)
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_partial_recovery_ratio(self) -> None:
        ev = ErrorRecoveryAttemptedEvaluator()
        base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        err1 = make_observation(name="a", type="span", level="ERROR", start_time=base)
        # a는 회복됨
        retry_a = make_observation(
            name="a", type="span", level="DEFAULT", start_time=base + timedelta(seconds=1)
        )
        err2 = make_observation(
            name="b", type="span", level="ERROR", start_time=base + timedelta(seconds=2)
        )
        # b는 회복 안 됨
        trace = make_trace(extra_observations=[err1, retry_a, err2], base_time=base)
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 0.5


# --------------------------------------------------------------------------- #
# 7. AgentLoopBoundedEvaluator
# --------------------------------------------------------------------------- #
class TestAgentLoopBounded:
    @pytest.mark.asyncio
    async def test_within_limit_returns_1(self) -> None:
        ev = AgentLoopBoundedEvaluator()
        trace = make_trace(llm_call_count=3)
        score = await ev.evaluate_trace(trace, None, {"max_generations": 5})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_at_boundary_returns_1(self) -> None:
        ev = AgentLoopBoundedEvaluator()
        trace = make_trace(llm_call_count=10)
        score = await ev.evaluate_trace(trace, None, {})  # default 10
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_exceeded_returns_0(self) -> None:
        ev = AgentLoopBoundedEvaluator()
        trace = make_trace(llm_call_count=15)
        score = await ev.evaluate_trace(trace, None, {"max_generations": 10})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_zero_calls_within_default(self) -> None:
        ev = AgentLoopBoundedEvaluator()
        trace = make_trace(llm_call_count=0)
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_negative_max_raises(self) -> None:
        ev = AgentLoopBoundedEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"max_generations": -1})


# --------------------------------------------------------------------------- #
# 8. LatencyBreakdownHealthyEvaluator
# --------------------------------------------------------------------------- #
class TestLatencyBreakdownHealthy:
    @pytest.mark.asyncio
    async def test_no_thresholds_returns_1(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        trace = make_trace(tool_calls=[("a", {}, "ok")], tool_latencies=[5000.0])
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_all_within_threshold_returns_1(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        trace = make_trace(
            tool_calls=[("a", {}, ""), ("b", {}, "")],
            tool_latencies=[100.0, 200.0],
        )
        score = await ev.evaluate_trace(trace, None, {"tool_max_ms": 500})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_some_violations_partial_score(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        trace = make_trace(
            tool_calls=[("a", {}, ""), ("b", {}, "")],
            tool_latencies=[100.0, 1000.0],
        )
        score = await ev.evaluate_trace(trace, None, {"tool_max_ms": 500})
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_all_violate_returns_0(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        trace = make_trace(
            tool_calls=[("a", {}, ""), ("b", {}, "")],
            tool_latencies=[1000.0, 2000.0],
        )
        score = await ev.evaluate_trace(trace, None, {"tool_max_ms": 500})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_mix_tool_and_llm_thresholds(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        # tool 1 violation (1000ms > 500), llm 0 violation (200ms < 1000)
        trace = make_trace(
            tool_calls=[("a", {}, "")],
            tool_latencies=[1000.0],
            llm_call_count=1,
            llm_latency_ms=200.0,
        )
        score = await ev.evaluate_trace(trace, None, {"tool_max_ms": 500, "llm_max_ms": 1000})
        # 1 violation / 2 applicable = 0.5 → 1 - 0.5 = 0.5
        assert score == 0.5

    @pytest.mark.asyncio
    async def test_observations_without_latency_skipped(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        # latency_ms=None인 observation은 무시되어야 함
        trace = make_trace(
            tool_calls=[("a", {}, "")],
            tool_latencies=[None],  # type: ignore[list-item]
        )
        score = await ev.evaluate_trace(trace, None, {"tool_max_ms": 500})
        # applicable=0 → 1.0
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_invalid_threshold_raises(self) -> None:
        ev = LatencyBreakdownHealthyEvaluator()
        with pytest.raises(TraceEvaluatorError):
            await ev.evaluate_trace(make_trace(), None, {"tool_max_ms": "not-a-number"})


# --------------------------------------------------------------------------- #
# 9. ToolResultGroundingEvaluator (LLM Judge)
# --------------------------------------------------------------------------- #
class TestToolResultGrounding:
    @pytest.mark.asyncio
    async def test_no_litellm_returns_None(self) -> None:
        ev = ToolResultGroundingEvaluator(litellm=None)
        trace = make_trace(tool_calls=[("web", {"q": "x"}, "result")], output="answer")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_no_tool_calls_returns_None(self) -> None:
        ev = ToolResultGroundingEvaluator(litellm=MockLiteLLMProxy())
        trace = make_trace(tool_calls=[], output="answer")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_no_output_returns_None(self) -> None:
        ev = ToolResultGroundingEvaluator(litellm=MockLiteLLMProxy())
        trace = make_trace(tool_calls=[("web", {}, "ok")], output=None)
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_judge_score_8_normalized_to_0_8(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_response('{"score": 8, "reasoning": "good grounding"}')
        ev = ToolResultGroundingEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {"q": "x"}, "result")], output="based on web")
        score = await ev.evaluate_trace(trace, None, {})
        assert score == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_judge_failure_returns_None(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_failure(RuntimeError("boom"))
        ev = ToolResultGroundingEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "ok")], output="x")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_unparseable_response_returns_None(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_response("totally non-json text without score")
        ev = ToolResultGroundingEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "ok")], output="x")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None


# --------------------------------------------------------------------------- #
# 10. HallucinationCheckEvaluator
# --------------------------------------------------------------------------- #
class TestHallucinationCheck:
    @pytest.mark.asyncio
    async def test_no_litellm_returns_None(self) -> None:
        ev = HallucinationCheckEvaluator(litellm=None)
        trace = make_trace(tool_calls=[("web", {}, "ok")], output="answer")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_no_tools_returns_None(self) -> None:
        ev = HallucinationCheckEvaluator(litellm=MockLiteLLMProxy())
        trace = make_trace(tool_calls=[], output="answer")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_judge_score_10_no_hallucination(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_response('{"score": 10, "reasoning": "all grounded"}')
        ev = HallucinationCheckEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "fact")], output="answer")
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_judge_score_0_full_hallucination(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_response('{"score": 0, "reasoning": "all hallucinated"}')
        ev = HallucinationCheckEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "fact")], output="x")
        score = await ev.evaluate_trace(trace, None, {})
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_judge_failure_returns_None(self) -> None:
        litellm = MockLiteLLMProxy()
        litellm.set_failure(RuntimeError("boom"))
        ev = HallucinationCheckEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "ok")], output="x")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None

    @pytest.mark.asyncio
    async def test_judge_score_out_of_range_returns_None(self) -> None:
        litellm = MockLiteLLMProxy()
        # 0~10 범위 밖
        litellm.set_response('{"score": 99, "reasoning": "weird"}')
        ev = HallucinationCheckEvaluator(litellm=litellm)
        trace = make_trace(tool_calls=[("web", {}, "ok")], output="x")
        score = await ev.evaluate_trace(trace, None, {})
        assert score is None


# --------------------------------------------------------------------------- #
# OutputAdapter (간단 통합 — 기존 evaluator 재사용)
# --------------------------------------------------------------------------- #
class TestOutputAdapter:
    @pytest.mark.asyncio
    async def test_adapts_exact_match_to_trace(self) -> None:
        from app.evaluators.built_in import ExactMatchEvaluator

        adapter = OutputAdapter(ExactMatchEvaluator())
        trace = make_trace(output="hello")
        score = await adapter.evaluate_trace(trace, {"expected_output": "hello"}, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_adapts_dict_output_via_json_serialization(self) -> None:
        from app.evaluators.built_in import JsonValidityEvaluator

        adapter = OutputAdapter(JsonValidityEvaluator())
        trace = make_trace(output={"k": "v"})
        score = await adapter.evaluate_trace(trace, None, {})
        assert score == 1.0

    @pytest.mark.asyncio
    async def test_adapter_name_inherits_from_inner(self) -> None:
        from app.evaluators.built_in import ContainsEvaluator

        inner = ContainsEvaluator()
        adapter = OutputAdapter(inner)
        assert adapter.name == inner.name == "contains"

    @pytest.mark.asyncio
    async def test_metadata_includes_trace_metrics(self) -> None:
        """metadata에 latency_ms / cost_usd / tool_call_count / llm_call_count 포함."""

        captured: dict[str, Any] = {}

        class _CaptureEvaluator:
            name = "capture"

            async def evaluate(
                self,
                output: Any,
                expected: Any,
                metadata: dict[str, Any],
                **config: Any,
            ) -> float | None:
                captured.update(metadata)
                return 1.0

        adapter = OutputAdapter(_CaptureEvaluator())
        trace = make_trace(
            tool_calls=[("a", {}, ""), ("b", {}, "")],
            llm_call_count=1,
            total_cost=0.5,
            total_latency=1234.0,
            metadata={"custom": "yes"},
        )
        await adapter.evaluate_trace(trace, None, {})
        assert captured["latency_ms"] == 1234.0
        assert captured["cost_usd"] == 0.5
        assert captured["tool_call_count"] == 2
        assert captured["llm_call_count"] == 1
        assert captured["custom"] == "yes"

    @pytest.mark.asyncio
    async def test_None_output_becomes_empty_string(self) -> None:
        captured: dict[str, Any] = {}

        class _CaptureEvaluator:
            name = "capture"

            async def evaluate(
                self,
                output: Any,
                expected: Any,
                metadata: dict[str, Any],
                **config: Any,
            ) -> float | None:
                captured["output"] = output
                return 1.0

        adapter = OutputAdapter(_CaptureEvaluator())
        trace = make_trace(output=None)
        await adapter.evaluate_trace(trace, None, {})
        assert captured["output"] == ""
