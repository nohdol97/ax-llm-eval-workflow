"""프로젝트 API (API_DESIGN.md §9, IMPLEMENTATION.md §3).

2개 엔드포인트:
- ``GET  /projects``         프로젝트 목록 (viewer+)
- ``POST /projects/switch``  프로젝트 전환 검증 (user+)

전환은 stateless — 응답은 클라이언트가 컨텍스트로 사용할 정보만 echo한다.
실제 프로젝트 분기는 이후 호출의 ``project_id`` 파라미터로 결정된다.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import get_project_configs
from app.core.errors import LabsError
from app.core.security import require_role
from app.models.auth import User
from app.models.project import (
    ProjectConfig,
    ProjectInfo,
    ProjectListResponse,
    ProjectSwitchRequest,
    ProjectSwitchResponse,
)

router = APIRouter(prefix="/projects", tags=["projects"])


# ---------- 도메인 예외 ----------
class ProjectNotFoundError(LabsError):
    """프로젝트 미존재 — RFC 7807 ``404 PROJECT_NOT_FOUND``."""

    code = "PROJECT_NOT_FOUND"
    status_code = 404
    title = "Project not found"


# ---------- Endpoints ----------
@router.get(
    "",
    response_model=ProjectListResponse,
    summary="등록된 프로젝트 목록",
)
def list_projects(
    _user: User = Depends(require_role("viewer")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
) -> ProjectListResponse:
    """``Settings.projects()``의 정적 목록을 반환 (시크릿 제외)."""
    items = [
        ProjectInfo(
            id=project.id,
            name=project.name,
            description=project.description,
            created_at=None,  # static config — 등록 시각 정보 없음
        )
        for project in projects
    ]
    return ProjectListResponse(items=items, total=len(items))


@router.post(
    "/switch",
    response_model=ProjectSwitchResponse,
    summary="프로젝트 전환 (검증용 echo)",
)
def switch_project(
    body: ProjectSwitchRequest,
    _user: User = Depends(require_role("user")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
) -> ProjectSwitchResponse:
    """``project_id``가 등록 프로젝트인지 검증 후 정보를 echo한다.

    실제 클라이언트 분기는 stateless — 클라이언트는 응답을 받은 뒤
    이후 모든 API 호출에 ``project_id``를 명시한다 (IMPLEMENTATION.md §3.4).
    """
    for project in projects:
        if project.id == body.project_id:
            return ProjectSwitchResponse(
                project_id=project.id,
                name=project.name,
            )
    raise ProjectNotFoundError(detail=f"project_id={body.project_id!r} not found")
