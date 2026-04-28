"""``GET /api/v1/models`` — LiteLLM Proxy ``/model/info`` 응답을 본 프로젝트 형식으로 변환.

응답은 프로바이더별로 그룹핑하여 UI 좌측 트리 렌더링이 단순화되도록 한다.

Cache-Control: 정적 카탈로그성 GET이므로 ``private, max-age=300``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response

from app.core.deps import get_litellm_client
from app.core.logging import get_logger
from app.core.security import get_current_user
from app.models.auth import User
from app.models.model import (
    PROVIDER_DISPLAY,
    PROVIDER_ORDER,
    ModelInfo,
    ModelListResponse,
    ProviderGroup,
    ProviderID,
)
from app.services.litellm_client import LiteLLMClient

logger = get_logger(__name__)

router = APIRouter(tags=["models"])


# ---------- 프로바이더 추론 ----------
def _detect_provider(model_id: str) -> ProviderID:
    """모델 ID에서 프로바이더 ID 추론.

    LiteLLM은 일반적으로 ``<provider>/<model>`` 형태로 모델 ID를 노출한다.
    예: ``azure/gpt-4o``, ``anthropic/claude-3-5``, ``vertex_ai/gemini-2.0-pro``.
    """
    lower = model_id.lower()
    if "/" in lower:
        prefix = lower.split("/", 1)[0]
        # vertex_ai → google (UI 표기 일관성)
        if prefix in ("vertex_ai", "vertex"):
            return "google"
        if prefix == "google_genai":
            return "google"
        if prefix == "bedrock":
            return "bedrock"
        if prefix in ("azure", "azure_ai"):
            return "azure"
        if prefix in ("anthropic",):
            return "anthropic"
        if prefix in ("openai",):
            return "openai"
        if prefix in ("groq",):
            return "groq"
        if prefix in PROVIDER_DISPLAY:
            return prefix  # type: ignore[return-value]
    # 패턴 매칭 fallback
    if "claude" in lower:
        return "anthropic"
    if "gemini" in lower:
        return "google"
    if "gpt" in lower or "o1" in lower or "o3" in lower:
        return "azure"  # 본 프로젝트는 OpenAI 모델을 Azure 경유로 운영
    if "llama" in lower:
        return "groq"
    return "other"


def _detect_vision(model_id: str, info: dict[str, Any]) -> bool:
    """모델이 vision 입력을 지원하는지."""
    if info.get("supports_vision") is True:
        return True
    if info.get("vision") is True:
        return True
    capabilities = info.get("capabilities") or []
    if isinstance(capabilities, list) and "vision" in capabilities:
        return True
    lower = model_id.lower()
    # 패턴 fallback — 본 시점 LiteLLM 카탈로그 기준
    vision_keywords = ("vision", "gpt-4o", "claude-3", "claude-opus", "gemini-")
    return any(k in lower for k in vision_keywords)


def _capabilities(info: dict[str, Any], vision: bool) -> list[str]:
    """능력 태그 리스트 생성."""
    caps: list[str] = []
    if info.get("supports_function_calling"):
        caps.append("function_calling")
    if info.get("supports_streaming") is not False:
        # LiteLLM은 대부분 streaming 지원 — 명시적 false만 제외
        caps.append("streaming")
    if vision:
        caps.append("vision")
    if info.get("supports_response_schema"):
        caps.append("response_schema")
    # 중복 제거 + 정렬
    return sorted(set(caps))


def _display_name(model_id: str, info: dict[str, Any]) -> str:
    """표시명 추출 — ``info.display_name`` 우선, 없으면 모델 ID 끝부분."""
    name = info.get("display_name") or info.get("name")
    if name:
        return str(name)
    if "/" in model_id:
        return model_id.split("/", 1)[1]
    return model_id


def _cost_per_k(info: dict[str, Any], key: str) -> float:
    """LiteLLM의 토큰당 비용 → 1K 토큰당 비용으로 환산.

    LiteLLM 응답은 ``input_cost_per_token`` 등 토큰 단위. ×1000.
    응답에 직접 ``cost_per_1k_*``가 있으면 그것을 우선 사용.
    """
    direct = info.get(f"cost_per_1k_{'input' if 'input' in key else 'output'}")
    if direct is not None:
        try:
            return float(direct)
        except (TypeError, ValueError):
            pass
    raw = info.get(key)
    if raw is None:
        return 0.0
    try:
        return float(raw) * 1000.0
    except (TypeError, ValueError):
        return 0.0


def _context_window(info: dict[str, Any]) -> int:
    """컨텍스트 윈도우 토큰 수."""
    for key in ("max_input_tokens", "max_tokens", "context_window"):
        raw = info.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue
    return 0


# ---------- 변환 ----------
def transform_model_info(raw_models: list[dict[str, Any]]) -> ModelListResponse:
    """LiteLLM ``/model/info`` 응답 → ``ModelListResponse``.

    LiteLLM 응답은 일반적으로 다음 형태:
    ``{"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"},
    "model_info": {"max_tokens": 128000, "input_cost_per_token": 0.0000025, ...}}``
    """
    grouped: dict[str, list[ModelInfo]] = {}

    for entry in raw_models:
        # model_id 추출 — litellm_params.model 우선, 없으면 model_name
        litellm_params = entry.get("litellm_params") or {}
        model_id = (
            litellm_params.get("model") or entry.get("model") or entry.get("model_name") or ""
        )
        if not model_id:
            continue

        info = entry.get("model_info") or {}
        # 일부 필드는 entry 최상위에도 존재 가능 — merge
        merged: dict[str, Any] = {**entry, **info}

        provider = _detect_provider(model_id)
        vision = _detect_vision(model_id, merged)
        caps = _capabilities(merged, vision)
        display = entry.get("model_name") or _display_name(model_id, merged)

        model_info = ModelInfo(
            id=model_id,
            name=str(display),
            provider=provider,
            vision=vision,
            context_window=_context_window(merged),
            input_cost_per_k=_cost_per_k(merged, "input_cost_per_token"),
            output_cost_per_k=_cost_per_k(merged, "output_cost_per_token"),
            capabilities=caps,
        )
        grouped.setdefault(provider, []).append(model_info)

    # 결정적 순서로 그룹 정렬
    providers: list[ProviderGroup] = []
    for pid in PROVIDER_ORDER:
        if pid not in grouped:
            continue
        models = sorted(grouped[pid], key=lambda m: m.name.lower())
        providers.append(
            ProviderGroup(
                id=pid,  # type: ignore[arg-type]
                name=PROVIDER_DISPLAY[pid],
                models=models,
            )
        )
    # PROVIDER_ORDER에 없는 그룹은 ``other`` 카테고리로 합침
    leftover = [pid for pid in grouped if pid not in PROVIDER_ORDER]
    if leftover:
        other_models: list[ModelInfo] = []
        for pid in leftover:
            other_models.extend(grouped[pid])
        # 기존 other 그룹 병합
        existing = next((g for g in providers if g.id == "other"), None)
        if existing is not None:
            existing.models = sorted(
                [*existing.models, *other_models], key=lambda m: m.name.lower()
            )
        else:
            providers.append(
                ProviderGroup(
                    id="other",
                    name=PROVIDER_DISPLAY["other"],
                    models=sorted(other_models, key=lambda m: m.name.lower()),
                )
            )

    return ModelListResponse(providers=providers)


# ---------- 라우트 ----------
@router.get(
    "/models",
    response_model=ModelListResponse,
    summary="사용 가능 모델 목록",
)
async def list_models(
    response: Response,
    _user: User = Depends(get_current_user),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> ModelListResponse:
    """LiteLLM Proxy ``/model/info``를 호출하여 프로바이더별 그룹핑된 모델 카탈로그를 반환한다.

    - 권한: viewer 이상 (인증만 요구)
    - 캐시: 5분 (``private, max-age=300``)
    """
    raw = await litellm.model_info()
    payload = transform_model_info(raw)
    response.headers["Cache-Control"] = "private, max-age=300"
    return payload
