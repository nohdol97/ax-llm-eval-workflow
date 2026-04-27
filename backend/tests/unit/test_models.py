"""모델 라우터 + 변환 함수 단위 테스트.

검증:
- ``transform_model_info``의 LiteLLM 응답 → ProviderGroup 변환
- 프로바이더 그룹핑 (azure / openai / anthropic / google / bedrock)
- 비전/스트리밍/function_calling capability 추출
- 토큰당 비용 → 1K 토큰당 비용 환산
- ``GET /api/v1/models`` 인증 + Cache-Control 헤더
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.v1.models import _detect_provider, _detect_vision, transform_model_info
from app.core.deps import get_litellm_client
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User


# ---------- 단위: 프로바이더/비전 감지 ----------
@pytest.mark.unit
class TestDetectProvider:
    """``_detect_provider`` — 모델 ID에서 프로바이더 추론."""

    def test_azure_prefix(self) -> None:
        assert _detect_provider("azure/gpt-4o") == "azure"

    def test_anthropic_prefix(self) -> None:
        assert _detect_provider("anthropic/claude-3-5-sonnet") == "anthropic"

    def test_vertex_ai_maps_to_google(self) -> None:
        assert _detect_provider("vertex_ai/gemini-2.0-pro") == "google"

    def test_bedrock_prefix(self) -> None:
        assert _detect_provider("bedrock/anthropic.claude-3-5") == "bedrock"

    def test_openai_prefix(self) -> None:
        assert _detect_provider("openai/gpt-4o") == "openai"

    def test_groq_prefix(self) -> None:
        assert _detect_provider("groq/llama-3.3-70b") == "groq"

    def test_pattern_fallback_claude(self) -> None:
        """prefix 없는 ``claude-3-5-sonnet`` → anthropic."""
        assert _detect_provider("claude-3-5-sonnet") == "anthropic"

    def test_pattern_fallback_gemini(self) -> None:
        """``gemini-2.0`` → google."""
        assert _detect_provider("gemini-2.0-pro") == "google"

    def test_unknown_falls_back_to_other(self) -> None:
        assert _detect_provider("custom-model-xyz") == "other"


@pytest.mark.unit
class TestDetectVision:
    """``_detect_vision`` — vision 지원 여부 추론."""

    def test_explicit_supports_vision_field(self) -> None:
        assert _detect_vision("any/model", {"supports_vision": True}) is True

    def test_capabilities_list(self) -> None:
        assert _detect_vision("any/model", {"capabilities": ["vision", "stream"]}) is True

    def test_pattern_gpt_4o(self) -> None:
        assert _detect_vision("azure/gpt-4o", {}) is True

    def test_pattern_claude_3(self) -> None:
        assert _detect_vision("anthropic/claude-3-5", {}) is True

    def test_no_vision_default(self) -> None:
        assert _detect_vision("groq/llama-3-70b", {}) is False


# ---------- 단위: transform_model_info ----------
@pytest.mark.unit
class TestTransformModelInfo:
    """LiteLLM 응답 → ``ModelListResponse`` 변환."""

    def test_basic_grouping(self) -> None:
        """프로바이더별로 그룹핑."""
        raw = [
            {
                "model_name": "GPT-4o",
                "litellm_params": {"model": "azure/gpt-4o"},
                "model_info": {
                    "max_tokens": 128000,
                    "input_cost_per_token": 0.0000025,
                    "output_cost_per_token": 0.00001,
                    "supports_function_calling": True,
                },
            },
            {
                "model_name": "Claude 3.5 Sonnet",
                "litellm_params": {"model": "anthropic/claude-3-5-sonnet"},
                "model_info": {
                    "max_tokens": 200000,
                    "input_cost_per_token": 0.000003,
                    "output_cost_per_token": 0.000015,
                },
            },
        ]
        result = transform_model_info(raw)
        provider_ids = [g.id for g in result.providers]
        assert "azure" in provider_ids
        assert "anthropic" in provider_ids

    def test_provider_order_preserved(self) -> None:
        """``PROVIDER_ORDER`` 순서로 정렬."""
        raw = [
            {
                "model_name": "g1",
                "litellm_params": {"model": "anthropic/claude-3"},
                "model_info": {},
            },
            {
                "model_name": "g2",
                "litellm_params": {"model": "azure/gpt-4o"},
                "model_info": {},
            },
        ]
        result = transform_model_info(raw)
        # azure가 anthropic보다 먼저 나와야 함
        ids = [g.id for g in result.providers]
        assert ids.index("azure") < ids.index("anthropic")

    def test_cost_conversion_per_k(self) -> None:
        """토큰당 비용 → 1K 토큰당 비용으로 ×1000 환산."""
        raw = [
            {
                "model_name": "test",
                "litellm_params": {"model": "openai/gpt-4o"},
                "model_info": {
                    "input_cost_per_token": 0.0000025,
                    "output_cost_per_token": 0.00001,
                },
            }
        ]
        result = transform_model_info(raw)
        model = result.providers[0].models[0]
        assert model.input_cost_per_k == pytest.approx(0.0025, abs=1e-9)
        assert model.output_cost_per_k == pytest.approx(0.01, abs=1e-9)

    def test_vision_detection_from_pattern(self) -> None:
        """vision 키워드 모델은 자동 감지."""
        raw = [
            {
                "model_name": "GPT-4o",
                "litellm_params": {"model": "azure/gpt-4o"},
                "model_info": {},
            },
        ]
        result = transform_model_info(raw)
        assert result.providers[0].models[0].vision is True

    def test_capabilities_includes_streaming(self) -> None:
        """streaming은 명시적 false가 아니면 기본 포함."""
        raw = [
            {
                "model_name": "X",
                "litellm_params": {"model": "openai/gpt"},
                "model_info": {},
            }
        ]
        result = transform_model_info(raw)
        caps = result.providers[0].models[0].capabilities
        assert "streaming" in caps

    def test_function_calling_capability(self) -> None:
        raw = [
            {
                "model_name": "X",
                "litellm_params": {"model": "openai/gpt"},
                "model_info": {"supports_function_calling": True},
            }
        ]
        result = transform_model_info(raw)
        caps = result.providers[0].models[0].capabilities
        assert "function_calling" in caps

    def test_empty_response(self) -> None:
        result = transform_model_info([])
        assert result.providers == []

    def test_skips_entries_without_model_id(self) -> None:
        """model_id가 없는 entry는 무시."""
        raw: list[dict[str, Any]] = [{"model_name": ""}, {}]
        result = transform_model_info(raw)
        assert result.providers == []

    def test_context_window_extraction(self) -> None:
        raw = [
            {
                "model_name": "X",
                "litellm_params": {"model": "openai/gpt"},
                "model_info": {"max_input_tokens": 200000},
            }
        ]
        result = transform_model_info(raw)
        assert result.providers[0].models[0].context_window == 200000

    def test_unknown_provider_goes_to_other(self) -> None:
        """알려진 ``PROVIDER_ORDER``에 없는 프로바이더는 ``other`` 그룹으로."""
        raw = [
            {
                "model_name": "Strange",
                "litellm_params": {"model": "strange_provider/model"},
                "model_info": {},
            }
        ]
        result = transform_model_info(raw)
        assert any(g.id == "other" for g in result.providers)


# ---------- 통합: 라우터 ----------
class _StubLiteLLM:
    """``model_info()``만 노출하는 최소 stub."""

    def __init__(self, data: list[dict[str, Any]]) -> None:
        self._data = data

    async def model_info(self) -> list[dict[str, Any]]:
        return self._data


@pytest.fixture
def viewer_user() -> User:
    return User(id="user-1", email="v@x.com", role="viewer")


@pytest.fixture
def app_with_models(viewer_user: User) -> TestClient:
    """``/models`` 라우터를 stub LiteLLM과 인증 우회로 연결."""
    raw = [
        {
            "model_name": "GPT-4o",
            "litellm_params": {"model": "azure/gpt-4o"},
            "model_info": {
                "max_tokens": 128000,
                "input_cost_per_token": 0.0000025,
                "output_cost_per_token": 0.00001,
            },
        }
    ]
    app = create_app()
    app.dependency_overrides[get_litellm_client] = lambda: _StubLiteLLM(raw)
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    return TestClient(app)


@pytest.mark.unit
class TestModelsEndpoint:
    """``GET /api/v1/models`` 통합."""

    def test_returns_200_and_providers(self, app_with_models: TestClient) -> None:
        resp = app_with_models.get("/api/v1/models")
        assert resp.status_code == 200
        body = resp.json()
        assert "providers" in body
        assert any(g["id"] == "azure" for g in body["providers"])

    def test_cache_control_header(self, app_with_models: TestClient) -> None:
        resp = app_with_models.get("/api/v1/models")
        assert resp.headers.get("cache-control") == "private, max-age=300"

    def test_unauthenticated_request_rejected(self) -> None:
        """인증 없이 호출 시 401."""
        app = create_app()
        app.dependency_overrides[get_litellm_client] = lambda: _StubLiteLLM([])
        client = TestClient(app)
        resp = client.get("/api/v1/models")
        assert resp.status_code == 401
