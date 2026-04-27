"""실험 라우터 (API_DESIGN.md §4, §11.1).

본 파일은 다음 엔드포인트를 정의한다 (BUILD_ORDER §4-4, §4-5, §4-7):

- ``POST   /api/v1/experiments``                       생성 (Agent 17)
- ``GET    /api/v1/experiments``                       목록 (Agent 18)
- ``GET    /api/v1/experiments/{id}``                  상세 (Agent 18)
- ``GET    /api/v1/experiments/{id}/stream``           SSE 진행률 (Agent 17)
- ``DELETE /api/v1/experiments/{id}``                  삭제 (admin, Agent 18)
- ``POST   /api/v1/experiments/{id}/pause``            일시정지 (Agent 18)
- ``POST   /api/v1/experiments/{id}/resume``           재개 (Agent 18)
- ``POST   /api/v1/experiments/{id}/cancel``           중단 (Agent 18)
- ``POST   /api/v1/experiments/{id}/retry-failed``     실패 재시도 (Agent 18)

상태 전이 액션은 ``ETag/If-Match`` 헤더로 낙관적 동시성 제어가 적용된다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.deps import (
    get_batch_runner,
    get_redis_client,
)
from app.core.security import get_current_user, require_role
from app.models.auth import User
from app.models.experiment import (
    ExperimentCreate,
    ExperimentDetail,
    ExperimentInitResponse,
    ExperimentListResponse,
    ExperimentStatus,
    ExperimentStatusResponse,
)
from app.services.batch_runner import BatchExperimentRunner
from app.services.experiment_control import (
    ExperimentControl,
    ExperimentETagMismatchError,
)
from app.services.experiment_control import (
    ExperimentNotFoundError as ControlNotFoundError,
)
from app.services.experiment_query import (
    ExperimentNotFoundError as QueryNotFoundError,
)
from app.services.experiment_query import (
    ExperimentQuery,
)
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient
from app.services.sse import sse_response

router = APIRouter(prefix="/experiments", tags=["experiments"])


# ---------- 의존성 ----------
def get_experiment_control_router(
    redis: RedisClient = Depends(get_redis_client),
) -> ExperimentControl:
    """라우터 단위 ``ExperimentControl`` 의존성.

    ``app/core/deps.py``의 ``get_experiment_control``과 동일하지만,
    테스트의 ``dependency_overrides`` 호환성을 위해 라우터에서 재선언.
    """
    return ExperimentControl(redis=redis)


def get_experiment_query_router(
    redis: RedisClient = Depends(get_redis_client),
) -> ExperimentQuery:
    """라우터 단위 ``ExperimentQuery`` 의존성."""
    # langfuse는 현재 미사용(향후 trace metadata 폴백용 예약). lazy 인스턴스화.
    langfuse = LangfuseClient.__new__(LangfuseClient)  # type: ignore[call-arg]
    return ExperimentQuery(redis=redis, langfuse=langfuse)


CurrentUserDep = Annotated[User, Depends(get_current_user)]
ControlDep = Annotated[ExperimentControl, Depends(get_experiment_control_router)]
QueryDep = Annotated[ExperimentQuery, Depends(get_experiment_query_router)]


# ---------- 헬퍼 ----------
def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _load_etag(
    control: ExperimentControl, experiment_id: str
) -> tuple[str, dict[str, object]]:
    """현재 메타에서 ETag 계산 + meta dict 반환."""
    # 라우터에서 ETag 계산은 control._read_meta로 위임 (private 접근 — 같은 패키지 내)
    meta = await control._read_meta(experiment_id)  # noqa: SLF001
    if not meta:
        raise ControlNotFoundError(detail=f"experiment_id={experiment_id!r} not found")
    return ExperimentControl.compute_etag(meta), meta


# ---------- GET /experiments (목록) ----------
@router.get(
    "",
    response_model=ExperimentListResponse,
    summary="실험 목록 (페이지네이션 + 필터)",
)
async def list_experiments(
    user: CurrentUserDep,
    query: QueryDep,
    project_id: str = Query(..., min_length=1, description="대상 프로젝트 ID"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status_filter: ExperimentStatus | None = Query(
        None, alias="status", description="상태 필터 (옵션)"
    ),
    search: str | None = Query(None, description="이름 부분 일치 (case-insensitive)"),
    sort_by: str = Query("created_at"),
    sort_order: str = Query("desc", pattern="^(asc|desc)$"),
) -> ExperimentListResponse:
    """프로젝트 단위 실험 목록.

    - admin: 프로젝트 내 모든 실험
    - 그 외: 본인이 생성한 실험만
    """
    return await query.list_experiments(
        project_id=project_id,
        user_id=user.id,
        page=page,
        page_size=page_size,
        status=status_filter,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
        user_role=user.role,
    )


# ---------- GET /experiments/{id} (상세) ----------
@router.get(
    "/{experiment_id}",
    response_model=ExperimentDetail,
    summary="실험 상세 조회",
)
async def get_experiment(
    experiment_id: str,
    response: Response,
    user: CurrentUserDep,
    query: QueryDep,
    control: ControlDep,
    project_id: str | None = Query(None, description="프로젝트 격리 검증 (옵션)"),
) -> ExperimentDetail:
    """상세 + ``runs`` 요약 + ``config_snapshot`` 노출. ETag 헤더 부착."""
    try:
        detail = await query.get_experiment(
            experiment_id=experiment_id,
            user_id=user.id,
            user_role=user.role,
            project_id=project_id,
        )
    except QueryNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=exc.detail or "실험을 찾을 수 없습니다.",
        ) from exc

    etag, _ = await _load_etag(control, experiment_id)
    response.headers["ETag"] = etag
    return detail


# ---------- DELETE /experiments/{id} (admin) ----------
@router.delete(
    "/{experiment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="실험 삭제 (admin only, ETag/If-Match 필수)",
)
async def delete_experiment(
    experiment_id: str,
    control: ControlDep,
    user: User = Depends(require_role("admin")),
    if_match: str = Header(..., alias="If-Match"),
) -> Response:
    """admin only 실험 삭제. running/paused 상태에서는 409 STATE_CONFLICT."""
    etag, _meta = await _load_etag(control, experiment_id)
    ExperimentControl.verify_if_match(if_match, etag)
    await control.delete(experiment_id, user_id=user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- POST /experiments/{id}/pause ----------
@router.post(
    "/{experiment_id}/pause",
    response_model=ExperimentStatusResponse,
    summary="실험 일시정지 (running → paused)",
)
async def pause_experiment(
    experiment_id: str,
    user: CurrentUserDep,
    control: ControlDep,
    if_match: str = Header(..., alias="If-Match"),
) -> ExperimentStatusResponse:
    etag, _meta = await _load_etag(control, experiment_id)
    ExperimentControl.verify_if_match(if_match, etag)
    new_status = await control.pause(experiment_id, user.id)
    return ExperimentStatusResponse(
        experiment_id=experiment_id,
        status=new_status,
        transitioned_at=_now_utc(),
    )


# ---------- POST /experiments/{id}/resume ----------
@router.post(
    "/{experiment_id}/resume",
    response_model=ExperimentStatusResponse,
    summary="실험 재개 (paused → running)",
)
async def resume_experiment(
    experiment_id: str,
    user: CurrentUserDep,
    control: ControlDep,
    if_match: str = Header(..., alias="If-Match"),
) -> ExperimentStatusResponse:
    etag, _meta = await _load_etag(control, experiment_id)
    ExperimentControl.verify_if_match(if_match, etag)
    new_status = await control.resume(experiment_id, user.id)
    return ExperimentStatusResponse(
        experiment_id=experiment_id,
        status=new_status,
        transitioned_at=_now_utc(),
    )


# ---------- POST /experiments/{id}/cancel ----------
@router.post(
    "/{experiment_id}/cancel",
    response_model=ExperimentStatusResponse,
    summary="실험 중단 (pending|queued|running|paused|degraded → cancelled)",
)
async def cancel_experiment(
    experiment_id: str,
    user: CurrentUserDep,
    control: ControlDep,
    if_match: str = Header(..., alias="If-Match"),
) -> ExperimentStatusResponse:
    etag, _meta = await _load_etag(control, experiment_id)
    ExperimentControl.verify_if_match(if_match, etag)
    new_status = await control.cancel(experiment_id, user.id)
    return ExperimentStatusResponse(
        experiment_id=experiment_id,
        status=new_status,
        transitioned_at=_now_utc(),
    )


# ---------- POST /experiments/{id}/retry-failed ----------
@router.post(
    "/{experiment_id}/retry-failed",
    response_model=ExperimentStatusResponse,
    summary="실패 아이템 재실행 (completed|failed|degraded → running)",
)
async def retry_failed_experiment(
    experiment_id: str,
    user: CurrentUserDep,
    control: ControlDep,
    if_match: str = Header(..., alias="If-Match"),
) -> ExperimentStatusResponse:
    etag, _meta = await _load_etag(control, experiment_id)
    ExperimentControl.verify_if_match(if_match, etag)
    new_status = await control.retry_failed(experiment_id, user.id)
    return ExperimentStatusResponse(
        experiment_id=experiment_id,
        status=new_status,
        transitioned_at=_now_utc(),
    )


BatchRunnerDep = Annotated[BatchExperimentRunner, Depends(get_batch_runner)]


# ---------- POST /experiments (생성, Agent 17) ----------
@router.post(
    "",
    response_model=ExperimentInitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="배치 실험 생성 + 백그라운드 실행 시작 (user+)",
)
async def create_experiment(
    request: ExperimentCreate,
    runner: BatchRunnerDep,
    user: User = Depends(require_role("user")),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),  # noqa: ARG001
) -> ExperimentInitResponse:
    """배치 실험을 생성하고 백그라운드에서 실행을 시작한다.

    - 즉시 ``ExperimentInitResponse`` 반환 (실행은 비동기)
    - 워크스페이스 동시 실행 한도 초과 시 ``status=queued``
    - 데이터셋이 존재하지 않으면 502 ``LangfuseError`` 또는 500 ``LabsError``
    - ``Idempotency-Key`` 처리는 향후 추가 (현재는 received only)
    """
    return await runner.create_experiment(request=request, user_id=user.id)


# ---------- GET /experiments/{id}/stream (SSE, Agent 17) ----------
@router.get(
    "/{experiment_id}/stream",
    summary="실험 진행률 SSE 스트리밍 (소유자 또는 admin)",
)
async def stream_experiment(
    experiment_id: str,
    runner: BatchRunnerDep,
    user: CurrentUserDep,
    redis: RedisClient = Depends(get_redis_client),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
) -> Response:
    """``ax:exp_events:{id}``를 polling하여 SSE로 스트리밍.

    소유자 본인 또는 admin만 접근 가능. 권한 위반 시 404 (정보 노출 방지).
    """
    # 소유자 검증 — Hash에서 owner_user_id 비교
    # RedisClient.underlying / MockRedisClient._client / fallback redis
    underlying: Any = (
        getattr(redis, "underlying", None)
        or getattr(redis, "_client", None)
        or redis
    )
    raw = await underlying.hgetall(f"ax:experiment:{experiment_id}")
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="experiment not found"
        )
    # bytes/str 모두 수용
    owner_value = raw.get("owner_user_id") or raw.get(b"owner_user_id")
    owner = (
        owner_value.decode("utf-8") if isinstance(owner_value, bytes) else owner_value
    )
    if user.role != "admin" and owner is not None and owner != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="experiment not found"
        )

    return await sse_response(
        runner.stream_progress(
            experiment_id=experiment_id,
            last_event_id=last_event_id,
        )
    )


__all__ = [
    "ExperimentETagMismatchError",
    "router",
    "get_experiment_control_router",
    "get_experiment_query_router",
]
