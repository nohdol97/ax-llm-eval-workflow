"""Agent Trace Fetcher (Phase 8-A-1).

Langfuse trace를 조회하는 서비스. 두 가지 모드를 지원:

1. **ClickHouse 직접 모드** (기본): ``ClickHouseClient`` + ``clickhouse_queries`` 의
   trace 쿼리 (TRACE_SEARCH/COUNT/DETAIL/OBSERVATIONS/SCORES) 사용.
   - parameterized query 강제 (``app.services.clickhouse_client._validate_sql``)
   - 다중 쿼리 병렬화 (``asyncio.gather``)
2. **Langfuse public API 폴백 모드** (``USE_LANGFUSE_PUBLIC_API_FALLBACK=true``):
   ``LangfuseClient`` SDK를 통해 list_traces/get_trace 호출.
   - SDK 메서드가 없을 경우 ``LangfuseClient`` 측에서 graceful 에러를 raise
   - 폴백 모드는 SQL 미지원이라 본 fetcher가 직접 트래픽을 처리

라우터 ``app/api/v1/traces.py`` 에서 단일 인터페이스로 호출한다.
"""

from __future__ import annotations

import asyncio
import json
import random
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.trace import (
    ObservationLevel,
    ObservationType,
    SampleStrategy,
    TraceFilter,
    TraceObservation,
    TraceSummary,
    TraceTree,
)
from app.services.clickhouse_queries import (
    TRACE_COUNT_QUERY,
    TRACE_DETAIL_QUERY,
    TRACE_OBSERVATIONS_QUERY,
    TRACE_SCORES_QUERY,
    TRACE_SEARCH_QUERY,
)

if TYPE_CHECKING:
    from app.services.clickhouse_client import (
        ClickHouseClient,
        LangfusePublicAPIFallbackClient,
    )
    from app.services.langfuse_client import LangfuseClient

    ClickHouseLike = ClickHouseClient | LangfusePublicAPIFallbackClient

logger = get_logger(__name__)

_OBS_TYPES: tuple[ObservationType, ...] = ("span", "generation", "event")
_OBS_LEVELS: tuple[ObservationLevel, ...] = ("DEBUG", "DEFAULT", "WARNING", "ERROR")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TraceNotFoundError(LabsError):
    """trace_id가 존재하지 않는 경우."""

    code = "trace_not_found"
    status_code = 404
    title = "Trace not found"


class TraceFetcherError(LabsError):
    """trace 조회 일반 실패 (폴백 미지원, 변환 실패 등)."""

    code = "trace_fetcher_error"
    status_code = 502
    title = "Trace fetcher error"


# ---------- 공통 변환 helper ----------


def _to_datetime(value: Any) -> datetime:
    """ClickHouse 결과/SDK 결과의 시간 값을 ``datetime``(UTC) 으로 변환."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, int | float):
        # Unix epoch (s 또는 ms) 추정 — ms이면 1e10 초과
        ts = float(value)
        if ts > 1e11:  # ms
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=UTC)
    if isinstance(value, str):
        text = value.strip().rstrip("Z")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise TraceFetcherError(detail=f"datetime 파싱 실패: {value!r} ({exc})") from exc
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    raise TraceFetcherError(detail=f"datetime 변환 불가 타입: {type(value).__name__}")


def _to_optional_datetime(value: Any) -> datetime | None:
    """``None`` 가능 datetime 변환."""
    if value is None:
        return None
    return _to_datetime(value)


def _to_float(value: Any) -> float | None:
    """float 변환 (None/bad → None)."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any, default: int = 0) -> int:
    """int 변환 (None/bad → default)."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_io(raw: Any) -> dict[str, Any] | list[Any] | str | None:
    """trace.input/output 등 JSON 가능 컬럼을 dict/list/str/None 으로 정규화."""
    if raw is None:
        return None
    if isinstance(raw, dict | list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text[0] in "{[":
            try:
                parsed = json.loads(text)
            except (ValueError, TypeError):
                return text
            if isinstance(parsed, dict | list):
                return parsed
            return str(parsed)
        return text
    # 기타 타입 — 문자열화
    return str(raw)


def _normalize_metadata(raw: Any) -> dict[str, Any]:
    """metadata는 dict로만 허용 — 그 외는 빈 dict."""
    parsed = _normalize_io(raw)
    if isinstance(parsed, dict):
        return parsed
    return {}


def _normalize_tags(raw: Any) -> list[str]:
    """tags는 list[str]만 허용."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except (ValueError, TypeError):
            return [text]
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
        return [text]
    return []


def _normalize_obs_type(raw: Any) -> ObservationType:
    """ClickHouse SPAN/GENERATION/EVENT (대문자 가능) → 소문자 Literal."""
    if raw is None:
        return "span"
    text = str(raw).strip().lower()
    if text in _OBS_TYPES:
        return text  # type: ignore[return-value]
    return "span"


def _normalize_obs_level(raw: Any) -> ObservationLevel:
    """ClickHouse 레벨 문자열을 Literal 로 정규화."""
    if raw is None:
        return "DEFAULT"
    text = str(raw).strip().upper()
    if text in _OBS_LEVELS:
        return text  # type: ignore[return-value]
    return "DEFAULT"


def _normalize_usage(raw: Any) -> dict[str, int] | None:
    """usage(또는 usage_details) → dict[str, int] | None."""
    parsed = _normalize_io(raw)
    if not isinstance(parsed, dict):
        return None
    out: dict[str, int] = {}
    for key, value in parsed.items():
        if value is None:
            continue
        try:
            out[str(key)] = int(value)
        except (TypeError, ValueError):
            continue
    return out or None


def _build_observation(row: dict[str, Any]) -> TraceObservation:
    """ClickHouse observation 행 → ``TraceObservation`` 변환."""
    return TraceObservation(
        id=str(row.get("id") or ""),
        type=_normalize_obs_type(row.get("type")),
        name=str(row.get("name") or ""),
        parent_observation_id=row.get("parent_observation_id") or None,
        input=_normalize_io(row.get("input")),
        output=_normalize_io(row.get("output")),
        level=_normalize_obs_level(row.get("level")),
        status_message=row.get("status_message") or None,
        start_time=_to_datetime(row.get("start_time")),
        end_time=_to_optional_datetime(row.get("end_time")),
        latency_ms=_to_float(row.get("latency_ms")),
        model=row.get("model") or None,
        usage=_normalize_usage(row.get("usage")),
        cost_usd=_to_float(row.get("cost_usd")),
        metadata=_normalize_metadata(row.get("metadata")),
    )


def _build_score_dict(row: dict[str, Any]) -> dict[str, Any]:
    """score 행 → dict 형태 (TraceTree.scores 항목)."""
    created_at = _to_optional_datetime(row.get("created_at"))
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("name") or ""),
        "value": _to_float(row.get("value")),
        "comment": row.get("comment") or None,
        "created_at": created_at.isoformat() if created_at else None,
    }


def _build_summary(row: dict[str, Any]) -> TraceSummary:
    """ClickHouse 검색 행 → ``TraceSummary``."""
    return TraceSummary(
        id=str(row.get("id") or ""),
        name=str(row.get("name") or ""),
        user_id=row.get("user_id") or None,
        session_id=row.get("session_id") or None,
        tags=_normalize_tags(row.get("tags")),
        total_cost_usd=_to_float(row.get("total_cost_usd")) or 0.0,
        total_latency_ms=_to_float(row.get("total_latency_ms")),
        timestamp=_to_datetime(row.get("timestamp")),
        observation_count=_to_int(row.get("observation_count")),
    )


def _compute_trace_aggregates(
    observations: Iterable[TraceObservation],
) -> tuple[float, float | None]:
    """observations로부터 ``total_cost_usd`` / ``total_latency_ms`` 추정.

    ClickHouse가 직접 집계 컬럼을 주지 않을 때(폴백 등) 사용.
    """
    cost_total = 0.0
    starts: list[datetime] = []
    ends: list[datetime] = []
    for obs in observations:
        if obs.cost_usd is not None:
            cost_total += obs.cost_usd
        starts.append(obs.start_time)
        if obs.end_time is not None:
            ends.append(obs.end_time)
    if starts and ends:
        latency_ms = (max(ends) - min(starts)).total_seconds() * 1000.0
    else:
        latency_ms = None
    return cost_total, latency_ms


# ---------- 메인 클래스 ----------


class TraceFetcher:
    """Langfuse trace 조회 서비스.

    ``clickhouse`` 가 ``None`` 이거나 ``use_fallback=True`` 면 Langfuse public API
    폴백 경로를 사용한다. 그 외는 직접 ClickHouse 쿼리.
    """

    # observation 기본 LIMIT (검색 시 사용) — ClickHouseClient 자동 LIMIT 보호.
    DEFAULT_SEARCH_LIMIT: int = 1000
    EVALUATOR_TIMEOUT_SEC: float = 30.0

    def __init__(
        self,
        clickhouse: ClickHouseLike | None,
        langfuse: LangfuseClient,
        use_fallback: bool = False,
    ) -> None:
        self._ch = clickhouse
        self._langfuse = langfuse
        self._use_fallback = bool(use_fallback or clickhouse is None)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def search(self, filter: TraceFilter) -> tuple[list[TraceSummary], int]:  # noqa: A002
        """필터에 매칭되는 trace 메타 목록 + 전체 매칭 개수.

        ``sample_size`` 가 지정되면 결과를 ``sample_strategy`` 기준으로 추출한다.
        ``total`` 은 샘플링 적용 후 (UI 페이지네이션 일관성 우선) 의 크기다.
        """
        if self._use_fallback:
            return await self._search_via_langfuse(filter)
        summaries, total = await self._search_via_clickhouse(filter)
        if filter.sample_size is not None and filter.sample_size < len(summaries):
            sampled = self._sample(summaries, filter.sample_size, filter.sample_strategy)
            return sampled, len(sampled)
        return summaries, total

    async def get(self, trace_id: str, project_id: str) -> TraceTree:
        """단건 trace + 모든 observations + scores."""
        if self._use_fallback:
            return await self._get_via_langfuse(trace_id, project_id)
        return await self._get_via_clickhouse(trace_id, project_id)

    async def get_many(
        self,
        trace_ids: list[str],
        project_id: str,
    ) -> list[TraceTree]:
        """여러 trace 병렬 조회. 누락된 trace는 결과에서 제외하지 않고 raise."""
        if not trace_ids:
            return []
        return list(await asyncio.gather(*[self.get(tid, project_id) for tid in trace_ids]))

    # ------------------------------------------------------------------
    # ClickHouse 직접 모드
    # ------------------------------------------------------------------
    async def _search_via_clickhouse(
        self,
        filter: TraceFilter,  # noqa: A002
    ) -> tuple[list[TraceSummary], int]:
        if self._ch is None:  # pragma: no cover — _use_fallback 분기에서 차단
            raise TraceFetcherError(detail="ClickHouseClient 가 주입되지 않았습니다.")

        params = self._build_filter_params(filter)
        # 페이지네이션은 라우터가 page/page_size 로 잘라낸다. fetcher 는 sample_size
        # 또는 DEFAULT_SEARCH_LIMIT 만큼 한꺼번에 받아 메모리에 적재한다.
        list_params = dict(params)
        list_params["limit"] = filter.sample_size or self.DEFAULT_SEARCH_LIMIT
        list_params["offset"] = 0

        count_rows, list_rows = await asyncio.gather(
            self._ch.query(TRACE_COUNT_QUERY, parameters=params),
            self._ch.query(TRACE_SEARCH_QUERY, parameters=list_params),
        )

        total = _to_int(count_rows[0].get("total")) if count_rows else 0
        summaries = [_build_summary(row) for row in list_rows]
        return summaries, total

    async def _get_via_clickhouse(self, trace_id: str, project_id: str) -> TraceTree:
        if self._ch is None:  # pragma: no cover
            raise TraceFetcherError(detail="ClickHouseClient 가 주입되지 않았습니다.")

        trace_rows, obs_rows, score_rows = await asyncio.gather(
            self._ch.query(
                TRACE_DETAIL_QUERY,
                parameters={"trace_id": trace_id, "project_id": project_id},
            ),
            self._ch.query(
                TRACE_OBSERVATIONS_QUERY,
                parameters={"trace_id": trace_id},
            ),
            self._ch.query(
                TRACE_SCORES_QUERY,
                parameters={"trace_id": trace_id},
            ),
        )

        if not trace_rows:
            raise TraceNotFoundError(detail=f"trace {trace_id!r} not found")

        trace_row = trace_rows[0]
        observations = sorted(
            (_build_observation(r) for r in obs_rows),
            key=lambda o: o.start_time,
        )
        scores = [_build_score_dict(r) for r in score_rows]
        cost_total, latency_total = _compute_trace_aggregates(observations)

        return TraceTree(
            id=str(trace_row.get("id") or ""),
            project_id=str(trace_row.get("project_id") or project_id),
            name=str(trace_row.get("name") or ""),
            input=_normalize_io(trace_row.get("input")),
            output=_normalize_io(trace_row.get("output")),
            user_id=trace_row.get("user_id") or None,
            session_id=trace_row.get("session_id") or None,
            tags=_normalize_tags(trace_row.get("tags")),
            metadata=_normalize_metadata(trace_row.get("metadata")),
            observations=observations,
            scores=scores,
            total_cost_usd=cost_total,
            total_latency_ms=latency_total,
            timestamp=_to_datetime(trace_row.get("timestamp")),
        )

    @staticmethod
    def _build_filter_params(filter: TraceFilter) -> dict[str, Any]:  # noqa: A002
        """``TraceFilter`` → ClickHouse parameter dict."""
        tags = list(filter.tags or [])
        user_ids = list(filter.user_ids or [])
        session_ids = list(filter.session_ids or [])
        from_ts = filter.from_timestamp
        to_ts = filter.to_timestamp
        return {
            "project_id": filter.project_id,
            "name": filter.name or "",
            "tags": tags,
            "tags_count": len(tags),
            "user_ids": user_ids,
            "user_ids_count": len(user_ids),
            "session_ids": session_ids,
            "session_ids_count": len(session_ids),
            "has_from": 1 if from_ts is not None else 0,
            "from_timestamp": from_ts if from_ts is not None else _EPOCH,
            "has_to": 1 if to_ts is not None else 0,
            "to_timestamp": to_ts if to_ts is not None else _EPOCH,
        }

    # ------------------------------------------------------------------
    # Langfuse public API 폴백 모드
    # ------------------------------------------------------------------
    async def _search_via_langfuse(
        self,
        filter: TraceFilter,  # noqa: A002
    ) -> tuple[list[TraceSummary], int]:
        """Langfuse SDK ``list_traces`` 위임 — SDK 미지원 시 명시 에러."""
        list_method = getattr(self._langfuse, "list_traces", None)
        if not callable(list_method):
            raise TraceFetcherError(
                detail=(
                    "Langfuse public API 폴백 모드는 list_traces 메서드가 필요합니다. "
                    "LangfuseClient 에 메서드를 추가하거나 ClickHouse 직접 모드를 사용하세요."
                )
            )
        try:
            raw = await asyncio.to_thread(
                list_method,
                project_id=filter.project_id,
                name=filter.name,
                tags=filter.tags,
                user_ids=filter.user_ids,
                session_ids=filter.session_ids,
                from_timestamp=filter.from_timestamp,
                to_timestamp=filter.to_timestamp,
                limit=filter.sample_size or self.DEFAULT_SEARCH_LIMIT,
            )
        except Exception as exc:  # noqa: BLE001
            raise TraceFetcherError(detail=f"Langfuse list_traces 실패: {exc}") from exc

        items = list(raw or [])
        summaries: list[TraceSummary] = []
        for item in items:
            row = item if isinstance(item, dict) else self._sdk_to_dict(item)
            summaries.append(
                TraceSummary(
                    id=str(row.get("id") or ""),
                    name=str(row.get("name") or ""),
                    user_id=row.get("user_id") or row.get("userId") or None,
                    session_id=row.get("session_id") or row.get("sessionId") or None,
                    tags=_normalize_tags(row.get("tags")),
                    total_cost_usd=_to_float(row.get("total_cost_usd") or row.get("totalCost"))
                    or 0.0,
                    total_latency_ms=_to_float(row.get("total_latency_ms") or row.get("latency")),
                    timestamp=_to_datetime(
                        row.get("timestamp") or row.get("created_at") or row.get("createdAt")
                    ),
                    observation_count=_to_int(
                        row.get("observation_count") or row.get("observationCount")
                    ),
                )
            )
        if filter.sample_size is not None and filter.sample_size < len(summaries):
            summaries = self._sample(summaries, filter.sample_size, filter.sample_strategy)
        return summaries, len(summaries)

    async def _get_via_langfuse(self, trace_id: str, project_id: str) -> TraceTree:
        """Langfuse SDK ``get_trace`` 위임."""
        get_method = getattr(self._langfuse, "get_trace", None)
        if not callable(get_method):
            raise TraceFetcherError(
                detail=(
                    "Langfuse public API 폴백 모드는 get_trace 메서드가 필요합니다. "
                    "LangfuseClient 에 메서드를 추가하거나 ClickHouse 직접 모드를 사용하세요."
                )
            )
        try:
            raw = await asyncio.to_thread(get_method, trace_id)
        except Exception as exc:  # noqa: BLE001
            raise TraceFetcherError(detail=f"Langfuse get_trace 실패: {exc}") from exc

        if raw is None:
            raise TraceNotFoundError(detail=f"trace {trace_id!r} not found")

        row = raw if isinstance(raw, dict) else self._sdk_to_dict(raw)

        obs_rows = row.get("observations") or []
        observations = sorted(
            (
                _build_observation(o if isinstance(o, dict) else self._sdk_to_dict(o))
                for o in obs_rows
            ),
            key=lambda o: o.start_time,
        )
        score_rows = row.get("scores") or []
        scores = [
            _build_score_dict(s if isinstance(s, dict) else self._sdk_to_dict(s))
            for s in score_rows
        ]
        cost_total, latency_total = _compute_trace_aggregates(observations)

        return TraceTree(
            id=str(row.get("id") or trace_id),
            project_id=str(row.get("project_id") or row.get("projectId") or project_id),
            name=str(row.get("name") or ""),
            input=_normalize_io(row.get("input")),
            output=_normalize_io(row.get("output")),
            user_id=row.get("user_id") or row.get("userId") or None,
            session_id=row.get("session_id") or row.get("sessionId") or None,
            tags=_normalize_tags(row.get("tags")),
            metadata=_normalize_metadata(row.get("metadata")),
            observations=observations,
            scores=scores,
            total_cost_usd=_to_float(row.get("total_cost_usd") or row.get("totalCost"))
            or cost_total,
            total_latency_ms=_to_float(row.get("total_latency_ms") or row.get("latency"))
            or latency_total,
            timestamp=_to_datetime(
                row.get("timestamp") or row.get("created_at") or row.get("createdAt")
            ),
        )

    @staticmethod
    def _sdk_to_dict(obj: Any) -> dict[str, Any]:
        """SDK 객체 → dict (best-effort)."""
        if isinstance(obj, dict):
            return obj
        for attr in ("model_dump", "dict", "_asdict"):
            method = getattr(obj, attr, None)
            if callable(method):
                try:
                    result = method()
                except TypeError:
                    continue
                if isinstance(result, dict):
                    return result
        if hasattr(obj, "__dict__"):
            return {k: v for k, v in vars(obj).items() if not k.startswith("_")}
        return {}

    # ------------------------------------------------------------------
    # 샘플링
    # ------------------------------------------------------------------
    @staticmethod
    def _sample(
        items: list[TraceSummary],
        k: int,
        strategy: SampleStrategy,
    ) -> list[TraceSummary]:
        """``sample_size`` 적용. k가 items 길이 이상이면 그대로 반환."""
        if k >= len(items) or k <= 0:
            return list(items)
        if strategy == "random":
            return list(random.sample(items, k))
        if strategy == "first":
            return list(items[:k])
        if strategy == "stratified":
            return TraceFetcher._stratified_sample(items, k)
        return list(items[:k])  # pragma: no cover — Literal 기준 도달 불가

    @staticmethod
    def _stratified_sample(items: list[TraceSummary], k: int) -> list[TraceSummary]:
        """tag 첫 번째 값을 stratum 으로 사용한 균등 샘플링.

        - tag가 없는 trace는 ``__no_tag__`` 그룹
        - 각 stratum에서 ``ceil(k * group_size / total_size)`` 만큼 추출 후 절단
        """
        if not items:
            return []
        groups: dict[str, list[TraceSummary]] = {}
        for it in items:
            key = it.tags[0] if it.tags else "__no_tag__"
            groups.setdefault(key, []).append(it)

        total = len(items)
        result: list[TraceSummary] = []
        for group_items in groups.values():
            quota = max(1, round(k * len(group_items) / total))
            quota = min(quota, len(group_items))
            result.extend(random.sample(group_items, quota))

        # quota 합산이 k보다 클 수 있어 절단, 부족하면 보충
        if len(result) > k:
            result = random.sample(result, k)
        elif len(result) < k:
            remaining = [it for it in items if it not in result]
            extra_needed = k - len(result)
            if extra_needed > 0 and remaining:
                result.extend(random.sample(remaining, min(extra_needed, len(remaining))))
        return result


__all__ = [
    "TraceFetcher",
    "TraceFetcherError",
    "TraceNotFoundError",
]
