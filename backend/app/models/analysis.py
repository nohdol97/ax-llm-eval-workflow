"""분석(Analysis) 도메인 Pydantic 모델.

Phase 6 — 실험 결과 분석 API의 요청/응답 스키마.

- ``CompareRequest`` / ``CompareResponse``: Run 단위 요약 비교
- ``CompareItemsRequest`` / ``CompareItemsResponse``: 아이템(dataset_item_id) 단위 비교
- ``ScoreDistributionResponse``: 스코어 분포 히스토그램
- ``LatencyDistributionResponse``: 지연 분포 히스토그램 + percentile
- ``CostDistributionResponse``: Run별 model_cost / eval_cost 분리

API_DESIGN.md §7 분석 API 참조.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------- 공통 ----------
SortBy = Literal["score_range", "latency", "cost"]
SortOrder = Literal["asc", "desc"]


# ---------- Run 비교 ----------
class CompareRequest(BaseModel):
    """``POST /analysis/compare`` 요청.

    ``run_names``는 2~5개 사이로 제한 (UI 가독성/쿼리 비용)."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="대상 프로젝트 ID")
    run_names: list[str] = Field(..., description="비교할 Run 이름 목록 (2~5개)")

    @field_validator("run_names")
    @classmethod
    def _validate_runs(cls, v: list[str]) -> list[str]:
        """run_names 개수 + 비어있지 않음 검증."""
        if len(v) < 2:
            raise ValueError("at least 2 run_names required")
        if len(v) > 5:
            raise ValueError("max 5 run_names")
        for name in v:
            if not name or not name.strip():
                raise ValueError("run_names entries must be non-empty")
        return v


class RunMetrics(BaseModel):
    """Run 단위 요약 메트릭."""

    model_config = ConfigDict(extra="forbid")

    run_name: str = Field(..., description="Run 이름 (= traces.name)")
    avg_latency_ms: float | None = Field(None, description="평균 지연 (ms)")
    p50_latency_ms: float | None = Field(None, description="P50 지연 (ms)")
    p90_latency_ms: float | None = Field(None, description="P90 지연 (ms)")
    p99_latency_ms: float | None = Field(None, description="P99 지연 (ms)")
    total_cost_usd: float = Field(0.0, ge=0.0, description="누적 비용 USD")
    avg_total_tokens: float | None = Field(None, description="평균 total tokens")
    avg_score: float | None = Field(None, description="모든 score 평균")
    items_completed: int = Field(0, ge=0, description="완료 아이템 수")


class CompareResponse(BaseModel):
    """Run 비교 응답."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., description="대상 프로젝트 ID")
    runs: list[RunMetrics] = Field(default_factory=list, description="Run별 요약")
    scores: dict[str, dict[str, float | None]] = Field(
        default_factory=dict,
        description="{score_name: {run_name: avg_value}}",
    )


# ---------- 아이템 비교 ----------
class CompareItemsRequest(BaseModel):
    """``POST /analysis/compare/items`` 요청."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1)
    run_names: list[str] = Field(..., description="비교할 Run 이름 (2~5개)")
    score_name: str | None = Field(
        None, description="필터/정렬 기준 score 이름 (없으면 모든 score)"
    )
    sort_by: SortBy = Field("score_range", description="정렬 기준")
    sort_order: SortOrder = Field("desc", description="정렬 방향")
    score_min: float | None = Field(None, description="score 하한 필터")
    score_max: float | None = Field(None, description="score 상한 필터")
    page: int = Field(1, ge=1, description="페이지 번호")
    page_size: int = Field(50, ge=1, le=100, description="페이지 크기 (최대 100)")

    @field_validator("run_names")
    @classmethod
    def _validate_runs(cls, v: list[str]) -> list[str]:
        if len(v) < 2:
            raise ValueError("at least 2 run_names required")
        if len(v) > 5:
            raise ValueError("max 5 run_names")
        return v


class ItemComparison(BaseModel):
    """동일 dataset_item_id에 대한 Run별 결과 묶음."""

    model_config = ConfigDict(extra="forbid")

    dataset_item_id: str = Field(..., description="Langfuse dataset item ID")
    input: dict[str, Any] | None = Field(None, description="아이템 입력")
    expected: str | dict[str, Any] | None = Field(None, description="기대 출력")
    outputs: dict[str, str] = Field(default_factory=dict, description="{run_name: output_text}")
    scores: dict[str, dict[str, float | None]] = Field(
        default_factory=dict,
        description="{run_name: {score_name: value}}",
    )
    score_range: float | None = Field(
        None, description="|max(score) - min(score)| (score_name 기준)"
    )
    latencies: dict[str, float] = Field(default_factory=dict, description="{run_name: latency_ms}")
    costs: dict[str, float] = Field(default_factory=dict, description="{run_name: total_cost_usd}")


class CompareItemsResponse(BaseModel):
    """아이템별 비교 응답."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemComparison] = Field(default_factory=list)
    total: int = Field(..., ge=0, description="전체 아이템 수 (필터 후)")
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


# ---------- 분포 ----------
class HistogramBin(BaseModel):
    """히스토그램 bin 1개."""

    model_config = ConfigDict(extra="forbid")

    range_start: float = Field(..., description="bin 시작 (포함)")
    range_end: float = Field(..., description="bin 끝 (제외)")
    count: int = Field(..., ge=0, description="bin 내 샘플 수")


class ScoreStatistics(BaseModel):
    """Run별 score 통계."""

    model_config = ConfigDict(extra="forbid")

    avg: float | None = None
    stddev: float | None = None
    min: float | None = None
    max: float | None = None
    count: int = Field(0, ge=0)


class ScoreDistributionResponse(BaseModel):
    """``GET /analysis/scores/distribution`` 응답."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    score_name: str
    bins: list[HistogramBin] = Field(default_factory=list)
    statistics: dict[str, ScoreStatistics] = Field(
        default_factory=dict, description="{run_name: ScoreStatistics}"
    )


class LatencyDistributionResponse(BaseModel):
    """``GET /analysis/latency/distribution`` 응답."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    run_name: str
    bins: list[HistogramBin] = Field(default_factory=list)
    p50: float | None = None
    p90: float | None = None
    p99: float | None = None
    avg: float | None = None
    stddev: float | None = None
    count: int = Field(0, ge=0)


class CostBreakdown(BaseModel):
    """비용 분해 (model_cost / eval_cost / total)."""

    model_config = ConfigDict(extra="forbid")

    model_cost: float = Field(0.0, ge=0.0, description="LLM 호출 비용")
    eval_cost: float = Field(0.0, ge=0.0, description="Judge / Embedding 등 평가 비용")
    total_cost: float = Field(0.0, ge=0.0)


class CostDistributionResponse(BaseModel):
    """``GET /analysis/cost/distribution`` 응답."""

    model_config = ConfigDict(extra="forbid")

    project_id: str
    runs: dict[str, CostBreakdown] = Field(
        default_factory=dict, description="{run_name: breakdown}"
    )
