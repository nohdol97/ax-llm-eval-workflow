"""Context Engine — 프롬프트 변수 파싱·바인딩 엔진.

본 모듈은 본 프로젝트의 단일 테스트 / 배치 실험 양쪽이 공유하는 캐노니컬 엔진이다.
핵심 책임:

1. ``{{var}}`` 패턴 추출 (text + chat 형식 모두 지원)
2. 변수 → 실제 값 치환(compile)
3. 데이터셋 아이템 ``input`` dict를 프롬프트 변수에 자동/명시적 바인딩
4. 필수 변수 누락 검증

설계 원칙
---------
- 외부 의존성 0 (정규식 + json만 사용)
- ``Langfuse SDK prompt.compile()``이 존재하면 위임 가능 (인터페이스 호환)
- 누락 변수는 기본 빈 문자열 fallback (strict 모드는 별도 호출 ``validate``로 보장)
- text/json/file/list 변수 타입은 v1에서 string-cast로 단순화 (json은 ``json.dumps``)
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

# ``{{var}}`` 또는 ``{{ var }}`` (앞뒤 공백 허용) — ``services/prompt_utils.py``와 동일한 패턴
VARIABLE_PATTERN: re.Pattern[str] = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


def _coerce_value(value: Any) -> str:
    """변수 값을 문자열로 강제 변환한다.

    규칙:
    - ``None`` → 빈 문자열
    - ``str`` → 그대로
    - ``bool`` → ``"true"`` / ``"false"`` (Python ``True``/``False`` 회피, 일관성)
    - ``int`` / ``float`` → ``str(value)``
    - ``dict`` / ``list`` → ``json.dumps(..., ensure_ascii=False)``
    - 그 외 → ``str(value)``
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _replace_in_text(
    text: str,
    variables: dict[str, str],
    *,
    missing: list[str],
) -> str:
    """문자열 내 ``{{var}}``를 치환. 누락 변수는 ``missing``에 누적하고 ``""``로 fallback."""

    def _sub(match: re.Match[str]) -> str:
        name = match.group(1)
        if name in variables:
            return variables[name]
        missing.append(name)
        return ""

    return VARIABLE_PATTERN.sub(_sub, text)


def _extract_text_segments(prompt: str | list[dict[str, Any]] | Any) -> list[str]:
    """프롬프트 본문에서 변수 추출 대상 ``str`` 세그먼트를 평탄화하여 반환.

    chat 포맷의 다중 segment(``content`` 가 list)도 펼쳐서 처리한다.
    """
    segments: list[str] = []
    if isinstance(prompt, str):
        segments.append(prompt)
        return segments
    if isinstance(prompt, list):
        for message in prompt:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                segments.append(content)
            elif isinstance(content, list):
                for seg in content:
                    if isinstance(seg, str):
                        segments.append(seg)
                    elif isinstance(seg, dict):
                        text = seg.get("text") or seg.get("content")
                        if isinstance(text, str):
                            segments.append(text)
    return segments


class ContextEngine:
    """프롬프트 변수 파싱·바인딩 엔진 (캐노니컬).

    인스턴스는 무상태(stateless)이며, 단일 인스턴스를 ``app.state``에 보관해
    여러 요청에서 공유한다. 동시성 안전.
    """

    # ---------- 1) 변수 추출 ----------
    def parse_variables(self, prompt: str | list[dict[str, Any]] | Any) -> list[str]:
        """프롬프트 본문에서 ``{{variable}}`` 변수명을 추출한다.

        Args:
            prompt: ``str`` (text 프롬프트) 또는 chat messages ``list[dict]``.

        Returns:
            변수명 리스트(중복 제거, 최초 발견 순서 유지).
        """
        seen: dict[str, None] = {}
        for segment in _extract_text_segments(prompt):
            for match in VARIABLE_PATTERN.finditer(segment):
                name = match.group(1)
                if name not in seen:
                    seen[name] = None
        return list(seen.keys())

    # ---------- 2) 변수 치환 ----------
    def compile(
        self,
        prompt: str | list[dict[str, Any]],
        variables: dict[str, Any],
        *,
        strict: bool = False,
    ) -> str | list[dict[str, Any]]:
        """변수를 실제 값으로 치환한 프롬프트를 반환한다.

        - 누락 변수는 빈 문자열로 대체 (strict=False, 기본).
        - ``strict=True``인 경우 누락이 있으면 ``ValueError``.
        - dict/list 변수 값은 ``json.dumps``로 직렬화하여 삽입.
        - chat 포맷이면 각 message의 content/segment를 순회하여 새 객체를 만들어 반환.

        Args:
            prompt: 원본 프롬프트 (text 또는 chat messages).
            variables: 변수 dict.
            strict: True면 누락 변수에 대해 ``ValueError``.

        Returns:
            변수가 치환된 프롬프트. 입력이 ``list[dict]``면 동형의 새로운 리스트를 반환.
        """
        # 변수 값을 미리 문자열로 강제 변환 (반복 호출 회피)
        coerced: dict[str, str] = {k: _coerce_value(v) for k, v in variables.items()}
        missing: list[str] = []

        if isinstance(prompt, str):
            result_text = _replace_in_text(prompt, coerced, missing=missing)
            if strict and missing:
                raise ValueError(f"필수 변수 누락 (strict=True): {sorted(set(missing))}")
            return result_text

        if isinstance(prompt, list):
            new_messages: list[dict[str, Any]] = []
            for message in prompt:
                if not isinstance(message, dict):
                    # 비 dict 메시지는 그대로 통과
                    new_messages.append(message)
                    continue
                new_msg: dict[str, Any] = dict(message)  # shallow copy
                content = new_msg.get("content")
                if isinstance(content, str):
                    new_msg["content"] = _replace_in_text(content, coerced, missing=missing)
                elif isinstance(content, list):
                    new_segments: list[Any] = []
                    for seg in content:
                        if isinstance(seg, str):
                            new_segments.append(_replace_in_text(seg, coerced, missing=missing))
                        elif isinstance(seg, dict):
                            new_seg = dict(seg)
                            text = new_seg.get("text")
                            inner = new_seg.get("content")
                            if isinstance(text, str):
                                new_seg["text"] = _replace_in_text(text, coerced, missing=missing)
                            elif isinstance(inner, str):
                                new_seg["content"] = _replace_in_text(
                                    inner, coerced, missing=missing
                                )
                            new_segments.append(new_seg)
                        else:
                            new_segments.append(seg)
                    new_msg["content"] = new_segments
                new_messages.append(new_msg)

            if strict and missing:
                raise ValueError(f"필수 변수 누락 (strict=True): {sorted(set(missing))}")
            return new_messages

        # 알 수 없는 형식 — 변환 없이 반환 (방어적)
        return prompt  # type: ignore[return-value]

    # ---------- 3) Langfuse SDK 위임 ----------
    def compile_with_sdk(
        self,
        prompt_obj: Any,
        variables: dict[str, Any],
    ) -> str | list[dict[str, Any]]:
        """Langfuse SDK ``prompt.compile(**vars)``가 있으면 위임한다.

        SDK prompt는 ``compile`` 메서드가 있을 수 있다 (TextPromptClient/ChatPromptClient).
        없는 경우 ``self.compile``로 fallback.

        Args:
            prompt_obj: Langfuse SDK가 반환한 prompt 객체. ``body``/``prompt`` 속성 보유.
            variables: 변수 dict.
        """
        compile_fn = getattr(prompt_obj, "compile", None)
        if callable(compile_fn):
            try:
                return compile_fn(**variables)  # type: ignore[no-any-return]
            except TypeError:
                # SDK가 dict 인자를 요구하는 변형
                try:
                    return compile_fn(variables)  # type: ignore[no-any-return]
                except Exception:  # noqa: BLE001, S110
                    # 양쪽 시그니처 모두 실패 — 엔진 자체 로직으로 fallback
                    pass
        body = (
            getattr(prompt_obj, "prompt", None) or getattr(prompt_obj, "body", None) or prompt_obj
        )
        if not isinstance(body, (str, list)):
            body = str(body)
        return self.compile(body, variables)

    # ---------- 4) 데이터셋 아이템 바인딩 ----------
    def bind_dataset_item(
        self,
        prompt: str | list[dict[str, Any]],
        item_input: dict[str, Any],
        variable_mapping: dict[str, str] | None = None,
        *,
        strict: bool = False,
    ) -> str | list[dict[str, Any]]:
        """데이터셋 아이템 ``input`` dict를 프롬프트 변수에 바인딩.

        - ``variable_mapping`` ``None``: 자동 매핑(변수명 == item_input 키).
        - ``variable_mapping`` 명시: ``{"variable_name": "item_input_field_name"}``.
          동일 변수명이 자동 매핑보다 우선한다.

        Args:
            prompt: 원본 프롬프트.
            item_input: 데이터셋 아이템의 ``input`` dict.
            variable_mapping: ``{프롬프트 변수명: item_input의 key}``.
            strict: True면 매핑되지 않은 필수 변수에 대해 ``ValueError``.

        Returns:
            변수 치환된 프롬프트.
        """
        if not isinstance(item_input, dict):
            raise TypeError(f"item_input은 dict이어야 합니다 (got {type(item_input).__name__})")
        prompt_vars = self.parse_variables(prompt)

        resolved: dict[str, Any] = {}
        for var_name in prompt_vars:
            # 1) 명시적 매핑이 있으면 우선 적용
            if variable_mapping and var_name in variable_mapping:
                src_key = variable_mapping[var_name]
                if src_key in item_input:
                    resolved[var_name] = item_input[src_key]
                continue
            # 2) 자동 매핑 — 변수명이 item_input 키와 일치
            if var_name in item_input:
                resolved[var_name] = item_input[var_name]

        return self.compile(prompt, resolved, strict=strict)

    # ---------- 5) 필수 변수 검증 ----------
    def validate(
        self,
        prompt: str | list[dict[str, Any]],
        required_variables: Iterable[str],
    ) -> list[str]:
        """프롬프트에 ``required_variables``가 모두 존재하는지 검증한다.

        Args:
            prompt: 원본 프롬프트.
            required_variables: 필수 변수명 iterable.

        Returns:
            누락된 변수명 리스트 (빈 리스트면 통과).
        """
        present = set(self.parse_variables(prompt))
        missing = [v for v in required_variables if v not in present]
        return missing
