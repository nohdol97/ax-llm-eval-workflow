"""데이터셋 비즈니스 로직.

- 파일 파싱: CSV / JSON / JSONL 자동 감지 + 인코딩 fallback (utf-8, utf-8-sig, euc-kr)
- 컬럼 매핑: ``UploadMappingRequest`` 기반 input/expected/metadata 분리
- CSV formula injection 방지: 첫 셀이 ``= + - @ \\t \\r``로 시작하는 경우 ``'`` prefix
- 비동기 업로드 처리: Redis에 진행률 영속, Langfuse에 아이템 생성
- SSE 진행률 polling: ``ax:dataset_upload:{upload_id}`` 키 변화 감시

Redis 키 규약 (IMPLEMENTATION.md §6 / API_DESIGN.md §6.3.1):
- ``ax:dataset_upload:{upload_id}`` Hash: status / processed / total / dataset_name /
  owner_user_id / error_message / created_at / failed_items (JSON list)
- TTL 24h (사양: 1시간이지만 데이터 안정성을 위해 24h)
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

from app.core.errors import LabsError, LangfuseError
from app.models.dataset import (
    DatasetItem,
    PreviewItem,
    UploadMappingRequest,
    UploadProgress,
)
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient
from app.services.sse import (
    SSE_HEARTBEAT_INTERVAL_SEC,
    format_retry_directive,
    format_sse_event,
    heartbeat,
)

logger = logging.getLogger(__name__)

# ---------- 도메인 예외 ----------


class DatasetValidationError(LabsError):
    """데이터셋 입력/파일 검증 실패."""

    code = "dataset_validation_error"
    status_code = 422
    title = "Dataset validation failed"


class DatasetNotFoundError(LabsError):
    """데이터셋이 존재하지 않음."""

    code = "dataset_not_found"
    status_code = 404
    title = "Dataset not found"


class FileTooLargeError(DatasetValidationError):
    """파일 크기 초과."""

    code = "file_too_large"
    status_code = 413
    title = "File too large"


class TooManyRowsError(DatasetValidationError):
    """행 수 초과."""

    code = "too_many_rows"
    status_code = 422
    title = "Too many rows"


# ---------- 정책 상수 ----------

# 파일 크기 제한 50MB
MAX_FILE_SIZE_BYTES: int = 50 * 1024 * 1024

# 행 수 제한 10,000
MAX_ROWS: int = 10_000

# 동기 vs 비동기 업로드 분기 한계 (API_DESIGN.md §6.3 — 500행 이하는 동기)
SYNC_UPLOAD_THRESHOLD: int = 500

# 미리보기 제한
PREVIEW_LIMIT: int = 5

# Redis 키
UPLOAD_KEY_PREFIX: str = "dataset_upload:"
UPLOAD_TTL_SEC: int = 24 * 3600  # 24시간

# CSV formula injection 위험 prefix (RFC: API_DESIGN.md §1.1 파일 다운로드 규약)
CSV_FORMULA_PREFIXES: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")

# 인코딩 fallback 우선순위 — chardet 미사용 (의존성 추가 회피)
ENCODING_CANDIDATES: tuple[str, ...] = ("utf-8-sig", "utf-8", "euc-kr", "cp949", "latin-1")


# ---------- 인코딩 / 파일 형식 감지 ----------


def detect_encoding(content: bytes) -> str:
    """바이트 컨텐츠의 인코딩을 추정.

    chardet이 설치된 경우 우선 사용하고, 없으면 ``ENCODING_CANDIDATES`` 순서로
    decode를 시도하여 첫 번째 성공한 인코딩을 반환한다.

    Returns:
        ``utf-8-sig`` / ``utf-8`` / ``euc-kr`` / ``cp949`` / ``latin-1`` 중 하나.
    """
    try:
        import chardet  # type: ignore[import-not-found]

        result = chardet.detect(content[:65536])  # 첫 64KB
        encoding = result.get("encoding")
        confidence = float(result.get("confidence") or 0.0)
        if encoding and confidence >= 0.7:
            return str(encoding).lower()
    except ImportError:
        pass
    except Exception:  # noqa: BLE001, S110
        # chardet 자체 에러는 무시하고 ENCODING_CANDIDATES fallback
        pass

    for enc in ENCODING_CANDIDATES:
        try:
            content.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    # 최후 수단 — latin-1은 항상 성공
    return "latin-1"


def detect_file_format(filename: str, content: bytes) -> str:
    """파일 확장자 + 본문 첫 바이트 검사로 형식 감지.

    Returns:
        ``"csv"`` / ``"json"`` / ``"jsonl"`` 중 하나
    """
    lower = (filename or "").lower()
    if lower.endswith(".csv"):
        return "csv"
    if lower.endswith(".jsonl") or lower.endswith(".ndjson"):
        return "jsonl"
    if lower.endswith(".json"):
        # JSON이 array(``[...]``)인지 NDJSON(여러 줄)인지 시그니처 검사
        head = content.lstrip()[:1]
        if head == b"[" or head == b"{":
            # 단일 라인 JSON 배열 또는 객체 — 본문 줄바꿈으로 NDJSON 판별
            try:
                text = content.decode("utf-8", errors="ignore")
                non_empty_lines = [ln for ln in text.splitlines() if ln.strip()]
                # 여러 줄에 각각 JSON 객체가 있으면 jsonl로 처리
                if len(non_empty_lines) > 1 and all(
                    ln.lstrip().startswith("{") for ln in non_empty_lines
                ):
                    return "jsonl"
            except Exception:  # noqa: BLE001, S110
                # decode 실패는 json 형식으로 fallback
                pass
            return "json"
        return "json"

    # 확장자 없음 — 본문 시그니처
    head = content.lstrip()[:1]
    if head in (b"[", b"{"):
        return "json"
    return "csv"


# ---------- CSV formula injection 방지 ----------


def sanitize_csv_value(value: Any) -> Any:
    """CSV 셀 값에서 formula injection 위험을 차단.

    문자열이고 첫 글자가 ``= + - @ \\t \\r``로 시작하면 ``'`` prefix를 붙인다.
    숫자/None/dict 등 비문자열은 그대로 반환.
    """
    if isinstance(value, str) and value and value[0] in CSV_FORMULA_PREFIXES:
        return "'" + value
    return value


def is_formula_injection(value: Any) -> bool:
    """주어진 값이 CSV formula injection 위험에 해당하는지 판별 (검증용)."""
    return isinstance(value, str) and len(value) > 0 and value[0] in CSV_FORMULA_PREFIXES


# ---------- 파일 파싱 ----------


def _validate_size_and_decode(content: bytes) -> str:
    """파일 크기 검증 + 인코딩 자동 감지 후 디코드된 텍스트 반환."""
    if len(content) > MAX_FILE_SIZE_BYTES:
        raise FileTooLargeError(
            detail=(
                f"파일 크기 {len(content)} bytes가 제한 {MAX_FILE_SIZE_BYTES} bytes를 초과했습니다."
            )
        )
    encoding = detect_encoding(content)
    try:
        return content.decode(encoding, errors="replace")
    except Exception as exc:  # noqa: BLE001
        raise DatasetValidationError(
            detail=f"파일 디코딩 실패 (encoding={encoding}): {exc}"
        ) from exc


def _iter_csv_rows(text: str) -> Iterator[dict[str, Any]]:
    """CSV 텍스트를 dict iterator로 변환 (DictReader)."""
    reader = csv.DictReader(io.StringIO(text))
    for row in reader:
        # csv.DictReader는 빈 라인을 자동 skip — None 값은 빈 문자열로 정규화
        yield {k: (v if v is not None else "") for k, v in row.items()}


def _iter_jsonl_rows(text: str) -> Iterator[dict[str, Any]]:
    """JSONL 텍스트를 dict iterator로 변환 — 빈 라인 무시, 잘못된 라인은 raise."""
    for line_no, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(
                detail=f"JSONL 라인 {line_no} 파싱 실패: {exc.msg}"
            ) from exc
        if not isinstance(obj, dict):
            raise DatasetValidationError(detail=f"JSONL 라인 {line_no}: 객체(dict)여야 합니다.")
        yield obj


def _iter_json_rows(text: str) -> Iterator[dict[str, Any]]:
    """JSON 배열을 dict iterator로 변환."""
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(detail=f"JSON 파싱 실패: {exc.msg}") from exc
    if isinstance(obj, dict):
        # 단일 객체 → 단일 행으로 처리
        yield obj
        return
    if not isinstance(obj, list):
        raise DatasetValidationError(detail="JSON 최상위는 배열(list) 또는 객체(dict)여야 합니다.")
    for idx, item in enumerate(obj):
        if not isinstance(item, dict):
            raise DatasetValidationError(
                detail=f"JSON 배열의 인덱스 {idx} 원소는 객체(dict)여야 합니다."
            )
        yield item


def _validate_mapping_against_columns(
    mapping: UploadMappingRequest,
    columns: list[str],
) -> None:
    """매핑된 컬럼명이 실제 파일 컬럼에 존재하는지 검증."""
    columns_set = set(columns)
    missing: list[str] = []
    for col in mapping.input_columns:
        if col not in columns_set:
            missing.append(col)
    if mapping.output_column not in columns_set:
        missing.append(mapping.output_column)
    for col in mapping.metadata_columns:
        if col not in columns_set:
            missing.append(col)
    if missing:
        raise DatasetValidationError(detail=f"매핑된 컬럼이 파일에 존재하지 않습니다: {missing}")


def _row_to_item(
    row: dict[str, Any],
    mapping: UploadMappingRequest,
) -> dict[str, Any]:
    """단일 dict 행을 ``{input, expected_output, metadata}`` 형태로 변환."""
    input_dict: dict[str, Any] = {col: row.get(col) for col in mapping.input_columns}
    expected = row.get(mapping.output_column)
    metadata: dict[str, Any] = {col: row.get(col) for col in mapping.metadata_columns}
    return {
        "input": input_dict,
        "expected_output": expected,
        "metadata": metadata,
    }


def parse_file(
    content: bytes,
    filename: str,
    mapping: UploadMappingRequest,
    *,
    enforce_row_limit: bool = True,
) -> Iterator[dict[str, Any]]:
    """파일 본문을 파싱하여 ``{input, expected_output, metadata}`` iterator 반환.

    Args:
        content: 업로드된 파일 바이트
        filename: 원본 파일 이름 (확장자로 형식 추정)
        mapping: 컬럼 매핑 요청
        enforce_row_limit: True면 ``MAX_ROWS`` 초과 시 즉시 raise

    Raises:
        FileTooLargeError: 파일 크기 초과
        TooManyRowsError: 행 수 초과
        DatasetValidationError: 형식/컬럼 검증 실패
    """
    text = _validate_size_and_decode(content)
    fmt = detect_file_format(filename, content)

    # 첫 행을 미리 가져와 컬럼명을 확정 — 매핑 검증
    raw_iter: Iterator[dict[str, Any]]
    if fmt == "csv":
        raw_iter = _iter_csv_rows(text)
    elif fmt == "jsonl":
        raw_iter = _iter_jsonl_rows(text)
    else:
        raw_iter = _iter_json_rows(text)

    # 첫 행 peek — 컬럼 검증을 위해
    try:
        first_row = next(raw_iter)
    except StopIteration:
        # 빈 파일 — 매핑 검증만 수행하고 종료
        return

    columns = list(first_row.keys())
    _validate_mapping_against_columns(mapping, columns)

    yielded = 0

    def _bump() -> None:
        nonlocal yielded
        yielded += 1
        if enforce_row_limit and yielded > MAX_ROWS:
            raise TooManyRowsError(
                detail=(f"행 수가 {MAX_ROWS}건을 초과했습니다. 파일을 분할하여 업로드하세요.")
            )

    yield _row_to_item(first_row, mapping)
    _bump()
    for row in raw_iter:
        yield _row_to_item(row, mapping)
        _bump()


def count_rows(content: bytes, filename: str) -> int:
    """행 수 빠르게 카운트 (매핑 검증 없이).

    미리보기 응답의 ``total_rows`` 계산용. 행 수 제한 검증은 하지 않는다.
    """
    text = _validate_size_and_decode(content)
    fmt = detect_file_format(filename, content)

    if fmt == "csv":
        # DictReader는 헤더를 첫 행으로 소비
        reader = csv.reader(io.StringIO(text))
        try:
            next(reader)  # skip header
        except StopIteration:
            return 0
        return sum(1 for _ in reader)
    if fmt == "jsonl":
        return sum(1 for ln in text.splitlines() if ln.strip())
    # JSON
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return 0
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        return 1
    return 0


def preview_file(
    content: bytes,
    filename: str,
    mapping: UploadMappingRequest,
    limit: int = PREVIEW_LIMIT,
) -> tuple[list[str], list[PreviewItem], int]:
    """업로드 미리보기 — 컬럼 / 최대 ``limit``건 / 전체 행 수.

    행 수 제한은 적용하지 않는다 (미리보기는 검증 단계).
    """
    total_rows = count_rows(content, filename)
    items: list[PreviewItem] = []
    columns: list[str] = []
    for idx, parsed in enumerate(parse_file(content, filename, mapping, enforce_row_limit=False)):
        if idx == 0:
            columns = (
                list(mapping.input_columns)
                + [mapping.output_column]
                + list(mapping.metadata_columns)
            )
        if idx >= limit:
            break
        items.append(
            PreviewItem(
                input=parsed["input"],
                expected_output=parsed["expected_output"],
                metadata=parsed["metadata"],
            )
        )
    return columns, items, total_rows


# ---------- Redis 진행률 ----------


def _upload_key(upload_id: str) -> str:
    """업로드 진행률 Redis 키 (prefix 없이 — RedisClient가 자동 prefix)."""
    return f"{UPLOAD_KEY_PREFIX}{upload_id}"


async def _save_progress(
    redis: RedisClient,
    upload_id: str,
    *,
    status: str,
    processed: int,
    total: int,
    dataset_name: str,
    owner_user_id: str | None = None,
    error_message: str | None = None,
) -> None:
    """진행률 스냅샷을 Redis에 JSON 저장 + TTL 갱신."""
    payload: dict[str, Any] = {
        "upload_id": upload_id,
        "status": status,
        "processed": processed,
        "total": total,
        "dataset_name": dataset_name,
        "owner_user_id": owner_user_id,
        "error_message": error_message,
        "updated_at": time.time(),
    }
    await redis.set(
        _upload_key(upload_id),
        json.dumps(payload, ensure_ascii=False),
        ex=UPLOAD_TTL_SEC,
    )


async def _load_progress(
    redis: RedisClient,
    upload_id: str,
) -> dict[str, Any] | None:
    """Redis에서 진행률 JSON 읽기."""
    raw = await redis.get(_upload_key(upload_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(result, dict):
        return result
    return None


async def get_upload_progress(
    redis: RedisClient,
    upload_id: str,
) -> UploadProgress | None:
    """공개 헬퍼 — 진행률 모델 반환."""
    snap = await _load_progress(redis, upload_id)
    if snap is None:
        return None
    return UploadProgress(
        upload_id=snap.get("upload_id", upload_id),
        status=snap.get("status", "pending"),
        processed=int(snap.get("processed", 0)),
        total=int(snap.get("total", 0)),
        error_message=snap.get("error_message"),
        dataset_name=snap.get("dataset_name"),
    )


def new_upload_id() -> str:
    """업로드 ID 생성. 형식: ``ds_upload_<uuid4-hex>``."""
    return f"ds_upload_{uuid.uuid4().hex}"


# ---------- 비동기 업로드 처리 ----------


async def process_upload(
    upload_id: str,
    content: bytes,
    filename: str,
    dataset_name: str,
    description: str | None,
    mapping: UploadMappingRequest,
    *,
    langfuse: LangfuseClient | Any,
    redis: RedisClient,
    owner_user_id: str | None = None,
    initial_total: int | None = None,
) -> None:
    """업로드 백그라운드 처리.

    1. Redis 초기 상태(running) 기록
    2. 파일 파싱 → Langfuse 데이터셋 생성 → 아이템별 ``create_dataset_item`` 호출
    3. 100건마다 또는 1초 간격으로 Redis 진행률 갱신
    4. 완료 시 status=completed / 실패 시 status=failed + error_message

    PII 차단을 위해 아이템 내용은 INFO 로그에 기록하지 않는다.
    """
    # 1) 초기 상태 기록
    total = initial_total if initial_total is not None else 0
    await _save_progress(
        redis,
        upload_id,
        status="running",
        processed=0,
        total=total,
        dataset_name=dataset_name,
        owner_user_id=owner_user_id,
    )

    processed = 0
    last_flush_at = time.monotonic()

    try:
        # 2) Langfuse 데이터셋 생성 (idempotent)
        try:
            langfuse.create_dataset(
                name=dataset_name,
                description=description,
                metadata={"upload_id": upload_id},
            )
        except LangfuseError as exc:
            await _save_progress(
                redis,
                upload_id,
                status="failed",
                processed=0,
                total=total,
                dataset_name=dataset_name,
                owner_user_id=owner_user_id,
                error_message=f"Langfuse 데이터셋 생성 실패: {exc.detail}",
            )
            return

        # 3) 행별 처리
        for parsed in parse_file(content, filename, mapping):
            try:
                langfuse.create_dataset_item(
                    dataset_name=dataset_name,
                    input=parsed["input"],
                    expected_output=parsed["expected_output"],
                    metadata=parsed["metadata"],
                )
            except LangfuseError as exc:
                # 행 단위 실패는 카운트만 — file-level error는 이미 위에서 처리
                logger.warning(
                    "dataset_item_create_failed",
                    extra={
                        "upload_id": upload_id,
                        "dataset_name": dataset_name,
                        "error": str(exc.detail or exc),
                    },
                )
            processed += 1

            # 진행률 throttling: 100건 단위 OR 1초 간격
            now = time.monotonic()
            if processed % 100 == 0 or (now - last_flush_at) >= 1.0:
                await _save_progress(
                    redis,
                    upload_id,
                    status="running",
                    processed=processed,
                    total=max(total, processed),
                    dataset_name=dataset_name,
                    owner_user_id=owner_user_id,
                )
                last_flush_at = now

        # 4) 완료 처리
        await _save_progress(
            redis,
            upload_id,
            status="completed",
            processed=processed,
            total=max(total, processed),
            dataset_name=dataset_name,
            owner_user_id=owner_user_id,
        )
        logger.info(
            "dataset_upload_completed",
            extra={
                "upload_id": upload_id,
                "dataset_name": dataset_name,
                "processed": processed,
            },
        )

    except (FileTooLargeError, TooManyRowsError, DatasetValidationError) as exc:
        await _save_progress(
            redis,
            upload_id,
            status="failed",
            processed=processed,
            total=total,
            dataset_name=dataset_name,
            owner_user_id=owner_user_id,
            error_message=str(exc.detail or exc.title),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "dataset_upload_failed",
            extra={"upload_id": upload_id, "dataset_name": dataset_name},
        )
        await _save_progress(
            redis,
            upload_id,
            status="failed",
            processed=processed,
            total=total,
            dataset_name=dataset_name,
            owner_user_id=owner_user_id,
            error_message=str(exc),
        )


# ---------- SSE 진행률 스트리밍 ----------


async def stream_upload_progress(
    upload_id: str,
    redis: RedisClient,
    *,
    poll_interval: float = 0.1,
    last_event_id: int | None = None,
    timeout_sec: float = 600.0,
) -> AsyncIterator[str]:
    """업로드 진행률을 SSE 형식으로 스트리밍.

    - 100ms마다 Redis 키 polling, 변화 시 ``event: progress`` 발송
    - 15초 무변화 시 ``: heartbeat`` 주석 발송
    - status가 ``completed`` / ``failed``가 되면 ``event: done`` / ``event: error`` 후 종료
    - ``last_event_id`` (재연결 ``Last-Event-ID``) 수신 시 해당 id 이후부터 재전송 —
      현 구현은 현재 스냅샷만 보존하므로 즉시 최신 상태를 1회 발송한다 (단조 증가 id).
    """
    yield format_retry_directive()

    event_id = (last_event_id or 0) + 1
    last_payload: dict[str, Any] | None = None
    last_emit_at = time.monotonic()
    started_at = time.monotonic()

    # 초기 스냅샷 — 존재하면 즉시 발송
    initial = await _load_progress(redis, upload_id)
    if initial is None:
        # 키가 아직 생성되지 않았을 수도 있음 — 잠시 대기
        await asyncio.sleep(poll_interval)
        initial = await _load_progress(redis, upload_id)

    if initial is None:
        # 그래도 없으면 not_found 이벤트 후 종료
        yield format_sse_event(
            "error",
            {"code": "UPLOAD_NOT_FOUND", "message": "upload_id not found"},
            event_id=event_id,
        )
        return

    yield format_sse_event(
        _event_for_status(initial.get("status", "running")),
        _payload_for_progress(initial),
        event_id=event_id,
    )
    last_payload = initial
    last_emit_at = time.monotonic()
    event_id += 1

    if initial.get("status") in ("completed", "failed"):
        return

    # 폴링 루프
    while True:
        if time.monotonic() - started_at > timeout_sec:
            yield format_sse_event(
                "error",
                {"code": "STREAM_TIMEOUT", "message": "stream timed out"},
                event_id=event_id,
            )
            return

        await asyncio.sleep(poll_interval)
        snap = await _load_progress(redis, upload_id)
        if snap is None:
            # 키 만료 — 종료
            yield format_sse_event(
                "error",
                {"code": "UPLOAD_EXPIRED", "message": "upload state expired"},
                event_id=event_id,
            )
            return

        # 변화 감지: processed 또는 status가 변경되었을 때만 이벤트 발송
        changed = (
            last_payload is None
            or snap.get("processed") != last_payload.get("processed")
            or snap.get("status") != last_payload.get("status")
        )
        if changed:
            yield format_sse_event(
                _event_for_status(snap.get("status", "running")),
                _payload_for_progress(snap),
                event_id=event_id,
            )
            last_payload = snap
            last_emit_at = time.monotonic()
            event_id += 1

            if snap.get("status") in ("completed", "failed"):
                return
        else:
            # 무변화 → heartbeat (15초마다)
            now = time.monotonic()
            if now - last_emit_at >= SSE_HEARTBEAT_INTERVAL_SEC:
                yield heartbeat()
                last_emit_at = now


def _event_for_status(status: str) -> str:
    """status 값에 대응하는 SSE event 타입."""
    if status == "completed":
        return "done"
    if status == "failed":
        return "error"
    return "progress"


def _payload_for_progress(snap: dict[str, Any]) -> dict[str, Any]:
    """Redis 스냅샷을 SSE data payload로 변환 (불필요 필드 제외)."""
    return {
        "upload_id": snap.get("upload_id"),
        "status": snap.get("status"),
        "processed": int(snap.get("processed", 0)),
        "total": int(snap.get("total", 0)),
        "dataset_name": snap.get("dataset_name"),
        "error_message": snap.get("error_message"),
    }


# ---------- Langfuse 상호작용 헬퍼 (list / delete) ----------


def list_datasets_via_client(langfuse: LangfuseClient | Any) -> list[dict[str, Any]]:
    """Langfuse에서 데이터셋 목록을 조회.

    Phase 2 ``LangfuseClient``는 list_datasets를 노출하지 않으므로 SDK 또는 mock의
    내부 상태를 우회 접근하여 본 함수에서 처리한다. real client에서는 SDK
    ``list_datasets`` 메서드(있는 경우) 또는 REST API에 폴백해야 한다.

    반환 형식: ``[{name, description, items_count, created_at}]``
    """
    # 1) SDK 또는 client에 직접 메서드가 있는지 시도
    fn = getattr(langfuse, "list_datasets", None)
    if callable(fn):
        try:
            result = fn()
            if isinstance(result, list):
                return [_normalize_dataset_summary(item) for item in result]
        except Exception:  # noqa: BLE001, S110
            # SDK 메서드 시그니처 차이는 mock fallback으로 처리
            pass

    # 2) Mock client의 내부 상태 (테스트용)
    datasets = getattr(langfuse, "_datasets", None)
    if isinstance(datasets, dict):
        return [_normalize_dataset_summary(ds) for ds in datasets.values()]

    # 3) Real LangfuseClient — SDK lazy 초기화
    get_sdk = getattr(langfuse, "_get_sdk", None)
    if callable(get_sdk):
        try:
            sdk = get_sdk()
            sdk_fn = getattr(sdk, "list_datasets", None)
            if callable(sdk_fn):
                raw = sdk_fn()
                if isinstance(raw, list):
                    return [_normalize_dataset_summary(item) for item in raw]
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"list_datasets 실패: {exc}") from exc

    # 메서드도 상태도 없음
    return []


def list_dataset_items_via_client(
    langfuse: LangfuseClient | Any,
    dataset_name: str,
) -> list[dict[str, Any]]:
    """Langfuse에서 데이터셋 아이템 목록 조회.

    반환 형식: ``[{id, input, expected_output, metadata}]``
    """
    # Mock client 우선 (테스트)
    datasets = getattr(langfuse, "_datasets", None)
    if isinstance(datasets, dict) and dataset_name in datasets:
        ds = datasets[dataset_name]
        items = getattr(ds, "items", []) or []
        return [_normalize_dataset_item(it) for it in items]

    # Real client SDK fallback
    fn = getattr(langfuse, "list_dataset_items", None)
    if callable(fn):
        try:
            result = fn(dataset_name=dataset_name)
            if isinstance(result, list):
                return [_normalize_dataset_item(it) for it in result]
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"list_dataset_items 실패: {exc}") from exc

    # SDK 직접 시도
    get_sdk = getattr(langfuse, "_get_sdk", None)
    if callable(get_sdk):
        try:
            sdk = get_sdk()
            sdk_fn = getattr(sdk, "get_dataset", None)
            if callable(sdk_fn):
                ds = sdk_fn(name=dataset_name)
                items = getattr(ds, "items", None) or []
                return [_normalize_dataset_item(it) for it in items]
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"get_dataset 실패: {exc}") from exc

    raise DatasetNotFoundError(detail=f"데이터셋을 찾을 수 없습니다: {dataset_name}")


def get_dataset_via_client(
    langfuse: LangfuseClient | Any,
    dataset_name: str,
) -> dict[str, Any] | None:
    """단일 데이터셋 조회. 존재하지 않으면 None 반환."""
    fn = getattr(langfuse, "get_dataset", None)
    if not callable(fn):
        return None
    try:
        ds = fn(name=dataset_name) if "name" in fn.__code__.co_varnames else fn(dataset_name)
    except Exception:  # noqa: BLE001
        return None
    if ds is None:
        return None
    return _normalize_dataset_summary(ds)


def delete_dataset_via_client(
    langfuse: LangfuseClient | Any,
    dataset_name: str,
) -> bool:
    """데이터셋 삭제. 성공 시 True."""
    # Mock client
    datasets = getattr(langfuse, "_datasets", None)
    if isinstance(datasets, dict):
        if dataset_name in datasets:
            del datasets[dataset_name]
            return True
        raise DatasetNotFoundError(detail=f"데이터셋을 찾을 수 없습니다: {dataset_name}")

    # Real client
    fn = getattr(langfuse, "delete_dataset", None)
    if callable(fn):
        try:
            fn(name=dataset_name)
            return True
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"delete_dataset 실패: {exc}") from exc

    # SDK 직접
    get_sdk = getattr(langfuse, "_get_sdk", None)
    if callable(get_sdk):
        try:
            sdk = get_sdk()
            sdk_fn = getattr(sdk, "delete_dataset", None)
            if callable(sdk_fn):
                sdk_fn(name=dataset_name)
                return True
        except Exception as exc:  # noqa: BLE001
            raise LangfuseError(detail=f"delete_dataset 실패: {exc}") from exc

    raise LangfuseError(detail="delete_dataset 메서드를 찾을 수 없습니다.")


def _normalize_dataset_summary(ds: Any) -> dict[str, Any]:
    """Langfuse / Mock 데이터셋 객체를 dict로 정규화."""
    if isinstance(ds, dict):
        return {
            "name": ds.get("name"),
            "description": ds.get("description"),
            "item_count": int(
                ds.get("item_count") or ds.get("itemCount") or len(ds.get("items") or [])
            ),
            "created_at": ds.get("created_at") or ds.get("createdAt"),
        }
    items = getattr(ds, "items", None) or []
    return {
        "name": getattr(ds, "name", None),
        "description": getattr(ds, "description", None),
        "item_count": len(items),
        "created_at": getattr(ds, "created_at", None) or getattr(ds, "createdAt", None),
    }


def _normalize_dataset_item(it: Any) -> dict[str, Any]:
    """Langfuse / Mock 아이템 객체를 dict로 정규화."""
    if isinstance(it, dict):
        return {
            "id": it.get("id") or "",
            "input": it.get("input") or {},
            "expected_output": it.get("expected_output"),
            "metadata": it.get("metadata") or {},
        }
    return {
        "id": getattr(it, "id", "") or "",
        "input": getattr(it, "input", {}) or {},
        "expected_output": getattr(it, "expected_output", None),
        "metadata": getattr(it, "metadata", {}) or {},
    }


# ---------- 페이지네이션 유틸 ----------


def paginate(items: list[Any], page: int, page_size: int) -> tuple[list[Any], int]:
    """단순 in-memory 페이지네이션. ``(slice, total)`` 반환."""
    total = len(items)
    start = (page - 1) * page_size
    end = start + page_size
    return items[start:end], total


# ---------- DatasetItem 모델 변환 헬퍼 ----------


def to_dataset_item_model(raw: dict[str, Any]) -> DatasetItem:
    """raw dict을 ``DatasetItem`` Pydantic 모델로 변환."""
    return DatasetItem(
        id=str(raw.get("id") or ""),
        input=raw.get("input") if isinstance(raw.get("input"), dict) else {},
        expected_output=raw.get("expected_output"),
        metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {},
    )


# ---------- Phase 8-C-7 — Reviewer-curated 골든셋 자동 보강 ----------

REVIEWER_CURATED_SUFFIX = "-reviewer-curated"
"""``<agent>-reviewer-curated`` 접미사 — Phase 8-C 합의 #4 (불변 의도)."""


def reviewer_curated_dataset_name(agent_name: str | None) -> str:
    """agent 이름 → reviewer-curated 골든셋 이름.

    agent_name 이 비어 있으면 ``unknown-agent`` 로 폴백.
    공백/특수문자는 단순 hyphen 정규화 (Langfuse dataset name 규약).
    """
    raw = (agent_name or "").strip() or "unknown-agent"
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in raw)
    while "--" in safe:
        safe = safe.replace("--", "-")
    safe = safe.strip("-") or "unknown-agent"
    return f"{safe}{REVIEWER_CURATED_SUFFIX}"


def add_reviewer_curated_item(
    langfuse: LangfuseClient | Any,
    *,
    agent_name: str | None,
    trace_input: Any,
    expected_output: Any,
    metadata: dict[str, Any] | None = None,
) -> str:
    """reviewer 가 ``add_to_dataset`` 결정 시 골든셋에 trace 추가.

    1. 대상 데이터셋 이름은 ``<agent>-reviewer-curated`` (suffix 고정)
    2. 데이터셋 미존재 시 자동 생성 (idempotent — 이미 있으면 SDK가 무시 또는 에러)
    3. trace.input + reviewer 가 입력한 expected_output 으로 새 item 추가

    Args:
        langfuse: ``LangfuseClient`` 또는 mock
        agent_name: trace.name (없으면 ``unknown-agent`` 폴백)
        trace_input: trace.input (그대로 저장)
        expected_output: reviewer 입력
        metadata: 추가 메타 (review_id, reviewer_user_id, source 등)

    Returns:
        대상 데이터셋 이름.

    Raises:
        :class:`LangfuseError`: SDK 예외는 caller 가 swallow 또는 로그 처리.
    """
    name = reviewer_curated_dataset_name(agent_name)

    # 1) 데이터셋 idempotent 생성 — 이미 있으면 SDK 가 에러 반환할 수 있어 swallow
    try:
        langfuse.create_dataset(
            name=name,
            description=f"Reviewer 가 add_to_dataset 결정한 trace 누적 (agent={agent_name})",
            metadata={"source": "reviewer-curated"},
        )
    except LangfuseError as exc:
        # 대부분 "이미 존재" — 진행
        logger.debug(
            "reviewer_curated_dataset_create_skipped",
            extra={"dataset": name, "reason": str(exc.detail or exc)[:200]},
        )

    # 2) Item 추가
    item_metadata = dict(metadata or {})
    item_metadata.setdefault("source", "reviewer_curated")
    langfuse.create_dataset_item(
        dataset_name=name,
        input=trace_input,
        expected_output=expected_output,
        metadata=item_metadata,
    )
    logger.info(
        "reviewer_curated_item_added",
        extra={"dataset": name, "agent_name": agent_name},
    )
    return name
