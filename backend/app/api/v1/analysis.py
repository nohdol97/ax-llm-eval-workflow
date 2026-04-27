"""분석 API 라우터 (Phase 6).

엔드포인트:
- ``POST /api/v1/analysis/compare``              — Run 요약 비교
- ``POST /api/v1/analysis/compare/items``        — 아이템 단위 비교
- ``GET  /api/v1/analysis/scores/distribution``  — 스코어 분포
- ``GET  /api/v1/analysis/latency/distribution`` — 지연 분포
- ``GET  /api/v1/analysis/cost/distribution``    — 비용 분포

권한: viewer 이상 (모든 인증된 사용자가 분석 결과를 볼 수 있다).

ClickHouse 미설정 시 503 ``service_unavailable`` 응답.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import get_analysis_service
from app.core.security import require_role
from app.models.analysis import (
    CompareItemsRequest,
    CompareItemsResponse,
    CompareRequest,
    CompareResponse,
    CostDistributionResponse,
    LatencyDistributionResponse,
    ScoreDistributionResponse,
)
from app.models.auth import User
from app.services.analysis_service import AnalysisService

router = APIRouter(prefix="/analysis", tags=["analysis"])


# ---------- 의존성 alias ----------
AnalysisServiceDep = Annotated[AnalysisService, Depends(get_analysis_service)]
ViewerDep = Annotated[User, Depends(require_role("viewer"))]


# ---------- 1) Run 요약 비교 ----------
@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="Run 요약 비교 (viewer+)",
)
async def compare_runs_endpoint(
    request: CompareRequest,
    service: AnalysisServiceDep,
    _user: ViewerDep,
) -> CompareResponse:
    """2~5개 Run을 요약 메트릭으로 비교."""
    return await service.compare_runs(
        project_id=request.project_id,
        run_names=request.run_names,
    )


# ---------- 2) 아이템 비교 ----------
@router.post(
    "/compare/items",
    response_model=CompareItemsResponse,
    summary="아이템 단위 비교 (viewer+)",
)
async def compare_items_endpoint(
    request: CompareItemsRequest,
    service: AnalysisServiceDep,
    _user: ViewerDep,
) -> CompareItemsResponse:
    """동일 dataset_item_id에 대해 Run별 output / score 비교 + 정렬/필터/페이지네이션."""
    return await service.compare_items(
        project_id=request.project_id,
        run_names=request.run_names,
        score_name=request.score_name,
        sort_by=request.sort_by,
        sort_order=request.sort_order,
        score_min=request.score_min,
        score_max=request.score_max,
        page=request.page,
        page_size=request.page_size,
    )


# ---------- 3) 스코어 분포 ----------
@router.get(
    "/scores/distribution",
    response_model=ScoreDistributionResponse,
    summary="스코어 분포 (viewer+)",
)
async def score_distribution_endpoint(
    service: AnalysisServiceDep,
    _user: ViewerDep,
    project_id: str = Query(..., min_length=1),
    run_names: list[str] = Query(..., description="Run 이름 (2~5개)"),
    score_name: str = Query(..., min_length=1),
    bins: int = Query(10, ge=2, le=50),
) -> ScoreDistributionResponse:
    """``[0.0, 1.0]`` 스코어 히스토그램."""
    if len(run_names) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at least 2 run_names required",
        )
    if len(run_names) > 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="max 5 run_names",
        )
    return await service.score_distribution(
        project_id=project_id,
        run_names=run_names,
        score_name=score_name,
        bins=bins,
    )


# ---------- 4) 지연 분포 ----------
@router.get(
    "/latency/distribution",
    response_model=LatencyDistributionResponse,
    summary="지연 분포 (viewer+)",
)
async def latency_distribution_endpoint(
    service: AnalysisServiceDep,
    _user: ViewerDep,
    project_id: str = Query(..., min_length=1),
    run_name: str = Query(..., min_length=1),
    bins: int = Query(20, ge=2, le=50),
) -> LatencyDistributionResponse:
    """단일 Run의 지연 히스토그램 + percentile."""
    return await service.latency_distribution(
        project_id=project_id,
        run_name=run_name,
        bins=bins,
    )


# ---------- 5) 비용 분포 ----------
@router.get(
    "/cost/distribution",
    response_model=CostDistributionResponse,
    summary="비용 분포 (viewer+)",
)
async def cost_distribution_endpoint(
    service: AnalysisServiceDep,
    _user: ViewerDep,
    project_id: str = Query(..., min_length=1),
    run_names: list[str] = Query(..., description="Run 이름 (2~5개)"),
) -> CostDistributionResponse:
    """Run별 model_cost / eval_cost / total_cost 분리."""
    if len(run_names) < 2:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="at least 2 run_names required",
        )
    if len(run_names) > 5:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="max 5 run_names",
        )
    return await service.cost_distribution(
        project_id=project_id,
        run_names=run_names,
    )
