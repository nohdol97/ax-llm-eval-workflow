"""Custom Evaluator 거버넌스 + 카탈로그 라우터 (API_DESIGN.md §8 / §14).

엔드포인트
----------
- ``GET    /api/v1/evaluators/built-in``                       내장 13종 메타 (viewer+)
- ``POST   /api/v1/evaluators/validate``                       사전 검증 (user+)
- ``POST   /api/v1/evaluators/submissions``                    제출 (user+; admin은 자동 승인)
- ``GET    /api/v1/evaluators/submissions``                    목록 (본인/admin)
- ``GET    /api/v1/evaluators/submissions/{id}``               단건 조회
- ``POST   /api/v1/evaluators/submissions/{id}/approve``       승인 (admin, ETag/If-Match)
- ``POST   /api/v1/evaluators/submissions/{id}/reject``        반려 (admin, 사유 필수)
- ``POST   /api/v1/evaluators/submissions/{id}/deprecate``     폐기 (admin)
- ``GET    /api/v1/evaluators/approved``                       승인 evaluator 카탈로그 (user+)
- ``GET    /api/v1/evaluators/score-configs``                  Langfuse score config 상태 (admin)

본 라우터의 모든 응답은 RFC 7807 Problem Details와 호환된다 (errors.py).
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.deps import get_langfuse_client, get_redis_client
from app.core.security import get_current_user, require_role
from app.evaluators.registry import list_built_in
from app.models.auth import User
from app.models.evaluator import (
    ApprovalRequest,
    BuiltInEvaluatorInfo,
    RejectionRequest,
    ScoreConfigStatusItem,
    Submission,
    SubmissionCreate,
    SubmissionListResponse,
    SubmissionStatus,
    ValidateRequest,
    ValidateResponse,
)
from app.services.evaluator_governance import (
    EvaluatorGovernanceService,
    SubmissionInvalidCodeError,
    SubmissionNotFoundError,
    SubmissionStateConflictError,
)
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient
from app.services.score_registry import EVALUATOR_CATALOG

router = APIRouter(prefix="/evaluators", tags=["evaluators"])


# ---------- 의존성 ----------
def get_governance_service(
    redis: RedisClient = Depends(get_redis_client),
) -> EvaluatorGovernanceService:
    """라우터 단위 ``EvaluatorGovernanceService`` 의존성.

    테스트에서 ``app.dependency_overrides[get_governance_service]``로 mock 주입 가능.
    """
    return EvaluatorGovernanceService(redis=redis)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
GovernanceDep = Annotated[EvaluatorGovernanceService, Depends(get_governance_service)]


# ---------- 헬퍼 ----------
def _etag_for_submission(submission: Submission) -> str:
    """Submission의 ETag — 코드 hash + 상태 + 갱신 timestamp 기반."""
    parts = [
        submission.submission_id,
        submission.status,
        submission.code_hash,
        submission.submitted_at.isoformat(),
        submission.approved_at.isoformat() if submission.approved_at else "",
        submission.rejected_at.isoformat() if submission.rejected_at else "",
        submission.deprecated_at.isoformat() if submission.deprecated_at else "",
    ]
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


def _verify_if_match(if_match: str | None, etag: str) -> None:
    """If-Match 검증 — None/와일드카드는 통과, 불일치 시 412."""
    if if_match is None:
        return
    if if_match.strip() in ("*", etag):
        return
    raise HTTPException(
        status_code=status.HTTP_412_PRECONDITION_FAILED,
        detail="ETag가 일치하지 않습니다.",
    )


def _serialize_submission(
    submission: Submission,
    *,
    actor_user_id: str,
    actor_is_admin: bool,
) -> dict[str, Any]:
    """응답 직렬화 — 본인/admin이 아니면 ``code`` 마스킹."""
    include_code = actor_is_admin or submission.submitted_by == actor_user_id
    return EvaluatorGovernanceService.to_response(submission, include_code=include_code)


# ---------- GET /built-in ----------
@router.get(
    "/built-in",
    response_model=list[BuiltInEvaluatorInfo],
    summary="내장 evaluator 13종 메타데이터 (viewer+)",
)
async def get_built_in_evaluators(
    response: Response,
    user: CurrentUserDep,  # noqa: ARG001 — 인증만 강제
) -> list[BuiltInEvaluatorInfo]:
    """``app.evaluators.registry.list_built_in()``을 반환.

    응답 캐시: ``Cache-Control: private, max-age=300`` (5분).
    """
    response.headers["Cache-Control"] = "private, max-age=300"
    items = list_built_in()
    out: list[BuiltInEvaluatorInfo] = []
    for entry in items:
        rng = entry.get("range")
        rng_tuple: tuple[float, float] | None = None
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            rng_tuple = (float(rng[0]), float(rng[1]))
        out.append(
            BuiltInEvaluatorInfo(
                name=entry["name"],
                description=entry.get("description", ""),
                data_type=entry.get("data_type", "NUMERIC"),
                range=rng_tuple,
                config_schema=entry.get("config_schema", {}),
            )
        )
    return out


# ---------- POST /validate ----------
@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="평가 코드 사전 검증 (user+; Idempotency-Key 지원)",
)
async def validate_evaluator(
    request: ValidateRequest,
    governance: GovernanceDep,
    user: User = Depends(require_role("user")),  # noqa: ARG001
    idempotency_key: str | None = Header(  # noqa: ARG001 — received only
        default=None, alias="Idempotency-Key"
    ),
) -> ValidateResponse:
    """샌드박스 컨테이너에서 evaluator 코드를 test_cases와 함께 실행."""
    results = await governance.validate(code=request.code, test_cases=request.test_cases)
    return ValidateResponse(test_results=results)  # type: ignore[arg-type]


# ---------- POST /submissions ----------
@router.post(
    "/submissions",
    response_model=Submission,
    status_code=status.HTTP_201_CREATED,
    summary="Custom Evaluator 제출 (user+; admin은 자동 승인)",
)
async def create_submission(
    request: SubmissionCreate,
    response: Response,
    governance: GovernanceDep,
    user: User = Depends(require_role("user")),
    idempotency_key: str | None = Header(  # noqa: ARG001 — received only
        default=None, alias="Idempotency-Key"
    ),
) -> Any:
    """Custom Evaluator 제출.

    - ``test_cases`` 제공 시 사전 검증 후 모두 통과해야 ``pending``으로 진입
    - admin 제출 → 자동 ``approved`` (즉시 사용 가능)
    """
    try:
        submission = await governance.submit(
            user_id=user.id,
            is_admin=user.role == "admin",
            name=request.name,
            description=request.description,
            code=request.code,
            test_cases=request.test_cases,
        )
    except SubmissionInvalidCodeError:
        raise

    response.headers["ETag"] = _etag_for_submission(submission)
    return _serialize_submission(
        submission,
        actor_user_id=user.id,
        actor_is_admin=user.role == "admin",
    )


# ---------- GET /submissions ----------
@router.get(
    "/submissions",
    response_model=SubmissionListResponse,
    summary="제출 목록 (본인 또는 admin 전체)",
)
async def list_submissions_endpoint(
    user: CurrentUserDep,
    governance: GovernanceDep,
    status_filter: SubmissionStatus | None = Query(None, alias="status", description="상태 필터"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> SubmissionListResponse:
    """본인 제출 목록 또는 admin은 전체 — 최신순."""
    is_admin = user.role == "admin"
    result = await governance.list_submissions(
        user_id=user.id,
        is_admin=is_admin,
        status_filter=status_filter,
        page=page,
        page_size=page_size,
    )
    # 코드 마스킹 — 본인 외 항목은 비움 (admin은 모두 노출)
    masked_items: list[Submission] = []
    for item in result.items:
        if is_admin or item.submitted_by == user.id:
            masked_items.append(item)
        else:
            masked = item.model_copy(update={"code": ""})
            masked_items.append(masked)
    return SubmissionListResponse(
        items=masked_items,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


# ---------- GET /submissions/{id} ----------
@router.get(
    "/submissions/{submission_id}",
    response_model=Submission,
    summary="제출 단건 조회 (본인 또는 admin)",
)
async def get_submission_endpoint(
    submission_id: str,
    response: Response,
    user: CurrentUserDep,
    governance: GovernanceDep,
) -> Any:
    is_admin = user.role == "admin"
    try:
        submission = await governance.get_submission(
            submission_id, user_id=user.id, is_admin=is_admin
        )
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    response.headers["ETag"] = _etag_for_submission(submission)
    return _serialize_submission(
        submission,
        actor_user_id=user.id,
        actor_is_admin=is_admin,
    )


# ---------- POST /submissions/{id}/approve ----------
@router.post(
    "/submissions/{submission_id}/approve",
    response_model=Submission,
    summary="제출 승인 (admin; ETag/If-Match)",
)
async def approve_submission(
    submission_id: str,
    request: ApprovalRequest,
    response: Response,
    governance: GovernanceDep,
    user: User = Depends(require_role("admin")),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Any:
    """admin 승인 — pending → approved. ETag 검증 시 412."""
    try:
        current = await governance.get_submission(submission_id, user_id=user.id, is_admin=True)
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    _verify_if_match(if_match, _etag_for_submission(current))

    try:
        approved = await governance.approve(submission_id, admin_id=user.id, note=request.note)
    except SubmissionStateConflictError:
        raise
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    response.headers["ETag"] = _etag_for_submission(approved)
    return _serialize_submission(approved, actor_user_id=user.id, actor_is_admin=True)


# ---------- POST /submissions/{id}/reject ----------
@router.post(
    "/submissions/{submission_id}/reject",
    response_model=Submission,
    summary="제출 반려 (admin; 사유 필수)",
)
async def reject_submission(
    submission_id: str,
    request: RejectionRequest,
    response: Response,
    governance: GovernanceDep,
    user: User = Depends(require_role("admin")),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Any:
    """admin 반려. ``reason``은 RejectionRequest에서 검증."""
    try:
        current = await governance.get_submission(submission_id, user_id=user.id, is_admin=True)
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    _verify_if_match(if_match, _etag_for_submission(current))

    try:
        rejected = await governance.reject(submission_id, admin_id=user.id, reason=request.reason)
    except SubmissionStateConflictError:
        raise
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    response.headers["ETag"] = _etag_for_submission(rejected)
    return _serialize_submission(rejected, actor_user_id=user.id, actor_is_admin=True)


# ---------- POST /submissions/{id}/deprecate ----------
@router.post(
    "/submissions/{submission_id}/deprecate",
    response_model=Submission,
    summary="승인 evaluator 폐기 (admin)",
)
async def deprecate_submission(
    submission_id: str,
    response: Response,
    governance: GovernanceDep,
    user: User = Depends(require_role("admin")),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Any:
    try:
        current = await governance.get_submission(submission_id, user_id=user.id, is_admin=True)
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    _verify_if_match(if_match, _etag_for_submission(current))

    try:
        deprecated = await governance.deprecate(submission_id, admin_id=user.id)
    except SubmissionStateConflictError:
        raise
    except SubmissionNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="제출을 찾을 수 없습니다.",
        ) from None

    response.headers["ETag"] = _etag_for_submission(deprecated)
    return _serialize_submission(deprecated, actor_user_id=user.id, actor_is_admin=True)


# ---------- GET /approved ----------
@router.get(
    "/approved",
    response_model=SubmissionListResponse,
    summary="승인된 evaluator 카탈로그 (user+; 위저드 Step 3 사용)",
)
async def list_approved_evaluators(
    user: CurrentUserDep,
    governance: GovernanceDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
) -> SubmissionListResponse:
    """승인된 evaluator 목록 — 모든 사용자 조회 가능.

    ``code`` 본문은 본인/admin이 아닌 경우 마스킹.
    """
    is_admin = user.role == "admin"
    result = await governance.list_approved(page=page, page_size=page_size)
    masked: list[Submission] = []
    for item in result.items:
        if is_admin or item.submitted_by == user.id:
            masked.append(item)
        else:
            masked.append(item.model_copy(update={"code": ""}))
    return SubmissionListResponse(
        items=masked,
        total=result.total,
        page=result.page,
        page_size=result.page_size,
    )


# ---------- GET /score-configs ----------
@router.get(
    "/score-configs",
    response_model=list[ScoreConfigStatusItem],
    summary="Langfuse score config 상태 (admin)",
)
async def list_score_configs(
    user: User = Depends(require_role("admin")),  # noqa: ARG001
    langfuse: LangfuseClient = Depends(get_langfuse_client),  # noqa: ARG001
) -> list[ScoreConfigStatusItem]:
    """카탈로그 기준 score config 등록 상태 보고.

    Langfuse SDK의 ``list_score_configs`` 미존재 시 카탈로그를 그대로 노출하며
    상태는 ``registered`` (낙관 가정)로 표기. 미설정 환경에서는 ``missing``.
    """
    items: list[ScoreConfigStatusItem] = []
    list_method = getattr(langfuse, "list_score_configs", None)
    registered: dict[str, dict[str, Any]] = {}
    if callable(list_method):
        try:
            raw = list_method()
            if isinstance(raw, list):
                for entry in raw:
                    if isinstance(entry, dict) and "name" in entry:
                        registered[str(entry["name"])] = entry
        except Exception:  # noqa: BLE001
            registered = {}

    for ev in EVALUATOR_CATALOG:
        name = str(ev["name"])
        rng = ev.get("range")
        rng_tuple: tuple[float, float] | None = None
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            rng_tuple = (float(rng[0]), float(rng[1]))

        if list_method is None:
            # SDK 미지원 — 부팅 시 idempotent 등록되었다는 가정 하에 registered
            status_value = "registered"
        elif name in registered:
            entry = registered[name]
            entry_dt = entry.get("dataType") or entry.get("data_type")
            if entry_dt and entry_dt != ev["data_type"]:
                status_value = "mismatch"
            else:
                status_value = "registered"
        else:
            status_value = "missing"

        items.append(
            ScoreConfigStatusItem(
                name=name,
                status=status_value,  # type: ignore[arg-type]
                data_type=ev["data_type"],
                range=rng_tuple,
            )
        )
    return items


# 도메인 예외(``SubmissionInvalidCodeError`` / ``SubmissionStateConflictError`` /
# ``SubmissionNotFoundError``)는 모두 ``LabsError`` 파생이므로 ``app.core.errors``
# 의 글로벌 핸들러가 RFC 7807 Problem Details로 자동 변환한다.


__all__ = ["router", "get_governance_service"]
