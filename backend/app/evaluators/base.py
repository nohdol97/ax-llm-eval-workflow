"""Evaluator 공통 프로토콜 / 예외 / 헬퍼.

모든 평가자는 :class:`Evaluator` 프로토콜을 따라야 하며, ``evaluate`` 메서드는
0.0~1.0 범위의 float (또는 평가 불가 시 ``None``)을 반환한다.

설계 원칙:
- 동기 vs 비동기: ``evaluate``는 일관성을 위해 항상 ``async``로 정의한다.
  비동기 I/O가 필요 없는 evaluator(exact_match 등)도 async 함수로 노출된다.
- 반환 None의 의미: "skipped" — 입력 형식 부적합 / 외부 의존성 실패 등으로 평가
  불가. Pipeline에서 weighted_score 계산 시 가중치 재정규화 대상이 된다.
- 입력 클램핑: evaluator 내부 계산값이 0.0 미만 또는 1.0 초과인 경우 :func:`clamp`로
  강제 정규화한다.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Evaluator(Protocol):
    """모든 evaluator의 공통 인터페이스 (Protocol).

    구현 클래스는 ``name`` 클래스 속성과 ``async def evaluate(...)`` 메서드를 갖는다.
    """

    name: str

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        """평가 실행.

        Args:
            output: 모델 출력 (문자열 / JSON dict / list)
            expected: 정답 / 기대값 (선택적 — 일부 evaluator는 미사용)
            metadata: 실행 메타데이터 (latency_ms / output_tokens / cost_usd 등)
            **config: evaluator 별 임계값/옵션

        Returns:
            0.0~1.0 범위의 float, 또는 평가 불가 시 ``None``.
        """
        ...


class EvaluatorError(Exception):
    """Evaluator 실행 중 발생한 예외의 베이스."""


class EvaluatorTimeoutError(EvaluatorError):
    """Evaluator 실행이 timeout 한도를 초과한 경우."""


class EvaluatorOutputError(EvaluatorError):
    """Evaluator 입력(output/expected) 형식이 부적합한 경우."""


def clamp(value: float | None) -> float | None:
    """0.0~1.0 범위로 클램핑. ``None``은 그대로 반환한다.

    NaN / Infinity는 ``None``으로 변환한다 (값 비교가 정의되지 않으므로).
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # NaN / Infinity 차단
    if v != v or v in (float("inf"), float("-inf")):
        return None
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v
