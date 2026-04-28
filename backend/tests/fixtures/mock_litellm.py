"""LiteLLM Proxy 클라이언트 Mock.

실제 LiteLLM Proxy HTTP API와 호환되는 인메모리 mock.
``completion``, ``embedding``, ``health``, ``model_info`` 메서드를 제공한다.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator
from typing import Any


class MockLiteLLMError(Exception):
    """LiteLLM mock에서 발생하는 일반 에러."""


# 기본 시드 모델 목록 (LiteLLM Proxy ``/model/info`` 응답 형태)
_DEFAULT_MODELS = [
    {"model_name": "gpt-4o", "litellm_params": {"model": "openai/gpt-4o"}},
    {"model_name": "gpt-4o-mini", "litellm_params": {"model": "openai/gpt-4o-mini"}},
    {"model_name": "claude-opus-4", "litellm_params": {"model": "anthropic/claude-opus-4"}},
    {"model_name": "claude-sonnet-4", "litellm_params": {"model": "anthropic/claude-sonnet-4"}},
    {"model_name": "gemini-2.0-pro", "litellm_params": {"model": "vertex_ai/gemini-2.0-pro"}},
    {"model_name": "gemini-2.0-flash", "litellm_params": {"model": "vertex_ai/gemini-2.0-flash"}},
    {"model_name": "llama-3.3-70b", "litellm_params": {"model": "groq/llama-3.3-70b"}},
    {
        "model_name": "embedding-ada-002",
        "litellm_params": {"model": "openai/text-embedding-ada-002"},
    },
]


class MockLiteLLMProxy:
    """LiteLLM Proxy HTTP API mock.

    실제 ``services/litellm_client.py``에서 사용하는 ``httpx.AsyncClient`` 호출을
    이 클래스의 메서드로 대체한다.
    """

    def __init__(self) -> None:
        self._healthy = True
        self._response_content: str | None = None
        self._failure: Exception | None = None
        self._latency_ms: int = 0
        self._calls: list[dict[str, Any]] = []
        self._models: list[dict[str, Any]] = list(_DEFAULT_MODELS)

    # ---------- API 메서드 ----------
    async def health(self) -> dict[str, Any]:
        """``GET /health`` 응답 mock."""
        if not self._healthy:
            return {"status": "unhealthy", "reason": "mock-set-unhealthy"}
        return {"status": "healthy"}

    async def model_info(self) -> dict[str, Any]:
        """``GET /model/info`` 응답 mock."""
        return {"data": list(self._models)}

    async def completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        **params: Any,
    ) -> dict[str, Any] | AsyncIterator[dict[str, Any]]:
        """``POST /chat/completions`` mock.

        - ``stream=False``: 단일 dict 응답
        - ``stream=True``: AsyncIterator로 토큰 chunk 반환
        - ``set_failure()`` 시 해당 예외 raise
        - ``set_response()`` 시 명시 content 반환, 아니면 messages hash 기반 결정론적 응답
        """
        self._calls.append(
            {
                "model": model,
                "messages": list(messages),
                "stream": stream,
                "params": dict(params),
            }
        )

        if self._failure is not None:
            raise self._failure

        if self._latency_ms > 0:
            await asyncio.sleep(self._latency_ms / 1000.0)

        content = self._response_content
        if content is None:
            # 결정론적 mock — input 해시 기반 8자 응답 prefix
            seed = "|".join(f"{m.get('role', '')}:{m.get('content', '')}" for m in messages)
            digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
            content = f"mock response [{digest}] for model={model}"

        usage = {
            "prompt_tokens": sum(len(str(m.get("content", ""))) // 4 for m in messages),
            "completion_tokens": max(len(content) // 4, 1),
            "total_tokens": 0,
        }
        usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

        if stream:
            return self._stream_chunks(model, content)

        return {
            "id": f"mock-{hashlib.sha256(content.encode()).hexdigest()[:12]}",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": usage,
            "_litellm_cost": 0.0023,
        }

    async def _stream_chunks(
        self,
        model: str,
        content: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """스트리밍 응답 토큰 단위 chunk yield."""
        tokens = content.split(" ")
        for i, tok in enumerate(tokens):
            chunk_content = tok if i == 0 else " " + tok
            yield {
                "id": f"mock-stream-{i}",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": chunk_content},
                        "finish_reason": None,
                    }
                ],
            }
        yield {
            "id": "mock-stream-final",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }

    async def embedding(
        self,
        model: str,
        input: str | list[str],  # noqa: A002 — LiteLLM SDK 시그니처 일치
    ) -> dict[str, Any]:
        """``POST /embeddings`` mock. 결정론적 8차원 벡터 반환."""
        if self._failure is not None:
            raise self._failure

        inputs = [input] if isinstance(input, str) else list(input)
        data = []
        for idx, text in enumerate(inputs):
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            # 8차원 벡터 (각 byte를 [0,1)로 정규화)
            vector = [b / 255.0 for b in digest[:8]]
            data.append({"object": "embedding", "index": idx, "embedding": vector})

        return {
            "object": "list",
            "data": data,
            "model": model,
            "usage": {
                "prompt_tokens": sum(len(t) // 4 for t in inputs),
                "total_tokens": sum(len(t) // 4 for t in inputs),
            },
        }

    # ---------- 테스트 제어 ----------
    def set_response(self, content: str) -> None:
        """다음 ``completion`` 호출의 응답 content 고정."""
        self._response_content = content

    def clear_response(self) -> None:
        """고정 응답 해제."""
        self._response_content = None

    def set_failure(self, exc: Exception) -> None:
        """다음 호출에서 raise할 예외 지정."""
        self._failure = exc

    def clear_failure(self) -> None:
        """예외 지정 해제."""
        self._failure = None

    def set_latency(self, ms: int) -> None:
        """``completion`` 호출 지연 (ms) 설정."""
        self._latency_ms = max(0, ms)

    def set_unhealthy(self) -> None:
        """헬스 강제 unhealthy."""
        self._healthy = False

    def set_healthy(self) -> None:
        """헬스 복원."""
        self._healthy = True

    def register_model(self, model_name: str, litellm_model: str) -> None:
        """추가 모델 등록 (테스트용)."""
        self._models.append(
            {
                "model_name": model_name,
                "litellm_params": {"model": litellm_model},
            }
        )

    # ---------- 검증 헬퍼 ----------
    def _get_calls(self) -> list[dict[str, Any]]:
        """호출 이력 반환."""
        return list(self._calls)

    def _clear_calls(self) -> None:
        """호출 이력 초기화."""
        self._calls.clear()
