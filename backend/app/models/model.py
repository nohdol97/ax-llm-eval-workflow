"""모델 카탈로그 도메인 모델.

LiteLLM Proxy ``/model/info`` 응답을 본 프로젝트의 프로바이더 그룹 응답 형식으로
변환할 때 사용한다. 본 모델들은 응답 직렬화 전용이며, 외부 SDK에 직접 의존하지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# 본 프로젝트가 식별 가능한 프로바이더 ID — 표시 이름 매핑은 ``PROVIDER_DISPLAY``
ProviderID = Literal[
    "azure",
    "openai",
    "anthropic",
    "google",
    "bedrock",
    "groq",
    "vertex",
    "other",
]


class ModelInfo(BaseModel):
    """단일 모델 메타.

    LiteLLM 응답의 ``model_info`` / ``litellm_params``에서 추출 가능한 항목을
    합성하여 본 프로젝트 응답 스키마로 정규화한다.
    """

    id: str = Field(..., description="모델 고유 ID (LiteLLM 등록명, 예: ``azure/gpt-4o``)")
    name: str = Field(..., description="표시명 (예: ``GPT-4o``)")
    provider: ProviderID = Field(..., description="프로바이더 ID")
    vision: bool = Field(False, description="비전(이미지 입력) 지원 여부")
    context_window: int = Field(0, description="최대 컨텍스트 윈도우 토큰 수")
    input_cost_per_k: float = Field(
        0.0,
        description="입력 1K 토큰당 비용 (USD). LiteLLM은 토큰당 비용을 노출하므로 ×1000 환산.",
    )
    output_cost_per_k: float = Field(
        0.0, description="출력 1K 토큰당 비용 (USD)."
    )
    capabilities: list[str] = Field(
        default_factory=list,
        description="능력 태그 (``streaming``, ``function_calling``, ``vision`` 등)",
    )


class ProviderGroup(BaseModel):
    """프로바이더 그룹.

    UI에서 좌측 트리 형태로 표시하기 위해 프로바이더 단위로 그룹핑한다.
    """

    id: ProviderID = Field(..., description="프로바이더 ID")
    name: str = Field(..., description="프로바이더 표시명")
    models: list[ModelInfo] = Field(
        default_factory=list, description="해당 프로바이더의 모델 목록"
    )


class ModelListResponse(BaseModel):
    """``GET /api/v1/models`` 응답."""

    providers: list[ProviderGroup] = Field(
        default_factory=list, description="프로바이더 그룹 목록 (정렬 보장)"
    )


# ---------- 프로바이더 표시명 매핑 ----------
PROVIDER_DISPLAY: dict[str, str] = {
    "azure": "Azure OpenAI",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google",
    "vertex": "Google Vertex AI",
    "bedrock": "AWS Bedrock",
    "groq": "Groq",
    "other": "Other",
}


# 정렬 우선순위 — 응답 안정성을 위해 결정적 순서 사용
PROVIDER_ORDER: tuple[str, ...] = (
    "azure",
    "openai",
    "anthropic",
    "google",
    "vertex",
    "bedrock",
    "groq",
    "other",
)
