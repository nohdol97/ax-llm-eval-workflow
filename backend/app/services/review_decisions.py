"""Review 결정별 후처리 (Phase 8-C-6).

본 모듈은 ``docs/AGENT_EVAL.md`` §17.1 결정별 동작을 그대로 구현한다.

| Decision | 동작 |
|---|---|
| ``approve``        | no-op (자동 score 그대로 확정) |
| ``override``       | Langfuse score 갱신 (``reviewer_score`` + reviewer 코멘트) |
| ``dismiss``        | no-op (큐에서 제거 + reason_pattern 학습은 v2) |
| ``add_to_dataset`` | 골든셋 ``<agent>-reviewer-curated`` 에 trace 추가 + 선택적 score 갱신 |

caller 는 ``ReviewQueueService.resolve`` 호출 후 본 함수를 한 번 호출하면 된다.
실패는 swallow + logger.warning — 결정 자체는 이미 영속화된 상태이므로 best-effort.
"""

from __future__ import annotations

from typing import Any

from app.core.logging import get_logger
from app.models.review import ReviewItem
from app.services.dataset_service import add_reviewer_curated_item
from app.services.langfuse_client import LangfuseClient

logger = get_logger(__name__)

REVIEWER_OVERRIDE_SCORE_NAME = "reviewer_override"
"""Langfuse score name — reviewer override 결정 시 새 score 등록.

자동 evaluator score 와 충돌하지 않도록 별도 이름 사용. dashboard 에서는
weighted_score 와 reviewer_override 를 비교해 disagreement 시각화 가능.
"""


async def apply_decision_postprocess(
    item: ReviewItem,
    *,
    langfuse: LangfuseClient | Any,
    trace_fetcher: Any | None = None,
) -> None:
    """resolve 직후 결정별 후처리 — best-effort.

    Args:
        item: resolve 가 적용된 ReviewItem (status=resolved/dismissed)
        langfuse: ``LangfuseClient`` (score / dataset 호출용)
        trace_fetcher: ``TraceFetcher`` — ``add_to_dataset`` 시 trace.input 조회

    어떠한 후처리 실패도 caller 에 raise 하지 않는다 (logger.warning 만).
    """
    decision = item.decision
    if decision is None:
        return

    if decision == "approve":
        # 자동 score 그대로 — 추가 작업 없음
        logger.debug("review_decision_approve_noop", review_id=item.id)
        return

    if decision == "dismiss":
        logger.debug("review_decision_dismiss_noop", review_id=item.id)
        return

    if decision == "override":
        await _apply_override(item, langfuse=langfuse)
        return

    if decision == "add_to_dataset":
        await _apply_add_to_dataset(item, langfuse=langfuse, trace_fetcher=trace_fetcher)
        return


async def _apply_override(
    item: ReviewItem,
    *,
    langfuse: LangfuseClient | Any,
) -> None:
    """Langfuse score 등록 — reviewer_score 가 있을 때만.

    score name 은 ``reviewer_override`` 로 고정 — 자동 evaluator score 와 분리.
    """
    if item.reviewer_score is None:
        logger.warning(
            "review_override_skipped_no_score",
            review_id=item.id,
            reason="reviewer_score is None",
        )
        return
    if item.subject_type != "trace":
        logger.debug(
            "review_override_skipped_non_trace",
            review_id=item.id,
            subject_type=item.subject_type,
        )
        return

    try:
        langfuse.score(
            trace_id=item.subject_id,
            name=REVIEWER_OVERRIDE_SCORE_NAME,
            value=float(item.reviewer_score),
            comment=item.reviewer_comment,
        )
        logger.info(
            "review_override_score_recorded",
            review_id=item.id,
            trace_id=item.subject_id,
            value=item.reviewer_score,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "review_override_score_failed",
            review_id=item.id,
            trace_id=item.subject_id,
            error=str(exc),
        )


async def _apply_add_to_dataset(
    item: ReviewItem,
    *,
    langfuse: LangfuseClient | Any,
    trace_fetcher: Any | None,
) -> None:
    """골든셋 ``<agent>-reviewer-curated`` 에 trace 추가.

    1. trace_fetcher 로 trace.input + name 조회 (없으면 reason_detail에서 폴백)
    2. ``add_reviewer_curated_item`` 호출
    3. reviewer_score 가 있으면 score 갱신도 동반

    expected_output 우선순위: ``item.expected_output`` > trace.output (없으면 None).
    """
    if item.subject_type != "trace":
        logger.debug(
            "review_add_to_dataset_skipped_non_trace",
            review_id=item.id,
            subject_type=item.subject_type,
        )
        return

    agent_name: str | None = None
    trace_input: Any = None
    trace_output: Any = None

    if trace_fetcher is not None:
        try:
            trace = await trace_fetcher.get(item.subject_id, item.project_id)
            agent_name = getattr(trace, "name", None)
            trace_input = getattr(trace, "input", None)
            trace_output = getattr(trace, "output", None)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "review_add_to_dataset_trace_fetch_failed",
                review_id=item.id,
                trace_id=item.subject_id,
                error=str(exc),
            )

    expected_output = item.expected_output if item.expected_output is not None else trace_output

    metadata = {
        "review_id": item.id,
        "reviewer_user_id": item.resolved_by,
        "reviewer_comment": item.reviewer_comment,
        "trace_id": item.subject_id,
        "auto_eval_policy_id": item.auto_eval_policy_id,
        "auto_eval_run_id": item.auto_eval_run_id,
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    try:
        dataset_name = add_reviewer_curated_item(
            langfuse,
            agent_name=agent_name,
            trace_input=trace_input,
            expected_output=expected_output,
            metadata=metadata,
        )
        logger.info(
            "review_add_to_dataset_succeeded",
            review_id=item.id,
            dataset=dataset_name,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "review_add_to_dataset_failed",
            review_id=item.id,
            error=str(exc),
        )

    # reviewer_score 동반 시 score 도 등록
    if item.reviewer_score is not None:
        await _apply_override(item, langfuse=langfuse)


__all__ = [
    "REVIEWER_OVERRIDE_SCORE_NAME",
    "apply_decision_postprocess",
]
