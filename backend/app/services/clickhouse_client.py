"""ClickHouse async 클라이언트 + Langfuse public API 폴백.

- 직접 모드 (기본, ``USE_LANGFUSE_PUBLIC_API_FALLBACK=False``)
  - ``clickhouse-connect`` AsyncClient
  - readonly 계정 강제 — INSERT/UPDATE/DELETE는 코드 레벨에서 차단
  - 파라미터화 쿼리만 허용 — f-string/format 패턴 감지 시 ``ClickHouseSecurityError``
  - LIMIT 강제 — 미지정 시 ``LIMIT 10000`` 자동 추가

- 폴백 모드 (``USE_LANGFUSE_PUBLIC_API_FALLBACK=True``)
  - ``LangfuseClient``를 의존으로 받아 일부 메서드만 위임
  - 미지원 메서드는 ``NotImplementedError``
"""

from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings
from app.core.errors import ClickHouseError, ClickHouseSecurityError
from app.core.logging import get_logger
from app.models.health import ServiceHealth
from app.services.langfuse_client import LangfuseClient

logger = get_logger(__name__)

# 위험 패턴 — f-string, .format(), % 보간 등이 잔재한 경우 감지
_UNSAFE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\{[a-zA-Z_]\w*\}"),  # f-string {var} 잔재
    re.compile(r"%\(.*\)s\s*=\s*'[^']*\{"),  # 잘못된 % 보간
)
_WRITE_VERBS = re.compile(
    r"^\s*(insert|update|delete|alter|drop|create|truncate|rename|grant|revoke)\b",
    re.IGNORECASE,
)
_LIMIT_PATTERN = re.compile(r"\blimit\s+\d+", re.IGNORECASE)
_DEFAULT_LIMIT = 10000


def _validate_sql(sql: str) -> None:
    """SQL 보안 정책 검증.

    - 쓰기 동사 차단
    - 파라미터화 미적용 패턴 차단
    """
    if _WRITE_VERBS.search(sql):
        raise ClickHouseSecurityError(
            detail=f"쓰기 SQL은 허용되지 않습니다: {sql[:120]!r}"
        )
    for pat in _UNSAFE_PATTERNS:
        if pat.search(sql):
            raise ClickHouseSecurityError(
                detail=(
                    "보간된 SQL이 감지되었습니다. parameterized query를 사용하세요. "
                    f"pattern={pat.pattern!r}"
                )
            )


def _ensure_limit(sql: str, default_limit: int = _DEFAULT_LIMIT) -> str:
    """SELECT 쿼리에 LIMIT이 없으면 자동 추가."""
    if _LIMIT_PATTERN.search(sql):
        return sql
    return f"{sql.rstrip(';').rstrip()} LIMIT {default_limit}"


class ClickHouseClient:
    """ClickHouse async 클라이언트 — 직접 모드.

    Langfuse가 사내 ClickHouse(공유)를 가진다는 가정. readonly 계정만 사용한다.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: Any | None = None

    async def _get_client(self) -> Any:
        """``clickhouse_connect`` AsyncClient lazy 초기화."""
        if self._client is not None:
            return self._client
        if not self._settings.clickhouse_configured:
            raise ClickHouseError(
                detail=(
                    "ClickHouse 미설정 — CLICKHOUSE_HOST / CLICKHOUSE_READONLY_USER 필요"
                )
            )
        try:
            import clickhouse_connect
        except ImportError as exc:  # pragma: no cover
            raise ClickHouseError(
                detail=f"clickhouse_connect import 실패: {exc}"
            ) from exc

        try:
            self._client = await clickhouse_connect.get_async_client(
                host=self._settings.CLICKHOUSE_HOST,
                port=self._settings.CLICKHOUSE_PORT,
                username=self._settings.CLICKHOUSE_READONLY_USER,
                password=self._settings.CLICKHOUSE_READONLY_PASSWORD.get_secret_value(),
                database=self._settings.CLICKHOUSE_DB,
                secure=self._settings.CLICKHOUSE_SECURE,
            )
        except Exception as exc:  # noqa: BLE001
            raise ClickHouseError(
                detail=f"ClickHouse 연결 실패: {exc}"
            ) from exc

        return self._client

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """SQL 실행. 보안/정책 검증 후 결과 dict list 반환."""
        _validate_sql(sql)
        sql_with_limit = _ensure_limit(sql)
        client = await self._get_client()
        try:
            result = await client.query(sql_with_limit, parameters=parameters or {})
        except Exception as exc:  # noqa: BLE001
            raise ClickHouseError(
                detail=f"ClickHouse 쿼리 실패: {exc}"
            ) from exc
        # clickhouse_connect AsyncClient 결과 → named_results
        rows = getattr(result, "named_results", None)
        if callable(rows):
            return [dict(r) for r in rows()]
        if hasattr(result, "result_rows") and hasattr(result, "column_names"):
            cols = list(result.column_names)
            return [dict(zip(cols, row, strict=False)) for row in result.result_rows]
        return list(result) if isinstance(result, list) else []

    async def ping(self) -> bool:
        """``SELECT 1`` 실행."""
        client = await self._get_client()
        try:
            result = await client.query("SELECT 1")
            rows = getattr(result, "result_rows", None)
            return bool(rows and rows[0][0] == 1)
        except Exception:  # noqa: BLE001
            return False

    async def close(self) -> None:
        """연결 종료."""
        if self._client is not None:
            close = getattr(self._client, "close", None)
            if callable(close):
                result = close()
                if hasattr(result, "__await__"):
                    await result
            self._client = None

    async def health_check(self) -> ServiceHealth:
        """``SELECT 1`` 기반 헬스 체크."""
        if not self._settings.clickhouse_configured:
            return ServiceHealth(
                status="warn",
                endpoint=None,
                detail="ClickHouse not configured",
                checked_at=datetime.now(UTC),
            )
        endpoint = (
            f"{'https' if self._settings.CLICKHOUSE_SECURE else 'http'}://"
            f"{self._settings.CLICKHOUSE_HOST}:{self._settings.CLICKHOUSE_PORT}"
        )
        start = time.perf_counter()
        try:
            ok = await self.ping()
            latency_ms = (time.perf_counter() - start) * 1000.0
            if ok:
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
                detail="SELECT 1 failed",
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


class LangfusePublicAPIFallbackClient:
    """ClickHouse 폴백 모드 — Langfuse public API 위임 (제한적 지원)."""

    def __init__(self, langfuse: LangfuseClient) -> None:
        self._langfuse = langfuse

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """폴백 모드는 SQL 쿼리를 지원하지 않는다."""
        raise NotImplementedError(
            "Langfuse public API 폴백 모드는 SQL 쿼리를 지원하지 않습니다. "
            "필요 시 Langfuse SDK 메서드를 직접 사용하세요."
        )

    async def ping(self) -> bool:
        """Langfuse health에 위임."""
        result = await self._langfuse.health_check()
        return result.status == "ok"

    async def close(self) -> None:
        """no-op."""
        return None

    async def health_check(self) -> ServiceHealth:
        """Langfuse 헬스를 그대로 위임."""
        result = await self._langfuse.health_check()
        return ServiceHealth(
            status=result.status,
            latency_ms=result.latency_ms,
            endpoint=result.endpoint,
            detail=(result.detail or "") + " (langfuse_fallback)",
            checked_at=datetime.now(UTC),
        )


def build_clickhouse_client(
    settings: Settings,
    langfuse: LangfuseClient,
) -> ClickHouseClient | LangfusePublicAPIFallbackClient:
    """settings에 따라 직접 모드 또는 폴백 모드 클라이언트를 생성."""
    if settings.USE_LANGFUSE_PUBLIC_API_FALLBACK:
        return LangfusePublicAPIFallbackClient(langfuse)
    return ClickHouseClient(settings)
