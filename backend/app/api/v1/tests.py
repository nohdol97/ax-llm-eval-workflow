"""단일 테스트 API 라우터.

엔드포인트(API_DESIGN.md §3):
- ``POST /api/v1/tests/single`` — 단일 테스트 실행 (SSE 또는 JSON)

본 라우터는 ``stream=true``이면 ``text/event-stream``으로 token/done/error 이벤트를
순차 발행하고, ``stream=false``이면 ``SingleTestResponseMeta`` JSON을 반환한다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.deps import get_single_test_runner
from app.core.errors import LabsError
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.models.auth import User
from app.models.test import (
    SingleTestRequest,
    SingleTestResponseMeta,
    SingleTestUsage,
)
from app.services.single_test_runner import SingleTestRunner
from app.services.sse import (
    SSE_HEADERS,
    SSE_RETRY_MS,
    format_retry_directive,
    format_sse_event,
)

logger = get_logger(__name__)

router = APIRouter(tags=["tests"])

# ---------- 의존성 alias ----------
RunnerDep = Annotated[SingleTestRunner, Depends(get_single_test_runner)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


@router.post(
    "/tests/single",
    summary="단일 테스트 실행 (SSE 또는 JSON)",
)
async def single_test_endpoint(
    request: SingleTestRequest,
    runner: RunnerDep,
    user: CurrentUserDep,
) -> Any:
    """단일 테스트 실행.

    - ``stream=true`` (기본): ``text/event-stream``로 token/done/error 이벤트.
    - ``stream=false``: ``SingleTestResponseMeta`` JSON 반환.
    """
    prompt_source = request.prompt.model_dump(exclude_none=True)

    if request.stream:
        return StreamingResponse(
            _stream_single_test(
                runner=runner,
                request=request,
                prompt_source=prompt_source,
                user_id=user.id,
            ),
            media_type="text/event-stream",
            headers=dict(SSE_HEADERS),
        )

    # non-streaming
    try:
        result = await runner.run_non_streaming(
            project_id=request.project_id,
            prompt_source=prompt_source,
            variables=request.variables,
            model=request.model,
            parameters=request.parameters,
            evaluators=request.evaluators,
            user_id=user.id,
            system_prompt=request.system_prompt,
            expected_output=request.expected_output,
        )
    except LabsError:
        raise
    except (ValueError, TypeError) as exc:
        # PromptSource validator 통과 후의 inline body 등 런타임 검증 — 422
        return JSONResponse(
            status_code=422,
            content={
                "type": "about:blank",
                "title": "Validation error",
                "status": 422,
                "detail": str(exc),
                "code": "validation_error",
            },
            media_type="application/problem+json",
        )

    payload = SingleTestResponseMeta(
        trace_id=result["trace_id"],
        model=result["model"],
        output=result["output"],
        usage=SingleTestUsage(**result["usage"]),
        latency_ms=result["latency_ms"],
        cost_usd=result["cost_usd"],
        started_at=result["started_at"],
        completed_at=result["completed_at"],
    )
    return JSONResponse(
        status_code=200,
        content=payload.model_dump(mode="json"),
    )


# ---------- SSE generator ----------
async def _stream_single_test(
    *,
    runner: SingleTestRunner,
    request: SingleTestRequest,
    prompt_source: dict[str, Any],
    user_id: str,
) -> AsyncIterator[str]:
    """``runner.run_streaming`` 결과를 SSE 텍스트로 직렬화하여 yield."""
    # 첫 라인 — retry 지시문 (재연결 권장 시간)
    yield format_retry_directive(SSE_RETRY_MS)

    event_id = 0
    try:
        async for event in runner.run_streaming(
            project_id=request.project_id,
            prompt_source=prompt_source,
            variables=request.variables,
            model=request.model,
            parameters=request.parameters,
            evaluators=request.evaluators,
            user_id=user_id,
            system_prompt=request.system_prompt,
            expected_output=request.expected_output,
        ):
            event_id += 1
            yield format_sse_event(
                event["event"],
                event["data"],
                event_id=event_id,
            )
    except Exception as exc:  # noqa: BLE001
        # runner 내부에서 처리되지 않은 예외 — 안전망
        logger.exception("single_test_stream_unhandled", error=str(exc))
        event_id += 1
        yield format_sse_event(
            "error",
            {"code": "INTERNAL_ERROR", "message": str(exc)},
            event_id=event_id,
        )
