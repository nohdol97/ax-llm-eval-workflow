"""13개 내장 evaluator 단위 테스트.

각 evaluator의 happy path / edge case / 임계값 / 예외 처리를 검증한다.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from app.evaluators.base import clamp
from app.evaluators.built_in import (
    BleuEvaluator,
    ContainsEvaluator,
    CosineSimilarityEvaluator,
    CostCheckEvaluator,
    ExactMatchEvaluator,
    JsonKeyPresenceEvaluator,
    JsonSchemaMatchEvaluator,
    JsonValidityEvaluator,
    LatencyCheckEvaluator,
    LevenshteinSimilarityEvaluator,
    RegexMatchEvaluator,
    RougeEvaluator,
    TokenBudgetCheckEvaluator,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# clamp 헬퍼
# --------------------------------------------------------------------------- #
class TestClamp:
    def test_clamp_returns_None_when_value_is_None(self) -> None:
        assert clamp(None) is None

    def test_clamp_returns_value_when_in_range(self) -> None:
        assert clamp(0.5) == 0.5

    def test_clamp_lower_bound(self) -> None:
        assert clamp(-0.3) == 0.0

    def test_clamp_upper_bound(self) -> None:
        assert clamp(1.5) == 1.0

    def test_clamp_returns_None_when_NaN(self) -> None:
        assert clamp(float("nan")) is None

    def test_clamp_returns_None_when_infinity(self) -> None:
        assert clamp(float("inf")) is None
        assert clamp(float("-inf")) is None


# --------------------------------------------------------------------------- #
# 1. exact_match
# --------------------------------------------------------------------------- #
class TestExactMatch:
    @pytest.mark.asyncio
    async def test_exact_match_returns_1_when_strings_identical(self) -> None:
        ev = ExactMatchEvaluator()
        assert (await ev.evaluate("hello", "hello", {})) == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_returns_0_when_strings_differ(self) -> None:
        ev = ExactMatchEvaluator()
        assert (await ev.evaluate("hello", "world", {})) == 0.0

    @pytest.mark.asyncio
    async def test_exact_match_ignores_case_by_default(self) -> None:
        ev = ExactMatchEvaluator()
        assert (await ev.evaluate("Hello", "hello", {})) == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_respects_case_when_disabled(self) -> None:
        ev = ExactMatchEvaluator()
        result = await ev.evaluate("Hello", "hello", {}, ignore_case=False)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_exact_match_ignores_whitespace_by_default(self) -> None:
        ev = ExactMatchEvaluator()
        assert (await ev.evaluate("  hello  world  ", "hello world", {})) == 1.0

    @pytest.mark.asyncio
    async def test_exact_match_returns_None_when_expected_None(self) -> None:
        ev = ExactMatchEvaluator()
        assert (await ev.evaluate("hello", None, {})) is None

    @pytest.mark.asyncio
    async def test_exact_match_handles_dict_via_json_serialization(self) -> None:
        ev = ExactMatchEvaluator()
        result = await ev.evaluate({"a": 1, "b": 2}, {"b": 2, "a": 1}, {})
        assert result == 1.0  # sort_keys로 직렬화되어 동일


# --------------------------------------------------------------------------- #
# 2. contains
# --------------------------------------------------------------------------- #
class TestContains:
    @pytest.mark.asyncio
    async def test_contains_AND_mode_all_keywords_present_returns_1(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate(
            "the quick brown fox", None, {}, keywords=["quick", "fox"], mode="all"
        )
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_contains_AND_mode_one_keyword_missing_returns_0(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate(
            "the quick brown fox", None, {}, keywords=["quick", "cat"], mode="all"
        )
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_contains_OR_mode_one_keyword_present_returns_1(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate(
            "the quick brown fox", None, {}, keywords=["cat", "fox"], mode="any"
        )
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_contains_OR_mode_no_keyword_present_returns_0(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate("hello", None, {}, keywords=["foo", "bar"], mode="any")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_contains_uses_expected_when_keywords_omitted(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate("the quick brown fox", "fox", {})
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_contains_returns_None_when_no_keywords_resolved(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate("hello", None, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_contains_case_sensitive_when_disabled(self) -> None:
        ev = ContainsEvaluator()
        result = await ev.evaluate("FOX", None, {}, keywords=["fox"], ignore_case=False)
        assert result == 0.0


# --------------------------------------------------------------------------- #
# 3. regex_match
# --------------------------------------------------------------------------- #
class TestRegexMatch:
    @pytest.mark.asyncio
    async def test_regex_match_search_returns_1(self) -> None:
        ev = RegexMatchEvaluator()
        result = await ev.evaluate("phone: 010-1234-5678", None, {}, pattern=r"\d{3}-\d{4}-\d{4}")
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_regex_match_no_match_returns_0(self) -> None:
        ev = RegexMatchEvaluator()
        result = await ev.evaluate("hello world", None, {}, pattern=r"\d+")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_regex_match_full_match_mode(self) -> None:
        ev = RegexMatchEvaluator()
        result = await ev.evaluate("abc123", None, {}, pattern=r"abc", full_match=True)
        assert result == 0.0  # search hits but fullmatch fails

    @pytest.mark.asyncio
    async def test_regex_match_with_IGNORECASE_flag(self) -> None:
        ev = RegexMatchEvaluator()
        result = await ev.evaluate("HELLO", None, {}, pattern="hello", flags="IGNORECASE")
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_regex_match_returns_None_when_pattern_invalid(self) -> None:
        ev = RegexMatchEvaluator()
        result = await ev.evaluate("hello", None, {}, pattern="[")
        assert result is None

    @pytest.mark.asyncio
    async def test_regex_match_returns_None_when_pattern_missing(self) -> None:
        ev = RegexMatchEvaluator()
        assert (await ev.evaluate("hello", None, {})) is None


# --------------------------------------------------------------------------- #
# 4. json_validity
# --------------------------------------------------------------------------- #
class TestJsonValidity:
    @pytest.mark.asyncio
    async def test_json_validity_valid_object_string_returns_1(self) -> None:
        ev = JsonValidityEvaluator()
        assert (await ev.evaluate('{"a": 1}', None, {})) == 1.0

    @pytest.mark.asyncio
    async def test_json_validity_valid_array_string_returns_1(self) -> None:
        ev = JsonValidityEvaluator()
        assert (await ev.evaluate("[1,2,3]", None, {})) == 1.0

    @pytest.mark.asyncio
    async def test_json_validity_invalid_string_returns_0(self) -> None:
        ev = JsonValidityEvaluator()
        assert (await ev.evaluate("{not json}", None, {})) == 0.0

    @pytest.mark.asyncio
    async def test_json_validity_empty_string_returns_0(self) -> None:
        ev = JsonValidityEvaluator()
        assert (await ev.evaluate("   ", None, {})) == 0.0

    @pytest.mark.asyncio
    async def test_json_validity_dict_input_returns_1(self) -> None:
        ev = JsonValidityEvaluator()
        assert (await ev.evaluate({"a": 1}, None, {})) == 1.0


# --------------------------------------------------------------------------- #
# 5. json_schema_match
# --------------------------------------------------------------------------- #
class TestJsonSchemaMatch:
    @pytest.mark.asyncio
    async def test_json_schema_match_passes_returns_1(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
            "required": ["name", "age"],
        }
        result = await ev.evaluate('{"name": "Alice", "age": 30}', None, {}, schema=schema)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_json_schema_match_fails_when_required_key_missing(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        schema = {"type": "object", "required": ["name"]}
        result = await ev.evaluate('{"age": 30}', None, {}, schema=schema)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_json_schema_match_fails_with_invalid_json(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        result = await ev.evaluate("not json", None, {}, schema={"type": "object"})
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_json_schema_match_returns_None_when_no_schema(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        assert (await ev.evaluate('{"a": 1}', None, {})) is None

    @pytest.mark.asyncio
    async def test_json_schema_match_returns_None_for_invalid_schema(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        # 잘못된 type 키워드
        result = await ev.evaluate('{"a": 1}', None, {}, schema={"type": 42})
        assert result is None

    @pytest.mark.asyncio
    async def test_json_schema_match_accepts_dict_input(self) -> None:
        ev = JsonSchemaMatchEvaluator()
        schema = {"type": "object", "required": ["a"]}
        result = await ev.evaluate({"a": 1}, None, {}, schema=schema)
        assert result == 1.0


# --------------------------------------------------------------------------- #
# 6. json_key_presence
# --------------------------------------------------------------------------- #
class TestJsonKeyPresence:
    @pytest.mark.asyncio
    async def test_json_key_presence_all_keys_present_returns_1(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        result = await ev.evaluate('{"a": 1, "b": 2}', None, {}, required_keys=["a", "b"])
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_json_key_presence_partial_returns_ratio(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        result = await ev.evaluate('{"a": 1}', None, {}, required_keys=["a", "b", "c", "d"])
        assert result is not None
        assert abs(result - 0.25) < 1e-9

    @pytest.mark.asyncio
    async def test_json_key_presence_dot_path_nested(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        obj = {"user": {"name": "Alice", "age": 30}}
        result = await ev.evaluate(
            json.dumps(obj), None, {}, required_keys=["user.name", "user.age"]
        )
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_json_key_presence_dot_path_missing(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        result = await ev.evaluate(
            '{"user": {"name": "A"}}',
            None,
            {},
            required_keys=["user.name", "user.email"],
        )
        assert result == 0.5

    @pytest.mark.asyncio
    async def test_json_key_presence_returns_0_for_invalid_json(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        result = await ev.evaluate("not json", None, {}, required_keys=["a"])
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_json_key_presence_returns_None_when_no_keys(self) -> None:
        ev = JsonKeyPresenceEvaluator()
        assert (await ev.evaluate('{"a":1}', None, {})) is None


# --------------------------------------------------------------------------- #
# 7. levenshtein_similarity
# --------------------------------------------------------------------------- #
class TestLevenshteinSimilarity:
    @pytest.mark.asyncio
    async def test_levenshtein_identical_strings_return_1(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        assert (await ev.evaluate("hello", "hello", {})) == 1.0

    @pytest.mark.asyncio
    async def test_levenshtein_completely_different_low_score(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        result = await ev.evaluate("abcde", "fghij", {})
        assert result == 0.0  # 5/5 차이

    @pytest.mark.asyncio
    async def test_levenshtein_one_char_diff(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        # "kitten" vs "sitten" → 1 substitution / max_len=6 → 1 - 1/6 ≈ 0.833
        result = await ev.evaluate("kitten", "sitten", {})
        assert result is not None
        assert abs(result - (1 - 1 / 6)) < 1e-9

    @pytest.mark.asyncio
    async def test_levenshtein_classic_kitten_sitting(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        # 잘 알려진 거리 3 / max=7 → 1 - 3/7
        result = await ev.evaluate("kitten", "sitting", {})
        assert result is not None
        assert abs(result - (1 - 3 / 7)) < 1e-9

    @pytest.mark.asyncio
    async def test_levenshtein_both_empty_return_1(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        assert (await ev.evaluate("", "", {})) == 1.0

    @pytest.mark.asyncio
    async def test_levenshtein_None_expected_return_None(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        assert (await ev.evaluate("hello", None, {})) is None

    @pytest.mark.asyncio
    async def test_levenshtein_ignore_case_option(self) -> None:
        ev = LevenshteinSimilarityEvaluator()
        result = await ev.evaluate("Hello", "hello", {}, ignore_case=True)
        assert result == 1.0


# --------------------------------------------------------------------------- #
# 8. cosine_similarity (mock litellm 사용)
# --------------------------------------------------------------------------- #
class _StubEmbeddingClient:
    """결정론적 임베딩 mock — 명시 vector 또는 None(실패 시뮬레이션)."""

    def __init__(self, vectors: list[list[float]] | None = None) -> None:
        self._vectors = vectors
        self.calls: list[dict[str, Any]] = []

    async def embedding(
        self,
        model: str,
        input: list[str] | str,  # noqa: A002
    ) -> dict[str, Any]:
        self.calls.append({"model": model, "input": input})
        if self._vectors is None:
            raise RuntimeError("simulated failure")
        return {"data": [{"embedding": vec, "index": i} for i, vec in enumerate(self._vectors)]}


class TestCosineSimilarity:
    @pytest.mark.asyncio
    async def test_cosine_identical_vectors_return_1_after_rescale(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=[[1.0, 0.0], [1.0, 0.0]])
        result = await ev.evaluate("a", "a", {}, litellm_client=client, model="m")
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_cosine_orthogonal_returns_0_5_after_rescale(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=[[1.0, 0.0], [0.0, 1.0]])
        result = await ev.evaluate("a", "b", {}, litellm_client=client)
        assert result is not None
        assert abs(result - 0.5) < 1e-9  # cos=0 → (0+1)/2 = 0.5

    @pytest.mark.asyncio
    async def test_cosine_opposite_returns_0_after_rescale(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=[[1.0, 0.0], [-1.0, 0.0]])
        result = await ev.evaluate("a", "b", {}, litellm_client=client)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_cosine_no_rescale_returns_raw(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=[[1.0, 0.0], [1.0, 0.0]])
        result = await ev.evaluate("a", "a", {}, litellm_client=client, rescale=False)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_cosine_returns_None_on_embedding_failure(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=None)
        result = await ev.evaluate("a", "b", {}, litellm_client=client)
        assert result is None

    @pytest.mark.asyncio
    async def test_cosine_returns_None_when_no_client(self) -> None:
        ev = CosineSimilarityEvaluator()
        result = await ev.evaluate("a", "b", {})
        assert result is None

    @pytest.mark.asyncio
    async def test_cosine_returns_None_when_expected_None(self) -> None:
        ev = CosineSimilarityEvaluator()
        client = _StubEmbeddingClient(vectors=[[1.0], [1.0]])
        result = await ev.evaluate("a", None, {}, litellm_client=client)
        assert result is None


# --------------------------------------------------------------------------- #
# 9. bleu
# --------------------------------------------------------------------------- #
class TestBleu:
    @pytest.mark.asyncio
    async def test_bleu_identical_sentences_return_1(self) -> None:
        ev = BleuEvaluator()
        text = "the quick brown fox jumps over the lazy dog"
        result = await ev.evaluate(text, text, {})
        assert result is not None
        assert result > 0.99

    @pytest.mark.asyncio
    async def test_bleu_completely_different_returns_low(self) -> None:
        ev = BleuEvaluator()
        result = await ev.evaluate("alpha beta gamma delta", "one two three four", {})
        assert result is not None
        assert result < 0.01

    @pytest.mark.asyncio
    async def test_bleu_partial_overlap(self) -> None:
        ev = BleuEvaluator()
        # 일부 unigram 일치
        result = await ev.evaluate("the cat sat on mat", "the dog sat on rug", {})
        assert result is not None
        assert 0.0 < result < 1.0

    @pytest.mark.asyncio
    async def test_bleu_empty_hypothesis_returns_0(self) -> None:
        ev = BleuEvaluator()
        result = await ev.evaluate("", "the dog", {})
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_bleu_returns_None_when_expected_None(self) -> None:
        ev = BleuEvaluator()
        assert (await ev.evaluate("hello", None, {})) is None

    @pytest.mark.asyncio
    async def test_bleu_max_n_clamped_to_1_to_4(self) -> None:
        ev = BleuEvaluator()
        text = "one two three"
        # max_n=10이지만 내부적으로 4로 clamp
        result = await ev.evaluate(text, text, {}, max_n=10)
        assert result is not None
        assert result > 0.0


# --------------------------------------------------------------------------- #
# 10. rouge (ROUGE-L)
# --------------------------------------------------------------------------- #
class TestRouge:
    @pytest.mark.asyncio
    async def test_rouge_identical_returns_1(self) -> None:
        ev = RougeEvaluator()
        result = await ev.evaluate("the quick brown fox", "the quick brown fox", {})
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_rouge_no_overlap_returns_0(self) -> None:
        ev = RougeEvaluator()
        result = await ev.evaluate("alpha beta", "gamma delta", {})
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_rouge_partial_subsequence(self) -> None:
        ev = RougeEvaluator()
        # LCS = "the cat sat" (len 3), hyp_len=4, ref_len=5
        result = await ev.evaluate("the cat sat down", "the cat sat on mat", {})
        assert result is not None
        # P=3/4, R=3/5 → F1 = 2*0.75*0.6 / 1.35 ≈ 0.6667
        assert abs(result - (2 * 0.75 * 0.6) / (0.75 + 0.6)) < 1e-9

    @pytest.mark.asyncio
    async def test_rouge_empty_returns_0(self) -> None:
        ev = RougeEvaluator()
        assert (await ev.evaluate("", "the dog", {})) == 0.0

    @pytest.mark.asyncio
    async def test_rouge_None_expected_returns_None(self) -> None:
        ev = RougeEvaluator()
        assert (await ev.evaluate("hello", None, {})) is None


# --------------------------------------------------------------------------- #
# 11. latency_check
# --------------------------------------------------------------------------- #
class TestLatencyCheck:
    @pytest.mark.asyncio
    async def test_latency_under_threshold_returns_1(self) -> None:
        ev = LatencyCheckEvaluator()
        result = await ev.evaluate("x", None, {"latency_ms": 1500}, threshold_ms=2000)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_latency_at_threshold_returns_1(self) -> None:
        ev = LatencyCheckEvaluator()
        result = await ev.evaluate("x", None, {"latency_ms": 2000}, threshold_ms=2000)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_latency_over_threshold_returns_0(self) -> None:
        ev = LatencyCheckEvaluator()
        result = await ev.evaluate("x", None, {"latency_ms": 2500}, threshold_ms=2000)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_latency_returns_None_when_no_threshold(self) -> None:
        ev = LatencyCheckEvaluator()
        result = await ev.evaluate(
            "x",
            None,
            {"latency_ms": 100},
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_latency_returns_None_when_no_metadata_value(self) -> None:
        ev = LatencyCheckEvaluator()
        result = await ev.evaluate("x", None, {}, threshold_ms=2000)
        assert result is None


# --------------------------------------------------------------------------- #
# 12. token_budget_check
# --------------------------------------------------------------------------- #
class TestTokenBudgetCheck:
    @pytest.mark.asyncio
    async def test_token_under_budget_returns_1(self) -> None:
        ev = TokenBudgetCheckEvaluator()
        result = await ev.evaluate("x", None, {"output_tokens": 50}, budget=100)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_token_over_budget_returns_0(self) -> None:
        ev = TokenBudgetCheckEvaluator()
        result = await ev.evaluate("x", None, {"output_tokens": 150}, budget=100)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_token_scope_total(self) -> None:
        ev = TokenBudgetCheckEvaluator()
        result = await ev.evaluate("x", None, {"total_tokens": 500}, budget=1000, scope="total")
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_token_scope_prompt(self) -> None:
        ev = TokenBudgetCheckEvaluator()
        result = await ev.evaluate("x", None, {"prompt_tokens": 1500}, budget=1000, scope="prompt")
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_token_returns_None_when_metadata_missing(self) -> None:
        ev = TokenBudgetCheckEvaluator()
        result = await ev.evaluate("x", None, {}, budget=100)
        assert result is None


# --------------------------------------------------------------------------- #
# 13. cost_check
# --------------------------------------------------------------------------- #
class TestCostCheck:
    @pytest.mark.asyncio
    async def test_cost_under_threshold_returns_1(self) -> None:
        ev = CostCheckEvaluator()
        result = await ev.evaluate("x", None, {"cost_usd": 0.001}, threshold_usd=0.01)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_cost_over_threshold_returns_0(self) -> None:
        ev = CostCheckEvaluator()
        result = await ev.evaluate("x", None, {"cost_usd": 0.05}, threshold_usd=0.01)
        assert result == 0.0

    @pytest.mark.asyncio
    async def test_cost_at_threshold_returns_1(self) -> None:
        ev = CostCheckEvaluator()
        result = await ev.evaluate("x", None, {"cost_usd": 0.01}, threshold_usd=0.01)
        assert result == 1.0

    @pytest.mark.asyncio
    async def test_cost_returns_None_when_no_metadata(self) -> None:
        ev = CostCheckEvaluator()
        result = await ev.evaluate("x", None, {}, threshold_usd=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_cost_returns_None_when_no_threshold(self) -> None:
        ev = CostCheckEvaluator()
        result = await ev.evaluate("x", None, {"cost_usd": 0.001})
        assert result is None


# --------------------------------------------------------------------------- #
# Registry sanity
# --------------------------------------------------------------------------- #
class TestRegistry:
    def test_registry_has_13_built_in_evaluators(self) -> None:
        from app.evaluators.registry import BUILT_IN_REGISTRY

        assert len(BUILT_IN_REGISTRY) == 13

    def test_registry_get_built_in_returns_class(self) -> None:
        from app.evaluators.registry import get_built_in

        cls = get_built_in("exact_match")
        assert cls is ExactMatchEvaluator

    def test_registry_get_built_in_raises_on_unknown(self) -> None:
        from app.evaluators.registry import get_built_in

        with pytest.raises(KeyError):
            get_built_in("nonexistent")

    def test_list_built_in_returns_metadata_for_all(self) -> None:
        from app.evaluators.registry import list_built_in

        items = list_built_in()
        assert len(items) == 13
        names = {item["name"] for item in items}
        assert "exact_match" in names
        assert "cosine_similarity" in names
        # 모든 항목이 필수 키를 가짐
        for item in items:
            assert "name" in item
            assert "description" in item
            assert "data_type" in item
            assert "range" in item
            assert "config_schema" in item
