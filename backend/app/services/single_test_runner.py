"""단일 테스트 Runner — SSE 스트리밍 + non-streaming 모드.

API_DESIGN.md §3 단일 테스트 API의 비즈니스 로직.

흐름
----
1. ``prompt_source`` 해석:
   - ``source=langfuse`` → ``LangfuseClient.get_prompt(name, version, label)``
   - ``source=inline`` → 그대로 사용
2. ``ContextEngine.compile(prompt, variables)`` → 컴파일된 messages
3. ``LangfuseClient.create_trace(...)`` → ``trace_id``
4. ``LiteLLMClient.completion(model, messages, stream=True, **parameters)``
5. 각 chunk → ``event: token`` (스트리밍 모드)
6. ``LangfuseClient.create_generation(trace_id, ...)`` (input/output/usage/cost)
7. ``event: done`` (latency_ms, cost_usd, usage)
8. 실패 시 ``event: error`` + trace metadata에 error 기록

이벤트 시퀀스
-------------
- ``started``: ``{trace_id, model, started_at}``
- ``token``: ``{content: "청크"}`` (반복)
- ``done``: ``{trace_id, output, usage, latency_ms, cost_usd, model, completed_at}``
- ``error``: ``{code, message, trace_id?}``
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from app.core.errors import LabsError, LangfuseError, LiteLLMError
from app.core.logging import get_logger
from app.services.context_engine import ContextEngine
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient

logger = get_logger(__name__)


def _build_evaluator_configs(
    raw: list[dict[str, Any]] | None,
) -> list[Any]:
    """``request.evaluators`` raw dict 리스트를 ``EvaluatorConfig``로 검증/변환.

    개별 검증 실패는 캐치해 해당 evaluator만 무시한다 (전체 실험 중단 회피).
    Phase 5: ``app.models.experiment.EvaluatorConfig``를 lazy import.
    """
    if not raw:
        return []
    from app.models.experiment import EvaluatorConfig

    items: list[Any] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        try:
            items.append(EvaluatorConfig.model_validate(entry))
        except Exception as exc:  # noqa: BLE001 — Pydantic ValidationError 등
            logger.warning(
                "evaluator_config_invalid",
                error=str(exc),
                evaluator_name=entry.get("name"),
            )
    return items


def _build_pipeline(
    langfuse: Any,
    litellm: Any,
) -> Any:
    """``EvaluationPipeline`` 인스턴스 — lazy import."""
    from app.evaluators.pipeline import EvaluationPipeline

    return EvaluationPipeline(langfuse=langfuse, litellm_client=litellm)


# ---------- 내부 유틸 ----------
def _to_messages(
    prompt: str | list[dict[str, Any]],
    *,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """프롬프트(text 또는 chat)를 LiteLLM messages 형태로 정규화한다.

    - text 프롬프트 → ``[{"role":"user","content":...}]``.
      ``system_prompt`` 지정 시 ``system`` 메시지를 앞에 추가.
    - chat 프롬프트(list) → 그대로 사용 (리스트 사본).
    """
    if isinstance(prompt, str):
        msgs: list[dict[str, Any]] = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
        return msgs
    if isinstance(prompt, list):
        if system_prompt:
            return [{"role": "system", "content": system_prompt}, *prompt]
        return list(prompt)
    raise TypeError(f"지원하지 않는 prompt 타입: {type(prompt).__name__}")


def _extract_chunk_text(chunk: dict[str, Any]) -> str:
    """LiteLLM streaming chunk에서 ``delta.content``를 추출한다.

    OpenAI 호환 chunk 형식: ``{"choices": [{"delta": {"content": "..."}}]}``.
    content가 없거나 None이면 빈 문자열.
    """
    try:
        choices = chunk.get("choices") or []
        if not choices:
            return ""
        delta = choices[0].get("delta") or {}
        content = delta.get("content")
        if isinstance(content, str):
            return content
    except (AttributeError, IndexError, TypeError):
        return ""
    return ""


def _extract_usage(chunk_or_response: dict[str, Any]) -> dict[str, int] | None:
    """``usage`` 필드 추출. (input_tokens/output_tokens/total_tokens 표준화)."""
    usage = chunk_or_response.get("usage")
    if not isinstance(usage, dict):
        return None
    # OpenAI 호환 키 (prompt_tokens / completion_tokens / total_tokens) → 표준화
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or (input_tokens + output_tokens))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _extract_cost(chunk_or_response: dict[str, Any]) -> float:
    """``_litellm_cost`` 추출. 없으면 0.0."""
    cost = chunk_or_response.get("_litellm_cost")
    if cost is None:
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


# ---------- Runner ----------
class SingleTestRunner:
    """단일 테스트 실행기.

    의존성: ``LangfuseClient``, ``LiteLLMClient``, ``ContextEngine``.
    """

    def __init__(
        self,
        langfuse: LangfuseClient,
        litellm: LiteLLMClient,
        context_engine: ContextEngine,
        evaluation_pipeline: Any | None = None,
    ) -> None:
        self._langfuse = langfuse
        self._litellm = litellm
        self._engine = context_engine
        self._eval_pipeline = evaluation_pipeline

    # ---------- 프롬프트 해석 ----------
    def _resolve_prompt(
        self,
        prompt_source: dict[str, Any],
        variables: dict[str, Any],
    ) -> tuple[str | list[dict[str, Any]], str | list[dict[str, Any]]]:
        """``prompt_source`` dict를 (raw_body, compiled) 튜플로 반환.

        - ``source=langfuse``: Langfuse SDK로 ``get_prompt`` 후 compile
        - ``source=inline``: ``body`` 그대로 사용 후 compile
        """
        source = prompt_source.get("source")
        if source == "langfuse":
            name = prompt_source.get("name")
            if not name:
                raise ValueError("source=langfuse 일 때 name 필수")
            prompt_obj = self._langfuse.get_prompt(
                name=name,
                version=prompt_source.get("version"),
                label=prompt_source.get("label"),
            )
            # SDK prompt에는 prompt/body 필드 + compile 메서드가 있을 수 있음
            raw = (
                getattr(prompt_obj, "prompt", None)
                or getattr(prompt_obj, "body", None)
                or prompt_obj
            )
            if not isinstance(raw, (str, list)):
                raw = str(raw)
            compiled = self._engine.compile_with_sdk(prompt_obj, variables)
            return raw, compiled
        if source == "inline":
            body = prompt_source.get("body")
            if body is None:
                raise ValueError("source=inline 일 때 body 필수")
            if not isinstance(body, (str, list)):
                raise TypeError(
                    f"inline body 타입은 str|list 이어야 함 (got {type(body).__name__})"
                )
            compiled = self._engine.compile(body, variables)
            return body, compiled
        raise ValueError(f"지원하지 않는 prompt source: {source!r}")

    # ---------- streaming ----------
    async def run_streaming(
        self,
        project_id: str,
        prompt_source: dict[str, Any],
        variables: dict[str, Any],
        model: str,
        parameters: dict[str, Any],
        evaluators: list[dict[str, Any]] | None = None,
        user_id: str = "anonymous",
        system_prompt: str | None = None,
        expected_output: str | dict[str, Any] | list[Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """단일 테스트 실행 + SSE 이벤트 dict yield.

        각 yield는 ``{"event": str, "data": dict}`` 형태.
        호출 측은 이 dict를 ``format_sse_event(...)``로 직렬화해야 한다.

        Args:
            project_id: 프로젝트 ID (Langfuse trace metadata에 첨부).
            prompt_source: ``{source, name?, version?, label?, body?, type?}``.
            variables: 변수 dict.
            model: LiteLLM 등록 모델명.
            parameters: 모델 파라미터 (temperature/top_p/max_tokens 등).
            evaluators: ``EvaluatorConfig`` raw dict 목록 (Phase 5).
            user_id: 사용자 ID (trace user_id에 기록, observability용).
            system_prompt: 옵션 system 메시지.
            expected_output: 정답(레퍼런스) — evaluator의 ``expected``로 전달.

        Yields:
            ``{"event": "started"|"token"|"done"|"scores"|"error", "data": {...}}``
        """
        evaluator_configs = _build_evaluator_configs(evaluators)
        started_at = datetime.now(UTC)
        start_perf = time.perf_counter()
        trace_id: str | None = None
        full_output_parts: list[str] = []
        usage: dict[str, int] | None = None
        cost_usd = 0.0

        try:
            # 1) prompt 해석
            raw_prompt, compiled_prompt = self._resolve_prompt(prompt_source, variables)
            messages = _to_messages(compiled_prompt, system_prompt=system_prompt)

            # 2) Langfuse trace 생성
            trace_id = self._langfuse.create_trace(
                name="single_test",
                user_id=user_id,
                metadata={
                    "project_id": project_id,
                    "model": model,
                    "prompt_source": prompt_source.get("source"),
                    "prompt_name": prompt_source.get("name"),
                    "prompt_version": prompt_source.get("version"),
                    "prompt_label": prompt_source.get("label"),
                    "started_at": started_at.isoformat(),
                },
                tags=["single_test", f"project:{project_id}"],
            )

            # 3) started 이벤트
            yield {
                "event": "started",
                "data": {
                    "trace_id": trace_id,
                    "model": model,
                    "started_at": started_at.isoformat(),
                },
            }

            # 4) LiteLLM 스트리밍 호출
            stream = await self._litellm.completion(
                model=model,
                messages=messages,
                stream=True,
                **parameters,
            )

            # 5) chunk 단위 token 이벤트
            async for chunk in stream:  # type: ignore[union-attr]
                # usage / cost 메타가 마지막 chunk에 포함될 수 있음
                if usage is None:
                    chunk_usage = _extract_usage(chunk)
                    if chunk_usage is not None:
                        usage = chunk_usage
                chunk_cost = _extract_cost(chunk)
                if chunk_cost > 0:
                    cost_usd = chunk_cost
                content_delta = _extract_chunk_text(chunk)
                if content_delta:
                    full_output_parts.append(content_delta)
                    yield {
                        "event": "token",
                        "data": {"content": content_delta},
                    }

            # 6) Langfuse generation 기록
            output_text = "".join(full_output_parts)
            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            completed_at = datetime.now(UTC)
            usage_final = usage or {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            try:
                self._langfuse.create_generation(
                    trace_id=trace_id,
                    name="single_test_generation",
                    model=model,
                    input=raw_prompt,
                    output=output_text,
                    usage=usage_final,
                    metadata={
                        "parameters": parameters,
                        "cost_usd": cost_usd,
                        "latency_ms": latency_ms,
                        "completed_at": completed_at.isoformat(),
                    },
                )
            except LangfuseError as exc:
                # 관측성 기록 실패는 사용자 응답을 중단시키지 않음
                logger.warning(
                    "single_test_generation_record_failed",
                    trace_id=trace_id,
                    error=str(exc),
                )

            # 7) done 이벤트
            yield {
                "event": "done",
                "data": {
                    "trace_id": trace_id,
                    "model": model,
                    "output": output_text,
                    "usage": usage_final,
                    "latency_ms": latency_ms,
                    "cost_usd": cost_usd,
                    "completed_at": completed_at.isoformat(),
                },
            }

            # 8) Phase 5 — evaluator 평가 (선택)
            if evaluator_configs:
                scores = await self._evaluate_output(
                    evaluator_configs=evaluator_configs,
                    output=output_text,
                    expected=expected_output,
                    metadata={
                        "latency_ms": latency_ms,
                        "cost_usd": cost_usd,
                        "output_tokens": usage_final.get("output_tokens", 0),
                        "total_tokens": usage_final.get("total_tokens", 0),
                        "input_tokens": usage_final.get("input_tokens", 0),
                        "model": model,
                    },
                    trace_id=trace_id,
                )
                yield {
                    "event": "scores",
                    "data": {"trace_id": trace_id, "scores": scores},
                }

        except (LiteLLMError, LangfuseError) as exc:
            code = "LLM_ERROR" if isinstance(exc, LiteLLMError) else "LANGFUSE_ERROR"
            yield self._error_event(code=code, message=str(exc), trace_id=trace_id)
            await self._mark_trace_error(trace_id, exc)
        except (ValueError, TypeError) as exc:
            yield self._error_event(code="VALIDATION_ERROR", message=str(exc), trace_id=trace_id)
            await self._mark_trace_error(trace_id, exc)
        except LabsError as exc:
            yield self._error_event(
                code=getattr(exc, "code", "labs_error").upper(),
                message=str(exc),
                trace_id=trace_id,
            )
            await self._mark_trace_error(trace_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "single_test_unexpected_error",
                trace_id=trace_id,
            )
            yield self._error_event(
                code="INTERNAL_ERROR",
                message="단일 테스트 실행 중 예상치 못한 에러",
                trace_id=trace_id,
            )
            await self._mark_trace_error(trace_id, exc)

    # ---------- non-streaming ----------
    async def run_non_streaming(
        self,
        project_id: str,
        prompt_source: dict[str, Any],
        variables: dict[str, Any],
        model: str,
        parameters: dict[str, Any],
        evaluators: list[dict[str, Any]] | None = None,
        user_id: str = "anonymous",
        system_prompt: str | None = None,
        expected_output: str | dict[str, Any] | list[Any] | None = None,
    ) -> dict[str, Any]:
        """non-streaming 모드 — 단일 dict 반환.

        Returns:
            ``{trace_id, model, output, usage, latency_ms, cost_usd, started_at,
            completed_at, scores?}``. ``scores``는 ``evaluators`` 지정 시 포함.

        Raises:
            ``LabsError`` 계열 (LiteLLMError / LangfuseError 등) — 그대로 전파.
        """
        evaluator_configs = _build_evaluator_configs(evaluators)
        started_at = datetime.now(UTC)
        start_perf = time.perf_counter()
        trace_id: str | None = None

        try:
            raw_prompt, compiled_prompt = self._resolve_prompt(prompt_source, variables)
            messages = _to_messages(compiled_prompt, system_prompt=system_prompt)

            trace_id = self._langfuse.create_trace(
                name="single_test",
                user_id=user_id,
                metadata={
                    "project_id": project_id,
                    "model": model,
                    "prompt_source": prompt_source.get("source"),
                    "prompt_name": prompt_source.get("name"),
                    "prompt_version": prompt_source.get("version"),
                    "prompt_label": prompt_source.get("label"),
                    "started_at": started_at.isoformat(),
                    "stream": False,
                },
                tags=["single_test", f"project:{project_id}"],
            )

            response = await self._litellm.completion(
                model=model,
                messages=messages,
                stream=False,
                **parameters,
            )

            # mypy: completion(stream=False)는 dict 반환
            assert isinstance(response, dict)
            output_text = ""
            try:
                output_text = response["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError):
                output_text = ""

            usage = _extract_usage(response) or {
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            }
            cost_usd = _extract_cost(response)
            latency_ms = (time.perf_counter() - start_perf) * 1000.0
            completed_at = datetime.now(UTC)

            try:
                self._langfuse.create_generation(
                    trace_id=trace_id,
                    name="single_test_generation",
                    model=model,
                    input=raw_prompt,
                    output=output_text,
                    usage=usage,
                    metadata={
                        "parameters": parameters,
                        "cost_usd": cost_usd,
                        "latency_ms": latency_ms,
                        "completed_at": completed_at.isoformat(),
                    },
                )
            except LangfuseError as exc:
                logger.warning(
                    "single_test_generation_record_failed",
                    trace_id=trace_id,
                    error=str(exc),
                )

            result: dict[str, Any] = {
                "trace_id": trace_id,
                "model": model,
                "output": output_text,
                "usage": usage,
                "latency_ms": latency_ms,
                "cost_usd": cost_usd,
                "started_at": started_at,
                "completed_at": completed_at,
            }

            # Phase 5 — evaluator 평가
            if evaluator_configs:
                scores = await self._evaluate_output(
                    evaluator_configs=evaluator_configs,
                    output=output_text,
                    expected=expected_output,
                    metadata={
                        "latency_ms": latency_ms,
                        "cost_usd": cost_usd,
                        "output_tokens": usage.get("output_tokens", 0),
                        "total_tokens": usage.get("total_tokens", 0),
                        "input_tokens": usage.get("input_tokens", 0),
                        "model": model,
                    },
                    trace_id=trace_id,
                )
                result["scores"] = scores

            return result
        except LabsError:
            await self._mark_trace_error(trace_id, None)
            raise
        except (ValueError, TypeError):
            await self._mark_trace_error(trace_id, None)
            raise
        except Exception:
            logger.exception(
                "single_test_non_stream_unexpected_error",
                trace_id=trace_id,
            )
            await self._mark_trace_error(trace_id, None)
            raise

    # ---------- 헬퍼 ----------
    @staticmethod
    def _error_event(
        *,
        code: str,
        message: str,
        trace_id: str | None,
    ) -> dict[str, Any]:
        """SSE error 이벤트 dict 생성."""
        data: dict[str, Any] = {"code": code, "message": message}
        if trace_id:
            data["trace_id"] = trace_id
        return {"event": "error", "data": data}

    async def _evaluate_output(
        self,
        *,
        evaluator_configs: list[Any],
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        trace_id: str | None,
    ) -> dict[str, float | None]:
        """``EvaluationPipeline.evaluate_item`` 호출 — 미설정 시 lazy 생성.

        실패 시 빈 dict 반환 (best-effort — 평가 실패가 단일 테스트 응답을
        깨뜨리지 않도록 보장).
        """
        pipeline = self._eval_pipeline
        if pipeline is None:
            try:
                pipeline = _build_pipeline(self._langfuse, self._litellm)
                self._eval_pipeline = pipeline
            except Exception as exc:  # noqa: BLE001
                logger.warning("eval_pipeline_init_failed", error=str(exc))
                return {}

        try:
            return await pipeline.evaluate_item(
                evaluators=evaluator_configs,
                output=output,
                expected=expected,
                metadata=metadata,
                trace_id=trace_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("eval_pipeline_failed", error=str(exc), trace_id=trace_id)
            return {}

    async def _mark_trace_error(
        self,
        trace_id: str | None,
        exc: BaseException | None,
    ) -> None:
        """trace에 error level metadata를 첨부 (best-effort)."""
        if not trace_id:
            return
        try:
            self._langfuse.create_generation(
                trace_id=trace_id,
                name="single_test_error",
                model="n/a",
                input=None,
                output=None,
                usage={},
                metadata={
                    "level": "ERROR",
                    "error": str(exc) if exc is not None else "unknown",
                },
            )
        except Exception as e:  # noqa: BLE001
            # 관측성 기록 실패는 무시 (이미 에러 경로)
            logger.debug(
                "trace_error_record_failed",
                trace_id=trace_id,
                error=str(e),
            )
