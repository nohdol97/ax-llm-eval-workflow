"""Redis 비동기 클라이언트 래퍼.

- ``redis.asyncio.Redis`` 위에 본 프로젝트 정책을 강제
  - 모든 키는 ``ax:`` prefix 강제
  - 헬스 체크 (``PING``) 지원
- 기본 메서드: get / set / expire / delete / incr / scan_iter / eval / pipeline
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app.core.errors import RedisClientError
from app.models.health import ServiceHealth

# 본 프로젝트 키 prefix — Labs 키 네임스페이스
KEY_PREFIX = "ax:"


class RedisClient:
    """Redis async 클라이언트.

    실제 ``redis.asyncio.Redis``를 위임 호출하며, 모든 키에 ``ax:`` prefix를 적용한다.
    """

    def __init__(self, url: str, *, decode_responses: bool = True) -> None:
        """Redis URL로 연결 인스턴스 생성.

        Args:
            url: ``redis://host:port/db`` 형식
            decode_responses: True면 str ↔ str 인터페이스 (기본 권장)
        """
        self._url = url
        self._client = Redis.from_url(url, decode_responses=decode_responses)

    # ---------- 헬퍼 ----------
    @staticmethod
    def _full_key(key: str) -> str:
        """``ax:`` prefix 강제. 이미 붙어 있으면 그대로 반환."""
        return key if key.startswith(KEY_PREFIX) else f"{KEY_PREFIX}{key}"

    @property
    def underlying(self) -> Redis:
        """실제 ``redis.asyncio.Redis`` 인스턴스 (주의: prefix 미적용)."""
        return self._client

    # ---------- KV ----------
    async def get(self, key: str) -> str | bytes | None:
        """``GET`` (prefix 자동 적용)."""
        try:
            return await self._client.get(self._full_key(key))
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis GET 실패: {exc}") from exc

    async def set(  # noqa: A003 — Redis API 시그니처 일치
        self,
        key: str,
        value: str | bytes | int | float,
        *,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None:
        """``SET key value [EX][PX][NX|XX]``."""
        try:
            return await self._client.set(
                self._full_key(key), value, ex=ex, px=px, nx=nx, xx=xx
            )
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis SET 실패: {exc}") from exc

    async def delete(self, *keys: str) -> int:
        """``DEL`` (prefix 자동 적용)."""
        if not keys:
            return 0
        full = [self._full_key(k) for k in keys]
        try:
            return await self._client.delete(*full)
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis DEL 실패: {exc}") from exc

    async def expire(self, key: str, seconds: int) -> bool:
        """``EXPIRE``."""
        try:
            return bool(await self._client.expire(self._full_key(key), seconds))
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis EXPIRE 실패: {exc}") from exc

    async def ttl(self, key: str) -> int:
        """``TTL``."""
        try:
            return int(await self._client.ttl(self._full_key(key)))
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis TTL 실패: {exc}") from exc

    async def incr(self, key: str, amount: int = 1) -> int:
        """``INCRBY``."""
        try:
            return int(await self._client.incrby(self._full_key(key), amount))
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis INCR 실패: {exc}") from exc

    async def exists(self, *keys: str) -> int:
        """``EXISTS``."""
        if not keys:
            return 0
        full = [self._full_key(k) for k in keys]
        try:
            return int(await self._client.exists(*full))
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis EXISTS 실패: {exc}") from exc

    # ---------- Scan ----------
    async def scan_iter(
        self,
        match: str | None = None,
        count: int | None = None,
    ) -> AsyncIterator[str]:
        """``SCAN MATCH pattern`` — prefix 자동 적용."""
        full_match = self._full_key(match) if match else f"{KEY_PREFIX}*"
        async for key in self._client.scan_iter(match=full_match, count=count):
            yield key if isinstance(key, str) else key.decode("utf-8")

    # ---------- Lua Eval ----------
    async def eval(  # noqa: A003 — Redis API 시그니처 일치
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        """``EVAL`` — KEYS 인자에는 prefix를 자동 적용."""
        # numkeys만큼은 prefix 적용 후 args 전달
        keys = [self._full_key(str(k)) for k in keys_and_args[:numkeys]]
        args = list(keys_and_args[numkeys:])
        try:
            return await self._client.eval(script, numkeys, *keys, *args)
        except Exception as exc:  # noqa: BLE001
            raise RedisClientError(detail=f"Redis EVAL 실패: {exc}") from exc

    # ---------- Pipeline ----------
    def pipeline(self, *, transaction: bool = True) -> Any:
        """``pipeline()`` — 주의: 사용자가 prefix를 직접 관리해야 함."""
        return self._client.pipeline(transaction=transaction)

    # ---------- 라이프사이클 ----------
    async def ping(self) -> bool:
        """``PING``."""
        return bool(await self._client.ping())

    async def aclose(self) -> None:
        """연결 종료."""
        try:
            await self._client.aclose()
        except AttributeError:  # pragma: no cover
            close = getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    # ---------- 헬스 체크 ----------
    async def health_check(self) -> ServiceHealth:
        """``PING`` 기반 헬스 체크."""
        start = time.perf_counter()
        try:
            ok = await self.ping()
            latency_ms = (time.perf_counter() - start) * 1000.0
            if ok:
                return ServiceHealth(
                    status="ok",
                    latency_ms=latency_ms,
                    endpoint=self._url,
                    detail=None,
                    checked_at=datetime.now(UTC),
                )
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=self._url,
                detail="PING returned non-truthy",
                checked_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = (time.perf_counter() - start) * 1000.0
            return ServiceHealth(
                status="error",
                latency_ms=latency_ms,
                endpoint=self._url,
                detail=str(exc),
                checked_at=datetime.now(UTC),
            )
