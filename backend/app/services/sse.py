"""SSE(Server-Sent Events) 공용 헬퍼.

본 프로젝트의 모든 SSE 엔드포인트(데이터셋 업로드 진행률, 단일 테스트, 배치 실험)는
이 모듈의 포맷팅 함수를 통해 일관된 페이로드를 생성한다. API_DESIGN.md §1.1 SSE 포맷
규약에 따라 각 이벤트는 단조 증가 ``id:`` 라인을 포함하고, 15초 간격 heartbeat를
주석으로 발송한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any

from fastapi.responses import StreamingResponse

# SSE 표준 헤더 — Cache-Control no-store, X-Accel-Buffering no (Nginx 버퍼링 우회)
SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}

# 클라이언트 재연결 권장 시간 (ms) — API_DESIGN.md §1.1
SSE_RETRY_MS: int = 3000

# Heartbeat 간격 (초)
SSE_HEARTBEAT_INTERVAL_SEC: float = 15.0


def format_sse_event(
    event: str,
    data: Mapping[str, Any],
    event_id: int | str | None = None,
) -> str:
    """단일 SSE 이벤트를 텍스트로 포맷.

    Args:
        event: 이벤트 타입 (``progress`` / ``done`` / ``error`` 등)
        data: JSON 직렬화 가능한 페이로드. ``data:`` 라인은 한 줄로 직렬화한다
            (multi-line ``data:`` 금지 — API_DESIGN.md §1.1)
        event_id: 단조 증가 ID (재연결 ``Last-Event-ID`` 지원).
            None이면 ``id:`` 라인 생략

    Returns:
        ``id: ...\\nevent: ...\\ndata: ...\\n\\n`` 형식의 문자열
    """
    parts: list[str] = []
    if event_id is not None:
        parts.append(f"id: {event_id}")
    parts.append(f"event: {event}")
    # JSON 한 줄 직렬화 — 개행 포함된 값은 escape됨
    parts.append(f"data: {json.dumps(data, ensure_ascii=False, default=str)}")
    return "\n".join(parts) + "\n\n"


def format_retry_directive(retry_ms: int = SSE_RETRY_MS) -> str:
    """``retry:`` 지시문 — 클라이언트 재연결 대기시간 (ms)."""
    return f"retry: {retry_ms}\n\n"


def heartbeat() -> str:
    """heartbeat 주석 라인.

    EventSource API는 주석을 무시하지만, 중간 프록시(Nginx/CloudFlare)의
    idle timeout을 방지한다.
    """
    return ": heartbeat\n\n"


async def sse_response(
    generator: AsyncIterator[str],
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> StreamingResponse:
    """문자열 비동기 iterator를 ``StreamingResponse``로 래핑.

    Args:
        generator: 이미 SSE 형식으로 포맷된 텍스트를 yield하는 async iterator
        extra_headers: 추가 응답 헤더 (Last-Event-ID 등 — 일반적으로 불필요)

    Returns:
        ``text/event-stream`` MIME으로 응답하는 StreamingResponse
    """
    headers: dict[str, str] = dict(SSE_HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=headers,
    )
