"""단일 테스트 (``POST /api/v1/tests/single``) Pydantic 모델.

API_DESIGN.md §3 단일 테스트 API 참조.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

PromptType = Literal["text", "chat"]
PromptSourceKind = Literal["langfuse", "inline"]


class PromptSource(BaseModel):
    """프롬프트 소스 — Langfuse 등록 프롬프트 또는 inline.

    검증 규칙(API_DESIGN.md §3.1):
    - ``source=langfuse`` → ``name`` 필수. ``version``/``label`` 중 하나로 특정.
    - ``source=inline`` → ``body`` 필수. ``type``으로 text/chat 구분.
    """

    model_config = ConfigDict(extra="forbid")

    source: PromptSourceKind = Field(..., description="langfuse | inline")
    name: str | None = Field(default=None, description="Langfuse 프롬프트 이름")
    version: int | None = Field(default=None, ge=1, description="Langfuse 프롬프트 버전")
    label: str | None = Field(default=None, description="Langfuse 라벨 (production 등)")
    body: str | list[dict[str, Any]] | None = Field(
        default=None, description="inline 프롬프트 본문 (text=str, chat=list[dict])"
    )
    type: PromptType = Field(default="text", description="text 또는 chat")

    @model_validator(mode="after")
    def _validate_source_consistency(self) -> Self:
        """source-필드 일관성 검증."""
        if self.source == "langfuse":
            if not self.name:
                raise ValueError("source=langfuse 일 때 name 필수")
        elif self.source == "inline":
            if self.body is None:
                raise ValueError("source=inline 일 때 body 필수")
            if self.type == "chat" and not isinstance(self.body, list):
                raise ValueError("type=chat 이면 body는 messages list 이어야 함")
            if self.type == "text" and not isinstance(self.body, str):
                raise ValueError("type=text 이면 body는 string 이어야 함")
        return self


class SingleTestRequest(BaseModel):
    """``POST /api/v1/tests/single`` 요청 body."""

    model_config = ConfigDict(extra="forbid")

    project_id: str = Field(..., min_length=1, description="대상 프로젝트 ID")
    prompt: PromptSource = Field(..., description="프롬프트 소스")
    variables: dict[str, Any] = Field(
        default_factory=dict, description="``{{var}}`` 치환용 변수 dict"
    )
    model: str = Field(..., min_length=1, description="LiteLLM 등록 모델 이름")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="모델 파라미터 (temperature, top_p, max_tokens 등)",
    )
    stream: bool = Field(default=True, description="True=SSE, False=JSON 응답")
    evaluators: list[dict[str, Any]] | None = Field(
        default=None,
        description="평가자 목록 (Phase 5에서 활성화 예정 — 현재는 무시)",
    )
    system_prompt: str | None = Field(default=None, description="옵션 system 프롬프트")


class SingleTestUsage(BaseModel):
    """LLM 호출 토큰 사용량."""

    input_tokens: int = Field(..., ge=0)
    output_tokens: int = Field(..., ge=0)
    total_tokens: int = Field(..., ge=0)


class SingleTestResponseMeta(BaseModel):
    """non-streaming 모드 응답 본문 (API_DESIGN.md §3.1)."""

    trace_id: str
    model: str
    output: str | list[dict[str, Any]]
    usage: SingleTestUsage
    latency_ms: float = Field(..., ge=0)
    cost_usd: float = Field(..., ge=0)
    started_at: datetime
    completed_at: datetime
