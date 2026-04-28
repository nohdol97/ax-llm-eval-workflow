"""Agent Trace 도메인 모델 (Phase 8-A-1).

Langfuse v3 trace를 본 프로젝트 도메인 모델로 매핑한다.

- ``TraceObservation``: trace의 단일 span/generation/event
- ``TraceTree``: trace + 모든 observations + 연결된 score (시간순 정렬 보장)
- ``TraceFilter``: trace 검색 필터 (project_id 필수, 시간/태그/유저 등 선택 + 샘플링)
- 요청/응답 모델: ``TraceSearchRequest``, ``TraceSearchResponse``,
  ``TraceSummary``, ``TraceScoreRequest``, ``TraceScoreResponse``

설계 참고: ``docs/AGENT_EVAL.md`` §4.1.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ObservationType = Literal["span", "generation", "event"]
"""Langfuse observation 종류.

- ``span``: tool 호출/내부 단계 (input/output 임의 구조)
- ``generation``: LLM 호출 (model/usage/cost 부착)
- ``event``: 단일 시점 이벤트 (start_time만)
"""

ObservationLevel = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]
"""Langfuse observation 수준 — 에러 회복 평가에 사용."""


SampleStrategy = Literal["random", "first", "stratified"]
"""``TraceFilter.sample_size`` 적용 시 표본 추출 전략."""


class TraceObservation(BaseModel):
    """trace의 단일 observation (span/generation/event).

    Langfuse의 ``observations`` 행을 본 도메인 모델로 매핑한다. ``input``/``output``은
    원본이 dict일 수도 있고 plain string일 수도 있어 양쪽을 모두 허용한다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="observation 고유 ID")
    type: ObservationType = Field(..., description="span | generation | event")
    name: str = Field(..., description="observation 이름 (tool 이름 등)")
    parent_observation_id: str | None = Field(
        default=None, description="중첩 observation일 경우 부모 ID"
    )
    input: dict[str, Any] | list[Any] | str | None = Field(
        default=None, description="입력 페이로드"
    )
    output: dict[str, Any] | list[Any] | str | None = Field(
        default=None, description="출력 페이로드"
    )
    level: ObservationLevel = Field(default="DEFAULT", description="로그 레벨")
    status_message: str | None = Field(default=None, description="상태 메시지 (에러 사유 등)")
    start_time: datetime = Field(..., description="시작 시각 (UTC)")
    end_time: datetime | None = Field(default=None, description="종료 시각 (UTC)")
    latency_ms: float | None = Field(default=None, description="실행 시간 ms")
    # generation 전용 필드 (다른 type이면 None)
    model: str | None = Field(default=None, description="LLM 모델 식별자")
    usage: dict[str, int] | None = Field(default=None, description="토큰 사용량 dict")
    cost_usd: float | None = Field(default=None, description="비용 USD")
    metadata: dict[str, Any] = Field(default_factory=dict, description="자유 메타데이터")


class TraceTree(BaseModel):
    """trace + 모든 observations + 연결된 score.

    ``observations`` 는 ``start_time`` 오름차순으로 정렬된 상태로 보장한다.
    helper 메서드 (``find_observations``, ``tool_calls``, ``llm_calls``) 로 자식
    레벨에서 관심 observation 만 빠르게 추출한다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., description="trace 고유 ID")
    project_id: str = Field(..., description="Langfuse project_id")
    name: str = Field(..., description="agent 식별자 (Langfuse trace.name)")
    input: dict[str, Any] | list[Any] | str | None = Field(default=None, description="trace 입력")
    output: dict[str, Any] | list[Any] | str | None = Field(default=None, description="trace 출력")
    user_id: str | None = Field(default=None, description="유저 ID")
    session_id: str | None = Field(default=None, description="세션 ID")
    tags: list[str] = Field(default_factory=list, description="태그")
    metadata: dict[str, Any] = Field(default_factory=dict, description="자유 메타데이터")
    observations: list[TraceObservation] = Field(
        default_factory=list, description="자식 observations (시간순)"
    )
    scores: list[dict[str, Any]] = Field(default_factory=list, description="기존 부착된 score 목록")
    total_cost_usd: float = Field(default=0.0, description="trace 전체 비용 USD")
    total_latency_ms: float | None = Field(default=None, description="trace 전체 실행 시간 ms")
    timestamp: datetime = Field(..., description="trace 생성 시각 (UTC)")

    def find_observations(
        self,
        name: str | None = None,
        type: ObservationType | None = None,  # noqa: A002 — 도메인 의도상 type 사용
    ) -> list[TraceObservation]:
        """이름/타입 필터로 observation 검색.

        - ``name`` 만 전달: 해당 이름의 observation 전부
        - ``type`` 만 전달: 해당 타입 전부
        - 둘 다 전달: 두 조건 모두 충족
        - 둘 다 None: 빈 리스트 반환 (의도하지 않은 전체 노출 방지)
        """
        if name is None and type is None:
            return []
        result: list[TraceObservation] = []
        for obs in self.observations:
            if name is not None and obs.name != name:
                continue
            if type is not None and obs.type != type:
                continue
            result.append(obs)
        return result

    def tool_calls(self) -> list[TraceObservation]:
        """``type=span`` 인 observation 만 반환 (tool 호출로 간주)."""
        return [o for o in self.observations if o.type == "span"]

    def llm_calls(self) -> list[TraceObservation]:
        """``type=generation`` 인 observation 만 반환 (LLM 호출)."""
        return [o for o in self.observations if o.type == "generation"]


class TraceFilter(BaseModel):
    """trace 검색 필터.

    ``project_id`` 만 필수. 나머지는 모두 선택. 시간/태그/유저/세션/메타데이터 일치
    조건을 조합하고 ``sample_size`` 로 결과 표본을 제한할 수 있다.
    """

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="Langfuse project_id")
    name: str | None = Field(default=None, description="agent 이름 (trace.name)")
    tags: list[str] | None = Field(default=None, description="모든 태그가 일치해야 함")
    user_ids: list[str] | None = Field(default=None, description="유저 ID 화이트리스트")
    session_ids: list[str] | None = Field(default=None, description="세션 ID 화이트리스트")
    from_timestamp: datetime | None = Field(default=None, description="시작 시각 (포함)")
    to_timestamp: datetime | None = Field(default=None, description="종료 시각 (포함)")
    sample_size: int | None = Field(default=None, ge=1, description="표본 크기. None이면 전체")
    sample_strategy: SampleStrategy = Field(
        default="random", description="표본 추출 전략 (random | first | stratified)"
    )
    metadata_match: dict[str, Any] | None = Field(
        default=None,
        description="metadata 키-값 일치 조건 (직접 모드는 클라이언트 사이드 필터)",
    )


# ---------- API 요청/응답 모델 ----------


class TraceSearchRequest(BaseModel):
    """``POST /api/v1/traces/search`` 요청.

    ``include_observations=False`` (기본) 면 메타만 반환하여 페이지네이션 효율을
    우선한다. ``True`` 로 두면 단건 조회와 동등한 비용이 들기 때문에 본 라우터에서는
    아직 수용하지 않는다 (미래 확장용 필드).
    """

    model_config = ConfigDict(extra="forbid")

    filter: TraceFilter
    page: int = Field(default=1, ge=1, description="페이지 번호 (1-base)")
    page_size: int = Field(default=20, ge=1, le=100, description="페이지 크기 (최대 100)")
    include_observations: bool = Field(
        default=False, description="True면 observations까지 포함 (확장 예정)"
    )


class TraceSummary(BaseModel):
    """trace 목록 조회용 경량 모델 — observations 미포함."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    user_id: str | None = None
    session_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    total_latency_ms: float | None = None
    timestamp: datetime
    observation_count: int = Field(default=0, ge=0, description="trace에 속한 observation 개수")


class TraceSearchResponse(BaseModel):
    """``POST /api/v1/traces/search`` 응답."""

    model_config = ConfigDict(extra="forbid")

    items: list[TraceSummary]
    total: int = Field(..., ge=0, description="필터에 매칭되는 전체 trace 수 (샘플링 적용 후)")
    page: int = Field(..., ge=1)
    page_size: int = Field(..., ge=1, le=100)


class TraceScoreRequest(BaseModel):
    """``POST /api/v1/traces/{id}/score`` 요청.

    Review queue 결과 등 수동 점수 부여 용도. 점수 범위는 [0.0, 1.0].
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64, description="evaluator 이름")
    value: float = Field(..., ge=0.0, le=1.0, description="점수 (0.0 ~ 1.0)")
    comment: str | None = Field(default=None, max_length=2000, description="코멘트")


class TraceScoreResponse(BaseModel):
    """``POST /api/v1/traces/{id}/score`` 응답."""

    model_config = ConfigDict(extra="forbid")

    trace_id: str
    score_id: str
    name: str
    value: float
    created_at: datetime


__all__ = [
    "ObservationLevel",
    "ObservationType",
    "SampleStrategy",
    "TraceFilter",
    "TraceObservation",
    "TraceScoreRequest",
    "TraceScoreResponse",
    "TraceSearchRequest",
    "TraceSearchResponse",
    "TraceSummary",
    "TraceTree",
]
