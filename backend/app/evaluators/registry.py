"""내장 evaluator 카탈로그.

UI는 :func:`list_built_in`을 호출하여 evaluator 메타데이터(이름/설명/range/config schema)를
받아 사용자에게 노출한다. Pipeline은 :func:`get_built_in`으로 클래스를 조회한다.

Phase 8-A-2 추가: trace evaluator 카탈로그(:data:`TRACE_BUILT_IN_REGISTRY`,
:func:`get_trace_built_in`, :func:`list_trace_built_in`).
"""

from __future__ import annotations

from typing import Any

from app.evaluators.base import Evaluator
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
from app.evaluators.trace_built_in import (
    AgentLoopBoundedEvaluator,
    ErrorRecoveryAttemptedEvaluator,
    HallucinationCheckEvaluator,
    LatencyBreakdownHealthyEvaluator,
    NoErrorSpansEvaluator,
    ToolCallCountInRangeEvaluator,
    ToolCalledEvaluator,
    ToolCalledWithArgsEvaluator,
    ToolCallSequenceEvaluator,
    ToolResultGroundingEvaluator,
)

# 13개 내장 evaluator 카탈로그
BUILT_IN_REGISTRY: dict[str, type[Evaluator]] = {
    "exact_match": ExactMatchEvaluator,
    "contains": ContainsEvaluator,
    "regex_match": RegexMatchEvaluator,
    "json_validity": JsonValidityEvaluator,
    "json_schema_match": JsonSchemaMatchEvaluator,
    "json_key_presence": JsonKeyPresenceEvaluator,
    "levenshtein_similarity": LevenshteinSimilarityEvaluator,
    "cosine_similarity": CosineSimilarityEvaluator,
    "bleu": BleuEvaluator,
    "rouge": RougeEvaluator,
    "latency_check": LatencyCheckEvaluator,
    "token_budget_check": TokenBudgetCheckEvaluator,
    "cost_check": CostCheckEvaluator,
}


# 메타데이터 — UI 노출용
_METADATA: dict[str, dict[str, Any]] = {
    "exact_match": {
        "description": "output과 expected가 정확히 일치하는지 (대소문자/공백 정규화 옵션)",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {
                "ignore_case": {"type": "boolean", "default": True},
                "ignore_whitespace": {"type": "boolean", "default": True},
            },
        },
    },
    "contains": {
        "description": "output이 키워드를 포함하는지 (AND/OR 조건)",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {
                "keywords": {"type": "array", "items": {"type": "string"}},
                "mode": {"type": "string", "enum": ["all", "any"], "default": "all"},
                "ignore_case": {"type": "boolean", "default": True},
            },
        },
    },
    "regex_match": {
        "description": "output이 정규식 패턴에 매칭되는지",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["pattern"],
            "properties": {
                "pattern": {"type": "string"},
                "flags": {"type": "string", "default": ""},
                "full_match": {"type": "boolean", "default": False},
            },
        },
    },
    "json_validity": {
        "description": "output이 유효 JSON인지",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {"type": "object", "properties": {}},
    },
    "json_schema_match": {
        "description": "output이 jsonschema 스키마를 따르는지",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["schema"],
            "properties": {"schema": {"type": "object"}},
        },
    },
    "json_key_presence": {
        "description": "필수 키 존재 비율 (dot-path 지원)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["required_keys"],
            "properties": {"required_keys": {"type": "array", "items": {"type": "string"}}},
        },
    },
    "levenshtein_similarity": {
        "description": "1 - (편집거리 / max(len)) — 문자 수준 유사도",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {
                "ignore_case": {"type": "boolean", "default": False},
                "ignore_whitespace": {"type": "boolean", "default": False},
            },
        },
    },
    "cosine_similarity": {
        "description": "임베딩 코사인 유사도 (LiteLLM 임베딩 API)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "default": "text-embedding-3-small"},
                "rescale": {"type": "boolean", "default": True},
            },
        },
    },
    "bleu": {
        "description": "BLEU n-gram precision 스코어 (1~4 gram 평균)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {
                "max_n": {"type": "integer", "minimum": 1, "maximum": 4, "default": 4},
                "smoothing": {"type": "boolean", "default": True},
            },
        },
    },
    "rouge": {
        "description": "ROUGE-L F1 (LCS 기반)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "properties": {"beta": {"type": "number", "default": 1.0}},
        },
    },
    "latency_check": {
        "description": "metadata.latency_ms ≤ threshold_ms 검사",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["threshold_ms"],
            "properties": {"threshold_ms": {"type": "number"}},
        },
    },
    "token_budget_check": {
        "description": "토큰 사용량이 예산 이하인지 검사",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["budget"],
            "properties": {
                "budget": {"type": "integer", "minimum": 0},
                "scope": {
                    "type": "string",
                    "enum": ["output", "total", "prompt"],
                    "default": "output",
                },
            },
        },
    },
    "cost_check": {
        "description": "비용($)이 임계값 이하인지 검사",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "config_schema": {
            "type": "object",
            "required": ["threshold_usd"],
            "properties": {"threshold_usd": {"type": "number", "minimum": 0}},
        },
    },
}


def get_built_in(name: str) -> type[Evaluator]:
    """이름으로 evaluator 클래스 조회.

    Raises:
        KeyError: 등록되지 않은 이름인 경우.
    """
    if name not in BUILT_IN_REGISTRY:
        raise KeyError(f"내장 evaluator '{name}'이(가) 존재하지 않습니다.")
    return BUILT_IN_REGISTRY[name]


def list_built_in() -> list[dict[str, Any]]:
    """전체 내장 evaluator 메타데이터 목록 (UI 노출용).

    각 항목: ``{name, description, data_type, range, config_schema}``
    """
    items: list[dict[str, Any]] = []
    for name in BUILT_IN_REGISTRY:
        meta = _METADATA.get(name, {})
        items.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "data_type": meta.get("data_type", "NUMERIC"),
                "range": meta.get("range", [0.0, 1.0]),
                "config_schema": meta.get("config_schema", {"type": "object"}),
            }
        )
    return items


# --------------------------------------------------------------------------- #
# Trace Evaluator 카탈로그 (Phase 8-A-2)
# --------------------------------------------------------------------------- #

# 10종 trace evaluator — docs/AGENT_EVAL.md §5.2
TRACE_BUILT_IN_REGISTRY: dict[str, type] = {
    "tool_called": ToolCalledEvaluator,
    "tool_called_with_args": ToolCalledWithArgsEvaluator,
    "tool_call_sequence": ToolCallSequenceEvaluator,
    "tool_call_count_in_range": ToolCallCountInRangeEvaluator,
    "no_error_spans": NoErrorSpansEvaluator,
    "error_recovery_attempted": ErrorRecoveryAttemptedEvaluator,
    "agent_loop_bounded": AgentLoopBoundedEvaluator,
    "latency_breakdown_healthy": LatencyBreakdownHealthyEvaluator,
    "tool_result_grounding": ToolResultGroundingEvaluator,
    "hallucination_check": HallucinationCheckEvaluator,
}


# UI 노출용 메타데이터
_TRACE_METADATA: dict[str, dict[str, Any]] = {
    "tool_called": {
        "description": "특정 tool이 trace에 호출됐는지 (0/1)",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "required": ["tool_name"],
            "properties": {"tool_name": {"type": "string"}},
        },
    },
    "tool_called_with_args": {
        "description": "tool이 정해진 args 패턴으로 호출됐는지 (0~1, 매칭 비율)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "required": ["tool_name", "args_match"],
            "properties": {
                "tool_name": {"type": "string"},
                "args_match": {
                    "type": "object",
                    "additionalProperties": {"type": ["string", "number", "boolean"]},
                },
            },
        },
    },
    "tool_call_sequence": {
        "description": "정해진 순서대로 tool이 호출됐는지 (strict / subsequence)",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "required": ["sequence"],
            "properties": {
                "sequence": {"type": "array", "items": {"type": "string"}},
                "strict": {"type": "boolean", "default": False},
            },
        },
    },
    "tool_call_count_in_range": {
        "description": "tool 호출 횟수가 [min, max] 범위인지",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "required": ["min", "max"],
            "properties": {
                "min": {"type": "integer", "minimum": 0},
                "max": {"type": "integer", "minimum": 0},
                "tool_name": {"type": "string"},
            },
        },
    },
    "no_error_spans": {
        "description": "level=ERROR span이 0개인지 (0/1)",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "properties": {
                "ignore_names": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "error_recovery_attempted": {
        "description": "error span 발생 후 재시도 비율 (error 없으면 N/A)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {"type": "object", "properties": {}},
    },
    "agent_loop_bounded": {
        "description": "generation 호출 수가 max_generations 이하인지",
        "data_type": "BOOLEAN",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "properties": {
                "max_generations": {"type": "integer", "minimum": 0, "default": 10},
            },
        },
    },
    "latency_breakdown_healthy": {
        "description": "단계별 지연이 임계 이하인지 (위반 비율 기반)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "requires_llm": False,
        "config_schema": {
            "type": "object",
            "properties": {
                "tool_max_ms": {"type": "number", "minimum": 0},
                "llm_max_ms": {"type": "number", "minimum": 0},
            },
        },
    },
    "tool_result_grounding": {
        "description": "tool 결과 ↔ final output 인용 일치성 (LLM Judge)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "requires_llm": True,
        "config_schema": {
            "type": "object",
            "properties": {"judge_model": {"type": "string", "default": "gpt-4o"}},
        },
    },
    "hallucination_check": {
        "description": "output에 tool 결과 외 fact가 있는지 (LLM Judge)",
        "data_type": "NUMERIC",
        "range": [0.0, 1.0],
        "requires_llm": True,
        "config_schema": {
            "type": "object",
            "properties": {"judge_model": {"type": "string", "default": "gpt-4o"}},
        },
    },
}


def get_trace_built_in(name: str) -> type:
    """이름으로 trace evaluator 클래스 조회.

    Raises:
        KeyError: 등록되지 않은 이름인 경우.
    """
    if name not in TRACE_BUILT_IN_REGISTRY:
        raise KeyError(f"trace evaluator '{name}'이(가) 존재하지 않습니다.")
    return TRACE_BUILT_IN_REGISTRY[name]


def list_trace_built_in() -> list[dict[str, Any]]:
    """전체 trace evaluator 메타데이터 목록 (UI 카탈로그용).

    각 항목: ``{name, description, data_type, range, requires_llm, config_schema}``
    """
    items: list[dict[str, Any]] = []
    for name in TRACE_BUILT_IN_REGISTRY:
        meta = _TRACE_METADATA.get(name, {})
        items.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "data_type": meta.get("data_type", "NUMERIC"),
                "range": meta.get("range", [0.0, 1.0]),
                "requires_llm": meta.get("requires_llm", False),
                "config_schema": meta.get("config_schema", {"type": "object"}),
            }
        )
    return items


__all__ = [
    "BUILT_IN_REGISTRY",
    "TRACE_BUILT_IN_REGISTRY",
    "get_built_in",
    "get_trace_built_in",
    "list_built_in",
    "list_trace_built_in",
]
