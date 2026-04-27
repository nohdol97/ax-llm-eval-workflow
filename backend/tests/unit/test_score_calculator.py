"""score_calculator: weight 검증 + weighted_score 계산 단위 테스트."""

from __future__ import annotations

import pytest

from app.evaluators.score_calculator import (
    calculate_weighted_score,
    validate_weights,
)
from app.models.experiment import EvaluatorConfig

pytestmark = pytest.mark.unit


def _make_ev(name: str, weight: float = 1.0) -> EvaluatorConfig:
    return EvaluatorConfig(type="builtin", name=name, weight=weight)


# --------------------------------------------------------------------------- #
# validate_weights
# --------------------------------------------------------------------------- #
class TestValidateWeights:
    def test_returns_empty_dict_for_empty_list(self) -> None:
        assert validate_weights([]) == {}

    def test_all_default_weights_distributes_evenly(self) -> None:
        evs = [_make_ev("a"), _make_ev("b"), _make_ev("c")]
        weights = validate_weights(evs)
        assert weights == pytest.approx({"a": 1 / 3, "b": 1 / 3, "c": 1 / 3})

    def test_all_explicit_weights_summing_to_1_passes(self) -> None:
        evs = [_make_ev("a", 0.5), _make_ev("b", 0.3), _make_ev("c", 0.2)]
        weights = validate_weights(evs)
        assert weights == pytest.approx({"a": 0.5, "b": 0.3, "c": 0.2})

    def test_all_explicit_weights_within_tolerance(self) -> None:
        # 0.0005 오차 — 허용 (1e-3)
        evs = [_make_ev("a", 0.5005), _make_ev("b", 0.5)]
        weights = validate_weights(evs)
        assert "a" in weights
        assert "b" in weights

    def test_all_explicit_weights_not_summing_to_1_raises(self) -> None:
        evs = [_make_ev("a", 0.4), _make_ev("b", 0.3)]
        with pytest.raises(ValueError, match="가중치 합계가 1.0이 아닙니다"):
            validate_weights(evs)

    def test_partial_explicit_distributes_remainder(self) -> None:
        # a=0.6 명시, b/c는 default → (1-0.6)/2 = 0.2 each
        evs = [_make_ev("a", 0.6), _make_ev("b"), _make_ev("c")]
        weights = validate_weights(evs)
        assert weights["a"] == pytest.approx(0.6)
        assert weights["b"] == pytest.approx(0.2)
        assert weights["c"] == pytest.approx(0.2)

    def test_partial_explicit_with_zero_remainder(self) -> None:
        # a=1.0이 explicit인 경우 — but default와 동일이라 implicit로 분류됨
        # 별도 케이스: a=0.7, b=0.3, c=default(1.0) → c는 implicit
        # explicit_sum = 1.0 → remaining = 0.0 → c=0
        evs = [_make_ev("a", 0.7), _make_ev("b", 0.3), _make_ev("c")]
        weights = validate_weights(evs)
        assert weights["a"] == pytest.approx(0.7)
        assert weights["b"] == pytest.approx(0.3)
        assert weights["c"] == pytest.approx(0.0)

    def test_partial_explicit_sum_over_1_raises(self) -> None:
        evs = [_make_ev("a", 0.7), _make_ev("b", 0.5), _make_ev("c")]
        with pytest.raises(ValueError, match="합계 .* 1.0을 초과"):
            validate_weights(evs)

    def test_single_evaluator_default_gets_full_weight(self) -> None:
        weights = validate_weights([_make_ev("a")])
        assert weights == pytest.approx({"a": 1.0})


# --------------------------------------------------------------------------- #
# calculate_weighted_score
# --------------------------------------------------------------------------- #
class TestCalculateWeightedScore:
    def test_returns_None_for_empty_scores(self) -> None:
        assert calculate_weighted_score({}, {}) is None

    def test_basic_weighted_average(self) -> None:
        scores = {"a": 1.0, "b": 0.0}
        weights = {"a": 0.5, "b": 0.5}
        assert calculate_weighted_score(scores, weights) == pytest.approx(0.5)

    def test_uneven_weights(self) -> None:
        # a=1.0(weight=0.7), b=0.0(weight=0.3) → 0.7
        scores = {"a": 1.0, "b": 0.0}
        weights = {"a": 0.7, "b": 0.3}
        assert calculate_weighted_score(scores, weights) == pytest.approx(0.7)

    def test_None_scores_excluded_and_weights_renormalized(self) -> None:
        # b=None → renormalize → only a counts → 1.0
        scores = {"a": 1.0, "b": None}
        weights = {"a": 0.3, "b": 0.7}
        assert calculate_weighted_score(scores, weights) == pytest.approx(1.0)

    def test_partial_None_renormalization(self) -> None:
        # a=1.0(0.4), b=None, c=0.0(0.2) → (1*0.4 + 0*0.2)/0.6 = 0.6667
        scores = {"a": 1.0, "b": None, "c": 0.0}
        weights = {"a": 0.4, "b": 0.4, "c": 0.2}
        result = calculate_weighted_score(scores, weights)
        assert result == pytest.approx(0.4 / 0.6)

    def test_all_None_returns_None(self) -> None:
        assert (
            calculate_weighted_score(
                {"a": None, "b": None}, {"a": 0.5, "b": 0.5}
            )
            is None
        )

    def test_zero_total_weight_returns_None(self) -> None:
        assert (
            calculate_weighted_score({"a": 1.0}, {"a": 0.0}) is None
        )

    def test_clamps_out_of_range_score(self) -> None:
        # 1.5는 1.0으로 clamp
        scores = {"a": 1.5}
        weights = {"a": 1.0}
        assert calculate_weighted_score(scores, weights) == 1.0

    def test_ignores_score_without_weight_entry(self) -> None:
        scores = {"a": 1.0, "b": 0.0}
        weights = {"a": 1.0}  # b 미존재
        # b는 weights[b]=0 → skip → only a → 1.0
        assert calculate_weighted_score(scores, weights) == 1.0

    def test_handles_NaN_score_as_None(self) -> None:
        scores = {"a": float("nan"), "b": 1.0}
        weights = {"a": 0.5, "b": 0.5}
        # a는 clamp가 None 처리 → b만 사용 → 1.0
        assert calculate_weighted_score(scores, weights) == 1.0
