"""structlog 기반 JSON 로깅 설정.

- JSON 출력 (사내 Loki 수집기 stdout pickup)
- PII 차단 processor (주민번호 / 전화번호 / 이메일 마스킹)
- request_id / trace_id / experiment_id 자동 주입 지원
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# PII 정규식 — 보수적으로 작성 (false positive 허용)
_REGEX_KRN = re.compile(r"\b\d{6}-?[1-4]\d{6}\b")  # 주민등록번호
_REGEX_PHONE = re.compile(r"\b01[0-9]-?\d{3,4}-?\d{4}\b")  # 휴대전화
_REGEX_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_REDACTED = "[REDACTED]"


def _redact_pii_processor(_logger: Any, _method_name: str, event_dict: EventDict) -> EventDict:
    """이벤트 딕셔너리 내 모든 string 값에 대해 PII 마스킹 적용."""
    for key, value in list(event_dict.items()):
        if isinstance(value, str):
            event_dict[key] = _redact_string(value)
    return event_dict


def _redact_string(value: str) -> str:
    """단일 문자열에서 PII 패턴을 ``[REDACTED]``로 치환."""
    redacted = _REGEX_KRN.sub(_REDACTED, value)
    redacted = _REGEX_PHONE.sub(_REDACTED, redacted)
    redacted = _REGEX_EMAIL.sub(_REDACTED, redacted)
    return redacted


def _add_log_level_upper(_logger: Any, method_name: str, event_dict: EventDict) -> EventDict:
    """level 필드를 대문자로 표준화."""
    event_dict["level"] = method_name.upper()
    return event_dict


def configure_logging(
    *,
    log_level: str = "INFO",
    log_format: str = "json",
) -> None:
    """structlog + 표준 logging 설정.

    Args:
        log_level: 로깅 레벨 문자열 (DEBUG/INFO/WARNING/ERROR)
        log_format: ``json`` (Loki 수집용) 또는 ``console`` (개발용)
    """
    level_value = getattr(logging, log_level.upper(), logging.INFO)

    # 표준 logging 기본 핸들러 (uvicorn / FastAPI 내부 로그도 같이 잡힘)
    logging.basicConfig(
        level=level_value,
        format="%(message)s",
        stream=sys.stdout,
        force=True,
    )

    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        _add_log_level_upper,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        _redact_pii_processor,
    ]

    if log_format == "json":
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=False)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level_value),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """structlog Logger 인스턴스 반환.

    Args:
        name: logger 이름 (보통 ``__name__``)
    """
    return structlog.get_logger(name)


def is_json_formatter_active() -> bool:
    """현재 structlog 설정이 JSON renderer를 사용 중인지 확인 (Loki 자체점검용)."""
    try:
        cfg = structlog.get_config()
    except Exception:
        return False
    processors = cfg.get("processors", [])
    return any(isinstance(p, structlog.processors.JSONRenderer) for p in processors)
