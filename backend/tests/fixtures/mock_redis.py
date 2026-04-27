"""Redis 클라이언트 Mock.

``fakeredis``의 ``FakeAsyncRedis``를 래핑하여 본 프로젝트 Redis client interface
(set/get/expire/incr/scan_iter/eval Lua 스크립트/pubsub 등)와 동일한 형태로 노출한다.

기본 ``decode_responses=True`` (UTF-8) — Phase 2 코드는 str ↔ str 인터페이스를 가정.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import fakeredis


class MockRedisClient:
    """fakeredis 기반 Redis async client mock.

    실제 ``redis.asyncio.Redis`` 인스턴스가 노출하는 주요 메서드를 위임 형태로 제공한다.
    fakeredis가 지원하는 메서드는 ``__getattr__``로 자동 프록시한다.
    """

    def __init__(self, decode_responses: bool = True) -> None:
        # fakeredis FakeAsyncRedis는 redis.asyncio.Redis와 동일 인터페이스
        self._client = fakeredis.FakeAsyncRedis(decode_responses=decode_responses)
        self._decode_responses = decode_responses

    # ---------- 핵심 KV ----------
    async def set(  # noqa: A003 — Redis API 시그니처 일치
        self,
        key: str,
        value: str | bytes | int | float,
        ex: int | None = None,
        px: int | None = None,
        nx: bool = False,
        xx: bool = False,
    ) -> bool | None:
        """SET key value [EX seconds] [PX milliseconds] [NX|XX]."""
        return await self._client.set(key, value, ex=ex, px=px, nx=nx, xx=xx)

    async def get(self, key: str) -> str | bytes | None:
        """GET key."""
        return await self._client.get(key)

    async def delete(self, *keys: str) -> int:
        """DEL key [key ...]."""
        return await self._client.delete(*keys)

    async def exists(self, *keys: str) -> int:
        """EXISTS key [key ...]."""
        return await self._client.exists(*keys)

    async def expire(self, key: str, seconds: int) -> bool:
        """EXPIRE key seconds."""
        return await self._client.expire(key, seconds)

    async def ttl(self, key: str) -> int:
        """TTL key."""
        return await self._client.ttl(key)

    async def incr(self, key: str, amount: int = 1) -> int:
        """INCRBY key amount."""
        return await self._client.incrby(key, amount)

    async def decr(self, key: str, amount: int = 1) -> int:
        """DECRBY key amount."""
        return await self._client.decrby(key, amount)

    # ---------- Hash ----------
    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: Any = None,
        mapping: dict[str, Any] | None = None,
    ) -> int:
        """HSET name field value 또는 HSET name mapping."""
        return await self._client.hset(name, key=key, value=value, mapping=mapping)

    async def hget(self, name: str, key: str) -> str | bytes | None:
        """HGET name field."""
        return await self._client.hget(name, key)

    async def hgetall(self, name: str) -> dict[str, Any]:
        """HGETALL name."""
        return await self._client.hgetall(name)

    # ---------- Scan ----------
    async def scan_iter(
        self,
        match: str | None = None,
        count: int | None = None,
    ) -> AsyncIterator[str]:
        """SCAN MATCH pattern COUNT count — async iterator로 반환."""
        async for key in self._client.scan_iter(match=match, count=count):
            yield key

    # ---------- Eval Lua ----------
    async def eval(  # noqa: A003 — Redis API 시그니처 일치
        self,
        script: str,
        numkeys: int,
        *keys_and_args: Any,
    ) -> Any:
        """EVAL script numkeys key [key ...] arg [arg ...]."""
        return await self._client.eval(script, numkeys, *keys_and_args)

    # ---------- PubSub ----------
    def pubsub(self) -> Any:
        """``pubsub()`` 객체 반환 (fakeredis 위임)."""
        return self._client.pubsub()

    async def publish(self, channel: str, message: str) -> int:
        """PUBLISH channel message."""
        return await self._client.publish(channel, message)

    # ---------- 라이프사이클 ----------
    async def ping(self) -> bool:
        """PING — fakeredis는 항상 True."""
        return await self._client.ping()

    async def aclose(self) -> None:
        """연결 종료. fakeredis는 close 메서드를 가질 수도 있음."""
        try:
            await self._client.aclose()
        except AttributeError:  # pragma: no cover — fakeredis 버전 호환
            close = getattr(self._client, "close", None)
            if close is not None:
                result = close()
                if hasattr(result, "__await__"):
                    await result

    # ---------- 검증 헬퍼 ----------
    async def _get_keys_with_prefix(self, prefix: str) -> list[str]:
        """주어진 prefix(예: ``ax:``)로 시작하는 모든 키 반환."""
        result: list[str] = []
        async for key in self._client.scan_iter(match=f"{prefix}*"):
            result.append(key if isinstance(key, str) else key.decode("utf-8"))
        return sorted(result)

    # ---------- fakeredis 위임 ----------
    def __getattr__(self, name: str) -> Any:
        """미정의 메서드는 fakeredis로 위임."""
        return getattr(self._client, name)
