"""ClickHouse 클라이언트 Mock.

``clickhouse-connect`` AsyncClient 호환 mock.
- ``register_response(pattern, rows)``로 (sql_pattern → rows) 매핑 등록
- f-string/format 문자열 보간 패턴 감지 시 보안 에러 raise
- 호출 이력은 ``_get_executed_queries()``로 검증
"""

from __future__ import annotations

import re
from typing import Any


class ClickHouseSecurityError(Exception):
    """ClickHouse 보안 정책 위반 (파라미터화 미적용)."""


class MockClickHouseClient:
    """ClickHouse async client mock.

    실제 코드의 ``clickhouse_connect.create_async_client(...).query(sql, parameters=...)``
    인터페이스와 호환. 등록된 패턴이 매치되지 않으면 빈 list 반환.
    """

    # 보안 정책 — 위험한 문자열 보간 패턴 감지
    _UNSAFE_PATTERNS = (
        re.compile(r"%\(.*\)s\s*=\s*'[^']*\{"),  # 잘못된 % 보간
        re.compile(r"\{[a-zA-Z_]\w*\}"),  # f-string {var} 잔재
    )

    def __init__(self) -> None:
        self._registered: list[tuple[re.Pattern[str], list[dict[str, Any]]]] = []
        self._executed: list[tuple[str, dict[str, Any]]] = []
        self._healthy = True

    def register_response(
        self,
        sql_pattern: str | re.Pattern[str],
        rows: list[dict[str, Any]],
    ) -> None:
        """SQL 패턴(정규식) 매치 시 반환할 rows 등록."""
        if isinstance(sql_pattern, str):
            pattern = re.compile(sql_pattern, re.IGNORECASE | re.DOTALL)
        else:
            pattern = sql_pattern
        self._registered.append((pattern, list(rows)))

    async def query(
        self,
        sql: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """SQL 실행 mock.

        - 등록된 패턴 매치 시 해당 rows 반환
        - 매치 없으면 빈 list 반환
        - 파라미터화 미적용 시 ``ClickHouseSecurityError``
        """
        params = dict(parameters or {})
        self._executed.append((sql, params))

        # 보안 정책: f-string/format 패턴 감지
        for pat in self._UNSAFE_PATTERNS:
            if pat.search(sql):
                raise ClickHouseSecurityError(
                    f"unsafe SQL detected (use parameterized query): "
                    f"pattern={pat.pattern!r} sql={sql!r}"
                )

        for pattern, rows in self._registered:
            if pattern.search(sql):
                return [dict(r) for r in rows]
        return []

    async def ping(self) -> bool:
        """연결 ping mock."""
        return self._healthy

    async def close(self) -> None:
        """연결 종료 mock — no-op."""
        return None

    # ---------- 테스트 제어 ----------
    def set_unhealthy(self) -> None:
        """``ping()`` 강제 False."""
        self._healthy = False

    def set_healthy(self) -> None:
        """헬스 복원."""
        self._healthy = True

    # ---------- 검증 헬퍼 ----------
    def _get_executed_queries(self) -> list[tuple[str, dict[str, Any]]]:
        """실행된 (sql, parameters) 이력 반환."""
        return list(self._executed)

    def _clear_executed(self) -> None:
        """이력 초기화."""
        self._executed.clear()
