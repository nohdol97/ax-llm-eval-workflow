"""Agent Trace API 라우터 (Phase 8-A-1).

엔드포인트:
- ``POST /api/v1/traces/search``         : trace 메타 목록 (페이지네이션, viewer+)
- ``GET  /api/v1/traces/{trace_id}``     : trace tree 단건 (전체 observations, viewer+)
- ``POST /api/v1/traces/{trace_id}/score``: trace 에 score 수동 부착 (user+)

설계 참고: ``docs/AGENT_EVAL.md`` §7.1, ``docs/API_DESIGN.md`` §1.1.

권한:
- 검색/조회: ``viewer`` 이상
- score 부여: ``user`` 이상

오류:
- ``trace_id`` 미존재: 404
- 폴백 모드에서 SDK 미지원: 502
- 권한 부족: 403
- 인증 실패: 401
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.core.deps import get_langfuse_client, get_trace_fetcher
from app.core.errors import LangfuseError
from app.core.security import get_current_user, require_role
from app.models.auth import User
from app.models.trace import (
    TraceScoreRequest,
    TraceScoreResponse,
    TraceSearchRequest,
    TraceSearchResponse,
    TraceTree,
)
from app.services.langfuse_client import LangfuseClient
from app.services.trace_fetcher import TraceFetcher, TraceNotFoundError

router = APIRouter(prefix="/traces", tags=["traces"])


# ---------- 의존성 alias ----------
TraceFetcherDep = Annotated[TraceFetcher, Depends(get_trace_fetcher)]
LangfuseDep = Annotated[LangfuseClient, Depends(get_langfuse_client)]
ViewerDep = Annotated[User, Depends(get_current_user)]
UserDep = Annotated[User, Depends(require_role("user"))]


# ---------- 1) Trace 검색 ----------
@router.post(
    "/search",
    response_model=TraceSearchResponse,
    summary="Trace 메타 검색 (viewer+)",
)
async def search_traces(
    request: TraceSearchRequest,
    fetcher: TraceFetcherDep,
    _user: ViewerDep,
) -> TraceSearchResponse:
    """``TraceFilter`` 매칭 trace 의 메타만 페이지네이션하여 반환.

    ``include_observations=True`` 는 현 단계에서 미지원 (메모리/응답 비용 우려).
    필요 시 단건 조회 ``GET /traces/{id}`` 를 사용한다.
    """
    if request.include_observations:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "include_observations=True 는 아직 지원되지 않습니다. "
                "GET /traces/{id} 를 사용하세요."
            ),
        )

    summaries, total = await fetcher.search(request.filter)

    page = request.page
    page_size = request.page_size
    start = (page - 1) * page_size
    end = start + page_size
    items = summaries[start:end]

    return TraceSearchResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------- 2) Trace 단건 조회 ----------
@router.get(
    "/{trace_id}",
    response_model=TraceTree,
    summary="Trace 단건 조회 (viewer+)",
)
async def get_trace(
    trace_id: str,
    fetcher: TraceFetcherDep,
    _user: ViewerDep,
    project_id: str = Query(..., min_length=1, description="Langfuse project_id"),
) -> TraceTree:
    """trace + 모든 observations + scores. 미존재 시 404."""
    try:
        return await fetcher.get(trace_id, project_id)
    except TraceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail or f"trace {trace_id!r} not found",
        ) from exc


# ---------- 3) Trace 에 score 추가 ----------
@router.post(
    "/{trace_id}/score",
    response_model=TraceScoreResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Trace 에 score 부착 (user+)",
)
async def add_trace_score(
    trace_id: str,
    request: TraceScoreRequest,
    langfuse: LangfuseDep,
    _user: UserDep,
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> TraceScoreResponse:
    """trace 에 수동 score 부여 (review queue 결과 반영 등).

    ``If-Match`` 헤더를 옵션으로 받아 향후 낙관적 동시성 제어에 사용 (현 단계는
    값을 검증하지 않고 그대로 통과). 구현 시 score 객체의 ETag 검증으로 확장.
    """
    _ = if_match  # 미사용 placeholder — 향후 구현 예정
    try:
        result = langfuse.score(
            trace_id=trace_id,
            name=request.name,
            value=request.value,
            comment=request.comment,
        )
    except LangfuseError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=exc.detail or "Langfuse score 호출 실패",
        ) from exc

    score_id = str(getattr(result, "id", "") or f"manual:{trace_id}:{request.name}")
    created_at = getattr(result, "created_at", None) or datetime.now(UTC)
    if isinstance(created_at, str):
        try:
            created_at = datetime.fromisoformat(created_at.rstrip("Z"))
        except ValueError:
            created_at = datetime.now(UTC)
    if not isinstance(created_at, datetime):
        created_at = datetime.now(UTC)
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)

    return TraceScoreResponse(
        trace_id=trace_id,
        score_id=score_id,
        name=request.name,
        value=request.value,
        created_at=created_at,
    )


__all__ = ["router"]
