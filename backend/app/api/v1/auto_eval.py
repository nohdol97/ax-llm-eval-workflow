"""Auto-Eval REST API 라우터 (Phase 8-B-2).

본 라우터는 ``docs/AGENT_EVAL.md`` §13 의 12 개 엔드포인트를 정확히 구현한다.

엔드포인트
---------
Policy CRUD:
    POST   /api/v1/auto-eval/policies                  생성 (user+, Idempotency-Key)
    GET    /api/v1/auto-eval/policies                  목록 (viewer+)
    GET    /api/v1/auto-eval/policies/{id}             상세 (viewer+, ETag)
    PATCH  /api/v1/auto-eval/policies/{id}             수정 (owner|admin, If-Match)
    DELETE /api/v1/auto-eval/policies/{id}             삭제 (admin, If-Match)
    POST   /api/v1/auto-eval/policies/{id}/pause       일시정지 (owner|admin)
    POST   /api/v1/auto-eval/policies/{id}/resume      재개 (owner|admin)
    POST   /api/v1/auto-eval/policies/{id}/run-now     즉시 실행 (user+)

Run history:
    GET    /api/v1/auto-eval/runs                      정책별 run 이력
    GET    /api/v1/auto-eval/runs/{id}                 run 단건 상세
    GET    /api/v1/auto-eval/runs/{id}/items           trace 평가 결과 (Phase 8-C placeholder)

비용:
    GET    /api/v1/auto-eval/policies/{id}/cost-usage  일자별 비용 + 누적

공통 정책
---------
- ETag: 정책 본체 JSON (updated_at 제외) sha256 prefix 16
- If-Match: ``"*"`` 또는 ETag 정확 일치 — 불일치 시 412
- 시간: UTC ISO 8601 + ``Z`` 표기
- 인증: JWT Bearer 필수 (test 시 ``app.dependency_overrides[get_current_user]``)
"""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.deps import get_auto_eval_engine, get_auto_eval_repo
from app.core.security import get_current_user, require_role
from app.models.auth import User
from app.models.auto_eval import (
    AutoEvalPolicy,
    AutoEvalPolicyCreate,
    AutoEvalPolicyListResponse,
    AutoEvalPolicyUpdate,
    AutoEvalRun,
    AutoEvalRunListResponse,
    AutoEvalRunStatus,
    CostUsage,
    PolicyStatus,
)

if TYPE_CHECKING:  # pragma: no cover — runtime import 회피
    from app.services.auto_eval_engine import AutoEvalEngine
    from app.services.auto_eval_repo import AutoEvalRepo

router = APIRouter(prefix="/auto-eval", tags=["auto-eval"])


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _compute_etag(policy: AutoEvalPolicy) -> str:
    """정책 본체의 ETag 계산.

    정책 JSON 직렬화 → ``updated_at`` 제외 → sha256 prefix 16자.

    ``updated_at`` 을 제외함으로써, ``GET`` 응답에서 부여한 ETag 가
    이후 동일 내용 정책 갱신 (no-op) 시 재현 가능하도록 한다.
    """
    payload = policy.model_dump_json(exclude={"updated_at"})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _quoted_etag(etag_value: str) -> str:
    """ETag 값에 strong-quotation 부착."""
    return f'"{etag_value}"'


def _check_if_match(if_match: str | None, current_etag: str) -> None:
    """If-Match 검증. 불일치 시 412 raise.

    - None: skip (정책: optional)
    - ``"*"``: any-match — 통과
    - 따옴표 유무 무시하고 비교
    """
    if if_match is None:
        return
    raw = if_match.strip()
    if raw == "*":
        return
    # 양쪽 따옴표 제거
    if raw.startswith('"') and raw.endswith('"'):
        raw = raw[1:-1]
    if raw != current_etag:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="ETag 불일치 — If-Match 헤더가 현재 정책과 일치하지 않습니다.",
        )


def _check_owner_or_admin(user: User, policy: AutoEvalPolicy) -> None:
    """소유자 또는 admin 만 통과. 그 외 403."""
    if user.role == "admin":
        return
    if policy.owner == user.id:
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="권한 부족 — 정책 소유자 또는 admin 만 가능합니다.",
    )


# ---------------------------------------------------------------------------
# Policy CRUD
# ---------------------------------------------------------------------------
@router.post(
    "/policies",
    response_model=AutoEvalPolicy,
    status_code=status.HTTP_201_CREATED,
    summary="Auto-Eval 정책 생성",
)
async def create_policy(
    payload: AutoEvalPolicyCreate,
    response: Response,
    user: User = Depends(require_role("user")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AutoEvalPolicy:
    """정책 신규 생성.

    - 권한: ``user`` 이상
    - ``Idempotency-Key`` 헤더 수용 (현재 placeholder — 동일 키 재시도 방지는
      Phase 8-B-1 repo 가 구현 시 활성화)
    """
    policy = await repo.create_policy(payload, owner=user.id)
    response.headers["ETag"] = _quoted_etag(_compute_etag(policy))
    return policy


@router.get(
    "/policies",
    response_model=AutoEvalPolicyListResponse,
    summary="Auto-Eval 정책 목록 조회",
)
async def list_policies(
    project_id: str = Query(..., min_length=1, description="대상 프로젝트 ID"),
    status_filter: PolicyStatus | None = Query(
        default=None, alias="status", description="상태 필터"
    ),
    page: int = Query(default=1, ge=1, description="페이지 (1-base)"),
    page_size: int = Query(default=20, ge=1, le=100, description="페이지 크기"),
    user: User = Depends(get_current_user),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalPolicyListResponse:
    """정책 목록 — 페이지네이션 + ``status`` 필터."""
    items, total = await repo.list_policies(
        project_id=project_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return AutoEvalPolicyListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/policies/{policy_id}",
    response_model=AutoEvalPolicy,
    summary="Auto-Eval 정책 상세",
)
async def get_policy(
    policy_id: str,
    response: Response,
    user: User = Depends(get_current_user),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalPolicy:
    """정책 상세 조회 — 응답 헤더에 ``ETag`` 포함."""
    policy = await repo.get_policy(policy_id)
    response.headers["ETag"] = _quoted_etag(_compute_etag(policy))
    return policy


@router.patch(
    "/policies/{policy_id}",
    response_model=AutoEvalPolicy,
    summary="Auto-Eval 정책 수정",
)
async def update_policy(
    policy_id: str,
    updates: AutoEvalPolicyUpdate,
    response: Response,
    user: User = Depends(require_role("user")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> AutoEvalPolicy:
    """정책 수정 — ``owner`` 또는 ``admin`` 만 가능. ``If-Match`` 권장."""
    existing = await repo.get_policy(policy_id)
    _check_owner_or_admin(user, existing)
    _check_if_match(if_match, _compute_etag(existing))

    updated = await repo.update_policy(policy_id, updates)
    response.headers["ETag"] = _quoted_etag(_compute_etag(updated))
    return updated


@router.delete(
    "/policies/{policy_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Auto-Eval 정책 삭제",
)
async def delete_policy(
    policy_id: str,
    user: User = Depends(require_role("admin")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> Response:
    """정책 삭제 — ``admin`` 만 가능. ``If-Match`` 권장."""
    existing = await repo.get_policy(policy_id)
    _check_if_match(if_match, _compute_etag(existing))
    await repo.delete_policy(policy_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/policies/{policy_id}/pause",
    response_model=AutoEvalPolicy,
    summary="Auto-Eval 정책 일시정지",
)
async def pause_policy(
    policy_id: str,
    response: Response,
    user: User = Depends(require_role("user")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalPolicy:
    """일시정지 — ``status=paused``. ``owner`` 또는 ``admin`` 만 가능."""
    existing = await repo.get_policy(policy_id)
    _check_owner_or_admin(user, existing)
    paused = await repo.pause_policy(policy_id)
    response.headers["ETag"] = _quoted_etag(_compute_etag(paused))
    return paused


@router.post(
    "/policies/{policy_id}/resume",
    response_model=AutoEvalPolicy,
    summary="Auto-Eval 정책 재개",
)
async def resume_policy(
    policy_id: str,
    response: Response,
    user: User = Depends(require_role("user")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalPolicy:
    """재개 — ``status=active`` + ``next_run_at`` 재계산.

    ``owner`` 또는 ``admin`` 만 가능.
    """
    existing = await repo.get_policy(policy_id)
    _check_owner_or_admin(user, existing)
    resumed = await repo.resume_policy(policy_id)
    response.headers["ETag"] = _quoted_etag(_compute_etag(resumed))
    return resumed


@router.post(
    "/policies/{policy_id}/run-now",
    response_model=AutoEvalRun,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Auto-Eval 정책 즉시 실행",
)
async def run_policy_now(
    policy_id: str,
    user: User = Depends(require_role("user")),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
    engine: AutoEvalEngine = Depends(get_auto_eval_engine),
) -> AutoEvalRun:
    """정책 즉시 실행 — 검증 후 백그라운드 task 로 분리.

    응답: ``202 Accepted`` — 임시 ``id="pending"`` 상태로 즉시 반환한다.
    실제 run ID 는 엔진이 ``create_run`` 내부에서 발급한다.

    상태 검증:
        - ``status != active`` 인 정책은 409 Conflict.
    """
    policy = await repo.get_policy(policy_id)
    if policy.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"정책이 active 상태가 아닙니다: status={policy.status}",
        )

    # 백그라운드 실행 — 응답을 막지 않는다
    asyncio.create_task(_invoke_engine(engine, policy_id))

    return AutoEvalRun(
        id="pending",
        policy_id=policy_id,
        started_at=datetime.now(UTC),
        status="running",
    )


async def _invoke_engine(engine: Any, policy_id: str) -> None:
    """engine.run_policy 를 swallow 모드로 호출 — 백그라운드 task 용.

    engine 예외는 logger 에 한 번 남기고 swallow 한다 (이미 engine 내부에서
    ``run.status=failed`` + ``error_message`` 영속화 처리).
    """
    from app.core.logging import get_logger

    logger = get_logger(__name__)
    try:
        await engine.run_policy(policy_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "auto_eval_run_now_failed",
            policy_id=policy_id,
            error=str(exc),
        )


# ---------------------------------------------------------------------------
# Run history
# ---------------------------------------------------------------------------
@router.get(
    "/runs",
    response_model=AutoEvalRunListResponse,
    summary="Auto-Eval run 이력",
)
async def list_runs(
    policy_id: str = Query(..., min_length=1),
    status_filter: AutoEvalRunStatus | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalRunListResponse:
    """특정 정책의 run 이력 (started_at desc)."""
    items, total = await repo.list_runs(
        policy_id=policy_id,
        status=status_filter,
        page=page,
        page_size=page_size,
    )
    return AutoEvalRunListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/runs/{run_id}",
    response_model=AutoEvalRun,
    summary="Auto-Eval run 단건 상세",
)
async def get_run(
    run_id: str,
    user: User = Depends(get_current_user),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> AutoEvalRun:
    """run 단건 상세 — 집계 결과 + triggered_alerts + review_items_created."""
    return await repo.get_run(run_id)


@router.get(
    "/runs/{run_id}/items",
    summary="Auto-Eval run 의 trace 평가 결과 (Phase 8-C placeholder)",
)
async def get_run_items(
    run_id: str,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """run 의 trace 평가 항목.

    Phase 8-B-2 (v1) 에서는 placeholder — Phase 8-C ReviewItem 통합 시 활성.
    """
    return {
        "items": [],
        "total": 0,
        "page": page,
        "page_size": page_size,
        "run_id": run_id,
    }


# ---------------------------------------------------------------------------
# 비용 조회
# ---------------------------------------------------------------------------
@router.get(
    "/policies/{policy_id}/cost-usage",
    response_model=CostUsage,
    summary="Auto-Eval 정책의 일자별 비용",
)
async def get_cost_usage(
    policy_id: str,
    from_date: date = Query(..., description="조회 시작 일자 (YYYY-MM-DD)"),
    to_date: date = Query(..., description="조회 종료 일자 (YYYY-MM-DD)"),
    user: User = Depends(get_current_user),
    repo: AutoEvalRepo = Depends(get_auto_eval_repo),
) -> CostUsage:
    """기간별 일일 비용 + 누적. ``from_date > to_date`` 면 422."""
    if from_date > to_date:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_date must be <= to_date",
        )

    # daily_limit_usd 를 정책에서 가져와 응답에 포함
    policy = await repo.get_policy(policy_id)

    raw = await repo.get_cost_usage(policy_id, from_date, to_date)
    # repo 가 dict 반환 — daily_limit_usd 채우고 모델로 검증
    if isinstance(raw, dict):
        raw_payload = dict(raw)
        if raw_payload.get("daily_limit_usd") is None:
            raw_payload["daily_limit_usd"] = policy.daily_cost_limit_usd
        return CostUsage.model_validate(raw_payload)

    # 이미 CostUsage 인 경우
    if isinstance(raw, CostUsage):
        if raw.daily_limit_usd is None and policy.daily_cost_limit_usd is not None:
            return raw.model_copy(update={"daily_limit_usd": policy.daily_cost_limit_usd})
        return raw

    # 기타 — best-effort
    return CostUsage.model_validate(raw)
