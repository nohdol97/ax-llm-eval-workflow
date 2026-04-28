"""실험 결과 분석 서비스 (Phase 6).

ClickHouse 쿼리(또는 Langfuse public API 폴백)을 호출하여 분석 모델로 변환한다.

엔드포인트별 메서드:
- ``compare_runs``        : Run 단위 요약 + 평가 score 비교
- ``compare_items``       : 아이템(dataset_item_id) 단위 비교 + 정렬/필터/페이지네이션
- ``score_distribution``  : 스코어 히스토그램 + 통계
- ``latency_distribution``: 지연 히스토그램 + percentile
- ``cost_distribution``   : Run별 model/eval 비용 분리

폴백 모드(``LangfusePublicAPIFallbackClient``)는 SQL 쿼리를 지원하지 않으므로,
``ClickHouseError`` 가 그대로 전파된다 (라우터에서 503 매핑).
"""

from __future__ import annotations

import json
import math
from typing import Any

from app.core.logging import get_logger
from app.models.analysis import (
    CompareItemsResponse,
    CompareResponse,
    CostBreakdown,
    CostDistributionResponse,
    HistogramBin,
    ItemComparison,
    LatencyDistributionResponse,
    RunMetrics,
    ScoreDistributionResponse,
    ScoreStatistics,
)
from app.services.clickhouse_client import (
    ClickHouseClient,
    LangfusePublicAPIFallbackClient,
)
from app.services.clickhouse_queries import (
    COMPARE_RUNS_QUERY,
    COST_DISTRIBUTION_QUERY,
    ITEM_COMPARISON_QUERY,
    ITEM_SCORES_QUERY,
    LATENCY_DISTRIBUTION_QUERY,
    LATENCY_STATS_QUERY,
    SCORE_COMPARISON_QUERY,
    SCORE_DISTRIBUTION_QUERY,
    SCORE_STATISTICS_QUERY,
)

logger = get_logger(__name__)

# 타입 alias — 직접 ClickHouseClient 또는 폴백 클라이언트 모두 허용.
ClickHouseLike = ClickHouseClient | LangfusePublicAPIFallbackClient


def _to_float(value: Any) -> float | None:
    """``Decimal``/``int``/``float``/None 을 ``float | None`` 으로 정규화."""
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _to_int(value: Any, default: int = 0) -> int:
    """안전한 ``int`` 변환."""
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_json_field(raw: Any) -> Any:
    """trace.input/expected_output 등 JSON 가능 컬럼을 dict/str/None 으로 정규화."""
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
                return json.loads(text)
            except (ValueError, TypeError):
                return text
        return text
    return raw


class AnalysisService:
    """ClickHouse 분석 쿼리 서비스 — 라우터 단의 얇은 어댑터.

    ``clickhouse`` 인자에 ``ClickHouseClient`` 또는 ``LangfusePublicAPIFallbackClient``
    를 주입한다. 어느 쪽이든 ``async query(sql, parameters)`` 인터페이스를 가진다.
    """

    def __init__(self, clickhouse: ClickHouseLike) -> None:
        self._ch = clickhouse

    # ------------------------------------------------------------------
    # 1) Run 비교
    # ------------------------------------------------------------------
    async def compare_runs(
        self,
        project_id: str,
        run_names: list[str],
    ) -> CompareResponse:
        """Run별 요약 + score 평균 매트릭스 반환."""
        params = {"project_id": project_id, "run_names": list(run_names)}

        summary_rows = await self._ch.query(COMPARE_RUNS_QUERY, parameters=params)
        score_rows = await self._ch.query(SCORE_COMPARISON_QUERY, parameters=params)

        runs: list[RunMetrics] = []
        for row in summary_rows:
            runs.append(
                RunMetrics(
                    run_name=str(row.get("run_name") or ""),
                    avg_latency_ms=_to_float(row.get("avg_latency_ms")),
                    p50_latency_ms=_to_float(row.get("p50_latency_ms")),
                    p90_latency_ms=_to_float(row.get("p90_latency_ms")),
                    p99_latency_ms=_to_float(row.get("p99_latency_ms")),
                    total_cost_usd=_to_float(row.get("total_cost_usd")) or 0.0,
                    avg_total_tokens=_to_float(row.get("avg_total_tokens")),
                    avg_score=_to_float(row.get("avg_score")),
                    items_completed=_to_int(row.get("items_completed")),
                )
            )

        # 누락된 run을 0건으로 채워서 응답 안정성 유지
        present_names = {r.run_name for r in runs}
        for name in run_names:
            if name not in present_names:
                runs.append(
                    RunMetrics(
                        run_name=name,
                        total_cost_usd=0.0,
                        items_completed=0,
                    )
                )
        runs.sort(key=lambda r: r.run_name)

        scores: dict[str, dict[str, float | None]] = {}
        for row in score_rows:
            score_name = str(row.get("score_name") or "")
            run_name = str(row.get("run_name") or "")
            if not score_name or not run_name:
                continue
            bucket = scores.setdefault(score_name, {})
            bucket[run_name] = _to_float(row.get("avg_value"))

        return CompareResponse(
            project_id=project_id,
            runs=runs,
            scores=scores,
        )

    # ------------------------------------------------------------------
    # 2) 아이템 비교
    # ------------------------------------------------------------------
    async def compare_items(
        self,
        project_id: str,
        run_names: list[str],
        score_name: str | None = None,
        sort_by: str = "score_range",
        sort_order: str = "desc",
        score_min: float | None = None,
        score_max: float | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> CompareItemsResponse:
        """아이템 단위 비교 + 정렬/필터/페이지네이션."""
        params: dict[str, Any] = {
            "project_id": project_id,
            "run_names": list(run_names),
        }

        rows = await self._ch.query(ITEM_COMPARISON_QUERY, parameters=params)
        score_rows = await self._ch.query(ITEM_SCORES_QUERY, parameters=params)

        # 1) 아이템별 묶기
        item_index: dict[str, ItemComparison] = {}
        for row in rows:
            item_id = str(row.get("dataset_item_id") or "")
            if not item_id:
                continue
            run_name = str(row.get("run_name") or "")
            comp = item_index.get(item_id)
            if comp is None:
                comp = ItemComparison(
                    dataset_item_id=item_id,
                    input=_parse_json_field(row.get("input"))
                    if isinstance(_parse_json_field(row.get("input")), dict)
                    else None,
                    expected=_parse_json_field(row.get("expected")),
                    outputs={},
                    scores={},
                    score_range=None,
                    latencies={},
                    costs={},
                )
                item_index[item_id] = comp
            output_text = row.get("output")
            if output_text is not None:
                comp.outputs[run_name] = (
                    output_text
                    if isinstance(output_text, str)
                    else json.dumps(output_text, ensure_ascii=False)
                )
            latency_val = _to_float(row.get("latency_ms"))
            if latency_val is not None:
                comp.latencies[run_name] = latency_val
            cost_val = _to_float(row.get("cost_usd"))
            comp.costs[run_name] = cost_val if cost_val is not None else 0.0

        # 2) score 매트릭스 추가
        for row in score_rows:
            item_id = str(row.get("dataset_item_id") or "")
            run_name = str(row.get("run_name") or "")
            sname = str(row.get("score_name") or "")
            comp = item_index.get(item_id)
            if comp is None or not run_name or not sname:
                continue
            bucket = comp.scores.setdefault(run_name, {})
            bucket[sname] = _to_float(row.get("value"))

        # 3) score_range 계산 (score_name 지정 시 해당 score, 아니면 모든 score 평균 기준)
        for comp in item_index.values():
            target_values: list[float] = []
            if score_name is not None:
                for run, sm in comp.scores.items():
                    if run not in run_names:
                        continue
                    val = sm.get(score_name)
                    if val is not None:
                        target_values.append(val)
            else:
                # 모든 score를 평균낸 후 비교
                for run, sm in comp.scores.items():
                    if run not in run_names:
                        continue
                    vals = [v for v in sm.values() if v is not None]
                    if vals:
                        target_values.append(sum(vals) / len(vals))
            if len(target_values) >= 2:
                comp.score_range = max(target_values) - min(target_values)
            elif len(target_values) == 1:
                comp.score_range = 0.0
            else:
                comp.score_range = None

        items = list(item_index.values())

        # 4) score 필터
        if score_name is not None and (score_min is not None or score_max is not None):
            filtered: list[ItemComparison] = []
            for comp in items:
                values = [
                    v
                    for run, sm in comp.scores.items()
                    if run in run_names
                    for v in [sm.get(score_name)]
                    if v is not None
                ]
                if not values:
                    continue
                avg_val = sum(values) / len(values)
                if score_min is not None and avg_val < score_min:
                    continue
                if score_max is not None and avg_val > score_max:
                    continue
                filtered.append(comp)
            items = filtered

        # 5) 정렬
        reverse = sort_order == "desc"

        def _sort_key(comp: ItemComparison) -> tuple[int, float]:
            if sort_by == "latency":
                vals = list(comp.latencies.values())
            elif sort_by == "cost":
                vals = list(comp.costs.values())
            else:  # score_range
                return (
                    0 if comp.score_range is None else 1,
                    comp.score_range or 0.0,
                )
            if not vals:
                return (0, 0.0)
            return (1, sum(vals) / len(vals))

        # None 값은 항상 끝으로 가도록: tuple의 0번째 요소 우선
        items.sort(
            key=lambda c: (
                _sort_key(c)[0],
                _sort_key(c)[1] if reverse else -_sort_key(c)[1],
            ),
            reverse=reverse,
        )
        # tie-breaker: dataset_item_id 사전순 (안정성)
        items.sort(
            key=lambda c: c.dataset_item_id,
        )
        # 위 정렬을 무효화하지 않도록 다시 sort_by 기준으로 재적용
        items.sort(key=_sort_key, reverse=reverse)

        # 6) 페이지네이션
        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        paged = items[start:end]

        return CompareItemsResponse(
            items=paged,
            total=total,
            page=page,
            page_size=page_size,
        )

    # ------------------------------------------------------------------
    # 3) 스코어 분포
    # ------------------------------------------------------------------
    async def score_distribution(
        self,
        project_id: str,
        run_names: list[str],
        score_name: str,
        bins: int = 10,
    ) -> ScoreDistributionResponse:
        """스코어 [0.0, 1.0] 을 ``bins`` 개로 분할한 히스토그램 + Run별 통계."""
        if bins < 2:
            raise ValueError("bins must be >= 2")

        params: dict[str, Any] = {
            "project_id": project_id,
            "run_names": list(run_names),
            "score_name": score_name,
            "bins": bins,
        }

        dist_rows = await self._ch.query(SCORE_DISTRIBUTION_QUERY, parameters=params)
        stats_rows = await self._ch.query(SCORE_STATISTICS_QUERY, parameters=params)

        bin_width = 1.0 / float(bins)
        # bin_index → 누적 (모든 run 합산)
        totals: dict[int, int] = dict.fromkeys(range(bins), 0)
        for row in dist_rows:
            idx = _to_int(row.get("bin_index"))
            count = _to_int(row.get("sample_count"))
            if 0 <= idx < bins:
                totals[idx] += count

        bin_list: list[HistogramBin] = []
        for i in range(bins):
            bin_list.append(
                HistogramBin(
                    range_start=round(i * bin_width, 6),
                    range_end=round((i + 1) * bin_width, 6),
                    count=totals.get(i, 0),
                )
            )

        statistics: dict[str, ScoreStatistics] = {}
        for row in stats_rows:
            run_name = str(row.get("run_name") or "")
            if not run_name:
                continue
            statistics[run_name] = ScoreStatistics(
                avg=_to_float(row.get("avg_value")),
                stddev=_to_float(row.get("stddev_value")),
                min=_to_float(row.get("min_value")),
                max=_to_float(row.get("max_value")),
                count=_to_int(row.get("sample_count")),
            )

        return ScoreDistributionResponse(
            project_id=project_id,
            score_name=score_name,
            bins=bin_list,
            statistics=statistics,
        )

    # ------------------------------------------------------------------
    # 4) 지연 분포
    # ------------------------------------------------------------------
    async def latency_distribution(
        self,
        project_id: str,
        run_name: str,
        bins: int = 20,
    ) -> LatencyDistributionResponse:
        """지연 히스토그램 + percentile 통계."""
        if bins < 2:
            raise ValueError("bins must be >= 2")

        # 통계 먼저 (max를 알아야 bin_width 계산 가능)
        stats_params: dict[str, Any] = {
            "project_id": project_id,
            "run_name": run_name,
        }
        stats_rows = await self._ch.query(LATENCY_STATS_QUERY, parameters=stats_params)

        avg_v = stddev_v = p50_v = p90_v = p99_v = None
        max_v: float = 0.0
        count: int = 0
        if stats_rows:
            row = stats_rows[0]
            avg_v = _to_float(row.get("avg_latency_ms"))
            stddev_v = _to_float(row.get("stddev_ms"))
            p50_v = _to_float(row.get("p50_ms"))
            p90_v = _to_float(row.get("p90_ms"))
            p99_v = _to_float(row.get("p99_ms"))
            max_v = _to_float(row.get("max_ms")) or 0.0
            count = _to_int(row.get("sample_count"))

        # bin_width = max(1.0, max_latency / bins)
        bin_width = max(1.0, max_v / float(bins)) if max_v > 0 else 1.0

        dist_params: dict[str, Any] = {
            "project_id": project_id,
            "run_name": run_name,
            "bins": bins,
            "bin_width": bin_width,
        }
        dist_rows = await self._ch.query(LATENCY_DISTRIBUTION_QUERY, parameters=dist_params)

        bin_counts: dict[int, int] = dict.fromkeys(range(bins), 0)
        for row in dist_rows:
            idx = _to_int(row.get("bin_index"))
            if 0 <= idx < bins:
                bin_counts[idx] += _to_int(row.get("sample_count"))

        bin_list: list[HistogramBin] = []
        for i in range(bins):
            bin_list.append(
                HistogramBin(
                    range_start=round(i * bin_width, 3),
                    range_end=round((i + 1) * bin_width, 3),
                    count=bin_counts.get(i, 0),
                )
            )

        return LatencyDistributionResponse(
            project_id=project_id,
            run_name=run_name,
            bins=bin_list,
            p50=p50_v,
            p90=p90_v,
            p99=p99_v,
            avg=avg_v,
            stddev=stddev_v,
            count=count,
        )

    # ------------------------------------------------------------------
    # 5) 비용 분포
    # ------------------------------------------------------------------
    async def cost_distribution(
        self,
        project_id: str,
        run_names: list[str],
    ) -> CostDistributionResponse:
        """Run별 model/eval 비용 분리."""
        params: dict[str, Any] = {
            "project_id": project_id,
            "run_names": list(run_names),
        }
        rows = await self._ch.query(COST_DISTRIBUTION_QUERY, parameters=params)

        runs: dict[str, CostBreakdown] = {}
        for row in rows:
            run_name = str(row.get("run_name") or "")
            if not run_name:
                continue
            model_c = _to_float(row.get("model_cost")) or 0.0
            eval_c = _to_float(row.get("eval_cost")) or 0.0
            total_c = _to_float(row.get("total_cost"))
            if total_c is None:
                total_c = model_c + eval_c
            runs[run_name] = CostBreakdown(
                model_cost=model_c,
                eval_cost=eval_c,
                total_cost=total_c,
            )

        # 누락 run은 0으로
        for name in run_names:
            runs.setdefault(name, CostBreakdown())

        return CostDistributionResponse(project_id=project_id, runs=runs)
