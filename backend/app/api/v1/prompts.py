"""프롬프트 API (API_DESIGN.md §2).

5개 엔드포인트:
- ``GET    /prompts``                                  목록 (viewer+)
- ``GET    /prompts/{name}``                           상세 + ``{{var}}`` 자동 파싱 (viewer+)
- ``GET    /prompts/{name}/versions``                  버전 목록 (viewer+)
- ``POST   /prompts``                                  신규 버전 생성 (user+, Idempotency-Key)
- ``PATCH  /prompts/{name}/versions/{version}/labels`` 라벨 승격 (admin, ETag/If-Match)

본 라우터는 Langfuse 프록시이며, RBAC는 ``require_role`` 의존성으로 강제한다.
프로젝트 분기는 ``project_id`` 쿼리/바디 + ``Settings.projects()``로 검증한다.

ETag/If-Match 처리:
- PATCH labels는 ``If-Match`` 헤더 필수. 응답 본문(name+version+sorted(labels))의
  SHA-256 prefix 16자를 비교한다.
- ``If-Match: *``는 존재 확인만 수행 — 통과.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.deps import get_langfuse_client, get_project_configs
from app.core.errors import LabsError
from app.core.security import require_role
from app.models.auth import User
from app.models.project import ProjectConfig
from app.models.prompt import (
    PromptCreate,
    PromptCreateResponse,
    PromptDetail,
    PromptLabelUpdate,
    PromptLabelUpdateResponse,
    PromptListResponse,
    PromptSummary,
    PromptVersion,
    PromptVersionsResponse,
)
from app.services.langfuse_client import LangfuseClient
from app.services.prompt_utils import extract_variables

router = APIRouter(prefix="/prompts", tags=["prompts"])

# ---------- 도메인 예외 (라우터 로컬) ----------


class PromptNotFoundError(LabsError):
    """프롬프트 미존재 — RFC 7807 ``404 PROMPT_NOT_FOUND``."""

    code = "PROMPT_NOT_FOUND"
    status_code = 404
    title = "Prompt not found"


class ProjectNotFoundError(LabsError):
    """프로젝트 미존재 — RFC 7807 ``404 PROJECT_NOT_FOUND``."""

    code = "PROJECT_NOT_FOUND"
    status_code = 404
    title = "Project not found"


class ETagMismatchError(LabsError):
    """``If-Match`` 헤더 불일치 — RFC 7807 ``412 ETAG_MISMATCH``."""

    code = "ETAG_MISMATCH"
    status_code = 412
    title = "ETag mismatch"


# ---------- 헬퍼 ----------
def _ensure_project(project_id: str, projects: list[ProjectConfig]) -> ProjectConfig:
    """``project_id``가 등록 프로젝트인지 검증."""
    for project in projects:
        if project.id == project_id:
            return project
    raise ProjectNotFoundError(detail=f"project_id={project_id!r} not found")


def _to_aware_utc(value: Any) -> datetime:
    """Langfuse 응답의 시간 값을 ``datetime``(UTC)으로 정규화.

    SDK가 ``datetime``을 반환하면 그대로, 문자열이면 ISO 8601 파싱.
    누락 시 현재 시각을 사용 (정보 부재 방지).
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(UTC)
    return datetime.now(UTC)


def _prompt_to_summary(prompt_obj: Any) -> PromptSummary:
    """Langfuse Prompt → ``PromptSummary``."""
    return PromptSummary(
        name=prompt_obj.name,
        latest_version=int(getattr(prompt_obj, "version", 1)),
        labels=list(getattr(prompt_obj, "labels", []) or []),
        tags=list(getattr(prompt_obj, "tags", []) or []),
        created_at=_to_aware_utc(getattr(prompt_obj, "created_at", None)),
    )


def _prompt_to_detail(prompt_obj: Any) -> PromptDetail:
    """Langfuse Prompt → ``PromptDetail``. 변수 추출 포함."""
    name = prompt_obj.name
    version = int(getattr(prompt_obj, "version", 1))
    prompt_type = str(
        getattr(prompt_obj, "prompt_type", None) or getattr(prompt_obj, "type", "text")
    )
    if prompt_type not in ("text", "chat"):
        prompt_type = "text"

    body: str | list[dict[str, Any]]
    sdk_body = getattr(prompt_obj, "prompt", None)
    if sdk_body is not None:
        body = sdk_body
    else:
        # Mock 객체는 ``body`` 속성을 사용
        body = getattr(prompt_obj, "body", "")

    variables = extract_variables(body)

    return PromptDetail(
        name=name,
        version=version,
        type=prompt_type,  # type: ignore[arg-type]
        prompt=body,
        config=dict(getattr(prompt_obj, "config", {}) or {}),
        labels=list(getattr(prompt_obj, "labels", []) or []),
        tags=list(getattr(prompt_obj, "tags", []) or []),
        variables=variables,
        created_at=_to_aware_utc(getattr(prompt_obj, "created_at", None)),
    )


def _list_all_prompts(client: LangfuseClient) -> list[Any]:
    """클라이언트(또는 mock)에서 모든 프롬프트 객체를 수집.

    ``MockLangfuseClient``는 ``_prompts`` 내부 dict를 노출한다. 실 Langfuse SDK는
    ``get_prompt(name)``만 제공하므로 본 메서드는 Phase 3 시점의 mock-우선
    구현이다. 추후 REST API 호출(``GET /api/public/v2/prompts``)로 대체 가능.
    """
    prompts_attr = getattr(client, "_prompts", None)
    if prompts_attr is None:
        return []
    # ``MockLangfuseClient._prompts``는 ``{(name, version): MockPrompt}``
    return list(prompts_attr.values())


def _compute_etag(payload: dict[str, Any]) -> str:
    """응답 본문에서 SHA-256 prefix 16자 ETag 생성 (정규화 직렬화)."""
    serialized = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    digest = hashlib.sha256(serialized).hexdigest()[:16]
    return f'"{digest}"'


def _label_payload_for_etag(name: str, version: int, labels: list[str]) -> dict[str, Any]:
    """라벨 PATCH 응답의 ETag 산출용 정규화 페이로드."""
    return {"name": name, "version": int(version), "labels": sorted(labels)}


# ---------- Endpoints ----------
@router.get(
    "",
    response_model=PromptListResponse,
    summary="프롬프트 목록 조회",
)
def list_prompts(
    project_id: str = Query(..., min_length=1, description="대상 프로젝트 ID"),
    page: int = Query(1, ge=1, description="페이지 번호 (1-base)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지 크기 (≤100)"),
    _user: User = Depends(require_role("viewer")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> PromptListResponse:
    """프로젝트의 프롬프트 목록을 페이지네이션하여 반환한다.

    같은 ``name``의 여러 버전 중 최신 1건만 항목으로 노출한다.
    """
    _ensure_project(project_id, projects)

    all_prompts = _list_all_prompts(langfuse)
    # name 기준 그룹화 — 최신 version 1건 선택
    latest_by_name: dict[str, Any] = {}
    for prompt_obj in all_prompts:
        name = getattr(prompt_obj, "name", None)
        version = int(getattr(prompt_obj, "version", 0))
        if name is None:
            continue
        existing = latest_by_name.get(name)
        if existing is None or int(getattr(existing, "version", 0)) < version:
            latest_by_name[name] = prompt_obj

    summaries = [_prompt_to_summary(obj) for obj in latest_by_name.values()]
    summaries.sort(key=lambda s: s.created_at, reverse=True)

    total = len(summaries)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = summaries[start:end]

    return PromptListResponse(
        items=page_items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/{name}",
    response_model=PromptDetail,
    summary="프롬프트 상세 조회 (변수 자동 파싱)",
)
def get_prompt(
    name: str,
    project_id: str = Query(..., min_length=1),
    version: int | None = Query(default=None, ge=1),
    label: str | None = Query(default=None),
    _user: User = Depends(require_role("viewer")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> PromptDetail:
    """단건 프롬프트 + ``{{var}}`` 추출 결과를 반환한다.

    ``version`` 또는 ``label`` 미지정 시 최신 버전.
    """
    _ensure_project(project_id, projects)

    try:
        prompt_obj = langfuse.get_prompt(name=name, version=version, label=label)
    except Exception as exc:  # noqa: BLE001
        # MockLangfuseClient는 LangfuseNotFoundError, 실 SDK는 LangfuseError
        message = str(exc).lower()
        if "not found" in message:
            raise PromptNotFoundError(detail=f"prompt name={name!r} not found") from exc
        raise

    return _prompt_to_detail(prompt_obj)


@router.get(
    "/{name}/versions",
    response_model=PromptVersionsResponse,
    summary="프롬프트 버전 목록 조회",
)
def list_prompt_versions(
    name: str,
    project_id: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _user: User = Depends(require_role("viewer")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> PromptVersionsResponse:
    """단일 프롬프트의 모든 버전을 최신순으로 반환한다."""
    _ensure_project(project_id, projects)

    all_prompts = _list_all_prompts(langfuse)
    versions = [obj for obj in all_prompts if getattr(obj, "name", None) == name]
    if not versions:
        raise PromptNotFoundError(detail=f"prompt name={name!r} not found")

    versions.sort(key=lambda obj: int(getattr(obj, "version", 0)), reverse=True)

    items = [
        PromptVersion(
            version=int(getattr(obj, "version", 0)),
            labels=list(getattr(obj, "labels", []) or []),
            created_at=_to_aware_utc(getattr(obj, "created_at", None)),
            created_by=getattr(obj, "created_by", None),
        )
        for obj in versions
    ]

    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size

    return PromptVersionsResponse(
        items=items[start:end],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "",
    response_model=PromptCreateResponse,
    status_code=201,
    summary="프롬프트 생성/새 버전 등록",
)
def create_prompt(
    body: PromptCreate,
    response: Response,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    _user: User = Depends(require_role("user")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> PromptCreateResponse:
    """프롬프트 신규 버전을 등록한다.

    Langfuse는 동일 ``name``으로 호출 시 자동으로 새 버전을 매기며, 기존 버전은
    유지된다. ``Idempotency-Key`` 헤더는 본 시점에는 형식만 검증하고 echo로
    응답 헤더에 반영한다 (재시도 안전성은 미들웨어에서 부착될 예정).
    """
    _ensure_project(body.project_id, projects)

    if idempotency_key is not None:
        if not idempotency_key.strip():
            raise HTTPException(status_code=400, detail="Idempotency-Key가 비어 있습니다.")
        response.headers["Idempotency-Key"] = idempotency_key

    # Langfuse SDK는 chat 본문도 ``prompt`` 인자로 받는다 (str | list[dict]).
    # MockLangfuseClient는 str만 가정하므로 chat 시 직렬화 처리.
    prompt_payload: Any = body.prompt
    if isinstance(prompt_payload, list):
        # mock 호환을 위해 직렬화 (실 SDK는 list 그대로 수용 가능)
        try:
            prompt_payload = json.dumps(prompt_payload, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"chat prompt 직렬화 실패: {exc}") from exc

    try:
        created = langfuse.create_prompt(
            name=body.name,
            prompt=prompt_payload,
            labels=body.labels,
            config=body.config,
            tags=body.tags,
            prompt_type=body.type,
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "not found" in message:
            raise PromptNotFoundError(detail=str(exc)) from exc
        raise

    return PromptCreateResponse(
        name=getattr(created, "name", body.name),
        version=int(getattr(created, "version", 1)),
        labels=list(getattr(created, "labels", body.labels) or []),
    )


@router.patch(
    "/{name}/versions/{version}/labels",
    response_model=PromptLabelUpdateResponse,
    summary="프롬프트 라벨 승격 (admin)",
)
def update_prompt_labels(
    name: str,
    version: int,
    body: PromptLabelUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    _user: User = Depends(require_role("admin")),
    projects: list[ProjectConfig] = Depends(get_project_configs),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> PromptLabelUpdateResponse:
    """프롬프트 버전에 라벨을 부여(승격)한다.

    - ``If-Match`` 헤더 필수 (``*`` 또는 현재 ETag).
    - 현재 ETag는 ``GET /prompts/{name}?version=...`` 응답의 라벨/이름/버전에서 파생.
    - 불일치 시 ``412 ETAG_MISMATCH``.
    """
    _ensure_project(body.project_id, projects)

    if if_match is None or not if_match.strip():
        raise ETagMismatchError(detail="If-Match 헤더가 필요합니다.")

    # 현재 상태 조회 (ETag 산출 + 존재 검증)
    try:
        current = langfuse.get_prompt(name=name, version=version)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "not found" in message:
            raise PromptNotFoundError(
                detail=f"prompt name={name!r} version={version} not found"
            ) from exc
        raise

    current_labels = list(getattr(current, "labels", []) or [])
    current_etag = _compute_etag(_label_payload_for_etag(name, version, current_labels))

    requested = if_match.strip()
    if requested != "*" and requested != current_etag:
        raise ETagMismatchError(
            detail=f"If-Match mismatch: expected={current_etag} got={requested}"
        )

    try:
        updated = langfuse.update_prompt_labels(name=name, version=version, labels=body.labels)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        if "not found" in message:
            raise PromptNotFoundError(
                detail=f"prompt name={name!r} version={version} not found"
            ) from exc
        raise

    new_labels = list(getattr(updated, "labels", body.labels) or [])
    new_etag = _compute_etag(_label_payload_for_etag(name, version, new_labels))
    response.headers["ETag"] = new_etag

    return PromptLabelUpdateResponse(
        name=getattr(updated, "name", name),
        version=int(getattr(updated, "version", version)),
        labels=new_labels,
    )
