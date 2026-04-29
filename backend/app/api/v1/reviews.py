"""Review Queue REST API 라우터 (Phase 8-C-4).

본 라우터는 ``docs/AGENT_EVAL.md`` §18 의 모든 엔드포인트를 구현한다.

엔드포인트
---------
큐 listing / CRUD:
    GET    /api/v1/reviews/items                       목록 (viewer+, 필터/페이지)
    GET    /api/v1/reviews/items/{id}                  상세 (viewer+, ETag)
    POST   /api/v1/reviews/items                       수동 추가 (user+)
    PATCH  /api/v1/reviews/items/{id}/claim            claim (reviewer+)
    PATCH  /api/v1/reviews/items/{id}/release          release (본인 또는 admin)
    POST   /api/v1/reviews/items/{id}/resolve          결정 (reviewer+, ETag/If-Match)
    DELETE /api/v1/reviews/items/{id}                  삭제 (admin)

통계:
    GET    /api/v1/reviews/stats/summary               전체 큐 요약
    GET    /api/v1/reviews/stats/reviewer/{user_id}    reviewer 개인 통계
    GET    /api/v1/reviews/stats/disagreement          evaluator 별 override 비율

사용자 신고:
    POST   /api/v1/reviews/report                      신고 (user+)

공통 정책
---------
- ETag: ``ReviewQueueService.compute_etag`` 결과를 strong-quoted 로 반환
- If-Match: ``"*"`` 또는 ETag 정확 일치 — 불일치 시 412
- 시간: UTC ISO 8601 + ``Z`` 표기
- 인증: JWT Bearer 필수 (test 시 ``app.dependency_overrides[get_current_user]``)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status

from app.core.security import get_current_user, require_role
from app.models.auth import User
from app.models.review import (
    EvaluatorDisagreementResponse,
    ReviewItem,
    ReviewItemCreate,
    ReviewItemListResponse,
    ReviewItemResolve,
    ReviewItemType,
    ReviewQueueSummary,
    ReviewReport,
    ReviewSeverity,
    ReviewStatus,
)
from app.services.review_decisions import apply_decision_postprocess

if TYPE_CHECKING:  # pragma: no cover
    from app.services.review_queue import ReviewQueueService

router = APIRouter(prefix="/reviews", tags=["reviews"])


# ---------------------------------------------------------------------------
# DI helpers
# ---------------------------------------------------------------------------
def get_review_queue(request: Request) -> ReviewQueueService:
    """``app.state.review_queue`` 에서 service 반환.

    lifespan 미초기화 환경(테스트)에서는 ``app.state.redis`` 로부터 즉석 생성.
    """
    queue = getattr(request.app.state, "review_queue", None)
    if queue is not None:
        return queue
    redis = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError(
            "RedisClient 또는 ReviewQueueService 가 app.state 에 초기화되지 않았습니다."
        )
    from app.services.review_queue import ReviewQueueService as _Service  # noqa: WPS433

    return _Service(redis=redis)


def get_langfuse(request: Request) -> object:
    """``app.state.langfuse`` 반환 — 후처리(score / dataset 보강)용."""
    client = getattr(request.app.state, "langfuse", None)
    if client is None:
        raise RuntimeError("LangfuseClient 가 app.state 에 초기화되지 않았습니다.")
    return client


def get_trace_fetcher(request: Request) -> object | None:
    """``app.state.trace_fetcher`` 반환 — None 허용 (선택적 의존)."""
    return getattr(request.app.state, "trace_fetcher", None)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------
def _quoted_etag(value: str) -> str:
    return f'"{value}"'


# ---------------------------------------------------------------------------
# 큐 listing / CRUD
# ---------------------------------------------------------------------------
@router.get(
    "/items",
    response_model=ReviewItemListResponse,
    summary="Review Queue 목록",
)
async def list_items(
    project_id: str | None = Query(default=None, description="프로젝트 필터"),
    status_filter: ReviewStatus | None = Query(
        default=None, alias="status", description="status 필터"
    ),
    type_filter: ReviewItemType | None = Query(
        default=None, alias="type", description="type 필터"
    ),
    severity: ReviewSeverity | None = Query(default=None),
    assigned_to: str | None = Query(default=None, description="assigned_to (in_review 일 때)"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItemListResponse:
    """큐 목록 — status=open 일 때 우선순위 정렬, 그 외는 created_at desc."""
    items, total = await queue.list_items(
        project_id=project_id,
        status=status_filter,
        type_=type_filter,
        severity=severity,
        assigned_to=assigned_to,
        page=page,
        page_size=page_size,
    )
    return ReviewItemListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get(
    "/items/{item_id}",
    response_model=ReviewItem,
    summary="Review 항목 상세",
)
async def get_item(
    item_id: str,
    response: Response,
    user: User = Depends(get_current_user),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItem:
    """item 상세 — 응답 헤더 ``ETag``."""
    item = await queue.get_item(item_id)
    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


@router.post(
    "/items",
    response_model=ReviewItem,
    status_code=status.HTTP_201_CREATED,
    summary="Review 항목 수동 추가 (manual_addition)",
)
async def create_item(
    payload: ReviewItemCreate,
    response: Response,
    user: User = Depends(require_role("user")),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItem:
    """수동 추가 — type 은 항상 ``manual_addition``."""
    item = await queue.create_manual(payload)
    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


@router.patch(
    "/items/{item_id}/claim",
    response_model=ReviewItem,
    summary="claim (open → in_review)",
)
async def claim_item(
    item_id: str,
    response: Response,
    user: User = Depends(require_role("reviewer")),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItem:
    """claim — reviewer+ 만 가능. 이미 다른 사용자가 claim 중이면 409."""
    item = await queue.claim(item_id, user.id)
    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


@router.patch(
    "/items/{item_id}/release",
    response_model=ReviewItem,
    summary="release (in_review → open)",
)
async def release_item(
    item_id: str,
    response: Response,
    user: User = Depends(require_role("reviewer")),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItem:
    """release — 본인 claim 만 가능 (admin 은 force=True)."""
    force = user.role == "admin"
    item = await queue.release(item_id, user.id, force=force)
    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


@router.post(
    "/items/{item_id}/resolve",
    response_model=ReviewItem,
    summary="resolve — approve/override/dismiss/add_to_dataset",
)
async def resolve_item(
    item_id: str,
    payload: ReviewItemResolve,
    response: Response,
    user: User = Depends(require_role("reviewer")),
    queue: ReviewQueueService = Depends(get_review_queue),
    langfuse: object = Depends(get_langfuse),
    trace_fetcher: object | None = Depends(get_trace_fetcher),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ReviewItem:
    """결정 적용 + 후처리 — Langfuse score 갱신 / 골든셋 보강 (best-effort)."""
    item = await queue.resolve(item_id, user.id, payload, if_match=if_match)

    # 후처리는 best-effort — 결정 자체는 이미 영속화된 상태.
    # apply_decision_postprocess 가 자체 swallow + logger.warning 하므로 추가 try 불필요.
    await apply_decision_postprocess(item, langfuse=langfuse, trace_fetcher=trace_fetcher)

    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


@router.delete(
    "/items/{item_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Review 항목 삭제 (admin)",
)
async def delete_item(
    item_id: str,
    user: User = Depends(require_role("admin")),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> Response:
    """admin 전용 — item + 모든 인덱스 정리."""
    await queue.delete(item_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# 통계
# ---------------------------------------------------------------------------
@router.get(
    "/stats/summary",
    response_model=ReviewQueueSummary,
    summary="큐 요약 — open / in_review / today resolved",
)
async def get_summary(
    project_id: str | None = Query(default=None, description="프로젝트 필터"),
    user: User = Depends(get_current_user),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewQueueSummary:
    """전체 큐 요약."""
    return await queue.get_summary(project_id=project_id)


@router.get(
    "/stats/reviewer/{user_id}",
    summary="reviewer 개인 통계",
)
async def get_reviewer_stats(
    user_id: str,
    user: User = Depends(get_current_user),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> dict[str, object]:
    """본인 외 user_id 조회는 reviewer+ 또는 admin 만 허용."""
    if user.id != user_id and not user.has_role("reviewer"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="다른 사용자의 통계는 reviewer+ 권한 필요",
        )
    return await queue.get_reviewer_stats(user_id)


@router.get(
    "/stats/disagreement",
    response_model=EvaluatorDisagreementResponse,
    summary="evaluator 별 override 비율 (학습용)",
)
async def get_disagreement(
    user: User = Depends(get_current_user),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> EvaluatorDisagreementResponse:
    """evaluator 정확도 학습 — override 비율이 높은 evaluator 가 rubric 재조정 후보."""
    return await queue.get_disagreement_stats()


# ---------------------------------------------------------------------------
# 사용자 신고
# ---------------------------------------------------------------------------
@router.post(
    "/report",
    response_model=ReviewItem,
    status_code=status.HTTP_201_CREATED,
    summary="사용자 신고 — POST /api/v1/reviews/report",
)
async def report_trace(
    payload: ReviewReport,
    response: Response,
    user: User = Depends(require_role("user")),
    queue: ReviewQueueService = Depends(get_review_queue),
) -> ReviewItem:
    """신고 — 즉시 ``type=user_report`` ReviewItem 생성."""
    item = await queue.create_user_report(
        trace_id=payload.trace_id,
        project_id=payload.project_id,
        reporter_user_id=user.id,
        reason_text=payload.reason,
        severity=payload.severity,
        subject_type=payload.subject_type,
    )
    response.headers["ETag"] = _quoted_etag(queue.compute_etag(item))
    return item


__all__ = ["router", "get_review_queue", "get_langfuse", "get_trace_fetcher"]
