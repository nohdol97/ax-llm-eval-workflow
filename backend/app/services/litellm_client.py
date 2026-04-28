"""LiteLLM Proxy HTTP 클라이언트.

본 프로젝트 정책상 LiteLLM은 Proxy(Virtual Key 인증)만 호출한다. SDK 호출이 아닌
직접 HTTP 호출로 일관된 인증/관측성을 보장한다.

- ``health_check()`` — ``GET /health``
- ``model_info()`` — ``GET /model/info``
- ``completion()`` — ``POST /chat/completions`` (stream 지원)
- ``embedding()`` — ``POST /embeddings``

retry: tenacity (max 3, exponential backoff). ``health_check``는 retry 미적용.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.errors import LiteLLMError
from app.core.logging import get_logger
from app.models.health import ServiceHealth

logger = get_logger(__name__)

_retry_policy = retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.2, min=0.2, max=2.0),
    retry=retry_if_exception_type((httpx.HTTPError, LiteLLMError)),
)


class LiteLLMClient:
    """LiteLLM Proxy HTTP 클라이언트.

    Args:
        base_url: LiteLLM Proxy base URL (e.g. ``https://litellm.internal.example.com``)
        virtual_key: LiteLLM Virtual Key (Bearer 인증)
        timeout: 호출 타임아웃 (초)
    """

    def __init__(
        self,
        base_url: str,
        virtual_key: SecretStr,
        *,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/") if base_url else ""
        self._virtual_key = virtual_key
        self._timeout = timeout

    # ---------- 공통 ----------
    def _headers(self) -> dict[str, str]:
        """``Authorization: Bearer ...`` 헤더."""
        key_value = self._virtual_key.get_secret_value()
        if not key_value:
            return {"Content-Type": "application/json"}
        return {
            "Authorization": f"Bearer {key_value}",
            "Content-Type": "application/json",
        }

    def _ensure_configured(self) -> None:
        if not self._base_url:
            raise LiteLLMError(detail="LITELLM_BASE_URL이 설정되지 않았습니다.")
        if not self._virtual_key.get_secret_value():
            raise LiteLLMError(detail="LITELLM_VIRTUAL_KEY가 설정되지 않았습니다.")

    # ---------- Health ----------
    async def health_check(self) -> ServiceHealth:
        """``GET /health`` 호출 — retry 없음."""
        if not self._base_url:
            return ServiceHealth(
                status="warn",
                endpoint=None,
                detail="LITELLM_BASE_URL not configured",
                checked_at=datetime.now(UTC),
            )

        endpoint = f"{self._base_url}/health"
        start = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(endpoint, headers=self._headers())
            latency_ms = (time.perf_counter() - start) * 1000.0
            if 200 <= resp.status_code < 300:
                return ServiceHealth(
                    status="ok",
                    latency_ms=latency_ms,
                    endpoint=endpoint,
                    checked_at=datetime.now(UTC),
                )
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=endpoint,
                detail=f"HTTP {resp.status_code}",
                checked_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=endpoint,
                detail=str(exc),
                checked_at=datetime.now(UTC),
            )

    # ---------- Model info ----------
    @_retry_policy
    async def model_info(self) -> list[dict[str, Any]]:
        """``GET /model/info`` 응답의 ``data`` 배열 반환."""
        self._ensure_configured()
        endpoint = f"{self._base_url}/model/info"
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(endpoint, headers=self._headers())
            if resp.status_code >= 400:
                raise LiteLLMError(detail=f"model_info HTTP {resp.status_code}: {resp.text}")
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise LiteLLMError(detail=f"model_info 호출 실패: {exc}") from exc
        if isinstance(payload, dict) and "data" in payload:
            return list(payload["data"])
        if isinstance(payload, list):
            return list(payload)
        return []

    # ---------- Completion ----------
    @_retry_policy
    async def _completion_nonstream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **params: Any,
    ) -> dict[str, Any]:
        """non-streaming completion 호출."""
        self._ensure_configured()
        endpoint = f"{self._base_url}/chat/completions"
        body: dict[str, Any] = {"model": model, "messages": messages, **params}
        body["stream"] = False
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(endpoint, json=body, headers=self._headers())
            if resp.status_code >= 400:
                raise LiteLLMError(detail=f"completion HTTP {resp.status_code}: {resp.text}")
            return dict(resp.json())
        except httpx.HTTPError as exc:
            raise LiteLLMError(detail=f"completion 호출 실패: {exc}") from exc

    async def _completion_stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        **params: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """streaming completion 호출 — SSE chunk yield."""
        self._ensure_configured()
        endpoint = f"{self._base_url}/chat/completions"
        body: dict[str, Any] = {"model": model, "messages": messages, **params}
        body["stream"] = True

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", endpoint, json=body, headers=self._headers()) as resp:
                if resp.status_code >= 400:
                    text = await resp.aread()
                    raise LiteLLMError(
                        detail=f"completion(stream) HTTP {resp.status_code}: "
                        f"{text.decode('utf-8', errors='ignore')}"
                    )
                async for raw_line in resp.aiter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if line.startswith("data:"):
                        line = line[len("data:") :].strip()
                    if line == "[DONE]":
                        return
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        # 비-JSON 라인은 스킵 (heartbeat 등)
                        continue

    async def completion(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        stream: bool = False,
        **params: Any,
    ) -> dict[str, Any] | AsyncIterator[dict[str, Any]]:
        """``POST /chat/completions`` 호출.

        Args:
            model: 모델 ID (LiteLLM 등록명)
            messages: ``[{"role": "user", "content": "..."}]``
            stream: True면 SSE chunk async iterator 반환
            **params: temperature / max_tokens 등 추가 파라미터
        """
        if stream:
            return self._completion_stream(model, messages, **params)
        return await self._completion_nonstream(model, messages, **params)

    # ---------- Embedding ----------
    @_retry_policy
    async def embedding(
        self,
        model: str,
        input: str | list[str],  # noqa: A002
    ) -> dict[str, Any]:
        """``POST /embeddings`` 호출."""
        self._ensure_configured()
        endpoint = f"{self._base_url}/embeddings"
        body = {"model": model, "input": input}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(endpoint, json=body, headers=self._headers())
            if resp.status_code >= 400:
                raise LiteLLMError(detail=f"embedding HTTP {resp.status_code}: {resp.text}")
            return dict(resp.json())
        except httpx.HTTPError as exc:
            raise LiteLLMError(detail=f"embedding 호출 실패: {exc}") from exc

    # ---------- 비용 계산 ----------
    @staticmethod
    def extract_cost(response: dict[str, Any]) -> float | None:
        """LiteLLM 응답에서 비용($) 추출.

        우선순위: ``_litellm_cost`` 필드 > 내부 ``usage`` 기반 계산은 미지원
        (Phase 3에서 LiteLLM SDK ``completion_cost()`` 활용 예정).
        """
        cost = response.get("_litellm_cost")
        if cost is not None:
            try:
                return float(cost)
            except (TypeError, ValueError):
                return None
        return None
