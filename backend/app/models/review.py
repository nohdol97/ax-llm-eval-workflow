"""Review Queue 도메인 모델 (Phase 8-C-1).

본 모듈은 ``docs/AGENT_EVAL.md`` Part III §15.1 데이터 모델 명세를 그대로 구현한다.

- :class:`ReviewItem` — 큐에 담기는 단일 항목
- :class:`ReviewerStats` — reviewer 별 일일 집계
- ``ReviewItemType`` / ``ReviewStatus`` / ``ReviewDecision`` Literal
- ``Create`` / ``Resolve`` / ``Report`` / ``ListResponse`` 등 API I/O 모델

설계 참고: ``docs/AGENT_EVAL.md`` §14~§18.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

ReviewItemType = Literal[
    "auto_eval_flagged",
    "judge_low_confidence",
    "user_report",
    "manual_addition",
    "evaluator_submission",
]
"""ReviewItem 진입 분류.

- ``auto_eval_flagged``: AutoEvalEngine 자동 진입 (low score / disagreement)
- ``judge_low_confidence``: LLM Judge 응답이 중간값 + uncertain 키워드
- ``user_report``: 사용자 신고 버튼
- ``manual_addition``: reviewer 가 직접 추가
- ``evaluator_submission``: Phase 5 거버넌스 통합 (선택)
"""


ReviewStatus = Literal["open", "in_review", "resolved", "dismissed"]
"""ReviewItem 상태.

상태 전이: ``open → in_review → (resolved | dismissed)``.
``resolved`` / ``dismissed`` 진입 후 재오픈 불가 (새 ReviewItem 생성).
"""


ReviewDecision = Literal["approve", "override", "dismiss", "add_to_dataset"]
"""resolve 결정.

- ``approve``: 자동 score 확정
- ``override``: ``reviewer_score`` 로 Langfuse score 갱신
- ``dismiss``: false positive — 큐에서 제거
- ``add_to_dataset``: 골든셋 (``<agent>-reviewer-curated``) 에 trace 추가
"""


ReviewSeverity = Literal["low", "medium", "high"]
"""우선순위 — ZSet 정렬에 ``low=1, medium=2, high=3`` 점수 부여."""


ReviewSubjectType = Literal["trace", "experiment_item", "submission"]
"""평가 대상 종류."""


SEVERITY_SCORE: dict[ReviewSeverity, int] = {"low": 1, "medium": 2, "high": 3}
"""ZSet 정렬용 severity 점수 (높을수록 우선)."""


REVIEW_REASONS = (
    "auto_eval_low_score",
    "judge_low_confidence",
    "evaluator_disagreement",
    "user_report",
    "manual_addition",
)
"""5가지 자동/수동 진입 사유 — AGENT_EVAL.md §14 표 참조."""


class ReviewItem(BaseModel):
    """Review Queue 단일 항목.

    AGENT_EVAL.md §15.1 명세 그대로. ID 형식 ``review_<uuid12>``,
    UTC ISO 8601 시각.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="Review ID — ``review_<uuid12>``")
    type: ReviewItemType = Field(..., description="진입 분류")
    severity: ReviewSeverity = Field(default="medium", description="우선순위")

    subject_type: ReviewSubjectType = Field(..., description="평가 대상 종류")
    subject_id: str = Field(..., min_length=1, description="trace_id / experiment_item_id 등")
    project_id: str = Field(..., min_length=1, description="대상 프로젝트")

    reason: str = Field(..., min_length=1, description="진입 사유 키 (REVIEW_REASONS)")
    reason_detail: dict[str, Any] = Field(
        default_factory=dict,
        description="진입 사유 메타 — weighted_score, variance, policy_id 등",
    )

    automatic_scores: dict[str, float | None] = Field(
        default_factory=dict, description="진입 시점 자동 평가 결과 snapshot"
    )

    status: ReviewStatus = Field(default="open", description="현재 상태")
    assigned_to: str | None = Field(default=None, description="claim 한 reviewer user_id")
    assigned_at: datetime | None = Field(default=None, description="claim 시각")

    decision: ReviewDecision | None = Field(default=None, description="resolve 결정")
    reviewer_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="decision=override 시 reviewer 가 입력한 점수 (0.0~1.0)",
    )
    reviewer_comment: str | None = Field(
        default=None, max_length=4000, description="reviewer 코멘트"
    )
    expected_output: dict[str, Any] | list[Any] | str | None = Field(
        default=None,
        description="decision=add_to_dataset 시 골든셋에 추가할 expected_output",
    )
    resolved_by: str | None = Field(default=None, description="resolve 한 user_id")
    resolved_at: datetime | None = Field(default=None, description="resolve 시각")

    auto_eval_policy_id: str | None = Field(default=None, description="진입 정책 ID")
    auto_eval_run_id: str | None = Field(default=None, description="진입 run ID")

    created_at: datetime = Field(..., description="생성 시각 (UTC)")
    updated_at: datetime = Field(..., description="최종 수정 시각 (UTC)")


class ReviewerStats(BaseModel):
    """reviewer 개인 통계 (일일 집계 + 누적)."""

    model_config = ConfigDict(extra="forbid")

    user_id: str = Field(..., min_length=1)
    open_count: int = Field(default=0, ge=0, description="현재 open 으로 보이는 큐 건수")
    in_review_count: int = Field(default=0, ge=0, description="본인이 claim 중인 건수")
    resolved_today: int = Field(default=0, ge=0, description="오늘 resolve/dismiss 한 건수")
    avg_resolution_time_min: float | None = Field(
        default=None, description="평균 처리 시간 (분) — None 이면 데이터 부족"
    )
    decisions_breakdown: dict[ReviewDecision, int] = Field(
        default_factory=dict, description="결정별 카운트"
    )


class ReviewItemCreate(BaseModel):
    """``POST /api/v1/reviews/items`` 요청 body — 수동 추가 (manual_addition).

    ``id`` / ``status`` / ``created_at`` / ``updated_at`` 은 서버 발급.
    ``type`` 은 항상 ``manual_addition``.
    """

    model_config = ConfigDict(extra="forbid")

    subject_type: ReviewSubjectType = "trace"
    subject_id: str = Field(..., min_length=1)
    project_id: str = Field(..., min_length=1)
    severity: ReviewSeverity = "medium"
    reason: str = Field(default="manual_addition", min_length=1)
    reason_detail: dict[str, Any] = Field(default_factory=dict)
    automatic_scores: dict[str, float | None] = Field(default_factory=dict)
    auto_eval_policy_id: str | None = None
    auto_eval_run_id: str | None = None


class ReviewItemResolve(BaseModel):
    """``POST /api/v1/reviews/items/{id}/resolve`` 요청 body."""

    model_config = ConfigDict(extra="forbid")

    decision: ReviewDecision = Field(..., description="결정")
    reviewer_score: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="decision=override 시 필수",
    )
    reviewer_comment: str | None = Field(default=None, max_length=4000)
    expected_output: dict[str, Any] | list[Any] | str | None = Field(
        default=None,
        description="decision=add_to_dataset 시 권장",
    )

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """decision=override 면 reviewer_score 필수."""
        if self.decision == "override" and self.reviewer_score is None:
            raise ValueError("decision=override 일 때 reviewer_score 필수")
        return self


class ReviewReport(BaseModel):
    """``POST /api/v1/reviews/report`` 요청 body — 사용자 신고.

    ``subject_type`` 기본은 ``trace`` (Langfuse trace 단위 신고).
    Compare 페이지에서는 ``experiment_item`` 으로 보낼 수 있다 — backend 는
    동일한 ReviewItem 으로 처리한다.
    """

    model_config = ConfigDict(extra="forbid")

    trace_id: str = Field(..., min_length=1, description="신고 대상 ID (subject_id)")
    project_id: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1, max_length=500, description="신고 사유 (free text)")
    severity: ReviewSeverity = "medium"
    subject_type: ReviewSubjectType = Field(
        default="trace", description="trace | experiment_item | submission"
    )


class ReviewItemListResponse(BaseModel):
    """``GET /api/v1/reviews/items`` 응답."""

    model_config = ConfigDict(extra="forbid")

    items: list[ReviewItem]
    total: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class ReviewQueueSummary(BaseModel):
    """``GET /api/v1/reviews/stats/summary`` 응답."""

    model_config = ConfigDict(extra="forbid")

    open: int = Field(default=0, ge=0)
    in_review: int = Field(default=0, ge=0)
    resolved_today: int = Field(default=0, ge=0)
    dismissed_today: int = Field(default=0, ge=0)
    avg_resolution_time_min: float | None = Field(default=None)


class EvaluatorDisagreementStat(BaseModel):
    """``GET /api/v1/reviews/stats/disagreement`` 응답 항목."""

    model_config = ConfigDict(extra="forbid")

    evaluator: str = Field(..., min_length=1)
    total_resolved: int = Field(default=0, ge=0)
    override_count: int = Field(default=0, ge=0)
    override_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class EvaluatorDisagreementResponse(BaseModel):
    """``GET /api/v1/reviews/stats/disagreement`` 응답 wrapper."""

    model_config = ConfigDict(extra="forbid")

    items: list[EvaluatorDisagreementStat]


__all__ = [
    "REVIEW_REASONS",
    "SEVERITY_SCORE",
    "EvaluatorDisagreementResponse",
    "EvaluatorDisagreementStat",
    "ReviewDecision",
    "ReviewItem",
    "ReviewItemCreate",
    "ReviewItemListResponse",
    "ReviewItemResolve",
    "ReviewItemType",
    "ReviewQueueSummary",
    "ReviewReport",
    "ReviewSeverity",
    "ReviewStatus",
    "ReviewSubjectType",
    "ReviewerStats",
]
