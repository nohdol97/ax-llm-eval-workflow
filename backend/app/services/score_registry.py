"""Langfuse Score Config idempotent 등록 (부팅 훅).

본 프로젝트 평가 함수 카탈로그를 Langfuse Score Config로 1회 등록한다. 이미 존재하는
경우 ``register_score_config``는 idempotent 동작으로 기존 id를 반환한다.

frontend의 mock data와 일치하는 14종 evaluator를 정의한다.
"""

from __future__ import annotations

from typing import Any, Literal

from app.core.errors import LangfuseError
from app.core.logging import get_logger
from app.services.langfuse_client import LangfuseClient

logger = get_logger(__name__)

ScoreDataType = Literal["BOOLEAN", "NUMERIC", "CATEGORICAL"]

# evaluator 카탈로그 — frontend mock과 동일한 14종
EVALUATOR_CATALOG: list[dict[str, Any]] = [
    # ----- 결정론적 (Deterministic) -----
    {
        "name": "exact_match",
        "data_type": "BOOLEAN",
        "range": (0, 1),
        "description": "정답 문자열과 완전 일치 여부",
    },
    {
        "name": "contains",
        "data_type": "BOOLEAN",
        "range": (0, 1),
        "description": "정답 문자열 포함 여부",
    },
    {
        "name": "regex_match",
        "data_type": "BOOLEAN",
        "range": (0, 1),
        "description": "정규식 매치 여부",
    },
    {
        "name": "json_validity",
        "data_type": "BOOLEAN",
        "range": (0, 1),
        "description": "출력이 유효한 JSON인지",
    },
    {
        "name": "json_schema_match",
        "data_type": "BOOLEAN",
        "range": (0, 1),
        "description": "JSON 스키마 일치 여부",
    },
    # ----- 유사도 / 통계 (Statistical) -----
    {
        "name": "rouge",
        "data_type": "NUMERIC",
        "range": (0.0, 1.0),
        "description": "ROUGE-L 유사도",
    },
    {
        "name": "bleu",
        "data_type": "NUMERIC",
        "range": (0.0, 1.0),
        "description": "BLEU 점수",
    },
    {
        "name": "levenshtein_similarity",
        "data_type": "NUMERIC",
        "range": (0.0, 1.0),
        "description": "정규화된 Levenshtein 유사도",
    },
    {
        "name": "embedding_similarity",
        "data_type": "NUMERIC",
        "range": (0.0, 1.0),
        "description": "임베딩 코사인 유사도",
    },
    # ----- LLM Judge -----
    {
        "name": "llm_judge_consistency",
        "data_type": "NUMERIC",
        "range": (0, 10),
        "description": "LLM 판정 — 일관성 (0~10)",
    },
    {
        "name": "llm_judge_factuality",
        "data_type": "NUMERIC",
        "range": (0, 10),
        "description": "LLM 판정 — 사실성 (0~10)",
    },
    {
        "name": "llm_judge_quality",
        "data_type": "NUMERIC",
        "range": (0, 10),
        "description": "LLM 판정 — 품질 종합 (0~10)",
    },
    # ----- 종합 / 메타 -----
    {
        "name": "weighted_score",
        "data_type": "NUMERIC",
        "range": (0.0, 1.0),
        "description": "가중 평균 점수",
    },
    {
        "name": "latency_seconds",
        "data_type": "NUMERIC",
        "range": (0.0, 600.0),
        "description": "응답 latency (초)",
    },
]


async def register_score_configs_on_startup(
    langfuse: LangfuseClient,
    catalog: list[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """부팅 시 Langfuse score config를 idempotent 등록한다.

    Args:
        langfuse: Langfuse 클라이언트
        catalog: 등록할 evaluator 카탈로그 (기본 ``EVALUATOR_CATALOG``)

    Returns:
        ``{evaluator_name: score_config_id}`` 매핑

    Raises:
        LangfuseError: 등록 실패 (idempotent 충돌 제외)
    """
    items = catalog if catalog is not None else EVALUATOR_CATALOG
    result: dict[str, str] = {}
    failures: list[tuple[str, str]] = []

    for ev in items:
        name = ev["name"]
        try:
            cfg_id = langfuse.register_score_config(
                name=name,
                data_type=ev["data_type"],
                range=ev.get("range"),
                description=ev.get("description"),
            )
            result[name] = cfg_id
            logger.info(
                "score_config_registered",
                name=name,
                config_id=cfg_id,
                data_type=ev["data_type"],
            )
        except LangfuseError as exc:
            failures.append((name, str(exc.detail or exc)))
            logger.error(
                "score_config_registration_failed",
                name=name,
                error=str(exc.detail or exc),
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((name, str(exc)))
            logger.error(
                "score_config_registration_failed",
                name=name,
                error=str(exc),
            )

    if failures:
        raise LangfuseError(
            detail=f"{len(failures)}개 score config 등록 실패",
            extras={"failures": [{"name": n, "error": e} for n, e in failures]},
        )

    return result
