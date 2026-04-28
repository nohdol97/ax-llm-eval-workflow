"""데이터셋 API 라우터.

엔드포인트:
- ``GET /api/v1/datasets`` — 목록 (viewer+)
- ``GET /api/v1/datasets/{name}/items`` — 아이템 조회 (viewer+)
- ``POST /api/v1/datasets/upload`` — 비동기 업로드 (user+, Idempotency-Key)
- ``POST /api/v1/datasets/upload/preview`` — 첫 5건 미리보기 (user+)
- ``GET /api/v1/datasets/upload/{upload_id}/stream`` — SSE 진행률 (소유자/admin)
- ``POST /api/v1/datasets/from-items`` — 실패 아이템 → 새 데이터셋 (user+, Idempotency-Key)
- ``DELETE /api/v1/datasets/{name}`` — 삭제 (admin only)

API_DESIGN.md §3, §6, §11.2, §12 참조.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    UploadFile,
)

from app.core.deps import get_langfuse_client, get_redis_client
from app.core.errors import LabsError
from app.core.security import get_current_user, require_role
from app.models.auth import User
from app.models.dataset import (
    DatasetItemListResponse,
    DatasetListResponse,
    DatasetSummary,
    DeleteResponse,
    FromItemsRequest,
    FromItemsResponse,
    PreviewResponse,
    UploadInitResponse,
    UploadMappingRequest,
)
from app.services.dataset_service import (
    MAX_FILE_SIZE_BYTES,
    DatasetNotFoundError,
    DatasetValidationError,
    FileTooLargeError,
    count_rows,
    delete_dataset_via_client,
    get_dataset_via_client,
    list_dataset_items_via_client,
    list_datasets_via_client,
    new_upload_id,
    paginate,
    preview_file,
    process_upload,
    stream_upload_progress,
    to_dataset_item_model,
)
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient
from app.services.sse import sse_response

router = APIRouter(tags=["datasets"])


# ---------- 의존성 alias ----------
RedisDep = Annotated[RedisClient, Depends(get_redis_client)]
LangfuseDep = Annotated[LangfuseClient, Depends(get_langfuse_client)]
CurrentUserDep = Annotated[User, Depends(get_current_user)]


# ---------- 1) 목록 조회 ----------
@router.get(
    "/datasets",
    response_model=DatasetListResponse,
    summary="데이터셋 목록 (viewer+)",
)
async def list_datasets_endpoint(
    langfuse: LangfuseDep,
    _user: Annotated[User, Depends(require_role("viewer"))],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DatasetListResponse:
    """Langfuse에 등록된 데이터셋 목록을 페이지네이션하여 반환."""
    raw = list_datasets_via_client(langfuse)
    sliced, total = paginate(raw, page, page_size)
    summaries = [
        DatasetSummary(
            name=str(d.get("name") or ""),
            description=d.get("description"),
            item_count=int(d.get("item_count") or 0),
            created_at=d.get("created_at") or datetime.now(UTC),
        )
        for d in sliced
    ]
    return DatasetListResponse(
        items=summaries,
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------- 2) 아이템 조회 ----------
@router.get(
    "/datasets/{name}/items",
    response_model=DatasetItemListResponse,
    summary="데이터셋 아이템 조회 (viewer+)",
)
async def list_dataset_items_endpoint(
    langfuse: LangfuseDep,
    _user: Annotated[User, Depends(require_role("viewer"))],
    name: str = Path(..., min_length=1, max_length=200),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> DatasetItemListResponse:
    """주어진 데이터셋의 아이템 목록을 페이지네이션."""
    try:
        raw = list_dataset_items_via_client(langfuse, name)
    except DatasetNotFoundError:
        raise
    sliced, total = paginate(raw, page, page_size)
    items = [to_dataset_item_model(r) for r in sliced]
    return DatasetItemListResponse(items=items, total=total, page=page, page_size=page_size)


# ---------- 3) 업로드 (비동기) ----------
@router.post(
    "/datasets/upload",
    response_model=UploadInitResponse,
    status_code=202,
    summary="데이터셋 비동기 업로드 (user+, Idempotency-Key)",
)
async def upload_dataset_endpoint(
    background_tasks: BackgroundTasks,
    redis: RedisDep,
    langfuse: LangfuseDep,
    user: Annotated[User, Depends(require_role("user"))],
    file: UploadFile = File(...),
    dataset_name: str = Form(..., min_length=1, max_length=200),
    description: str | None = Form(default=None),
    mapping: str = Form(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> UploadInitResponse:
    """파일 업로드 후 즉시 ``upload_id`` 반환, 처리는 백그라운드 태스크로 위임.

    - 파일 크기 50MB 초과 시 413
    - 행 수 10,000 초과 시 처리 중 failed 상태
    - ``Idempotency-Key``: 동일 user+key 조합은 24h Redis 캐시
    """
    # Idempotency 처리 (간단형: key 충돌 시 기존 upload_id 반환)
    if idempotency_key is not None:
        cached = await _idempotency_get(redis, user.id, idempotency_key)
        if cached is not None:
            return cached

    # 파일 본문 읽기 + 크기 검증
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            detail=(
                f"파일 크기 {len(content)} bytes가 제한 {MAX_FILE_SIZE_BYTES} bytes를 초과했습니다."
            )
        )

    # mapping JSON 파싱 + 검증
    try:
        import json as _json

        mapping_dict = _json.loads(mapping)
    except _json.JSONDecodeError as exc:
        raise DatasetValidationError(detail=f"mapping JSON 파싱 실패: {exc.msg}") from exc
    try:
        mapping_obj = UploadMappingRequest.model_validate(mapping_dict)
    except Exception as exc:  # noqa: BLE001
        raise DatasetValidationError(detail=f"mapping 검증 실패: {exc}") from exc

    # 행 수 사전 카운트 (제한 검증은 process_upload 내부에서도 수행)
    try:
        total_rows = count_rows(content, file.filename or "upload.csv")
    except DatasetValidationError:
        raise

    upload_id = new_upload_id()

    # 백그라운드 처리 등록
    background_tasks.add_task(
        process_upload,
        upload_id,
        content,
        file.filename or "upload.csv",
        dataset_name,
        description,
        mapping_obj,
        langfuse=langfuse,
        redis=redis,
        owner_user_id=user.id,
        initial_total=total_rows,
    )

    response = UploadInitResponse(
        upload_id=upload_id,
        status="pending",
        dataset_name=dataset_name,
    )

    if idempotency_key is not None:
        await _idempotency_set(redis, user.id, idempotency_key, response)

    return response


# ---------- 4) 업로드 미리보기 ----------
@router.post(
    "/datasets/upload/preview",
    response_model=PreviewResponse,
    summary="업로드 미리보기 (user+) — 첫 5건",
)
async def upload_preview_endpoint(
    _user: Annotated[User, Depends(require_role("user"))],
    file: UploadFile = File(...),
    mapping: str = Form(...),
) -> PreviewResponse:
    """파일을 즉시 파싱하여 컬럼 / 첫 5건 / 전체 행 수를 반환.

    Langfuse 호출 없이 메모리에서 처리한다 (행 수 제한 미적용).
    """
    content = await file.read()
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            detail=(
                f"파일 크기 {len(content)} bytes가 제한 {MAX_FILE_SIZE_BYTES} bytes를 초과했습니다."
            )
        )

    try:
        import json as _json

        mapping_dict = _json.loads(mapping)
        mapping_obj = UploadMappingRequest.model_validate(mapping_dict)
    except _json.JSONDecodeError as exc:
        raise DatasetValidationError(detail=f"mapping JSON 파싱 실패: {exc.msg}") from exc
    except Exception as exc:  # noqa: BLE001
        raise DatasetValidationError(detail=f"mapping 검증 실패: {exc}") from exc

    columns, items, total_rows = preview_file(content, file.filename or "upload.csv", mapping_obj)
    return PreviewResponse(columns=columns, preview=items, total_rows=total_rows)


# ---------- 5) SSE 진행률 ----------
@router.get(
    "/datasets/upload/{upload_id}/stream",
    summary="업로드 진행률 SSE (소유자 또는 admin)",
)
async def upload_stream_endpoint(
    redis: RedisDep,
    user: CurrentUserDep,
    upload_id: str = Path(..., min_length=1, max_length=200),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
) -> Any:  # type: ignore[misc]
    """``upload_id``의 진행률을 SSE로 스트리밍.

    소유자 본인 또는 admin만 접근 가능. 권한 위반 시 404 반환 (정보 노출 방지).
    """
    # 소유자 검증 — 키가 존재하면 owner_user_id 비교, 없으면 404
    from app.services.dataset_service import _load_progress  # 내부 헬퍼 재사용

    snap = await _load_progress(redis, upload_id)
    if snap is None:
        raise HTTPException(status_code=404, detail="upload_id not found")
    owner = snap.get("owner_user_id")
    if user.role != "admin" and owner is not None and owner != user.id:
        raise HTTPException(status_code=404, detail="upload_id not found")

    return await sse_response(stream_upload_progress(upload_id, redis, last_event_id=last_event_id))


# ---------- 6) from-items: 실패 아이템 → 새 데이터셋 ----------
@router.post(
    "/datasets/from-items",
    response_model=FromItemsResponse,
    summary="실패 아이템 기반 파생 데이터셋 생성 (user+)",
)
async def from_items_endpoint(
    redis: RedisDep,
    langfuse: LangfuseDep,
    user: Annotated[User, Depends(require_role("user"))],
    payload: FromItemsRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> FromItemsResponse:
    """기존 trace/실험에서 실패 아이템을 모아 새 데이터셋으로 생성."""
    if idempotency_key is not None:
        cached = await _idempotency_get_response(redis, user.id, idempotency_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

    # 새 데이터셋 생성
    try:
        langfuse.create_dataset(
            name=payload.new_dataset_name,
            description=payload.description,
            metadata={
                "derived_from": payload.source_experiment_id,
                "project_id": payload.project_id,
            },
        )
    except LabsError:
        raise

    items_created = 0
    failures = 0

    # 원본 실험에서 아이템 추출 — 단순화: source_experiment_id 매핑된 trace의
    # input/expected/metadata를 그대로 새 데이터셋에 복사.
    # Langfuse가 실험 매핑을 직접 노출하지 않을 수 있으므로 mock에서는
    # 모든 아이템 조회 후 item_ids 필터링 방식으로 구현.
    source_items: list[dict] = []
    try:
        # 우선 동일 이름의 source dataset이 있을 가능성 시도
        source_items = list_dataset_items_via_client(langfuse, payload.source_experiment_id)
    except DatasetNotFoundError:
        source_items = []

    by_id = {it.get("id"): it for it in source_items if it.get("id")}

    for item_id in payload.item_ids:
        src = by_id.get(item_id)
        if src is None:
            failures += 1
            continue
        try:
            langfuse.create_dataset_item(
                dataset_name=payload.new_dataset_name,
                input=src.get("input") or {},
                expected_output=src.get("expected_output"),
                metadata={
                    **(src.get("metadata") or {}),
                    "derived_from_item_id": item_id,
                    "derived_from_experiment_id": payload.source_experiment_id,
                },
            )
            items_created += 1
        except LabsError:
            failures += 1

    if items_created == 0:
        status_value: str = "failed"
    elif failures > 0:
        status_value = "partial"
    else:
        status_value = "completed"

    response = FromItemsResponse(
        dataset_name=payload.new_dataset_name,
        items_created=items_created,
        status=status_value,  # type: ignore[arg-type]
    )

    if idempotency_key is not None:
        await _idempotency_set_response(redis, user.id, idempotency_key, response)

    return response


# ---------- 7) DELETE (admin only) ----------
@router.delete(
    "/datasets/{name}",
    response_model=DeleteResponse,
    summary="데이터셋 삭제 (admin only, ETag/If-Match)",
)
async def delete_dataset_endpoint(
    langfuse: LangfuseDep,
    _admin: Annotated[User, Depends(require_role("admin"))],
    name: str = Path(..., min_length=1, max_length=200),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> DeleteResponse:
    """admin 전용 데이터셋 삭제. ETag/If-Match는 ``*`` 또는 일치 시 통과."""
    # 존재 확인
    existing = get_dataset_via_client(langfuse, name)
    if existing is None:
        raise DatasetNotFoundError(detail=f"데이터셋을 찾을 수 없습니다: {name}")

    # If-Match 처리 — 본 구현은 dataset 메타로부터 약식 ETag 생성. ``*``는 통과.
    if if_match is not None and if_match != "*":
        expected_etag = _compute_dataset_etag(existing)
        if expected_etag != if_match.strip('"'):
            raise HTTPException(
                status_code=412,
                detail="If-Match 헤더가 현재 ETag와 일치하지 않습니다.",
            )

    delete_dataset_via_client(langfuse, name)
    return DeleteResponse(dataset_name=name, deleted=True)


# ---------- ETag ----------
def _compute_dataset_etag(ds: dict) -> str:
    """데이터셋 메타로부터 16자리 sha256 prefix ETag 생성."""
    import hashlib

    src = f"{ds.get('name')}|{ds.get('item_count')}|{ds.get('created_at')}"
    digest = hashlib.sha256(src.encode("utf-8")).hexdigest()
    return digest[:16]


# ---------- Idempotency 헬퍼 ----------
async def _idempotency_get(redis: RedisClient, user_id: str, key: str) -> UploadInitResponse | None:
    """업로드 idempotency 캐시 조회."""
    raw = await redis.get(_idem_key(user_id, key))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        import json as _json

        return UploadInitResponse.model_validate(_json.loads(raw))
    except Exception:  # noqa: BLE001
        return None


async def _idempotency_set(
    redis: RedisClient,
    user_id: str,
    key: str,
    response: UploadInitResponse,
) -> None:
    """업로드 idempotency 캐시 저장 (TTL 24h)."""
    await redis.set(
        _idem_key(user_id, key),
        response.model_dump_json(),
        ex=24 * 3600,
    )


async def _idempotency_get_response(
    redis: RedisClient, user_id: str, key: str
) -> FromItemsResponse | None:
    """from-items idempotency 캐시 조회."""
    raw = await redis.get(_idem_key(user_id, key))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        import json as _json

        return FromItemsResponse.model_validate(_json.loads(raw))
    except Exception:  # noqa: BLE001
        return None


async def _idempotency_set_response(
    redis: RedisClient,
    user_id: str,
    key: str,
    response: FromItemsResponse,
) -> None:
    """from-items idempotency 캐시 저장 (TTL 24h)."""
    await redis.set(
        _idem_key(user_id, key),
        response.model_dump_json(),
        ex=24 * 3600,
    )


def _idem_key(user_id: str, key: str) -> str:
    """``ax:idem:{user_id}:{key}``."""
    return f"idem:{user_id}:{key}"
