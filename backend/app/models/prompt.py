"""프롬프트 도메인 Pydantic 모델 (API_DESIGN.md §2).

Langfuse Prompt Management의 응답을 본 백엔드의 표준 응답으로 매핑하기 위한
모델. 본문/메타에 더해 ``variables`` 필드로 ``{{var}}`` 추출 결과를 포함한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

PromptType = Literal["text", "chat"]
"""Langfuse가 지원하는 프롬프트 타입."""


class PromptVersion(BaseModel):
    """프롬프트 버전 메타.

    버전 목록 응답(``GET /prompts/{name}/versions``)에 사용된다.
    """

    version: int = Field(..., description="버전 번호 (1부터 단조 증가)")
    labels: list[str] = Field(default_factory=list, description="이 버전의 라벨")
    created_at: datetime = Field(..., description="버전 생성 시각 (UTC, ISO 8601)")
    created_by: str | None = Field(
        default=None, description="버전을 만든 사용자 (Langfuse가 노출하는 경우)"
    )


class PromptSummary(BaseModel):
    """프롬프트 요약 — 목록 조회용.

    같은 ``name``의 여러 버전 중 최신 1건의 메타만 노출한다.
    """

    name: str = Field(..., description="프롬프트 이름")
    latest_version: int = Field(..., description="최신 버전 번호")
    labels: list[str] = Field(default_factory=list, description="최신 버전의 라벨")
    tags: list[str] = Field(default_factory=list, description="프롬프트 태그")
    created_at: datetime = Field(..., description="최신 버전 생성 시각 (UTC)")


class PromptDetail(BaseModel):
    """프롬프트 상세 — 단건 조회용.

    ``variables`` 필드는 본문에서 추출한 ``{{var}}`` 목록.
    chat 타입일 경우 ``prompt``는 ``list[dict]``.
    """

    name: str
    version: int
    type: PromptType = Field(..., description="text 또는 chat")
    prompt: str | list[dict[str, Any]] = Field(
        ..., description="본문 — text는 ``str``, chat은 messages 리스트"
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="모델 호출 설정 (Langfuse config)"
    )
    labels: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    variables: list[str] = Field(
        default_factory=list, description="본문에서 추출한 변수명 (발견 순서 유지)"
    )
    created_at: datetime = Field(..., description="버전 생성 시각 (UTC)")


class PromptCreate(BaseModel):
    """``POST /api/v1/prompts`` 요청 body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="대상 프로젝트 ID")
    name: str = Field(..., min_length=1, max_length=200, description="프롬프트 이름")
    prompt: str | list[dict[str, Any]] = Field(..., description="프롬프트 본문")
    type: PromptType = Field(default="text")
    config: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class PromptCreateResponse(BaseModel):
    """``POST /api/v1/prompts`` 응답 body."""

    name: str
    version: int
    labels: list[str] = Field(default_factory=list)


class PromptLabelUpdate(BaseModel):
    """``PATCH /api/v1/prompts/{name}/versions/{version}/labels`` 요청 body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    labels: list[str] = Field(..., description="해당 버전에 부여할 라벨 목록")


class PromptLabelUpdateResponse(BaseModel):
    """라벨 승격 응답."""

    name: str
    version: int
    labels: list[str]


class PromptListResponse(BaseModel):
    """프롬프트 목록 응답 (페이지네이션 포함, API_DESIGN.md §1.1)."""

    items: list[PromptSummary]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class PromptVersionsResponse(BaseModel):
    """버전 목록 응답 (페이지네이션 포함)."""

    items: list[PromptVersion]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)
