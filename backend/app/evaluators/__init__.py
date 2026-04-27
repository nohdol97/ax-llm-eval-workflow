"""평가자(Evaluator) 패키지.

Phase 5의 코어 — 13개 내장 평가 함수 + LLM Judge + Custom Code 평가자를 정의한다.
모든 평가자는 :class:`app.evaluators.base.Evaluator` 프로토콜을 따른다.

공개 모듈:
- ``base``: 공통 프로토콜 / 예외 / 헬퍼
- ``built_in``: 13개 내장 evaluator 클래스
- ``registry``: 카탈로그 (이름 → 클래스)
- ``pipeline``: 다중 evaluator 병렬 실행 + Langfuse score 기록
- ``score_calculator``: weighted_score 계산 / weight 검증
"""

from __future__ import annotations

from app.evaluators.base import (
    Evaluator,
    EvaluatorError,
    EvaluatorOutputError,
    EvaluatorTimeoutError,
    clamp,
)

__all__ = [
    "Evaluator",
    "EvaluatorError",
    "EvaluatorOutputError",
    "EvaluatorTimeoutError",
    "clamp",
]
