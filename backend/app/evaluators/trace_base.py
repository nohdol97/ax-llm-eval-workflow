"""Trace Evaluator 공통 인터페이스 + 기존 Evaluator 어댑터 (Phase 8-A-2).

기존 Evaluator는 ``output / expected / metadata`` 만을 입력으로 받지만, agent trace
평가는 trace tree 전체(observations / 비용 / 지연 / 태그)를 보아야 하므로 별도
프로토콜 :class:`TraceEvaluator` 를 정의한다.

설계 원칙
- 기존 13 built-in + LLM Judge 자산을 재활용한다 → :class:`OutputAdapter` 가
  ``trace.output`` 을 기존 ``Evaluator`` 인터페이스로 위임한다.
- ``evaluate_trace`` 는 항상 ``async`` (LLM 호출이 필요한 trace evaluator 대응).
- 반환 ``None`` 은 "skipped" — :func:`weighted_score` 계산에서 재정규화 대상.

설계 참고: ``docs/AGENT_EVAL.md`` §5.1.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

from app.evaluators.base import Evaluator
from app.models.trace import TraceTree


@runtime_checkable
class TraceEvaluator(Protocol):
    """trace tree 전체를 입력받는 evaluator (Protocol).

    구현 클래스는 ``name`` 클래스 속성과 ``async def evaluate_trace(...)`` 메서드를
    가져야 한다. ``isinstance(obj, TraceEvaluator)`` 로 런타임 검증 가능.
    """

    name: str

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        """trace 단위 평가 실행.

        Args:
            trace: 평가 대상 trace tree (observations 포함, 시간순 정렬됨).
            expected: 골든셋/데이터셋의 기대값. ``expected_output`` /
                ``expected_tool_calls`` 등의 키를 가질 수 있다 (선택).
            config: evaluator 별 설정 (``tool_name`` / ``max_generations`` 등).

        Returns:
            0.0~1.0 float, 또는 평가 불가 시 ``None``.
        """
        ...


class TraceEvaluatorError(Exception):
    """Trace evaluator 실행 중 발생한 예외 (config 누락 등)."""


class OutputAdapter:
    """기존 :class:`Evaluator` 를 :class:`TraceEvaluator` 인터페이스로 감싸는 어댑터.

    trace 평가 파이프라인이 ``built-in / LLM Judge / Custom Code`` 등 기존 evaluator
    자산을 재사용할 수 있도록 ``trace.output`` 을 기존 ``evaluate(...)`` 시그니처로
    위임한다.

    매핑 규칙
    - ``trace.output`` → ``inner.evaluate(output=...)`` (dict/list는 JSON 문자열화)
    - ``expected.get("expected_output")`` → ``inner.evaluate(expected=...)``
    - ``trace.metadata`` 에 ``latency_ms`` / ``cost_usd`` / ``tool_call_count`` /
      ``llm_call_count`` 를 합쳐 metadata 로 전달.

    Notes:
        ``trace.output`` 이 ``None`` 인 경우 빈 문자열로 정규화한다 — 기존 evaluator는
        ``str`` 입력을 가정하므로 None 전달 시 TypeError가 발생할 수 있다.
    """

    def __init__(self, inner: Evaluator):
        self._inner = inner
        # name은 inner의 클래스 속성 — 인스턴스 속성으로도 노출하여 동일 인터페이스 보장.
        self.name: str = inner.name

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        """``trace.output`` 을 기존 ``inner.evaluate`` 로 위임."""
        output_for_inner: str | dict[str, Any] | list[Any]
        if trace.output is None:
            output_for_inner = ""
        elif isinstance(trace.output, str):
            output_for_inner = trace.output
        else:
            # dict / list — JSON 직렬화 (한글/유니코드 보존)
            try:
                output_for_inner = json.dumps(trace.output, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                output_for_inner = str(trace.output)

        # expected 추출 — dict 형태에서 expected_output 키만 추출 (없으면 None)
        expected_value: str | dict[str, Any] | list[Any] | None = None
        if isinstance(expected, dict):
            expected_value = expected.get("expected_output")

        # metadata 합성 — trace 레벨 메타 + 사전 계산 통계
        merged_metadata: dict[str, Any] = {
            "latency_ms": trace.total_latency_ms,
            "cost_usd": trace.total_cost_usd,
            "tool_call_count": len(trace.tool_calls()),
            "llm_call_count": len(trace.llm_calls()),
        }
        # trace.metadata 가 우선 키를 덮어쓰지 않도록 base 위에 덧씌운다.
        for k, v in trace.metadata.items():
            merged_metadata.setdefault(k, v)

        return await self._inner.evaluate(
            output=output_for_inner,
            expected=expected_value,
            metadata=merged_metadata,
            **config,
        )


__all__ = [
    "OutputAdapter",
    "TraceEvaluator",
    "TraceEvaluatorError",
]
