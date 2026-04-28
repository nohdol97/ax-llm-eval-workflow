"""실험 도메인 Pydantic 모델 (API_DESIGN.md §4, §11.1).

본 파일은 Phase 4의 실험 lifecycle/조회/제어 API 응답·요청에 사용되는 모델을 정의한다.

요청·생성 모델 (Agent 17)
- ``PromptConfig`` / ``ModelConfig`` / ``EvaluatorConfig``: 실험 생성 시 입력 단위
- ``ExperimentCreate``: ``POST /api/v1/experiments`` 요청 body
- ``ExperimentInitResponse``: 생성 직후 응답 (백그라운드 실행 시작)
- SSE 이벤트 모델: ``ProgressEvent`` / ``RunCompleteEvent`` / ``ExperimentCompleteEvent``

응답·조회 모델 (Agent 18)
- ``RunSummary`` / ``ExperimentSummary`` / ``ExperimentDetail`` / ``ExperimentListResponse`` /
  ``ExperimentStatusResponse``
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.trace import TraceFilter

# 실험 상태 — 본 프로젝트 정책 8개 상태 (IMPLEMENTATION.md §1.6 + degraded 확장)
ExperimentStatus = Literal[
    "pending",
    "queued",
    "running",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "degraded",
]


# 실험 모드 — Phase 8-A: trace_eval 모드 추가
ExperimentMode = Literal["live", "trace_eval"]
"""``live``: 데이터셋 아이템마다 LLM 호출 + 평가 (기존 동작).
``trace_eval``: Langfuse trace를 가져와 evaluator 적용 (LLM 호출 없음)."""


class RunSummary(BaseModel):
    """단일 Run 요약.

    배치 실험의 ``ax:run:{exp_id}:{run_name}`` Hash에서 파생된 요약값.
    avg_score / avg_latency_ms는 ``total_*_sum / *_count``로 응답 시점 계산한다.
    """

    model_config = ConfigDict(extra="forbid")

    run_name: str = Field(..., description="Run 식별자 (`<prompt>_v<n>_<model>_<date>`)")
    model: str = Field(..., description="LiteLLM 모델 이름")
    prompt_version: int = Field(..., ge=1, description="Langfuse 프롬프트 버전")
    status: ExperimentStatus = Field(..., description="Run 단위 상태")
    items_completed: int = Field(0, ge=0)
    items_total: int = Field(0, ge=0)
    avg_score: float | None = Field(default=None, description="`total_score_sum/scored_count`")
    total_cost: float = Field(0.0, ge=0.0)
    avg_latency_ms: float | None = Field(default=None, ge=0.0)


class ExperimentSummary(BaseModel):
    """목록 응답 1행 — 가벼운 메타만 노출."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    name: str
    status: ExperimentStatus
    runs_total: int = Field(0, ge=0)
    runs_completed: int = Field(0, ge=0)
    total_cost: float = Field(0.0, ge=0.0)
    avg_score: float | None = None
    created_at: datetime


class ExperimentDetail(BaseModel):
    """상세 응답 — runs + config_snapshot 포함 (API_DESIGN §4.3)."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    name: str
    description: str | None = None
    status: ExperimentStatus
    project_id: str
    owner: str = Field(..., description="실험 소유자 (started_by user_id)")
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    progress: dict[str, Any] = Field(
        default_factory=dict,
        description="processed/total/percentage/eta_sec",
    )
    runs: list[RunSummary] = Field(default_factory=list)
    config_snapshot: dict[str, Any] = Field(
        default_factory=dict,
        description="원본 ExperimentCreate JSON (immutable)",
    )
    evaluator_summary: dict[str, Any] = Field(
        default_factory=dict,
        description="Phase 5에서 활성화 — 현재는 빈 dict",
    )
    # Phase 8-A: trace_eval 모드 부가 정보
    mode: ExperimentMode = Field(
        default="live",
        description="실험 모드 (live | trace_eval)",
    )
    trace_filter: TraceFilter | None = Field(
        default=None,
        description="mode=trace_eval에서 사용된 trace 필터 (snapshot)",
    )
    traces_evaluated: int | None = Field(
        default=None,
        ge=0,
        description="mode=trace_eval에서 평가 완료된 trace 수",
    )


class ExperimentListResponse(BaseModel):
    """목록 응답 wrapper — 페이지네이션."""

    model_config = ConfigDict(extra="forbid")

    items: list[ExperimentSummary] = Field(default_factory=list)
    total: int = Field(0, ge=0)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


class ExperimentStatusResponse(BaseModel):
    """상태 전이 액션 응답 (pause/resume/cancel/retry-failed)."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    status: ExperimentStatus
    transitioned_at: datetime


# ---------- 생성/입력 모델 (Agent 17) ----------


class PromptConfig(BaseModel):
    """프롬프트 버전 지정 — Langfuse 등록 프롬프트만 지원."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, description="Langfuse 프롬프트 이름")
    version: int | None = Field(
        default=None, ge=1, description="프롬프트 버전 (label과 둘 중 하나 사용)"
    )
    label: str | None = Field(default=None, description="라벨 (production 등)")

    @model_validator(mode="after")
    def _validate_version_or_label(self) -> Self:
        """label이 빈 문자열이면 안 됨 — 둘 다 None이면 'latest'로 해석."""
        if self.label is not None and not self.label.strip():
            raise ValueError("label은 빈 문자열이 될 수 없습니다.")
        return self


class ModelConfig(BaseModel):
    """모델 + 파라미터 조합."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(..., min_length=1, description="LiteLLM 등록 모델 이름")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="모델 파라미터 (temperature/top_p/max_tokens 등)",
    )


class EvaluatorConfig(BaseModel):
    """평가자(Evaluator) 설정 — Phase 5에서 평가 로직 추가 예정.

    현재(Phase 4)는 입력만 검증하고 실제 평가는 수행하지 않는다.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["builtin", "judge", "approved", "inline_custom", "trace_builtin"] = Field(
        ..., description="평가자 타입 (Phase 8-A: trace_builtin 추가)"
    )
    name: str = Field(..., min_length=1, description="평가자 이름")
    config: dict[str, Any] = Field(
        default_factory=dict, description="평가자별 설정 (judge_model 등)"
    )
    weight: float = Field(default=1.0, ge=0.0, le=1.0, description="가중치 (0.0~1.0)")


class ExperimentCreate(BaseModel):
    """``POST /api/v1/experiments`` 요청 body.

    Phase 8-A에서 ``mode`` 필드가 추가되어 두 가지 실행 형태를 지원한다.

    ``mode=live`` (기존 기본값)
    - ``prompt_configs`` + ``dataset_name`` + ``model_configs`` 필수
    - 데이터셋 아이템마다 LLM 호출 후 evaluator 적용

    ``mode=trace_eval`` (Phase 8-A 신규)
    - ``trace_filter`` 필수 (Langfuse trace 검색 조건)
    - ``expected_dataset_name`` 선택 (골든셋 매칭 시)
    - LLM 호출 없음 — 가져온 trace에 evaluator만 적용

    공통 검증 규칙(API_DESIGN.md §4.1):
    - concurrency 1~20
    - 최소 1개 이상의 evaluator 필요
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="대상 프로젝트 ID")
    name: str = Field(..., min_length=1, max_length=100, description="실험 이름")
    description: str | None = Field(default=None, description="실험 설명")

    # 실행 모드 (Phase 8-A)
    mode: ExperimentMode = Field(
        default="live",
        description="실험 모드 — live(LLM 호출+평가) | trace_eval(기존 trace 평가)",
    )

    # mode=live 필드 (기존, optional)
    prompt_configs: list[PromptConfig] | None = Field(
        default=None,
        description="프롬프트 버전 목록 — mode=live에서 필수 (1개 이상)",
    )
    dataset_name: str | None = Field(
        default=None,
        description="Langfuse 데이터셋 이름 — mode=live에서 필수",
    )
    dataset_variable_mapping: dict[str, str] | None = Field(
        default=None,
        description="``{프롬프트 변수명: dataset.input 키}``. None이면 자동 매핑.",
    )
    model_configs: list[ModelConfig] | None = Field(
        default=None,
        description="모델/파라미터 조합 목록 — mode=live에서 필수 (1개 이상)",
    )

    # mode=trace_eval 필드 (Phase 8-A 신규)
    trace_filter: TraceFilter | None = Field(
        default=None,
        description="trace 검색 필터 — mode=trace_eval에서 필수",
    )
    expected_dataset_name: str | None = Field(
        default=None,
        description="골든셋 데이터셋 이름 — mode=trace_eval에서 trace.input과 매칭 (선택)",
    )

    # 공통 필드
    evaluators: list[EvaluatorConfig] = Field(
        default_factory=list,
        description="평가자 목록 (모드 공통, 최소 1개 필요)",
    )
    concurrency: int = Field(
        default=5, ge=1, le=20, description="아이템/Trace 단위 동시 실행 한도 (1~20)"
    )
    system_prompt: str | None = Field(default=None, description="옵션 system 프롬프트 (mode=live)")
    metadata: dict[str, Any] = Field(default_factory=dict, description="추가 메타데이터")

    @model_validator(mode="after")
    def _validate_combinations(self) -> Self:
        """모드별 필수 필드 검증 + 공통 검증.

        - mode=live: prompt_configs / dataset_name / model_configs 모두 필수
        - mode=trace_eval: trace_filter 필수
        - 공통: concurrency 1~20, evaluators 1개 이상
        """
        if self.mode == "live":
            if not self.prompt_configs:
                raise ValueError("mode=live에서는 prompt_configs가 최소 1개 이상이어야 합니다.")
            if not self.model_configs:
                raise ValueError("mode=live에서는 model_configs가 최소 1개 이상이어야 합니다.")
            if not self.dataset_name:
                raise ValueError("mode=live에서는 dataset_name이 필수입니다.")
        elif self.mode == "trace_eval":
            if self.trace_filter is None:
                raise ValueError("mode=trace_eval에서는 trace_filter가 필수입니다.")
            if not self.evaluators:
                raise ValueError("mode=trace_eval에서는 최소 1개 이상의 evaluator가 필요합니다.")
        if self.concurrency < 1 or self.concurrency > 20:
            raise ValueError("concurrency는 1~20 범위여야 합니다.")
        return self


class RunInitSummary(BaseModel):
    """``POST /api/v1/experiments`` 응답의 ``runs[]`` 항목.

    실험 생성 직후 Run 단위 초기 상태. 생성 시점에는 ``items_completed=0``.
    Agent 18의 ``RunSummary``는 진행 중/완료 후의 누적 통계를 위한 별도 모델.
    """

    model_config = ConfigDict(extra="forbid")

    run_name: str = Field(..., description="Run 식별 이름")
    prompt_name: str = Field(..., description="프롬프트 이름")
    prompt_version: int | None = Field(default=None, description="프롬프트 버전")
    model: str = Field(..., description="LiteLLM 모델 ID")
    status: ExperimentStatus = Field(..., description="Run 초기 상태")


class ExperimentInitResponse(BaseModel):
    """``POST /api/v1/experiments`` 응답.

    실험 생성 직후 즉시 반환되는 초기 응답. 실제 실행은 백그라운드에서 진행된다.
    """

    model_config = ConfigDict(extra="forbid")

    experiment_id: str = Field(..., description="실험 ID (UUID4)")
    status: ExperimentStatus = Field(..., description="초기 상태 (running 또는 queued)")
    total_runs: int = Field(..., ge=0, description="총 Run 수 (prompt × model)")
    total_items: int = Field(..., ge=0, description="총 평가 아이템 수")
    runs: list[RunInitSummary] = Field(default_factory=list, description="Run 목록")
    started_at: datetime = Field(..., description="시작 시각 (UTC)")


# ---------- SSE 이벤트 모델 ----------


class ProgressEvent(BaseModel):
    """SSE ``progress`` 이벤트."""

    model_config = ConfigDict(extra="forbid")

    run_name: str
    completed: int = Field(..., ge=0)
    total: int = Field(..., ge=0)
    failed: int = Field(default=0, ge=0)
    current_item: dict[str, Any] | None = Field(default=None)


class RunCompleteEvent(BaseModel):
    """SSE ``run_complete`` 이벤트."""

    model_config = ConfigDict(extra="forbid")

    run_name: str
    summary: dict[str, Any] = Field(default_factory=dict)


class ExperimentCompleteEvent(BaseModel):
    """SSE ``experiment_complete`` 이벤트."""

    model_config = ConfigDict(extra="forbid")

    experiment_id: str
    total_duration_sec: float = Field(..., ge=0.0)
    total_cost_usd: float = Field(..., ge=0.0)
    total_items: int = Field(..., ge=0)
    completed_items: int = Field(default=0, ge=0)
    failed_items: int = Field(default=0, ge=0)


# ---------- 상수 ----------
EXPERIMENT_TTL_ACTIVE_SEC: int = 86_400
"""활성 실험 TTL (24시간) — IMPLEMENTATION.md §1.7."""

EXPERIMENT_TTL_TERMINAL_SEC: int = 3_600
"""종료 실험 TTL (1시간)."""

WORKSPACE_MAX_CONCURRENT_EXPERIMENTS: int = 5
"""워크스페이스 단위 동시 실행 한도 — NFR §12.1."""

AUTO_PAUSE_FAILURE_RATE: float = 0.5
"""실패율 자동 일시정지 임계 (0.5 = 50%)."""

ITEM_RETRY_MAX_ATTEMPTS: int = 2
"""아이템 단위 자동 재시도 최대 횟수."""
