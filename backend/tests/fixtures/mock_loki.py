"""Loki sink Mock — 구조화 로그 캡처.

structlog의 processor 패턴을 활용하여 로그를 in-memory list로 캡처한다.
PII 미포함 검증을 위한 헬퍼(``assert_no_pii_leaked``)를 제공한다.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# 한국 환경에서 흔한 PII 정규식 패턴
_DEFAULT_PII_PATTERNS: tuple[str, ...] = (
    r"\d{6}-\d{7}",  # 주민번호 (YYMMDD-NNNNNNN)
    r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}",  # 한국 휴대폰
    r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",  # 이메일
)


class MockLokiSink:
    """구조화 로그 캡처 sink.

    structlog의 processor로 등록되면 (또는 직접 ``record()`` 호출 시) 로그를 보관한다.
    """

    def __init__(self) -> None:
        self._logs: list[dict[str, Any]] = []

    # ---------- 캡처 ----------
    def record(self, level: str, event: str, **kwargs: Any) -> None:
        """로그 한 줄 기록."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "event": event,
            **kwargs,
        }
        self._logs.append(entry)

    def as_processor(self) -> Any:
        """structlog용 processor 함수 반환.

        ``structlog.configure(processors=[..., sink.as_processor()])``로 등록하면
        모든 로그 이벤트가 본 sink에 캡처된다.
        """

        def processor(
            _logger: Any,
            method_name: str,
            event_dict: dict[str, Any],
        ) -> dict[str, Any]:
            entry = {
                "timestamp": datetime.now(UTC).isoformat(),
                "level": method_name,
                "event": event_dict.get("event", ""),
                **{k: v for k, v in event_dict.items() if k != "event"},
            }
            self._logs.append(entry)
            return event_dict

        return processor

    # ---------- 조회 ----------
    def get_logs(self) -> list[dict[str, Any]]:
        """캡처된 모든 로그 반환."""
        return list(self._logs)

    def get_logs_with_label(self, key: str, value: Any) -> list[dict[str, Any]]:
        """특정 key=value 라벨을 가진 로그만 반환."""
        return [log for log in self._logs if log.get(key) == value]

    def get_logs_by_level(self, level: str) -> list[dict[str, Any]]:
        """특정 레벨의 로그만 반환."""
        return [log for log in self._logs if log.get("level") == level]

    def clear(self) -> None:
        """캡처된 로그 초기화."""
        self._logs.clear()

    # ---------- PII 검증 ----------
    def assert_no_pii_leaked(
        self,
        patterns: list[str] | None = None,
    ) -> None:
        """PII 정규식과 매치되는 로그가 없는지 assert.

        Args:
            patterns: 검사할 정규식 list. ``None``이면 기본 패턴 사용.

        Raises:
            AssertionError: PII 패턴 매치 발견 시.
        """
        check_patterns = list(patterns) if patterns else list(_DEFAULT_PII_PATTERNS)
        compiled = [re.compile(p) for p in check_patterns]

        for log in self._logs:
            # 모든 string 값을 평탄화하여 검사
            for value in self._iter_string_values(log):
                for pat in compiled:
                    if pat.search(value):
                        raise AssertionError(
                            f"PII pattern leaked: pattern={pat.pattern!r} "
                            f"matched in log entry: {log!r}"
                        )

    @staticmethod
    def _iter_string_values(obj: Any) -> list[str]:
        """dict/list/scalar에서 모든 string 값을 평탄화."""
        result: list[str] = []
        if isinstance(obj, str):
            result.append(obj)
        elif isinstance(obj, dict):
            for v in obj.values():
                result.extend(MockLokiSink._iter_string_values(v))
        elif isinstance(obj, list | tuple):
            for v in obj:
                result.extend(MockLokiSink._iter_string_values(v))
        return result
