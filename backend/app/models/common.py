"""공통 응답 wrapper / 에러 모델 (RFC 7807 Problem Details)."""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class APIResponse(BaseModel, Generic[T]):  # noqa: UP046
    """일반 성공 응답 wrapper.

    페이로드를 ``data``에 담고 메타 정보를 별도 필드로 분리한다.

    PEP 695 type parameter 문법(``class APIResponse[T]``)도 가능하나,
    ``from __future__ import annotations``와 pydantic 2 호환을 위해 전통 ``Generic[T]``
    형태를 유지한다.
    """

    data: T
    meta: dict[str, Any] | None = Field(None, description="추가 메타데이터")


class ProblemDetails(BaseModel):
    """RFC 7807 Problem Details 형식의 에러 응답.

    ``application/problem+json`` content-type과 함께 반환한다.
    """

    type: str = Field("about:blank", description="에러 타입 URI")
    title: str = Field(..., description="간단한 에러 제목")
    status: int = Field(..., description="HTTP 상태 코드")
    detail: str | None = Field(None, description="상세 에러 설명")
    instance: str | None = Field(None, description="문제 발생 위치 (URL 등)")
    code: str | None = Field(None, description="본 프로젝트 내부 에러 코드")
    extras: dict[str, Any] | None = Field(None, description="추가 컨텍스트")
