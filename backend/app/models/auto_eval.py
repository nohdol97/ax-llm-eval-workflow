"""Auto-Eval Policy / Run / Schedule / AlertThreshold 도메인 모델 (Phase 8-B-1).

본 모듈은 ``docs/AGENT_EVAL.md`` Part II §8.1 데이터 모델 명세를 그대로 구현한다.

- :class:`AutoEvalSchedule` — 정책 실행 스케줄 (cron / interval / event)
- :class:`AlertThreshold` — 회귀 감지 임계값 (절대값 + 상대 drop_pct)
- :class:`AutoEvalPolicy` — 정책 본체 (필터 + evaluators + 스케줄 + 알림)
- :class:`AutoEvalRun` — 정책 1회 실행 결과 (집계 + 트리거된 알림 + 비용)
- ``Create``/``Update``/``ListResponse`` 등 API I/O 모델

설계 참고: ``docs/AGENT_EVAL.md`` §8~13.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter

ScheduleType = Literal["cron", "interval", "event"]
"""스케줄 종류.

- ``cron``: ``cron_expression`` 기반 — APScheduler / croniter 사용
- ``interval``: N초 주기 (최소 60초)
- ``event``: 새 trace 누적 등 이벤트 트리거 (v1에서는 placeholder, 등록만 가능)
"""


PolicyStatus = Literal["active", "paused", "deprecated"]
"""정책 상태."""


AutoEvalRunStatus = Literal["running", "completed", "failed", "skipped"]
"""정책 실행 상태."""


class AutoEvalSchedule(BaseModel):
    """정책 실행 스케줄.

    type 별 필수 필드:
    - ``cron``: ``cron_expression`` (예: ``"0 */1 * * *"``)
    - ``interval``: ``interval_seconds`` (>= 60)
    - ``event``: ``event_trigger`` + ``event_threshold`` (v1 placeholder)
    """

    model_config = ConfigDict(extra="forbid")

    type: ScheduleType = Field(..., description="스케줄 타입")
    # type=cron
    cron_expression: str | None = Field(default=None, description="cron 표현식")
    timezone: str = Field(default="Asia/Seoul", description="타임존 이름 (IANA)")
    # type=interval
    interval_seconds: int | None = Field(default=None, ge=60, description="인터벌 초 (최소 60초)")
    # type=event (v2 — v1 placeholder)
    event_trigger: Literal["new_traces", "scheduled_dataset_run"] | None = Field(
        default=None, description="이벤트 트리거 종류"
    )
    event_threshold: int | None = Field(
        default=None, ge=1, description="trigger=new_traces일 때 누적 임계"
    )

    @model_validator(mode="after")
    def _validate_schedule_fields(self) -> Self:
        """type 별 필수 필드 + cron 표현식 유효성 검증."""
        if self.type == "cron":
            if not self.cron_expression:
                raise ValueError("cron schedule requires cron_expression")
            try:
                from croniter import croniter

                croniter(self.cron_expression)
            except (ValueError, ImportError, KeyError, TypeError) as exc:
                raise ValueError(f"invalid cron expression: {exc}") from exc
        elif self.type == "interval":
            if not self.interval_seconds:
                raise ValueError("interval schedule requires interval_seconds")
        elif self.type == "event":
            # v1: placeholder — 등록은 허용, 실제 트리거는 cron 스케줄러가 무시.
            if not self.event_trigger:
                raise ValueError("event schedule requires event_trigger")
        return self


class AlertThreshold(BaseModel):
    """회귀 감지 임계값.

    - 절대값: ``operator`` + ``value`` 비교
    - 상대값: ``drop_pct`` 가 설정되면 직전 run 대비 N% 하락 시 발화
    - ``metric=evaluator_score`` 인 경우 ``evaluator_name`` 필수
    """

    model_config = ConfigDict(extra="forbid")

    metric: Literal["avg_score", "pass_rate", "evaluator_score"] = Field(
        ..., description="평가 대상 메트릭"
    )
    evaluator_name: str | None = Field(
        default=None,
        description="metric=evaluator_score 시 필수 — 어떤 evaluator를 볼지",
    )
    operator: Literal["lt", "lte", "gt", "gte"] = Field(..., description="비교 연산자")
    value: float = Field(..., ge=0.0, le=1.0, description="임계 절대값 (0.0~1.0)")
    drop_pct: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="직전 run 대비 N% 하락 시 발화 (0.0~1.0)",
    )
    window_minutes: int = Field(
        default=60,
        ge=1,
        description="비교 대상 직전 run의 시간 윈도우 (분)",
    )

    @model_validator(mode="after")
    def _validate_evaluator_name(self) -> Self:
        """metric=evaluator_score 일 때 evaluator_name 필수."""
        if self.metric == "evaluator_score" and not self.evaluator_name:
            raise ValueError("metric=evaluator_score requires evaluator_name")
        return self


class AutoEvalPolicy(BaseModel):
    """Auto-Eval 정책 본체."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="정책 ID — ``policy_<uuid12>``")
    name: str = Field(..., min_length=1, max_length=200, description="정책 이름")
    description: str | None = Field(default=None, description="설명")
    project_id: str = Field(..., min_length=1, description="대상 프로젝트")

    trace_filter: TraceFilter = Field(..., description="평가 대상 trace 필터")
    expected_dataset_name: str | None = Field(
        default=None, description="골든셋 매칭용 데이터셋 이름 (선택)"
    )

    evaluators: list[EvaluatorConfig] = Field(
        ..., min_length=1, description="적용할 evaluator 목록"
    )

    schedule: AutoEvalSchedule = Field(..., description="스케줄")

    alert_thresholds: list[AlertThreshold] = Field(
        default_factory=list, description="회귀 감지 임계 목록"
    )
    notification_targets: list[str] = Field(
        default_factory=list, description="알림 수신 user_id 목록"
    )

    daily_cost_limit_usd: float | None = Field(
        default=None, ge=0.0, description="정책당 일일 LLM Judge 비용 한도 (USD)"
    )

    status: PolicyStatus = Field(default="active", description="정책 상태")
    owner: str = Field(..., min_length=1, description="소유자 user_id")
    created_at: datetime = Field(..., description="생성 시각 (UTC)")
    updated_at: datetime = Field(..., description="최종 수정 시각 (UTC)")
    last_run_at: datetime | None = Field(default=None, description="직전 실행 시각")
    next_run_at: datetime | None = Field(default=None, description="다음 실행 시각")


class AutoEvalPolicyCreate(BaseModel):
    """``POST /api/v1/auto-eval/policies`` 요청 body.

    ``id`` / ``created_at`` / ``updated_at`` 은 서버 발급.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    project_id: str = Field(..., min_length=1)
    trace_filter: TraceFilter
    expected_dataset_name: str | None = None
    evaluators: list[EvaluatorConfig] = Field(default_factory=list)
    schedule: AutoEvalSchedule
    alert_thresholds: list[AlertThreshold] = Field(default_factory=list)
    notification_targets: list[str] = Field(default_factory=list)
    daily_cost_limit_usd: float | None = Field(default=None, ge=0.0)
    status: PolicyStatus = "active"

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """evaluators 비어 있으면 거부 (최소 1개)."""
        if not self.evaluators:
            raise ValueError("at least one evaluator required")
        return self


class AutoEvalPolicyUpdate(BaseModel):
    """``PATCH /api/v1/auto-eval/policies/{id}`` 요청 body.

    모든 필드 optional — 변경할 필드만 전달한다.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    trace_filter: TraceFilter | None = None
    expected_dataset_name: str | None = None
    evaluators: list[EvaluatorConfig] | None = None
    schedule: AutoEvalSchedule | None = None
    alert_thresholds: list[AlertThreshold] | None = None
    notification_targets: list[str] | None = None
    daily_cost_limit_usd: float | None = Field(default=None, ge=0.0)
    status: PolicyStatus | None = None


class AutoEvalRun(BaseModel):
    """정책 1회 실행 결과."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="run ID — ``run_<uuid12>``")
    policy_id: str = Field(..., description="실행된 정책 ID")
    started_at: datetime = Field(..., description="시작 시각 (UTC)")
    completed_at: datetime | None = Field(default=None, description="완료 시각")
    status: AutoEvalRunStatus = Field(..., description="실행 상태")
    skip_reason: str | None = Field(default=None, description="status=skipped 일 때 사유")

    traces_evaluated: int = Field(default=0, ge=0, description="평가한 trace 수")
    traces_total: int = Field(default=0, ge=0, description="필터 매칭 총 수 (sample_size 적용 전)")
    avg_score: float | None = Field(default=None, description="weighted_score 평균")
    pass_rate: float | None = Field(
        default=None,
        description="weighted_score >= 0.7 비율",
    )
    cost_usd: float = Field(default=0.0, ge=0.0, description="LLM Judge 호출 비용 (USD)")
    duration_ms: float | None = Field(default=None, description="실행 시간 (ms)")

    scores_by_evaluator: dict[str, float | None] = Field(
        default_factory=dict, description="evaluator 이름 → 평균 점수"
    )

    triggered_alerts: list[str] = Field(default_factory=list, description="발화된 임계 ID 목록")
    review_items_created: int = Field(default=0, ge=0, description="Review Queue에 진입한 항목 수")

    error_message: str | None = Field(default=None, description="status=failed 시 에러 메시지")


class AutoEvalRunListResponse(BaseModel):
    """``GET /api/v1/auto-eval/runs`` 응답."""

    model_config = ConfigDict(extra="forbid")

    items: list[AutoEvalRun]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class AutoEvalPolicyListResponse(BaseModel):
    """``GET /api/v1/auto-eval/policies`` 응답."""

    model_config = ConfigDict(extra="forbid")

    items: list[AutoEvalPolicy]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class CostUsage(BaseModel):
    """``GET /api/v1/auto-eval/policies/{id}/cost-usage`` 응답.

    daily_breakdown 항목: ``{date, cost_usd, runs_count}``.
    """

    model_config = ConfigDict(extra="forbid")

    policy_id: str
    date_range: str = Field(..., description="``YYYY-MM-DD:YYYY-MM-DD`` 형식")
    daily_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    daily_limit_usd: float | None = None


__all__ = [
    "AlertThreshold",
    "AutoEvalPolicy",
    "AutoEvalPolicyCreate",
    "AutoEvalPolicyListResponse",
    "AutoEvalPolicyUpdate",
    "AutoEvalRun",
    "AutoEvalRunListResponse",
    "AutoEvalRunStatus",
    "AutoEvalSchedule",
    "CostUsage",
    "PolicyStatus",
    "ScheduleType",
]
