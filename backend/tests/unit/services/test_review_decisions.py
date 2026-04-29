"""Review 결정 후처리 단위 테스트."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.review import ReviewItem
from app.services import review_decisions as module
from app.services.review_decisions import (
    REVIEWER_OVERRIDE_SCORE_NAME,
    _apply_add_to_dataset,
    _apply_override,
    apply_decision_postprocess,
)

T0 = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)


def make_item(
    *,
    item_id: str = "review-1",
    decision: str | None = None,
    reviewer_score: float | None = None,
    reviewer_comment: str | None = "reviewer note",
    expected_output: object | None = None,
    subject_type: str = "trace",
    subject_id: str = "trace-1",
    project_id: str = "proj-1",
    resolved_by: str | None = "reviewer-1",
    auto_eval_policy_id: str | None = None,
    auto_eval_run_id: str | None = None,
) -> ReviewItem:
    return ReviewItem(
        id=item_id,
        type="manual_addition",
        severity="medium",
        subject_type=subject_type,
        subject_id=subject_id,
        project_id=project_id,
        reason="manual_addition",
        reason_detail={},
        automatic_scores={},
        status="resolved" if decision != "dismiss" else "dismissed",
        assigned_to=None,
        assigned_at=None,
        decision=decision,  # type: ignore[arg-type]
        reviewer_score=reviewer_score,
        reviewer_comment=reviewer_comment,
        expected_output=expected_output,  # type: ignore[arg-type]
        resolved_by=resolved_by,
        resolved_at=T0,
        auto_eval_policy_id=auto_eval_policy_id,
        auto_eval_run_id=auto_eval_run_id,
        created_at=T0,
        updated_at=T0,
    )


@pytest.mark.unit
class TestApplyDecisionPostprocess:
    async def test_approve_is_noop(self) -> None:
        langfuse = MagicMock()

        await apply_decision_postprocess(make_item(decision="approve"), langfuse=langfuse)

        langfuse.score.assert_not_called()
        langfuse.create_dataset.assert_not_called()
        langfuse.create_dataset_item.assert_not_called()

    async def test_dismiss_is_noop(self) -> None:
        langfuse = MagicMock()

        await apply_decision_postprocess(make_item(decision="dismiss"), langfuse=langfuse)

        langfuse.score.assert_not_called()
        langfuse.create_dataset.assert_not_called()
        langfuse.create_dataset_item.assert_not_called()

    async def test_none_decision_is_noop(self) -> None:
        langfuse = MagicMock()

        await apply_decision_postprocess(make_item(decision=None), langfuse=langfuse)

        langfuse.score.assert_not_called()

    async def test_override_records_reviewer_override_score_for_trace(self) -> None:
        langfuse = MagicMock()
        item = make_item(
            decision="override",
            reviewer_score=0.95,
            reviewer_comment="manual correction",
            subject_type="trace",
            subject_id="trace-override-1",
        )

        await apply_decision_postprocess(item, langfuse=langfuse)

        langfuse.score.assert_called_once_with(
            trace_id="trace-override-1",
            name=REVIEWER_OVERRIDE_SCORE_NAME,
            value=0.95,
            comment="manual correction",
        )

    async def test_override_without_reviewer_score_skips_score_and_warns(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        warning = MagicMock()
        monkeypatch.setattr(module.logger, "warning", warning)

        await _apply_override(make_item(decision="override", reviewer_score=None), langfuse=langfuse)

        langfuse.score.assert_not_called()
        warning.assert_called_once()

    async def test_override_for_non_trace_subject_skips_score(self) -> None:
        langfuse = MagicMock()

        await apply_decision_postprocess(
            make_item(
                decision="override",
                reviewer_score=0.7,
                subject_type="experiment_item",
                subject_id="exp-1",
            ),
            langfuse=langfuse,
        )

        langfuse.score.assert_not_called()

    async def test_override_langfuse_score_exception_is_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        langfuse.score.side_effect = RuntimeError("score failed")
        warning = MagicMock()
        monkeypatch.setattr(module.logger, "warning", warning)

        await apply_decision_postprocess(
            make_item(decision="override", reviewer_score=0.4),
            langfuse=langfuse,
        )

        warning.assert_called_once()

    async def test_add_to_dataset_fetches_trace_and_adds_dataset_item(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.return_value = SimpleNamespace(
            name="qa-agent-v3",
            input={"question": "hello"},
            output={"answer": "trace output"},
        )
        add_item = MagicMock(return_value="qa-agent-v3-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(
                decision="add_to_dataset",
                expected_output={"answer": "manual gold"},
                resolved_by="reviewer-42",
                reviewer_comment="curated",
                auto_eval_policy_id="policy-1",
                auto_eval_run_id="run-1",
            ),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        fetcher.get.assert_awaited_once_with("trace-1", "proj-1")
        add_item.assert_called_once_with(
            langfuse,
            agent_name="qa-agent-v3",
            trace_input={"question": "hello"},
            expected_output={"answer": "manual gold"},
            metadata={
                "review_id": "review-1",
                "reviewer_user_id": "reviewer-42",
                "reviewer_comment": "curated",
                "trace_id": "trace-1",
                "auto_eval_policy_id": "policy-1",
                "auto_eval_run_id": "run-1",
            },
        )

    async def test_add_to_dataset_without_trace_fetcher_uses_none_input(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        add_item = MagicMock(return_value="unknown-agent-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(decision="add_to_dataset", expected_output="gold output"),
            langfuse=langfuse,
            trace_fetcher=None,
        )

        add_item.assert_called_once_with(
            langfuse,
            agent_name=None,
            trace_input=None,
            expected_output="gold output",
            metadata={
                "review_id": "review-1",
                "reviewer_user_id": "reviewer-1",
                "reviewer_comment": "reviewer note",
                "trace_id": "trace-1",
            },
        )

    async def test_add_to_dataset_trace_fetch_error_is_swallowed_and_add_still_attempted(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.side_effect = RuntimeError("trace fetch failed")
        add_item = MagicMock(return_value="unknown-agent-reviewer-curated")
        warning = MagicMock()
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)
        monkeypatch.setattr(module.logger, "warning", warning)

        await _apply_add_to_dataset(
            make_item(decision="add_to_dataset", expected_output=None),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        add_item.assert_called_once()
        assert warning.call_count == 1

    async def test_add_to_dataset_with_reviewer_score_also_records_override_score(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.return_value = SimpleNamespace(
            name="qa-agent-v3",
            input={"input": "x"},
            output={"answer": "y"},
        )
        add_item = MagicMock(return_value="qa-agent-v3-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await apply_decision_postprocess(
            make_item(
                decision="add_to_dataset",
                reviewer_score=0.83,
                reviewer_comment="keep this",
            ),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        add_item.assert_called_once()
        langfuse.score.assert_called_once_with(
            trace_id="trace-1",
            name=REVIEWER_OVERRIDE_SCORE_NAME,
            value=0.83,
            comment="keep this",
        )

    async def test_add_to_dataset_for_non_trace_subject_skips_everything(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        add_item = MagicMock()
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await apply_decision_postprocess(
            make_item(
                decision="add_to_dataset",
                subject_type="experiment_item",
                reviewer_score=0.2,
            ),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        fetcher.get.assert_not_called()
        add_item.assert_not_called()
        langfuse.score.assert_not_called()

    async def test_add_to_dataset_expected_output_prefers_item_value(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.return_value = SimpleNamespace(
            name="qa-agent-v3",
            input={"q": "hello"},
            output={"answer": "trace output"},
        )
        add_item = MagicMock(return_value="qa-agent-v3-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(decision="add_to_dataset", expected_output={"answer": "manual output"}),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        assert add_item.call_args.kwargs["expected_output"] == {"answer": "manual output"}

    async def test_add_to_dataset_expected_output_falls_back_to_trace_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.return_value = SimpleNamespace(
            name="qa-agent-v3",
            input={"q": "hello"},
            output={"answer": "trace fallback"},
        )
        add_item = MagicMock(return_value="qa-agent-v3-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(decision="add_to_dataset", expected_output=None),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        assert add_item.call_args.kwargs["expected_output"] == {"answer": "trace fallback"}

    async def test_add_to_dataset_expected_output_can_be_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        fetcher = AsyncMock()
        fetcher.get.return_value = SimpleNamespace(name="qa-agent-v3", input={"q": "hello"}, output=None)
        add_item = MagicMock(return_value="qa-agent-v3-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(decision="add_to_dataset", expected_output=None),
            langfuse=langfuse,
            trace_fetcher=fetcher,
        )

        assert add_item.call_args.kwargs["expected_output"] is None

    async def test_add_to_dataset_metadata_excludes_none_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        add_item = MagicMock(return_value="unknown-agent-reviewer-curated")
        monkeypatch.setattr(module, "add_reviewer_curated_item", add_item)

        await _apply_add_to_dataset(
            make_item(
                decision="add_to_dataset",
                reviewer_comment=None,
                resolved_by="reviewer-2",
                auto_eval_policy_id=None,
                auto_eval_run_id=None,
            ),
            langfuse=langfuse,
            trace_fetcher=None,
        )

        assert add_item.call_args.kwargs["metadata"] == {
            "review_id": "review-1",
            "reviewer_user_id": "reviewer-2",
            "trace_id": "trace-1",
        }

    async def test_add_to_dataset_error_from_dataset_helper_is_swallowed(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        langfuse = MagicMock()
        warning = MagicMock()
        monkeypatch.setattr(module, "add_reviewer_curated_item", MagicMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(module.logger, "warning", warning)

        await apply_decision_postprocess(
            make_item(decision="add_to_dataset", expected_output={"answer": "gold"}),
            langfuse=langfuse,
            trace_fetcher=None,
        )

        warning.assert_called_once()
