"""프롬프트 본문에서 ``{{variable}}`` 변수 추출 유틸.

본 모듈은 외부 의존성을 가지지 않으며, 순수 정규식 기반으로 ``str`` 또는
chat messages(``list[dict]``) 프롬프트에서 변수를 추출한다.

규칙:
- ``{{name}}`` 또는 ``{{ name }}`` 형식만 인식 (앞뒤 공백 허용)
- 변수명은 ``[A-Za-z_][A-Za-z0-9_]*`` 패턴
- 중복 제거하되 발견 순서를 유지
- chat 형식의 경우 모든 message의 ``content``(str) 또는 다중 segment를 순회
"""

from __future__ import annotations

import re
from typing import Any

# ``{{var}}`` 또는 ``{{ var }}`` (앞뒤 공백 허용)
_VAR_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _extract_from_text(text: str, seen: dict[str, None]) -> None:
    """단일 문자열에서 변수 추출 — ``seen``(insertion-ordered)에 누적."""
    for match in _VAR_PATTERN.finditer(text):
        name = match.group(1)
        if name not in seen:
            seen[name] = None


def _extract_from_message(message: Any, seen: dict[str, None]) -> None:
    """chat message 1건에서 변수 추출.

    지원 형태:
    - ``{"role": "user", "content": "안녕 {{name}}"}``
    - ``{"role": "user", "content": [{"type": "text", "text": "..."}]}``
    """
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        _extract_from_text(content, seen)
    elif isinstance(content, list):
        for segment in content:
            if isinstance(segment, dict):
                text = segment.get("text") or segment.get("content")
                if isinstance(text, str):
                    _extract_from_text(text, seen)
            elif isinstance(segment, str):
                _extract_from_text(segment, seen)


def extract_variables(prompt: str | list[dict[str, Any]] | Any) -> list[str]:
    """프롬프트 본문에서 ``{{variable}}`` 변수 목록을 추출한다.

    Args:
        prompt: ``str`` 또는 chat messages ``list[dict]``.

    Returns:
        변수명 목록(중복 제거, 최초 발견 순서 유지). 비어 있으면 빈 리스트.
    """
    seen: dict[str, None] = {}
    if isinstance(prompt, str):
        _extract_from_text(prompt, seen)
    elif isinstance(prompt, list):
        for message in prompt:
            _extract_from_message(message, seen)
    return list(seen.keys())
