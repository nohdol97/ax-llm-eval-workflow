"""실험 조회 서비스 — 목록·상세 (BUILD_ORDER §4-5/§4-7).

본 모듈은 두 개의 공개 메서드를 제공한다:

- :meth:`ExperimentQuery.list_experiments` — ``GET /api/v1/experiments``
- :meth:`ExperimentQuery.get_experiment`  — ``GET /api/v1/experiments/{id}``

Redis 스키마 (IMPLEMENTATION.md §1.3~§1.5)
- ``ax:experiment:{id}``                  Hash (메타)
- ``ax:experiment:{id}:runs``             Set (Run 이름 목록)
- ``ax:run:{id}:{run_name}``              Hash (Run 단위 집계)
- ``ax:project:{project_id}:experiments`` ZSet (`score=created_at_unix`, `member=experiment_id`)

상세 응답의 ``config_snapshot``은 ``ax:experiment:{id}.config`` JSON을 그대로 노출하며,
1MB 초과로 별도 키 ``...:config_blob``(gzip 압축)에 저장된 경우 본 메서드는 자동으로 폴백 조회한다.
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime
from typing import Any, cast

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.experiment import (
    ExperimentDetail,
    ExperimentListResponse,
    ExperimentStatus,
    ExperimentSummary,
    RunSummary,
)
from app.services.langfuse_client import LangfuseClient
from app.services.redis_client import RedisClient

logger = get_logger(__name__)

# Lazy cleanup 시 추가 조회 최대 횟수 (IMPLEMENTATION §1.8)
LAZY_CLEANUP_MAX_REFILL = 3


class ExperimentNotFoundError(LabsError):
    """실험 미존재 — 404."""

    code = "EXPERIMENT_NOT_FOUND"
    status_code = 404
    title = "Experiment not found"


class ExperimentForbiddenError(LabsError):
    """본인 외 사용자 + 비-admin 접근 — 403."""

    code = "FORBIDDEN"
    status_code = 403
    title = "Forbidden"


# ---------- 헬퍼 ----------
def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return value


def _raw_redis(redis: Any) -> Any:
    """``RedisClient.underlying`` 또는 Mock의 ``_client``를 추출.

    Mock(``MockRedisClient``)은 ``underlying`` 속성이 없으므로 ``_client``로 폴백.
    """
    underlying = getattr(redis, "underlying", None)
    if underlying is not None:
        return underlying
    return getattr(redis, "_client", redis)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_dt(value: Any) -> datetime | None:
    """ISO 8601 (``Z`` 또는 offset) 파싱. 실패 시 ``None``."""
    if value is None:
        return None
    raw = _decode(value)
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # Python 3.11+ fromisoformat은 'Z' 접미를 허용하지 않음 — 대체
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _to_status(value: Any, default: ExperimentStatus = "pending") -> ExperimentStatus:
    """문자열 → ``ExperimentStatus`` 좁히기 (미지값은 default로 강등)."""
    raw = str(_decode(value) or "").strip()
    valid: tuple[str, ...] = (
        "pending",
        "queued",
        "running",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "degraded",
    )
    if raw in valid:
        return cast(ExperimentStatus, raw)
    return default


def _calc_progress(meta: dict[str, Any]) -> dict[str, Any]:
    """``meta`` 기반 progress dict 계산.

    - processed = completed_items + failed_items
    - percentage = processed / total_items * 100 (total=0이면 0)
    - eta_sec = best-effort. 시작 후 진행률이 있으면 선형 외삽,
      그 외엔 ``None``.
    """
    total = _to_int(meta.get("total_items"), 0)
    completed = _to_int(meta.get("completed_items"), 0)
    failed = _to_int(meta.get("failed_items"), 0)
    processed = completed + failed
    percentage = (processed / total * 100.0) if total > 0 else 0.0

    eta_sec: float | None = None
    started = _parse_dt(meta.get("created_at"))
    if started and processed > 0 and total > processed:
        elapsed = (datetime.now(UTC) - started).total_seconds()
        if elapsed > 0:
            rate = processed / elapsed  # items/sec
            if rate > 0:
                eta_sec = (total - processed) / rate

    return {
        "processed": processed,
        "completed": completed,
        "failed": failed,
        "total": total,
        "percentage": round(percentage, 2),
        "eta_sec": round(eta_sec, 2) if eta_sec is not None else None,
    }


# ---------- 메인 클래스 ----------
class ExperimentQuery:
    """실험 조회 서비스 — Redis HGETALL/SMEMBERS + JSON 파싱."""

    def __init__(self, redis: RedisClient, langfuse: LangfuseClient) -> None:
        self._redis = redis
        self._langfuse = langfuse

    # ---------- 권한 ----------
    @staticmethod
    def _check_access(
        meta: dict[str, Any],
        user_id: str,
        user_role: str,
        project_id_filter: str | None = None,
    ) -> None:
        """소유자 일치 또는 admin 여부 검증.

        - admin: 모두 접근 가능
        - 그 외: ``started_by`` (=owner_user_id) == user_id 인 경우만 가능
        - ``project_id_filter``가 주어지면 그것과 meta의 project_id가 일치해야 함

        본 프로젝트 정책: 타 사용자 실험은 ``404``로 통일 응답하여 정보 노출 방지.
        """
        if project_id_filter:
            mp = str(meta.get("project_id", ""))
            if mp and mp != project_id_filter:
                raise ExperimentNotFoundError(detail="다른 프로젝트의 실험에 접근할 수 없습니다.")

        if user_role == "admin":
            return
        owner = str(meta.get("started_by", "") or meta.get("owner_user_id", ""))
        if owner and owner != user_id:
            # 타인 실험 → 정보 노출 회피로 404 통일
            raise ExperimentNotFoundError(detail="실험을 찾을 수 없습니다.")

    # ---------- Redis 헬퍼 ----------
    async def _read_meta(self, experiment_id: str) -> dict[str, Any]:
        full_key = f"ax:experiment:{experiment_id}"
        raw = await _raw_redis(self._redis).hgetall(full_key)
        if not raw:
            return {}
        return {_decode(k): _decode(v) for k, v in raw.items()}

    async def _read_run_names(self, experiment_id: str) -> list[str]:
        runs_key = f"ax:experiment:{experiment_id}:runs"
        raw = await _raw_redis(self._redis).smembers(runs_key)
        return sorted(_decode(name) for name in raw)

    async def _read_run(self, experiment_id: str, run_name: str) -> dict[str, Any]:
        run_key = f"ax:run:{experiment_id}:{run_name}"
        raw = await _raw_redis(self._redis).hgetall(run_key)
        if not raw:
            return {}
        return {_decode(k): _decode(v) for k, v in raw.items()}

    async def _read_config_snapshot(self, experiment_id: str) -> dict[str, Any]:
        """``config`` 필드를 JSON 파싱. 미존재 시 ``config_blob``(gzip) 폴백."""
        raw_client = _raw_redis(self._redis)
        meta_key = f"ax:experiment:{experiment_id}"
        config_raw = await raw_client.hget(meta_key, "config")
        config_str = _decode(config_raw) if config_raw is not None else None
        if config_str:
            try:
                parsed = json.loads(config_str)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                logger.warning(
                    "config_snapshot_parse_failed",
                    experiment_id=experiment_id,
                )
        # 폴백: config_blob (gzip)
        blob_key = f"ax:experiment:{experiment_id}:config_blob"
        blob = await raw_client.get(blob_key)
        if blob:
            try:
                if isinstance(blob, str):
                    blob_bytes = blob.encode("latin-1")
                else:
                    blob_bytes = blob
                decompressed = gzip.decompress(blob_bytes).decode("utf-8")
                parsed_blob = json.loads(decompressed)
                if isinstance(parsed_blob, dict):
                    return parsed_blob
            except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning(
                    "config_snapshot_blob_parse_failed",
                    experiment_id=experiment_id,
                    error=str(exc),
                )
        return {}

    # ---------- Run 요약 ----------
    def _build_run_summary(self, run_name: str, run_meta: dict[str, Any]) -> RunSummary:
        completed = _to_int(run_meta.get("completed_items"), 0)
        latency_total = _to_float(run_meta.get("total_latency_ms"), 0.0)
        score_sum = _to_float(run_meta.get("total_score_sum"), 0.0)
        scored = _to_int(run_meta.get("scored_count"), 0)

        avg_score: float | None = None
        if scored > 0:
            avg_score = round(score_sum / scored, 6)

        avg_latency: float | None = None
        if completed > 0:
            avg_latency = round(latency_total / completed, 3)

        return RunSummary(
            run_name=run_name,
            model=str(run_meta.get("model", "") or ""),
            prompt_version=_to_int(run_meta.get("prompt_version"), 1),
            status=_to_status(run_meta.get("status"), "pending"),
            items_completed=completed,
            items_total=_to_int(run_meta.get("total_items"), 0),
            avg_score=avg_score,
            total_cost=_to_float(run_meta.get("total_cost_usd"), 0.0),
            avg_latency_ms=avg_latency,
        )

    # ---------- 공개 API ----------
    async def get_experiment(
        self,
        experiment_id: str,
        user_id: str,
        user_role: str = "user",
        project_id: str | None = None,
    ) -> ExperimentDetail:
        """실험 상세 조회.

        본인(또는 admin)만 조회 가능. 타인 실험은 404로 통일 응답.

        Args:
            experiment_id: 실험 ID
            user_id: 호출자 user_id
            user_role: 호출자 역할 (admin은 전체 접근 가능)
            project_id: 명시 시 해당 프로젝트 소속만 허용
        """
        meta = await self._read_meta(experiment_id)
        if not meta:
            raise ExperimentNotFoundError(detail=f"experiment_id={experiment_id!r} not found")

        self._check_access(meta, user_id, user_role, project_id)

        run_names = await self._read_run_names(experiment_id)
        runs: list[RunSummary] = []
        for run_name in run_names:
            run_meta = await self._read_run(experiment_id, run_name)
            if run_meta:
                runs.append(self._build_run_summary(run_name, run_meta))

        config_snapshot = await self._read_config_snapshot(experiment_id)

        created_at = _parse_dt(meta.get("created_at"))
        started_at = _parse_dt(meta.get("started_at"))
        completed_at = _parse_dt(meta.get("completed_at"))
        # created_at이 없으면 현재 시각 (방어적 — 정상 데이터에선 발생 X)
        if created_at is None:
            created_at = datetime.now(UTC)

        return ExperimentDetail(
            experiment_id=experiment_id,
            name=str(meta.get("name", "") or ""),
            description=(str(meta.get("description")) if meta.get("description") else None),
            status=_to_status(meta.get("status"), "pending"),
            project_id=str(meta.get("project_id", "") or ""),
            owner=str(meta.get("started_by", "") or meta.get("owner_user_id", "") or ""),
            created_at=created_at,
            started_at=started_at,
            completed_at=completed_at,
            progress=_calc_progress(meta),
            runs=runs,
            config_snapshot=config_snapshot,
            evaluator_summary={},  # Phase 5에서 활성화
        )

    async def list_experiments(
        self,
        project_id: str,
        user_id: str,
        page: int = 1,
        page_size: int = 20,
        status: ExperimentStatus | None = None,
        search: str | None = None,
        sort_by: str = "created_at",
        sort_order: str = "desc",
        user_role: str = "user",
    ) -> ExperimentListResponse:
        """프로젝트 단위 실험 목록 — 페이지네이션 + 필터.

        ``ax:project:{project_id}:experiments`` ZSet에서 ID를 가져와 각 실험의 메타를
        HGETALL로 합친다. Lazy cleanup(IMPLEMENTATION §1.8)을 적용하여 만료된 ID를
        자동 정리한다.

        Args:
            project_id: 대상 프로젝트
            user_id: 호출자 user_id
            page: 1-based 페이지
            page_size: 페이지 크기 (1~100)
            status: 상태 필터 (없으면 전체)
            search: 이름 부분 일치(case-insensitive)
            sort_by: ``created_at`` (현재 단일 키)
            sort_order: ``asc`` 또는 ``desc``
            user_role: ``admin``이면 본인 외 실험도 노출
        """
        page = max(1, page)
        page_size = max(1, min(100, page_size))

        index_key = f"ax:project:{project_id}:experiments"

        # 정렬 + 페이지네이션 — ZSet 전체 조회 후 인메모리 필터 (search/status는 ZSet 외부 정보)
        # ZSet의 ``score=created_at_unix``라 가정.
        raw_client = _raw_redis(self._redis)
        if sort_order == "asc":
            raw_ids = await raw_client.zrange(index_key, 0, -1)
        else:
            raw_ids = await raw_client.zrevrange(index_key, 0, -1)

        all_ids: list[str] = [_decode(x) for x in raw_ids]

        # Lazy cleanup + 메타 dict 모음
        summaries: list[ExperimentSummary] = []
        cleanup_remove: list[str] = []
        for exp_id in all_ids:
            meta = await self._read_meta(exp_id)
            if not meta:
                cleanup_remove.append(exp_id)
                continue

            # 권한: admin이 아니면 본인 실험만 노출
            if user_role != "admin":
                owner = str(meta.get("started_by", "") or meta.get("owner_user_id", ""))
                if owner and owner != user_id:
                    continue

            # 프로젝트 격리 검증 (방어적 — ZSet 키와 meta가 다를 수 있는 corner case)
            mp = str(meta.get("project_id", ""))
            if mp and mp != project_id:
                continue

            current_status = _to_status(meta.get("status"), "pending")
            if status and current_status != status:
                continue
            name_value = str(meta.get("name", "") or "")
            if search and search.lower() not in name_value.lower():
                continue

            created_at = _parse_dt(meta.get("created_at")) or datetime.now(UTC)
            summaries.append(
                ExperimentSummary(
                    experiment_id=exp_id,
                    name=name_value,
                    status=current_status,
                    runs_total=_to_int(meta.get("total_runs"), 0),
                    runs_completed=_to_int(meta.get("completed_items"), 0),
                    total_cost=_to_float(meta.get("total_cost_usd"), 0.0),
                    avg_score=None,
                    created_at=created_at,
                )
            )

        # Lazy cleanup — 만료된 멤버 ZREM (best-effort)
        if cleanup_remove:
            try:
                await raw_client.zrem(index_key, *cleanup_remove)
            except Exception as exc:  # noqa: BLE001  # pragma: no cover
                logger.warning(
                    "lazy_cleanup_failed",
                    project_id=project_id,
                    error=str(exc),
                )

        total = len(summaries)
        start = (page - 1) * page_size
        end = start + page_size
        page_items = summaries[start:end]

        return ExperimentListResponse(
            items=page_items,
            total=total,
            page=page,
            page_size=page_size,
        )
