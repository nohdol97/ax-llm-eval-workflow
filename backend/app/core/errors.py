"""공통 예외 + FastAPI 핸들러 (RFC 7807 Problem Details).

Backend 내부 에러를 도메인별 예외 클래스로 정의하고, FastAPI 앱에 등록되는
``register_exception_handlers``를 통해 일관된 ``application/problem+json`` 응답으로
변환한다.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models.common import ProblemDetails

_PROBLEM_CONTENT_TYPE = "application/problem+json"

logger = logging.getLogger(__name__)


# ---------- 도메인 예외 ----------
class LabsError(Exception):
    """본 프로젝트 내부 에러 베이스 클래스."""

    code: str = "labs_error"
    status_code: int = 500
    title: str = "Internal Labs Error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail
        self.extras = extras


class LangfuseError(LabsError):
    """Langfuse 호출 실패."""

    code = "langfuse_error"
    status_code = 502
    title = "Langfuse upstream error"


class LiteLLMError(LabsError):
    """LiteLLM Proxy 호출 실패."""

    code = "litellm_error"
    status_code = 502
    title = "LiteLLM upstream error"


class ClickHouseError(LabsError):
    """ClickHouse 쿼리 실패 또는 정책 위반."""

    code = "clickhouse_error"
    status_code = 502
    title = "ClickHouse error"


class ClickHouseSecurityError(ClickHouseError):
    """ClickHouse 보안 정책 위반 (파라미터화 미적용)."""

    code = "clickhouse_security_error"
    status_code = 500
    title = "ClickHouse security policy violation"


class RedisClientError(LabsError):
    """Redis 클라이언트 에러."""

    code = "redis_error"
    status_code = 503
    title = "Redis error"


class EvaluatorError(LabsError):
    """Evaluator 실행 에러."""

    code = "evaluator_error"
    status_code = 500
    title = "Evaluator error"


class AuthError(LabsError):
    """인증/인가 에러."""

    code = "auth_error"
    status_code = 401
    title = "Authentication failed"


class ForbiddenError(AuthError):
    """권한 부족 에러."""

    code = "forbidden"
    status_code = 403
    title = "Forbidden"


# ---------- 응답 헬퍼 ----------
def _problem_response(
    *,
    status: int,
    title: str,
    detail: str | None = None,
    code: str | None = None,
    instance: str | None = None,
    extras: dict[str, Any] | None = None,
) -> JSONResponse:
    """``ProblemDetails`` JSON 응답 생성."""
    pd = ProblemDetails(
        type="about:blank",
        title=title,
        status=status,
        detail=detail,
        instance=instance,
        code=code,
        extras=extras,
    )
    return JSONResponse(
        status_code=status,
        content=pd.model_dump(exclude_none=True),
        media_type=_PROBLEM_CONTENT_TYPE,
    )


# ---------- 핸들러 ----------
async def _labs_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """``LabsError`` 계열 핸들러."""
    assert isinstance(exc, LabsError)
    logger.warning(
        "labs_error",
        extra={
            "code": exc.code,
            "status": exc.status_code,
            "path": str(request.url.path),
        },
    )
    return _problem_response(
        status=exc.status_code,
        title=exc.title,
        detail=exc.detail,
        code=exc.code,
        instance=str(request.url.path),
        extras=exc.extras,
    )


async def _http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI ``HTTPException`` → Problem Details 변환."""
    assert isinstance(exc, HTTPException)
    detail_text: str | None
    if isinstance(exc.detail, str):
        detail_text = exc.detail
    elif exc.detail is None:
        detail_text = None
    else:
        detail_text = str(exc.detail)
    return _problem_response(
        status=exc.status_code,
        title=detail_text or "HTTP Error",
        detail=detail_text,
        code=f"http_{exc.status_code}",
        instance=str(request.url.path),
    )


async def _validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Pydantic/FastAPI 검증 에러 핸들러."""
    assert isinstance(exc, RequestValidationError)
    return _problem_response(
        status=422,
        title="Request validation failed",
        detail="요청 본문이 스키마와 일치하지 않습니다.",
        code="validation_error",
        instance=str(request.url.path),
        extras={"errors": exc.errors()},
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """예상치 못한 예외 fallback 핸들러."""
    logger.exception(
        "unhandled_exception",
        extra={"path": str(request.url.path), "error": str(exc)},
    )
    return _problem_response(
        status=500,
        title="Internal Server Error",
        detail="예상하지 못한 에러가 발생했습니다.",
        code="internal_error",
        instance=str(request.url.path),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """FastAPI 앱에 모든 예외 핸들러를 등록한다."""
    app.add_exception_handler(LabsError, _labs_error_handler)
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)
