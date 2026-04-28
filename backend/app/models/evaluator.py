"""Custom Evaluator 거버넌스 도메인 Pydantic 모델 (API_DESIGN.md §8 / §14).

본 파일은 Phase 5에서 도입되는 Custom Evaluator 제출/승인/반려/폐기(거버넌스)
워크플로우와 사전 검증(Validate) API에서 사용하는 요청·응답 모델을 정의한다.

저장소: Redis Hash ``ax:evaluator_submission:{id}`` (TTL 영구; admin이 명시 폐기).
보조 인덱스 Sorted Set ``ax:evaluator_submissions:by_user:{user_id}``
(score=submitted_at_ms, member=submission_id) — 사용자별 최신순 조회.
관리자 전용 인덱스 ``ax:evaluator_submissions:all``과 상태 인덱스
``ax:evaluator_submissions:status:{status}``는 페이지네이션·필터에 사용된다.

응답 필터링(코드 본문 노출 제어)은 라우터 계층에서 처리한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------- 상태 ----------
SubmissionStatus = Literal["pending", "approved", "rejected", "deprecated"]
"""Custom Evaluator 제출 상태.

- ``pending``: 제출 후 admin 승인 대기
- ``approved``: admin 승인 완료 → 모든 사용자 사용 가능
- ``rejected``: admin 반려 (사유 필수)
- ``deprecated``: 승인 후 폐기 — 신규 사용 차단, 진행 중 실험은 snapshot 기반 유지
"""

# ---------- 사전 검증 (Validate) ----------


class TestCase(BaseModel):
    """``POST /api/v1/evaluators/validate`` test_case 1건."""

    __test__ = False  # pytest 수집 대상 아님 (test_ prefix 클래스가 아님)

    model_config = ConfigDict(extra="forbid")

    output: str | dict[str, Any] | list[Any] = Field(..., description="평가 대상 출력 (모델 응답)")
    expected: str | dict[str, Any] | list[Any] | None = Field(
        default=None, description="정답 (선택)"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="latency_ms / cost_usd 등 부가 메타"
    )


class TestResult(BaseModel):
    """validate API 응답의 단일 test_case 결과 — 성공 시 ``result``, 실패 시 ``error``."""

    __test__ = False

    model_config = ConfigDict(extra="forbid")

    result: float | None = Field(default=None, description="0.0~1.0 점수")
    error: str | None = Field(default=None, description="오류 메시지")


class ValidateRequest(BaseModel):
    """``POST /api/v1/evaluators/validate`` 요청 본문."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(..., min_length=1, description="평가 코드 (Python)")
    test_cases: list[TestCase] = Field(default_factory=list, description="실행할 test_case 목록")


class ValidateResponse(BaseModel):
    """validate API 응답."""

    model_config = ConfigDict(extra="forbid")

    test_results: list[TestResult] = Field(
        default_factory=list, description="입력 순서대로 결과 리스트"
    )


# ---------- 제출 (Submit) ----------


class SubmissionCreate(BaseModel):
    """``POST /api/v1/evaluators/submissions`` 요청 본문."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120, description="evaluator 이름")
    description: str = Field(..., min_length=1, max_length=2000, description="설명")
    code: str = Field(..., min_length=1, description="평가 코드 (Python)")
    test_cases: list[TestCase] | None = Field(
        default=None,
        description="제출 전 자동 사전 검증에 사용 (선택). 모두 통과해야 pending으로 진입.",
    )


class Submission(BaseModel):
    """Custom Evaluator 제출 객체.

    응답에서 ``code``는 본인/admin에게만 노출된다 — 라우터 계층에서 필터링.
    """

    model_config = ConfigDict(extra="forbid")

    submission_id: str = Field(..., description="제출 ID (UUID4)")
    name: str = Field(..., description="evaluator 이름")
    description: str = Field(..., description="설명")
    code: str = Field(..., description="평가 코드 본문 (본인/admin만 노출)")
    code_hash: str = Field(..., description="sha256(code).hexdigest()[:16]")
    status: SubmissionStatus = Field(..., description="현재 상태")
    submitted_by: str = Field(..., description="제출자 user_id")
    submitted_at: datetime = Field(..., description="제출 시각 (UTC)")
    approved_by: str | None = Field(default=None)
    approved_at: datetime | None = Field(default=None)
    rejected_by: str | None = Field(default=None)
    rejected_at: datetime | None = Field(default=None)
    rejection_reason: str | None = Field(default=None)
    deprecated_at: datetime | None = Field(default=None)


class SubmissionListResponse(BaseModel):
    """제출 목록 응답 — 페이지네이션."""

    model_config = ConfigDict(extra="forbid")

    items: list[Submission] = Field(default_factory=list)
    total: int = Field(0, ge=0)
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)


# ---------- 승인 / 반려 ----------


class ApprovalRequest(BaseModel):
    """승인 요청 본문 — 메모는 선택."""

    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000, description="승인 메모 (선택)")


class RejectionRequest(BaseModel):
    """반려 요청 본문 — 사유 필수."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=1, max_length=2000, description="반려 사유")


# ---------- 내장 evaluator 메타 ----------


class BuiltInEvaluatorInfo(BaseModel):
    """내장 evaluator 메타 — UI Step 3에서 사용."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    data_type: Literal["BOOLEAN", "NUMERIC"]
    range: tuple[float, float] | None = Field(
        default=None, description="(min, max) 또는 None (기본 0~1)"
    )
    config_schema: dict[str, Any] = Field(default_factory=dict)


# ---------- score config 상태 ----------

ScoreConfigStatus = Literal["registered", "missing", "mismatch"]


class ScoreConfigStatusItem(BaseModel):
    """``GET /api/v1/evaluators/score-configs`` 응답 1행."""

    model_config = ConfigDict(extra="forbid")

    name: str
    status: ScoreConfigStatus
    data_type: str | None = None
    range: tuple[float, float] | None = None


# ---------- 상수 ----------

SUBMISSION_TTL_SECONDS: int | None = None
"""제출 TTL — 무기한 보관 (admin이 명시적으로 deprecate/삭제). None이면 EXPIRE 미적용."""

CODE_HASH_LENGTH: int = 16
"""``sha256(code).hexdigest()[:N]`` — 짧은 hash 길이."""


__all__ = [
    "ApprovalRequest",
    "BuiltInEvaluatorInfo",
    "CODE_HASH_LENGTH",
    "RejectionRequest",
    "SUBMISSION_TTL_SECONDS",
    "ScoreConfigStatus",
    "ScoreConfigStatusItem",
    "Submission",
    "SubmissionCreate",
    "SubmissionListResponse",
    "SubmissionStatus",
    "TestCase",
    "TestResult",
    "ValidateRequest",
    "ValidateResponse",
]
