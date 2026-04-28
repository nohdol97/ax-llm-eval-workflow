"""평가 Pipeline — 다중 evaluator 병렬 실행 + Langfuse score 기록.

흐름 (EVALUATION.md §5):
    1. ``EvaluatorConfig`` 목록을 받아 evaluator 인스턴스 생성
    2. 각 evaluator를 ``asyncio.gather``로 병렬 실행 (5초 timeout/evaluator)
    3. 결과를 Langfuse score(trace_id, name, value)로 기록 (trace_id가 있을 때)
    4. weighted_score 계산 (null 제외 재정규화) → 별도 score로 기록
    5. ``{name: score | None}`` dict 반환

LLM Judge / Custom Code evaluator는 Agent 21이 만든 모듈에서 import하며, 미존재
(아직 구현되지 않은 시점)일 경우 우아하게 None 반환 + 경고 로그를 남긴다.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any

from app.core.logging import get_logger
from app.evaluators.base import clamp
from app.evaluators.registry import BUILT_IN_REGISTRY, TRACE_BUILT_IN_REGISTRY
from app.evaluators.score_calculator import (
    calculate_weighted_score,
    validate_weights,
)
from app.evaluators.trace_base import OutputAdapter, TraceEvaluatorError

if TYPE_CHECKING:  # pragma: no cover
    from app.models.experiment import EvaluatorConfig
    from app.models.trace import TraceTree
    from app.services.langfuse_client import LangfuseClient

logger = get_logger(__name__)

DEFAULT_EVALUATOR_TIMEOUT_SEC = 5.0
"""evaluator 단일 실행 타임아웃 (초)."""

WEIGHTED_SCORE_NAME = "weighted_score"
"""Langfuse에 기록되는 가중 평균 score 이름."""


class EvaluationPipeline:
    """다중 evaluator 병렬 실행 오케스트레이터.

    Args:
        langfuse: Langfuse 클라이언트 (실제 :class:`LangfuseClient` 또는 mock).
            ``score(trace_id, name, value, comment=None)`` 메서드만 사용.
        litellm_client: LiteLLM 클라이언트 (선택). ``cosine_similarity`` 등 임베딩
            기반 evaluator의 ``config["litellm_client"]``로 자동 주입된다.
        judge_runner: 선택적 LLM Judge 실행자(callable). Agent 21 구현 모듈에서
            주입한다. ``async (config, output, expected, metadata) -> float | None``.
        custom_code_runner: 선택적 Custom Code 실행자(callable). Agent 21 구현
            모듈에서 주입한다. 시그니처는 ``judge_runner``와 동일.
        timeout_sec: evaluator 단일 실행 타임아웃. 기본 ``DEFAULT_EVALUATOR_TIMEOUT_SEC``.
    """

    def __init__(
        self,
        langfuse: LangfuseClient | Any,
        *,
        litellm_client: Any | None = None,
        judge_runner: Any | None = None,
        custom_code_runner: Any | None = None,
        timeout_sec: float = DEFAULT_EVALUATOR_TIMEOUT_SEC,
    ) -> None:
        self._langfuse = langfuse
        self._litellm = litellm_client
        self._judge_runner = judge_runner or self._try_load_judge_runner()
        self._custom_runner = custom_code_runner or self._try_load_custom_runner()
        self._timeout_sec = max(0.1, float(timeout_sec))

    # ------------------------------------------------------------------ #
    # Agent 21 모듈 lazy import (없어도 안전)
    # ------------------------------------------------------------------ #
    # 외부 runner의 attribute 이름 — Agent 21이 모듈에 export한다는 가정.
    # 미존재 시 None fallback (graceful degradation).
    _JUDGE_MODULE = "app.evaluators.llm_judge"
    _JUDGE_RUNNER_ATTR = "run_llm_judge"
    _CUSTOM_MODULE = "app.evaluators.custom_code"
    _CUSTOM_RUNNER_ATTR = "run_custom_code"

    @classmethod
    def _try_load_judge_runner(cls) -> Any | None:
        return cls._try_import_attr(cls._JUDGE_MODULE, cls._JUDGE_RUNNER_ATTR)

    @classmethod
    def _try_load_custom_runner(cls) -> Any | None:
        return cls._try_import_attr(cls._CUSTOM_MODULE, cls._CUSTOM_RUNNER_ATTR)

    @staticmethod
    def _try_import_attr(module_name: str, attr_name: str) -> Any | None:
        """모듈 동적 import + attr 조회. 미존재 시 None.

        importlib을 사용해 mypy의 정적 검사를 우회한다 (Agent 21 모듈은 본 시점에
        존재하지 않을 수 있어 정적 검증이 불가능하다).
        """
        try:
            import importlib

            module = importlib.import_module(module_name)
        except ImportError:
            return None
        return getattr(module, attr_name, None)

    # ------------------------------------------------------------------ #
    # 메인 API
    # ------------------------------------------------------------------ #
    async def evaluate_item(
        self,
        evaluators: list[EvaluatorConfig],
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        trace_id: str | None = None,
    ) -> dict[str, float | None]:
        """단일 아이템에 대해 모든 evaluator 병렬 실행.

        Args:
            evaluators: ``EvaluatorConfig`` 목록.
            output: 모델 출력.
            expected: 정답 (선택적).
            metadata: latency_ms / output_tokens / cost_usd 등 부가 메타데이터.
            trace_id: Langfuse trace ID. 지정 시 각 score를 기록한다.

        Returns:
            ``{evaluator_name: score | None}`` — None은 평가 실패/skipped.
            ``WEIGHTED_SCORE_NAME``("weighted_score") 키도 함께 포함된다.
        """
        if not evaluators:
            return {}

        # 가중치 사전 검증 (실패해도 개별 evaluator 실행은 계속, 가중 평균만 None)
        try:
            weights = validate_weights(evaluators)
            weight_error: str | None = None
        except ValueError as exc:
            logger.warning("evaluation_pipeline_weight_invalid", error=str(exc))
            weights = {ev.name: 0.0 for ev in evaluators}
            weight_error = str(exc)

        # 각 evaluator 병렬 실행
        tasks = [self._execute_one(ev, output, expected, metadata) for ev in evaluators]
        raw_results = await asyncio.gather(*tasks, return_exceptions=False)

        scores: dict[str, float | None] = {}
        for ev, value in zip(evaluators, raw_results, strict=True):
            scores[ev.name] = value

        # weighted_score 계산
        if weight_error is None:
            weighted = calculate_weighted_score(scores, weights)
        else:
            weighted = None
        scores[WEIGHTED_SCORE_NAME] = weighted

        # Langfuse 기록 (trace_id 있을 때만)
        if trace_id:
            await self._record_scores(trace_id, scores)

        return scores

    # ------------------------------------------------------------------ #
    # 단일 evaluator 실행 (timeout / 예외 → None)
    # ------------------------------------------------------------------ #
    async def _execute_one(
        self,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        """단일 EvaluatorConfig 실행.

        - builtin → BUILT_IN_REGISTRY 조회 후 인스턴스화
        - judge → ``self._judge_runner`` 호출 (없으면 None + 경고)
        - approved / inline_custom → ``self._custom_runner`` 호출 (없으면 None + 경고)

        모든 예외/timeout은 캐치하여 ``None`` 반환 + 경고 로그.
        """
        try:
            coro = self._dispatch(ev, output, expected, metadata)
            value = await asyncio.wait_for(coro, timeout=self._timeout_sec)
        except TimeoutError:
            logger.warning(
                "evaluator_timeout",
                evaluator=ev.name,
                evaluator_type=ev.type,
                timeout_sec=self._timeout_sec,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "evaluator_failed",
                evaluator=ev.name,
                evaluator_type=ev.type,
                error=str(exc),
            )
            return None

        return clamp(value)

    async def _dispatch(
        self,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        """evaluator type별 실제 호출 분기."""
        ev_type = ev.type
        if ev_type == "builtin":
            return await self._run_builtin(ev, output, expected, metadata)
        if ev_type == "judge":
            return await self._run_judge(ev, output, expected, metadata)
        if ev_type in ("approved", "inline_custom"):
            return await self._run_custom(ev, output, expected, metadata)
        logger.warning("evaluator_unknown_type", evaluator=ev.name, type=ev_type)
        return None

    async def _run_builtin(
        self,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        cls = BUILT_IN_REGISTRY.get(ev.name)
        if cls is None:
            logger.warning("evaluator_builtin_unknown", evaluator=ev.name)
            return None
        instance = cls()
        config = dict(ev.config)
        # cosine_similarity 등이 LiteLLM client 필요 — 자동 주입 (미존재 시 evaluator가 None 처리)
        if self._litellm is not None and "litellm_client" not in config:
            config["litellm_client"] = self._litellm
        result = await instance.evaluate(
            output=output,
            expected=expected,
            metadata=metadata,
            **config,
        )
        if result is None:
            return None
        return float(result)

    async def _run_judge(
        self,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        if self._judge_runner is None:
            logger.warning("evaluator_judge_runner_missing", evaluator=ev.name)
            return None
        return await self._invoke_external_runner(
            self._judge_runner, ev, output, expected, metadata
        )

    async def _run_custom(
        self,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        if self._custom_runner is None:
            logger.warning(
                "evaluator_custom_runner_missing",
                evaluator=ev.name,
                type=ev.type,
            )
            return None
        return await self._invoke_external_runner(
            self._custom_runner, ev, output, expected, metadata
        )

    @staticmethod
    async def _invoke_external_runner(
        runner: Any,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        """외부 runner(judge / custom_code) 호출. sync/async 자동 판별."""
        result = runner(ev=ev, output=output, expected=expected, metadata=metadata)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return None
        try:
            return float(result)
        except (TypeError, ValueError):
            logger.warning(
                "evaluator_runner_return_invalid",
                evaluator=ev.name,
                value=str(result),
            )
            return None

    # ------------------------------------------------------------------ #
    # Trace 단위 평가 (Phase 8-A-2)
    # ------------------------------------------------------------------ #
    async def evaluate_trace(
        self,
        evaluators: list[EvaluatorConfig],
        trace: TraceTree,
        expected: dict[str, Any] | None = None,
    ) -> dict[str, float | None]:
        """trace 단위 평가 — trace evaluator + (어댑터로) 기존 evaluator 모두 실행.

        config.type 분기:
            - ``trace_builtin`` → :data:`TRACE_BUILT_IN_REGISTRY` 에서 클래스 조회 후 호출
            - ``builtin`` → 기존 :data:`BUILT_IN_REGISTRY` 인스턴스를 :class:`OutputAdapter`
              로 감싸 ``trace.output`` 에 적용
            - ``judge`` → :class:`LLMJudgeEvaluator` 를 :class:`OutputAdapter` 로 감싸 적용
            - ``approved`` / ``inline_custom`` → 기존 ``custom_runner`` 로 ``trace.output``
              평가 (custom code는 trace 인터페이스가 없으므로 output 적용)

        Args:
            evaluators: ``EvaluatorConfig`` 목록.
            trace: 평가 대상 :class:`TraceTree` (관측치 포함).
            expected: 골든셋 / 데이터셋의 기대값 (선택).

        Returns:
            ``{evaluator_name: score | None}`` + ``WEIGHTED_SCORE_NAME``.
            결과는 ``trace.id`` 가 truthy 면 Langfuse 에 기록된다.
        """
        if not evaluators:
            return {}

        # 가중치 검증 — 실패해도 개별 evaluator 실행은 계속 (weighted_score 만 None)
        try:
            weights = validate_weights(evaluators)
            weight_error: str | None = None
        except ValueError as exc:
            logger.warning("evaluation_pipeline_weight_invalid", error=str(exc))
            weights = {ev.name: 0.0 for ev in evaluators}
            weight_error = str(exc)

        # 각 evaluator 병렬 실행
        tasks = [self._execute_one_trace(ev, trace, expected) for ev in evaluators]
        raw_results = await asyncio.gather(*tasks, return_exceptions=False)

        scores: dict[str, float | None] = {}
        for ev, value in zip(evaluators, raw_results, strict=True):
            scores[ev.name] = value

        # weighted_score 계산
        if weight_error is None:
            weighted = calculate_weighted_score(scores, weights)
        else:
            weighted = None
        scores[WEIGHTED_SCORE_NAME] = weighted

        # Langfuse 기록 (trace.id 있을 때만)
        if trace.id:
            await self._record_scores(trace.id, scores)

        return scores

    async def _execute_one_trace(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """단일 EvaluatorConfig 를 trace 인터페이스로 실행.

        - timeout 초과 또는 예외 → ``None`` + 경고 로그.
        - 결과는 :func:`clamp` 로 0.0~1.0 정규화.
        """
        try:
            coro = self._dispatch_trace(ev, trace, expected)
            value = await asyncio.wait_for(coro, timeout=self._timeout_sec)
        except TimeoutError:
            logger.warning(
                "trace_evaluator_timeout",
                evaluator=ev.name,
                evaluator_type=ev.type,
                timeout_sec=self._timeout_sec,
            )
            return None
        except TraceEvaluatorError as exc:
            logger.warning(
                "trace_evaluator_config_invalid",
                evaluator=ev.name,
                error=str(exc),
            )
            return None
        except Exception as exc:  # noqa: BLE001 — evaluator 실패는 None 처리
            logger.warning(
                "trace_evaluator_failed",
                evaluator=ev.name,
                evaluator_type=ev.type,
                error=str(exc),
            )
            return None

        return clamp(value)

    async def _dispatch_trace(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """type 별 trace 실행 분기."""
        ev_type = ev.type
        if ev_type == "trace_builtin":
            return await self._run_trace_builtin(ev, trace, expected)
        if ev_type == "builtin":
            return await self._run_builtin_via_adapter(ev, trace, expected)
        if ev_type == "judge":
            return await self._run_judge_via_adapter(ev, trace, expected)
        if ev_type in ("approved", "inline_custom"):
            return await self._run_custom_for_trace(ev, trace, expected)
        logger.warning("trace_evaluator_unknown_type", evaluator=ev.name, type=ev_type)
        return None

    async def _run_trace_builtin(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """``trace_builtin`` — TRACE_BUILT_IN_REGISTRY 인스턴스 호출."""
        cls = TRACE_BUILT_IN_REGISTRY.get(ev.name)
        if cls is None:
            logger.warning("trace_evaluator_builtin_unknown", evaluator=ev.name)
            return None
        instance = self._instantiate_trace_builtin(ev.name, cls)
        result = await instance.evaluate_trace(trace, expected, dict(ev.config))
        if result is None:
            return None
        return float(result)

    def _instantiate_trace_builtin(self, name: str, cls: type) -> Any:
        """trace built-in 인스턴스 생성. LLM Judge 의존 evaluator는 litellm 주입."""
        if name in {"tool_result_grounding", "hallucination_check"}:
            return cls(litellm=self._litellm)
        return cls()

    async def _run_builtin_via_adapter(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """``builtin`` — 기존 evaluator를 OutputAdapter로 감싸 trace.output에 적용."""
        cls = BUILT_IN_REGISTRY.get(ev.name)
        if cls is None:
            logger.warning("evaluator_builtin_unknown", evaluator=ev.name)
            return None
        inner = cls()
        adapter = OutputAdapter(inner)
        config = dict(ev.config)
        if self._litellm is not None and "litellm_client" not in config:
            config["litellm_client"] = self._litellm
        result = await adapter.evaluate_trace(trace, expected, config)
        if result is None:
            return None
        return float(result)

    async def _run_judge_via_adapter(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """``judge`` — judge_runner 가 주입돼 있으면 그대로 호출.

        기존 ``judge_runner`` 는 ``output / expected / metadata`` 시그니처를 사용하므로
        :class:`OutputAdapter` 와 동일한 매핑으로 trace 데이터를 변환한다.
        """
        if self._judge_runner is None:
            logger.warning("evaluator_judge_runner_missing", evaluator=ev.name)
            return None
        output, expected_inner, metadata = self._adapt_trace_to_output(trace, expected)
        return await self._invoke_external_runner_with_payload(
            self._judge_runner, ev, output, expected_inner, metadata
        )

    async def _run_custom_for_trace(
        self,
        ev: EvaluatorConfig,
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> float | None:
        """``approved`` / ``inline_custom`` — custom_runner 로 trace.output 에 적용."""
        if self._custom_runner is None:
            logger.warning(
                "evaluator_custom_runner_missing",
                evaluator=ev.name,
                type=ev.type,
            )
            return None
        output, expected_inner, metadata = self._adapt_trace_to_output(trace, expected)
        return await self._invoke_external_runner_with_payload(
            self._custom_runner, ev, output, expected_inner, metadata
        )

    @staticmethod
    def _adapt_trace_to_output(
        trace: TraceTree,
        expected: dict[str, Any] | None,
    ) -> tuple[
        str | dict[str, Any] | list[Any],
        str | dict[str, Any] | list[Any] | None,
        dict[str, Any],
    ]:
        """trace → (output, expected, metadata) 변환 — :class:`OutputAdapter` 와 동일 정책."""
        import json as _json

        output_for_inner: str | dict[str, Any] | list[Any]
        if trace.output is None:
            output_for_inner = ""
        elif isinstance(trace.output, str):
            output_for_inner = trace.output
        else:
            try:
                output_for_inner = _json.dumps(trace.output, ensure_ascii=False, sort_keys=True)
            except (TypeError, ValueError):
                output_for_inner = str(trace.output)

        expected_value: str | dict[str, Any] | list[Any] | None = None
        if isinstance(expected, dict):
            expected_value = expected.get("expected_output")

        metadata: dict[str, Any] = {
            "latency_ms": trace.total_latency_ms,
            "cost_usd": trace.total_cost_usd,
            "tool_call_count": len(trace.tool_calls()),
            "llm_call_count": len(trace.llm_calls()),
        }
        for k, v in trace.metadata.items():
            metadata.setdefault(k, v)

        return output_for_inner, expected_value, metadata

    @staticmethod
    async def _invoke_external_runner_with_payload(
        runner: Any,
        ev: EvaluatorConfig,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
    ) -> float | None:
        """``_invoke_external_runner`` 의 유연 버전 — 사전에 변환된 payload 로 호출."""
        result = runner(ev=ev, output=output, expected=expected, metadata=metadata)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            return None
        try:
            return float(result)
        except (TypeError, ValueError):
            logger.warning(
                "evaluator_runner_return_invalid",
                evaluator=ev.name,
                value=str(result),
            )
            return None

    # ------------------------------------------------------------------ #
    # Langfuse 기록
    # ------------------------------------------------------------------ #
    async def _record_scores(self, trace_id: str, scores: dict[str, float | None]) -> None:
        """Langfuse에 score 기록. None은 기록하지 않음.

        score 메서드가 sync인 경우 to_thread로 위임하여 이벤트 루프 블로킹 방지.
        예외는 catch하여 경고만 남김 (실험 자체는 계속 진행).
        """
        for name, value in scores.items():
            if value is None:
                continue
            try:
                await self._call_langfuse_score(trace_id, name, value)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "langfuse_score_record_failed",
                    trace_id=trace_id,
                    score_name=name,
                    error=str(exc),
                )

    async def _call_langfuse_score(self, trace_id: str, name: str, value: float) -> None:
        """Langfuse score 호출 (sync/async 자동 처리)."""
        score_fn = getattr(self._langfuse, "score", None)
        if score_fn is None:
            return
        result = score_fn(trace_id=trace_id, name=name, value=value)
        if inspect.isawaitable(result):
            await result

    # ------------------------------------------------------------------ #
    # 정적 헬퍼 (편의)
    # ------------------------------------------------------------------ #
    @staticmethod
    def calculate_weighted_score(
        scores: dict[str, float | None],
        weights: dict[str, float],
    ) -> float | None:
        """:func:`app.evaluators.score_calculator.calculate_weighted_score` 위임."""
        return calculate_weighted_score(scores, weights)


__all__ = [
    "DEFAULT_EVALUATOR_TIMEOUT_SEC",
    "EvaluationPipeline",
    "WEIGHTED_SCORE_NAME",
]
