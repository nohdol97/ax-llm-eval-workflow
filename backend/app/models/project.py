"""프로젝트 도메인 Pydantic 모델 (API_DESIGN.md §9, IMPLEMENTATION.md §3).

Labs는 SaaS가 아닌 사내 인프라 도구이므로 프로젝트는 동적 생성하지 않는다.
``Settings.LABS_PROJECTS_JSON`` 환경변수의 JSON 파싱 결과를 본 모델로 매핑한다.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class ProjectConfig(BaseModel):
    """단일 프로젝트 설정 (Static Config).

    Langfuse 자격증명을 포함하므로 ``langfuse_secret_key``는 ``SecretStr``.
    응답으로 노출되지 않으며, 내부 클라이언트 분기에만 사용한다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="프로젝트 식별자")
    name: str = Field(..., min_length=1, description="프로젝트 표시 이름")
    description: str | None = Field(default=None, description="프로젝트 설명 (선택)")
    langfuse_host: str | None = Field(
        default=None, description="프로젝트별 Langfuse host (없으면 전역 기본값)"
    )
    langfuse_public_key: str | None = Field(default=None)
    langfuse_secret_key: SecretStr | None = Field(default=None)


class ProjectInfo(BaseModel):
    """프로젝트 목록 응답 항목 — 시크릿 미포함."""

    id: str
    name: str
    description: str | None = None
    created_at: datetime | None = Field(
        default=None, description="config 등록 시각 (Static Config의 경우 None일 수 있음)"
    )


class ProjectListResponse(BaseModel):
    """``GET /api/v1/projects`` 응답."""

    items: list[ProjectInfo]
    total: int = Field(..., ge=0)


class ProjectSwitchRequest(BaseModel):
    """``POST /api/v1/projects/switch`` 요청 body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)


class ProjectSwitchResponse(BaseModel):
    """프로젝트 전환 응답.

    실제 전환은 stateless. 본 응답은 클라이언트가 컨텍스트로 사용할
    프로젝트 정보를 echo한다.
    """

    project_id: str
    name: str
