"""13개 내장 평가 함수 구현.

각 evaluator는 :class:`app.evaluators.base.Evaluator` 프로토콜을 구현한다.
공통 규칙:

- ``evaluate`` 결과는 ``clamp``로 0.0~1.0 범위 강제
- 입력 형식 부적합 시 ``None`` 반환 (예외 raise하지 않음 — Pipeline에서 catch는 별도)
- 외부 I/O가 필요한 evaluator만 실제 await 사용
  (대부분은 빠른 동기 계산이지만 Protocol 일치를 위해 ``async`` 함수로 노출)

13개 목록:
1. exact_match           — 정확 일치 (대소문자/공백 옵션)
2. contains              — 키워드 포함 (AND/OR 조건)
3. regex_match           — 정규식 매칭
4. json_validity         — 유효 JSON 여부
5. json_schema_match     — jsonschema 스키마 일치
6. json_key_presence     — 필수 키 존재 비율
7. levenshtein_similarity — 1 - (편집거리/max_len)
8. cosine_similarity     — 임베딩 코사인 유사도
9. bleu                  — n-gram BLEU (1~4 gram 평균 precision)
10. rouge                — ROUGE-L (LCS 기반 F1)
11. latency_check        — metadata.latency_ms ≤ threshold
12. token_budget_check   — metadata.output_tokens ≤ budget
13. cost_check           — metadata.cost_usd ≤ threshold
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from typing import Any

from app.core.logging import get_logger
from app.evaluators.base import clamp

logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 헬퍼
# --------------------------------------------------------------------------- #
def _to_string(value: Any) -> str | None:
    """다양한 입력을 문자열로 정규화. JSON dict/list는 ``json.dumps`` 직렬화."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return None
    return str(value)


def _normalize(text: str, ignore_case: bool, ignore_whitespace: bool) -> str:
    """문자열 정규화 (대소문자/공백 옵션)."""
    if ignore_case:
        text = text.lower()
    if ignore_whitespace:
        # 연속 공백 → 단일 공백 + 양끝 trim
        text = re.sub(r"\s+", " ", text).strip()
    return text


# --------------------------------------------------------------------------- #
# 1. exact_match
# --------------------------------------------------------------------------- #
class ExactMatchEvaluator:
    """output == expected (옵션: 대소문자/공백 무시).

    config:
        ignore_case (bool, default True)
        ignore_whitespace (bool, default True)

    Returns: 0.0 또는 1.0 (expected None이면 None)
    """

    name = "exact_match"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if expected is None:
            return None
        out_s = _to_string(output)
        exp_s = _to_string(expected)
        if out_s is None or exp_s is None:
            return None

        ignore_case = bool(config.get("ignore_case", True))
        ignore_ws = bool(config.get("ignore_whitespace", True))

        a = _normalize(out_s, ignore_case, ignore_ws)
        b = _normalize(exp_s, ignore_case, ignore_ws)
        return 1.0 if a == b else 0.0


# --------------------------------------------------------------------------- #
# 2. contains
# --------------------------------------------------------------------------- #
class ContainsEvaluator:
    """output에 expected 키워드 포함 여부 검사.

    config:
        keywords (list[str]) — 검사 키워드 목록. 미지정 시 ``expected``를 단일 키워드로 사용.
        mode ("all" | "any", default "all") — AND / OR 조건
        ignore_case (bool, default True)

    Returns: 0.0 또는 1.0
    """

    name = "contains"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        out_s = _to_string(output)
        if out_s is None:
            return None

        keywords_cfg = config.get("keywords")
        if keywords_cfg:
            keywords = [str(k) for k in keywords_cfg if str(k)]
        elif isinstance(expected, list):
            keywords = [str(k) for k in expected if str(k)]
        elif expected is not None:
            kw = _to_string(expected)
            keywords = [kw] if kw else []
        else:
            keywords = []

        if not keywords:
            return None

        mode = str(config.get("mode", "all")).lower()
        if mode not in {"all", "any"}:
            mode = "all"

        ignore_case = bool(config.get("ignore_case", True))
        haystack = out_s.lower() if ignore_case else out_s
        needles = [k.lower() for k in keywords] if ignore_case else keywords

        hits = [n in haystack for n in needles]
        if mode == "all":
            return 1.0 if all(hits) else 0.0
        return 1.0 if any(hits) else 0.0


# --------------------------------------------------------------------------- #
# 3. regex_match
# --------------------------------------------------------------------------- #
class RegexMatchEvaluator:
    """output이 정규식 패턴에 매칭되는지 검사.

    config:
        pattern (str) — 정규식 문자열 (필수)
        flags (str, default "") — "IGNORECASE" / "MULTILINE" / "DOTALL" (콤마/공백 구분)
        full_match (bool, default False) — True면 fullmatch, False면 search

    Returns: 0.0 또는 1.0. pattern 미지정/컴파일 실패 시 None
    """

    name = "regex_match"

    _FLAG_MAP = {
        "IGNORECASE": re.IGNORECASE,
        "I": re.IGNORECASE,
        "MULTILINE": re.MULTILINE,
        "M": re.MULTILINE,
        "DOTALL": re.DOTALL,
        "S": re.DOTALL,
        "VERBOSE": re.VERBOSE,
        "X": re.VERBOSE,
        "ASCII": re.ASCII,
        "A": re.ASCII,
    }

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        pattern = config.get("pattern")
        if not pattern or not isinstance(pattern, str):
            return None

        flags_value = 0
        flag_str = config.get("flags", "")
        if isinstance(flag_str, str) and flag_str:
            for tok in re.split(r"[,\s|]+", flag_str):
                flag = self._FLAG_MAP.get(tok.upper().strip())
                if flag is not None:
                    flags_value |= flag

        try:
            regex = re.compile(pattern, flags_value)
        except re.error as exc:
            logger.warning("regex_compile_failed", pattern=pattern, error=str(exc))
            return None

        out_s = _to_string(output)
        if out_s is None:
            return None

        full_match = bool(config.get("full_match", False))
        if full_match:
            return 1.0 if regex.fullmatch(out_s) else 0.0
        return 1.0 if regex.search(out_s) else 0.0


# --------------------------------------------------------------------------- #
# 4. json_validity
# --------------------------------------------------------------------------- #
class JsonValidityEvaluator:
    """output이 유효 JSON인지 검사.

    output이 이미 dict/list인 경우 자동 1.0 (Python 객체는 유효 JSON-호환).
    """

    name = "json_validity"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if isinstance(output, (dict, list)):
            return 1.0
        if not isinstance(output, str):
            return None
        text = output.strip()
        if not text:
            return 0.0
        try:
            json.loads(text)
            return 1.0
        except (json.JSONDecodeError, ValueError):
            return 0.0


# --------------------------------------------------------------------------- #
# 5. json_schema_match
# --------------------------------------------------------------------------- #
class JsonSchemaMatchEvaluator:
    """output이 jsonschema 스키마를 따르는지 검사.

    config:
        schema (dict) — jsonschema 스키마 (필수)
    """

    name = "json_schema_match"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        schema = config.get("schema")
        if not isinstance(schema, dict):
            return None

        # output을 Python 객체로 정규화
        if isinstance(output, str):
            try:
                instance = json.loads(output)
            except (json.JSONDecodeError, ValueError):
                return 0.0
        elif isinstance(output, (dict, list)):
            instance = output
        else:
            return None

        try:
            from jsonschema import validate
            from jsonschema.exceptions import SchemaError, ValidationError
        except ImportError:  # pragma: no cover
            logger.warning("jsonschema_not_installed")
            return None

        try:
            validate(instance=instance, schema=schema)
            return 1.0
        except ValidationError:
            return 0.0
        except SchemaError as exc:
            logger.warning("json_schema_invalid", error=str(exc))
            return None


# --------------------------------------------------------------------------- #
# 6. json_key_presence
# --------------------------------------------------------------------------- #
class JsonKeyPresenceEvaluator:
    """필수 키 존재 비율 (top-level 또는 dot-path).

    config:
        required_keys (list[str]) — 필수 키 목록 (dot-path 지원: "user.name")
    """

    name = "json_key_presence"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        required_keys = config.get("required_keys")
        if not isinstance(required_keys, list) or not required_keys:
            return None

        if isinstance(output, str):
            try:
                obj: Any = json.loads(output)
            except (json.JSONDecodeError, ValueError):
                return 0.0
        elif isinstance(output, (dict, list)):
            obj = output
        else:
            return None

        present = 0
        for raw_key in required_keys:
            if not isinstance(raw_key, str) or not raw_key:
                continue
            if self._key_present(obj, raw_key.split(".")):
                present += 1

        total = sum(1 for k in required_keys if isinstance(k, str) and k)
        if total == 0:
            return None
        return clamp(present / total)

    @staticmethod
    def _key_present(node: Any, path: list[str]) -> bool:
        """dot-path를 따라 key 존재 여부 검사. 중간에 list가 있으면 모든 원소에서 검사."""
        for i, key in enumerate(path):
            if isinstance(node, dict):
                if key not in node:
                    return False
                node = node[key]
            elif isinstance(node, list):
                # 리스트 요소 중 하나라도 path를 만족하면 True
                rest = path[i:]
                return any(
                    JsonKeyPresenceEvaluator._key_present(child, rest)
                    for child in node
                )
            else:
                return False
        return True


# --------------------------------------------------------------------------- #
# 7. levenshtein_similarity
# --------------------------------------------------------------------------- #
def _levenshtein(a: str, b: str) -> int:
    """O(n*m) Levenshtein 편집거리 (자체 구현)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    # 단일 행 갱신으로 메모리 절약
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        curr = [i] + [0] * len(b)
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            curr[j] = min(
                curr[j - 1] + 1,        # insertion
                prev[j] + 1,            # deletion
                prev[j - 1] + cost,     # substitution
            )
        prev = curr
    return prev[-1]


class LevenshteinSimilarityEvaluator:
    """1 - (편집거리 / max(len(a), len(b))). 두 문자열 길이가 0이면 1.0.

    config:
        ignore_case (bool, default False)
        ignore_whitespace (bool, default False)
    """

    name = "levenshtein_similarity"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if expected is None:
            return None
        out_s = _to_string(output)
        exp_s = _to_string(expected)
        if out_s is None or exp_s is None:
            return None

        if config.get("ignore_case", False):
            out_s = out_s.lower()
            exp_s = exp_s.lower()
        if config.get("ignore_whitespace", False):
            out_s = re.sub(r"\s+", " ", out_s).strip()
            exp_s = re.sub(r"\s+", " ", exp_s).strip()

        max_len = max(len(out_s), len(exp_s))
        if max_len == 0:
            return 1.0
        dist = _levenshtein(out_s, exp_s)
        return clamp(1.0 - dist / max_len)


# --------------------------------------------------------------------------- #
# 8. cosine_similarity
# --------------------------------------------------------------------------- #
class CosineSimilarityEvaluator:
    """임베딩 기반 코사인 유사도. LiteLLM 임베딩 API 호출.

    config:
        model (str, default "text-embedding-3-small") — 임베딩 모델
        litellm_client (LiteLLMClient | mock, optional) — 외부 주입
        rescale (bool, default True) — [-1,1] → [0,1]로 선형 변환
    """

    name = "cosine_similarity"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if expected is None:
            return None
        out_s = _to_string(output)
        exp_s = _to_string(expected)
        if out_s is None or exp_s is None:
            return None
        if not out_s or not exp_s:
            return None

        client = config.get("litellm_client")
        if client is None:
            logger.warning("cosine_similarity_no_client")
            return None

        model = str(config.get("model") or "text-embedding-3-small")
        try:
            resp = await client.embedding(model=model, input=[out_s, exp_s])
        except Exception as exc:  # noqa: BLE001
            logger.warning("cosine_similarity_embedding_failed", error=str(exc))
            return None

        try:
            vectors = [item["embedding"] for item in resp["data"]]
        except (KeyError, TypeError, IndexError) as exc:
            logger.warning("cosine_similarity_response_invalid", error=str(exc))
            return None
        if len(vectors) < 2:
            return None

        sim = _cosine(vectors[0], vectors[1])
        if sim is None:
            return None

        rescale = bool(config.get("rescale", True))
        if rescale:
            sim = (sim + 1.0) / 2.0
        return clamp(sim)


def _cosine(a: list[float], b: list[float]) -> float | None:
    """벡터 a, b의 코사인 유사도. 길이 mismatch / zero 벡터 → None."""
    if len(a) != len(b) or not a:
        return None
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return None
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# 9. bleu
# --------------------------------------------------------------------------- #
def _ngrams(tokens: list[str], n: int) -> Counter[tuple[str, ...]]:
    """n-gram Counter."""
    if n <= 0 or len(tokens) < n:
        return Counter()
    return Counter(tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1))


def _modified_precision(
    hyp_tokens: list[str], ref_tokens: list[str], n: int
) -> tuple[int, int]:
    """BLEU 변형 precision: clipped match count / total hyp ngrams."""
    hyp_counts = _ngrams(hyp_tokens, n)
    ref_counts = _ngrams(ref_tokens, n)
    if not hyp_counts:
        return 0, 0
    clipped = sum(min(c, ref_counts.get(ng, 0)) for ng, c in hyp_counts.items())
    total = sum(hyp_counts.values())
    return clipped, total


class BleuEvaluator:
    """간단한 BLEU 스코어 (1~max_n gram, brevity penalty 적용).

    config:
        max_n (int, default 4) — 최대 n-gram (1~4)
        smoothing (bool, default True) — 0 precision 발생 시 epsilon 보정
    """

    name = "bleu"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if expected is None:
            return None
        hyp = _to_string(output)
        ref = _to_string(expected)
        if hyp is None or ref is None:
            return None

        hyp_tokens = hyp.split()
        ref_tokens = ref.split()
        if not hyp_tokens or not ref_tokens:
            return 0.0

        max_n = int(config.get("max_n", 4))
        max_n = max(1, min(max_n, 4))
        smoothing = bool(config.get("smoothing", True))

        log_sum = 0.0
        for n in range(1, max_n + 1):
            clipped, total = _modified_precision(hyp_tokens, ref_tokens, n)
            if total == 0:
                # n-gram 자체가 부족 — n-gram 정밀도가 의미 없음, 건너뜀
                p = 0.0
            else:
                p = clipped / total
            if p == 0.0:
                if smoothing:
                    # epsilon smoothing — 매우 작은 값
                    p = 1e-9
                else:
                    return 0.0
            log_sum += math.log(p)

        bp = self._brevity_penalty(len(hyp_tokens), len(ref_tokens))
        score = bp * math.exp(log_sum / max_n)
        return clamp(score)

    @staticmethod
    def _brevity_penalty(hyp_len: int, ref_len: int) -> float:
        if hyp_len == 0:
            return 0.0
        if hyp_len >= ref_len:
            return 1.0
        return math.exp(1.0 - ref_len / hyp_len)


# --------------------------------------------------------------------------- #
# 10. rouge
# --------------------------------------------------------------------------- #
def _lcs_length(a: list[str], b: list[str]) -> int:
    """LCS(Longest Common Subsequence) 길이."""
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        curr = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            if x == y:
                curr[j] = prev[j - 1] + 1
            else:
                curr[j] = max(prev[j], curr[j - 1])
        prev = curr
    return prev[-1]


class RougeEvaluator:
    """ROUGE-L F1 스코어 (LCS 기반 자체 구현).

    config:
        beta (float, default 1.0) — F-measure beta (1.0 = F1)
    """

    name = "rouge"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        if expected is None:
            return None
        hyp = _to_string(output)
        ref = _to_string(expected)
        if hyp is None or ref is None:
            return None
        hyp_tokens = hyp.split()
        ref_tokens = ref.split()
        if not hyp_tokens or not ref_tokens:
            return 0.0

        lcs = _lcs_length(hyp_tokens, ref_tokens)
        if lcs == 0:
            return 0.0

        precision = lcs / len(hyp_tokens)
        recall = lcs / len(ref_tokens)
        beta = float(config.get("beta", 1.0))
        beta_sq = beta * beta
        denom = precision + beta_sq * recall
        if denom == 0:
            return 0.0
        f = (1 + beta_sq) * precision * recall / denom
        return clamp(f)


# --------------------------------------------------------------------------- #
# 11. latency_check
# --------------------------------------------------------------------------- #
class LatencyCheckEvaluator:
    """metadata.latency_ms ≤ threshold_ms 검사.

    config:
        threshold_ms (float) — 임계값 (ms, 필수)
    """

    name = "latency_check"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        threshold = config.get("threshold_ms")
        if threshold is None:
            return None
        try:
            threshold_v = float(threshold)
        except (TypeError, ValueError):
            return None

        latency = metadata.get("latency_ms")
        if latency is None:
            return None
        try:
            latency_v = float(latency)
        except (TypeError, ValueError):
            return None
        return 1.0 if latency_v <= threshold_v else 0.0


# --------------------------------------------------------------------------- #
# 12. token_budget_check
# --------------------------------------------------------------------------- #
class TokenBudgetCheckEvaluator:
    """metadata.output_tokens ≤ budget 검사.

    config:
        budget (int) — 토큰 예산 (필수)
        scope ("output" | "total" | "prompt", default "output") — 검사 대상
    """

    name = "token_budget_check"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        budget = config.get("budget")
        if budget is None:
            return None
        try:
            budget_v = float(budget)
        except (TypeError, ValueError):
            return None

        scope = str(config.get("scope", "output")).lower()
        key_map = {
            "output": "output_tokens",
            "total": "total_tokens",
            "prompt": "prompt_tokens",
        }
        key = key_map.get(scope, "output_tokens")
        tokens = metadata.get(key)
        if tokens is None:
            return None
        try:
            tokens_v = float(tokens)
        except (TypeError, ValueError):
            return None
        return 1.0 if tokens_v <= budget_v else 0.0


# --------------------------------------------------------------------------- #
# 13. cost_check
# --------------------------------------------------------------------------- #
class CostCheckEvaluator:
    """metadata.cost_usd ≤ threshold_usd 검사.

    config:
        threshold_usd (float) — 비용 임계값 ($, 필수)
    """

    name = "cost_check"

    async def evaluate(
        self,
        output: str | dict[str, Any] | list[Any],
        expected: str | dict[str, Any] | list[Any] | None,
        metadata: dict[str, Any],
        **config: Any,
    ) -> float | None:
        threshold = config.get("threshold_usd")
        if threshold is None:
            return None
        try:
            threshold_v = float(threshold)
        except (TypeError, ValueError):
            return None

        cost = metadata.get("cost_usd")
        if cost is None:
            return None
        try:
            cost_v = float(cost)
        except (TypeError, ValueError):
            return None
        return 1.0 if cost_v <= threshold_v else 0.0


__all__ = [
    "BleuEvaluator",
    "ContainsEvaluator",
    "CosineSimilarityEvaluator",
    "CostCheckEvaluator",
    "ExactMatchEvaluator",
    "JsonKeyPresenceEvaluator",
    "JsonSchemaMatchEvaluator",
    "JsonValidityEvaluator",
    "LatencyCheckEvaluator",
    "LevenshteinSimilarityEvaluator",
    "RegexMatchEvaluator",
    "RougeEvaluator",
    "TokenBudgetCheckEvaluator",
]
