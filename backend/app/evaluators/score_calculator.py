"""가중치 검증 + weighted_score 계산 헬퍼 (EVALUATION.md §5.4).

규칙 (EVALUATION.md §5.4 / FEATURES.md §5.2 기준):

- 가중치 합계 ``≈ 1.0`` (오차 0.001 허용) — 모든 evaluator weight 명시 시 검증
- 일부만 명시된 경우: 명시된 합계 ≤ 1.0, 미설정은 균등 분배
- 모두 미설정 (default): 균등 분배 (1/N)
- weighted_score = ``Σ(score_i × weight_i) / Σ(weight_i)`` (null 제외 재정규화)
- 모두 null이거나 weight 합이 0 → ``None``
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from app.evaluators.base import clamp

if TYPE_CHECKING:  # pragma: no cover
    from app.models.experiment import EvaluatorConfig

WEIGHT_SUM_TOLERANCE = 1e-3


def validate_weights(evaluators: list[EvaluatorConfig]) -> dict[str, float]:
    """evaluator 목록에서 가중치 dict ``{name: weight}`` 산출.

    규칙:
        1. evaluator가 0개 → ``{}``
        2. 모든 weight가 1.0(default) → 균등 분배 (1/N)
        3. 부분 명시 (1.0이 아닌 값이 일부에만 존재) → 명시된 weight 합계 ≤ 1.0 검증
           나머지는 ``(1.0 - 명시 합계) / 미명시 개수``로 균등 분배
        4. 모두 명시 (1.0이 아닌 값이 모든 evaluator에 존재) → 합계 ≈ 1.0 검증

    Raises:
        ValueError: 가중치 합계가 1.0을 초과하거나, 모두 명시 시 합계가 ≈ 1.0이 아닐 때.

    Returns:
        ``{evaluator_name: weight}`` 정규화된 가중치 dict.
    """
    if not evaluators:
        return {}

    n = len(evaluators)
    # default 1.0인 entry는 "미설정"으로 간주
    explicit: dict[str, float] = {}
    implicit: list[str] = []

    for ev in evaluators:
        if math.isclose(ev.weight, 1.0, abs_tol=1e-9):
            implicit.append(ev.name)
        else:
            explicit[ev.name] = float(ev.weight)

    # 케이스 2: 모두 default → 균등 분배
    if not explicit:
        even = 1.0 / n
        return {ev.name: even for ev in evaluators}

    explicit_sum = sum(explicit.values())

    # 케이스 4: 모두 명시
    if not implicit:
        if not math.isclose(explicit_sum, 1.0, abs_tol=WEIGHT_SUM_TOLERANCE):
            raise ValueError(
                f"가중치 합계가 1.0이 아닙니다 (실제: {explicit_sum:.6f}). "
                f"모든 evaluator weight를 명시할 경우 합계는 1.0이어야 합니다."
            )
        return dict(explicit)

    # 케이스 3: 부분 명시
    if explicit_sum > 1.0 + WEIGHT_SUM_TOLERANCE:
        raise ValueError(
            f"명시된 가중치 합계 {explicit_sum:.6f}가 1.0을 초과합니다. "
            "미설정 evaluator에 분배할 잔여 가중치가 음수가 됩니다."
        )

    remaining = max(0.0, 1.0 - explicit_sum)
    even = remaining / len(implicit) if implicit else 0.0
    result: dict[str, float] = {}
    for ev in evaluators:
        if ev.name in explicit:
            result[ev.name] = explicit[ev.name]
        else:
            result[ev.name] = even
    return result


def calculate_weighted_score(
    scores: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    """가중 평균 (null 제외 재정규화).

    공식 (EVALUATION.md §5.4):
        ``weighted = Σ(score_i × weight_i) / Σ(weight_i)``  (i: not None)

    - null이 아닌 evaluator의 weight만 사용하여 재정규화
    - 모두 null → ``None``
    - 사용 가능한 weight 합계가 0이면 ``None``
    - score는 0.0~1.0 범위로 클램핑 후 가중 평균
    """
    if not scores:
        return None

    numerator = 0.0
    denominator = 0.0
    for name, raw_score in scores.items():
        if raw_score is None:
            continue
        weight = float(weights.get(name, 0.0))
        if weight <= 0.0:
            continue
        clamped = clamp(raw_score)
        if clamped is None:
            continue
        numerator += clamped * weight
        denominator += weight

    if denominator <= 0.0:
        return None
    return clamp(numerator / denominator)


__all__ = [
    "WEIGHT_SUM_TOLERANCE",
    "calculate_weighted_score",
    "validate_weights",
]
