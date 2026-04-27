"""``GET /api/v1/search`` — 통합 검색 라우터.

권한: viewer 이상 (인증만 요구).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import get_langfuse_client, get_redis_client
from app.core.security import get_current_user
from app.models.auth import User
from app.models.search import SearchResponse
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient
from app.services.search_service import search as do_search
from app.services.search_service import validate_query

router = APIRouter(tags=["search"])


@router.get(
    "/search",
    response_model=SearchResponse,
    summary="프롬프트 / 데이터셋 / 실험 통합 검색",
)
async def global_search(
    q: str = Query(..., min_length=2, max_length=200, description="검색어"),
    type: str = Query(  # noqa: A002 — 명세 일치
        "all",
        pattern="^(prompts|datasets|experiments|all)$",
        description="검색 범위",
    ),
    project_id: str | None = Query(None, description="프로젝트 ID (선택)"),
    limit: int = Query(20, ge=1, le=50, description="도메인별 최대 결과 수"),
    _user: User = Depends(get_current_user),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    redis: RedisClient = Depends(get_redis_client),
) -> SearchResponse:
    """통합 검색.

    - ``q``: 2~200자, 허용 문자만
    - ``type``: prompts | datasets | experiments | all
    - ``limit``: 도메인별 최대 결과 수 (기본 20, 최대 50)
    """
    try:
        normalized = validate_query(q)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return await do_search(
        query=normalized,
        type_=type,  # type: ignore[arg-type]
        project_id=project_id,
        limit=limit,
        langfuse=langfuse,
        redis=redis,
    )
