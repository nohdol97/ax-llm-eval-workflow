"""신규 Trace Evaluator 카탈로그 (Phase 8-A-2).

trace tree 전체를 보아야 평가 가능한 10종 evaluator를 :class:`TraceEvaluator`
프로토콜에 따라 구현한다. ``docs/AGENT_EVAL.md`` §5.2 표 기준.

| 이름 | 설명 |
|------|------|
| ``tool_called`` | 특정 tool이 trace에 호출됐는지 (0/1) |
| ``tool_called_with_args`` | tool이 정해진 args 패턴으로 호출됐는지 (0~1, ratio) |
| ``tool_call_sequence`` | 정해진 순서대로 호출됐는지 (0/1, strict 옵션) |
| ``tool_call_count_in_range`` | tool 호출 횟수가 [min, max] 범위인지 (0/1) |
| ``no_error_spans`` | level=ERROR span이 0개 (0/1) |
| ``error_recovery_attempted`` | error 발생 후 재시도 (0~1, error 없으면 None) |
| ``agent_loop_bounded`` | generation 수가 임계 이하 (0/1) |
| ``latency_breakdown_healthy`` | 단계별 지연이 합리적 (0~1) |
| ``tool_result_grounding`` | tool 결과 ↔ output 인용 일치성 (LLM Judge, 0~1) |
| ``hallucination_check`` | output에 tool 결과 외 fact가 없는지 (LLM Judge, 0~1) |

설계 원칙
- 모든 evaluator는 5초 timeout 안에서 실행되도록 가벼운 연산만 수행한다.
- LLM Judge 의존 evaluator(``tool_result_grounding`` / ``hallucination_check``)는
  ``litellm`` 미주입 시 ``None`` 반환(graceful skip). 파이프라인이 자동 주입.
- config 검증 실패는 :class:`TraceEvaluatorError` raise — pipeline 이 catch해 None.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.evaluators.trace_base import TraceEvaluatorError
from app.models.trace import TraceTree

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 내부 헬퍼
# --------------------------------------------------------------------------- #


def _looks_like_regex(value: str) -> bool:
    """문자열이 명시적 regex 패턴인지 휴리스틱 판단.

    - 양 끝 anchor (``^``/``$``)
    - 정규식 메타문자 포함 (``.+`` / ``.*`` / 문자 클래스 ``[...]``)
    """
    if not value:
        return False
    if value.startswith("^") or value.endswith("$"):
        return True
    if any(token in value for token in (".+", ".*", "\\d", "\\w", "\\s")):
        return True
    if "[" in value and "]" in value:
        return True
    return False


def _stringify_for_judge(val: Any, max_len: int = 500) -> str:
    """Judge 프롬프트 본문에 넣기 위한 안전 문자열화 (길이 제한)."""
    if val is None:
        return ""
    if isinstance(val, str):
        text = val
    else:
        try:
            text = json.dumps(val, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            text = str(val)
    if len(text) > max_len:
        text = text[:max_len] + "...[TRUNCATED]"
    return text


def _extract_judge_score(content: str) -> float | None:
    """Judge 응답 문자열에서 ``score`` 정수(0~10)를 추출 후 0~1로 정규화."""
    if not content:
        return None
    # 1차: JSON 객체 탐색
    json_match = re.search(r'\{[^{}]*"score"\s*:\s*(-?\d+(?:\.\d+)?)[^{}]*\}', content)
    if json_match:
        try:
            value = float(json_match.group(1))
        except ValueError:
            value = None
        if value is not None and 0.0 <= value <= 10.0:
            return value / 10.0
    # 2차: 자유 텍스트 fallback
    free_match = re.search(r'(?i)score["\']?\s*[:=]\s*(-?\d+(?:\.\d+)?)', content)
    if free_match:
        try:
            value = float(free_match.group(1))
        except ValueError:
            return None
        if 0.0 <= value <= 10.0:
            return value / 10.0
    return None


# --------------------------------------------------------------------------- #
# 1. ToolCalledEvaluator
# --------------------------------------------------------------------------- #
class ToolCalledEvaluator:
    """특정 tool이 trace에 호출됐는지.

    - config: ``{tool_name: str}``
    - expected fallback: ``{tool_name: str}`` 도 허용 (config 우선)
    - 반환: ``0.0`` (호출 없음) / ``1.0`` (호출 있음)
    """

    name: str = "tool_called"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        tool_name = config.get("tool_name")
        if not tool_name and isinstance(expected, dict):
            tool_name = expected.get("tool_name")
        if not tool_name:
            raise TraceEvaluatorError(
                "tool_called: config['tool_name'] 또는 expected['tool_name'] 필수"
            )
        called = any(o.name == tool_name for o in trace.tool_calls())
        return 1.0 if called else 0.0


# --------------------------------------------------------------------------- #
# 2. ToolCalledWithArgsEvaluator
# --------------------------------------------------------------------------- #
class ToolCalledWithArgsEvaluator:
    """tool이 정해진 args 패턴으로 호출됐는지.

    - config: ``{tool_name: str, args_match: dict[str, str | value]}``
    - args_match 값이 regex 형태면 :func:`re.search` 로 매칭, 아니면 동등 비교.
    - 동일 이름 tool이 여러 번 호출됐다면 그 중 가장 매칭률 높은 호출의 ratio.
    - 반환: 0.0~1.0 (모두 매치=1.0, tool 자체 미호출=0.0)
    """

    name: str = "tool_called_with_args"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        tool_name = config.get("tool_name")
        args_match = config.get("args_match")
        if not tool_name:
            raise TraceEvaluatorError("tool_called_with_args: config['tool_name'] 필수")
        if not isinstance(args_match, dict) or not args_match:
            raise TraceEvaluatorError(
                "tool_called_with_args: config['args_match']는 비어있지 않은 dict여야 함"
            )

        candidates = [o for o in trace.tool_calls() if o.name == tool_name]
        if not candidates:
            return 0.0

        total = len(args_match)
        best_ratio = 0.0
        for cand in candidates:
            input_dict = cand.input if isinstance(cand.input, dict) else {}
            matched = 0
            for key, expected_val in args_match.items():
                actual = input_dict.get(key)
                if actual is None:
                    continue
                if isinstance(expected_val, str) and _looks_like_regex(expected_val):
                    try:
                        if re.search(expected_val, str(actual)):
                            matched += 1
                    except re.error:
                        # invalid regex → 동등 비교로 fallback
                        if str(actual) == expected_val:
                            matched += 1
                elif isinstance(expected_val, str) and "*" in expected_val:
                    # 단순 와일드카드 → regex 변환
                    pattern = re.escape(expected_val).replace(r"\*", ".*")
                    if re.search(pattern, str(actual)):
                        matched += 1
                else:
                    if actual == expected_val:
                        matched += 1
            ratio = matched / total if total > 0 else 0.0
            if ratio > best_ratio:
                best_ratio = ratio
        return best_ratio


# --------------------------------------------------------------------------- #
# 3. ToolCallSequenceEvaluator
# --------------------------------------------------------------------------- #
class ToolCallSequenceEvaluator:
    """정해진 순서대로 tool이 호출됐는지.

    - config: ``{sequence: list[str], strict: bool=False}``
    - ``strict=True``: 정확히 그 순서 (다른 tool 끼면 0.0)
    - ``strict=False``: subsequence (사이에 다른 tool 끼어도 OK)
    - 반환: 0.0 또는 1.0
    """

    name: str = "tool_call_sequence"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        sequence = config.get("sequence")
        if not isinstance(sequence, list) or not sequence:
            raise TraceEvaluatorError(
                "tool_call_sequence: config['sequence']는 비어있지 않은 list여야 함"
            )
        if any(not isinstance(name, str) for name in sequence):
            raise TraceEvaluatorError("tool_call_sequence: sequence 항목은 모두 str이어야 함")

        strict = bool(config.get("strict", False))
        actual = [o.name for o in trace.tool_calls()]

        if strict:
            return 1.0 if actual == sequence else 0.0

        # subsequence 검사 — 2 포인터
        i = 0
        for actual_name in actual:
            if i < len(sequence) and actual_name == sequence[i]:
                i += 1
        return 1.0 if i == len(sequence) else 0.0


# --------------------------------------------------------------------------- #
# 4. ToolCallCountInRangeEvaluator
# --------------------------------------------------------------------------- #
class ToolCallCountInRangeEvaluator:
    """tool 호출 횟수가 ``[min, max]`` 범위인지.

    - config: ``{min: int, max: int, tool_name?: str}``
    - ``tool_name`` 미지정 → 전체 tool span 수, 지정 → 해당 이름만 카운트.
    - 반환: 0.0 (범위 밖) / 1.0 (범위 안)
    """

    name: str = "tool_call_count_in_range"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        if "min" not in config or "max" not in config:
            raise TraceEvaluatorError("tool_call_count_in_range: config['min'], config['max'] 필수")
        try:
            min_n = int(config["min"])
            max_n = int(config["max"])
        except (TypeError, ValueError) as exc:
            raise TraceEvaluatorError("tool_call_count_in_range: min/max는 int여야 함") from exc
        if min_n > max_n:
            raise TraceEvaluatorError("tool_call_count_in_range: min > max")

        tool_name = config.get("tool_name")
        calls = trace.tool_calls()
        if tool_name:
            calls = [c for c in calls if c.name == tool_name]
        count = len(calls)
        return 1.0 if min_n <= count <= max_n else 0.0


# --------------------------------------------------------------------------- #
# 5. NoErrorSpansEvaluator
# --------------------------------------------------------------------------- #
class NoErrorSpansEvaluator:
    """``level=ERROR`` span이 0개인지.

    - config: ``{ignore_names?: list[str]}``
    - 반환: 1.0 (error 없음) / 0.0 (error 있음)
    """

    name: str = "no_error_spans"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        ignore_raw = config.get("ignore_names") or []
        if not isinstance(ignore_raw, list):
            raise TraceEvaluatorError("no_error_spans: ignore_names는 list여야 함")
        ignore = set(ignore_raw)
        error_spans = [o for o in trace.observations if o.level == "ERROR" and o.name not in ignore]
        return 1.0 if not error_spans else 0.0


# --------------------------------------------------------------------------- #
# 6. ErrorRecoveryAttemptedEvaluator
# --------------------------------------------------------------------------- #
class ErrorRecoveryAttemptedEvaluator:
    """error span 발생 후 같은 tool을 재시도했는지.

    - config: ``{}``
    - 반환:
        - error span 0개 → ``None`` (평가 불가, skipped)
        - 그 외 → 회복된 비율 ``recovered / total_errors`` (0.0~1.0)

    "회복" 정의: error 발생 시각 이후, 동일 ``name`` 의 observation이 (level이 ERROR가
    아닌 상태로) 다시 등장하면 회복으로 간주.
    """

    name: str = "error_recovery_attempted"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        error_spans = [o for o in trace.observations if o.level == "ERROR"]
        if not error_spans:
            return None  # error 없으면 평가 불가

        recovered = 0
        for err in error_spans:
            after = [
                o
                for o in trace.observations
                if o.start_time > err.start_time and o.name == err.name and o.level != "ERROR"
            ]
            if after:
                recovered += 1
        return recovered / len(error_spans)


# --------------------------------------------------------------------------- #
# 7. AgentLoopBoundedEvaluator
# --------------------------------------------------------------------------- #
class AgentLoopBoundedEvaluator:
    """generation 호출 수가 임계 이하인지 (무한 루프 방지).

    - config: ``{max_generations: int=10}``
    - 반환: 1.0 (이하) / 0.0 (초과)
    """

    name: str = "agent_loop_bounded"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        try:
            max_n = int(config.get("max_generations", 10))
        except (TypeError, ValueError) as exc:
            raise TraceEvaluatorError("agent_loop_bounded: max_generations는 int여야 함") from exc
        if max_n < 0:
            raise TraceEvaluatorError("agent_loop_bounded: max_generations는 0 이상이어야 함")
        gen_count = len(trace.llm_calls())
        return 1.0 if gen_count <= max_n else 0.0


# --------------------------------------------------------------------------- #
# 8. LatencyBreakdownHealthyEvaluator
# --------------------------------------------------------------------------- #
class LatencyBreakdownHealthyEvaluator:
    """단계별 지연이 합리적인지.

    - config: ``{tool_max_ms?: int, llm_max_ms?: int}``
    - 적용 가능한 임계 미정의 시(둘 다 None) → 1.0 (검사 자체가 비활성).
    - 반환: ``1.0 - violations / applicable_total`` — 모두 OK면 1.0, 모두 초과면 0.0.

    Notes:
        ``latency_ms`` 값이 ``None`` 인 observation은 분모/분자 모두에서 제외.
    """

    name: str = "latency_breakdown_healthy"

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        tool_max = config.get("tool_max_ms")
        llm_max = config.get("llm_max_ms")

        # 임계가 둘 다 없으면 사실상 검사 비활성 — 1.0 반환 (위반 없음으로 간주)
        if tool_max is None and llm_max is None:
            return 1.0

        try:
            tool_max_f = float(tool_max) if tool_max is not None else None
            llm_max_f = float(llm_max) if llm_max is not None else None
        except (TypeError, ValueError) as exc:
            raise TraceEvaluatorError(
                "latency_breakdown_healthy: tool_max_ms/llm_max_ms는 number여야 함"
            ) from exc

        violations = 0
        applicable = 0
        for obs in trace.observations:
            if obs.latency_ms is None:
                continue
            if obs.type == "span" and tool_max_f is not None:
                applicable += 1
                if obs.latency_ms > tool_max_f:
                    violations += 1
            elif obs.type == "generation" and llm_max_f is not None:
                applicable += 1
                if obs.latency_ms > llm_max_f:
                    violations += 1

        if applicable == 0:
            return 1.0
        return 1.0 - (violations / applicable)


# --------------------------------------------------------------------------- #
# 9. ToolResultGroundingEvaluator (LLM Judge)
# --------------------------------------------------------------------------- #
_GROUNDING_PROMPT = """다음은 agent가 호출한 tool들의 결과와 최종 응답이다.

[Tool Results]
{tool_results}

[Final Output]
{final_output}

위 final output이 tool results의 정보에 근거하는지 0~10점으로 평가하라.
- 10: 모든 주장이 tool results에 근거함, 인용 명확
- 5: 일부만 근거, 일부는 추론
- 0: tool results와 무관한 정보가 다수 (할루시네이션)

JSON 형식으로 응답:
{{"score": 0~10, "reasoning": "..."}}
"""


class ToolResultGroundingEvaluator:
    """tool 결과 텍스트와 final output을 LLM Judge로 비교 (인용/근거성 평가).

    - config: ``{judge_model: str="gpt-4o"}``
    - 의존: LiteLLM 클라이언트 (생성자 ``litellm`` 인자로 주입). 미주입 → ``None``.
    - tool 호출 또는 trace.output 둘 중 하나라도 없으면 ``None`` (평가 불가).
    """

    name: str = "tool_result_grounding"

    def __init__(self, litellm: Any | None = None) -> None:
        self._litellm = litellm

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        if self._litellm is None:
            return None  # 의존성 미주입 → graceful skip
        if trace.output is None or not trace.tool_calls():
            return None

        # tool 결과 모두 안전 문자열화
        tool_results_text = "\n".join(
            f"[{o.name}] {_stringify_for_judge(o.output)}"
            for o in trace.tool_calls()
            if o.output is not None
        )
        if not tool_results_text:
            return None

        final_output_text = _stringify_for_judge(trace.output, max_len=2000)
        prompt = _GROUNDING_PROMPT.format(
            tool_results=tool_results_text, final_output=final_output_text
        )
        judge_model = config.get("judge_model", "gpt-4o")

        try:
            resp = await self._litellm.completion(
                model=judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=300,
            )
        except Exception as exc:  # noqa: BLE001 — LLM 호출 실패는 graceful skip
            logger.warning(
                "trace_evaluator_llm_failed",
                evaluator=self.name,
                error=str(exc),
            )
            return None

        content = _extract_message_content(resp)
        return _extract_judge_score(content)


# --------------------------------------------------------------------------- #
# 10. HallucinationCheckEvaluator (LLM Judge)
# --------------------------------------------------------------------------- #
_HALLUCINATION_PROMPT = """다음은 agent가 호출한 tool 결과와 최종 응답이다.

[Tool Results]
{tool_results}

[Final Output]
{final_output}

final output에 tool results에 없는 사실(fact)이 포함됐는지 0~10점으로 평가하라.
- 10: tool results 외 사실 없음 (할루시네이션 없음)
- 5: 일부 사실은 tool results 외 출처 (추론 또는 일반지식)
- 0: 다수 사실이 tool results와 무관 (심각한 할루시네이션)

JSON 형식으로 응답:
{{"score": 0~10, "reasoning": "..."}}
"""


class HallucinationCheckEvaluator:
    """output에 tool 결과 외 fact가 있는지 LLM Judge.

    - config: ``{judge_model: str="gpt-4o"}``
    - 의존: LiteLLM 클라이언트 (생성자 ``litellm`` 인자로 주입). 미주입 → ``None``.
    - 점수 의미: ``1.0`` (할루시네이션 없음) ~ ``0.0`` (다수 할루시네이션).
    """

    name: str = "hallucination_check"

    def __init__(self, litellm: Any | None = None) -> None:
        self._litellm = litellm

    async def evaluate_trace(
        self,
        trace: TraceTree,
        expected: dict[str, Any] | None,
        config: dict[str, Any],
    ) -> float | None:
        if self._litellm is None:
            return None
        if trace.output is None or not trace.tool_calls():
            return None

        tool_results_text = "\n".join(
            f"[{o.name}] {_stringify_for_judge(o.output)}"
            for o in trace.tool_calls()
            if o.output is not None
        )
        if not tool_results_text:
            return None

        final_output_text = _stringify_for_judge(trace.output, max_len=2000)
        prompt = _HALLUCINATION_PROMPT.format(
            tool_results=tool_results_text, final_output=final_output_text
        )
        judge_model = config.get("judge_model", "gpt-4o")

        try:
            resp = await self._litellm.completion(
                model=judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=300,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "trace_evaluator_llm_failed",
                evaluator=self.name,
                error=str(exc),
            )
            return None

        content = _extract_message_content(resp)
        return _extract_judge_score(content)


# --------------------------------------------------------------------------- #
# response 헬퍼
# --------------------------------------------------------------------------- #
def _extract_message_content(resp: Any) -> str:
    """LiteLLM completion 응답에서 첫 번째 message content 추출."""
    if not isinstance(resp, dict):
        return ""
    choices = resp.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    if not isinstance(first, dict):
        return ""
    message = first.get("message")
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
    return ""


__all__ = [
    "AgentLoopBoundedEvaluator",
    "ErrorRecoveryAttemptedEvaluator",
    "HallucinationCheckEvaluator",
    "LatencyBreakdownHealthyEvaluator",
    "NoErrorSpansEvaluator",
    "ToolCallCountInRangeEvaluator",
    "ToolCallSequenceEvaluator",
    "ToolCalledEvaluator",
    "ToolCalledWithArgsEvaluator",
    "ToolResultGroundingEvaluator",
]
