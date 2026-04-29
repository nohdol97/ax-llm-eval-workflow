"""Review 도메인 모델 단위 테스트 (Phase 8-C)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.review import (
    REVIEW_REASONS,
    SEVERITY_SCORE,
    EvaluatorDisagreementResponse,
    EvaluatorDisagreementStat,
    ReviewItem,
    ReviewItemCreate,
    ReviewItemListResponse,
    ReviewItemResolve,
    ReviewQueueSummary,
    ReviewReport,
    ReviewerStats,
)


def make_review_item_data() -> dict[str, object]:
    now = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
    return {
        "id": "review_abcdef123456",
        "type": "auto_eval_flagged",
        "severity": "high",
        "subject_type": "trace",
        "subject_id": "trace-1",
        "project_id": "proj-1",
        "reason": "auto_eval_low_score",
        "reason_detail": {"weighted_score": 0.21, "policy_id": "policy-1"},
        "automatic_scores": {"weighted_score": 0.21, "llm_judge": 0.2},
        "status": "in_review",
        "assigned_to": "reviewer-1",
        "assigned_at": now,
        "decision": "override",
        "reviewer_score": 0.91,
        "reviewer_comment": "manual correction",
        "expected_output": {"answer": "fixed"},
        "resolved_by": "reviewer-2",
        "resolved_at": now,
        "auto_eval_policy_id": "policy-1",
        "auto_eval_run_id": "run-1",
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.unit
class TestReviewItem:
    def test_review_item_constructs_with_all_fields(self) -> None:
        item = ReviewItem(**make_review_item_data())
        assert item.id == "review_abcdef123456"
        assert item.type == "auto_eval_flagged"
        assert item.severity == "high"
        assert item.assigned_to == "reviewer-1"
        assert item.decision == "override"
        assert item.reviewer_score == 0.91
        assert item.expected_output == {"answer": "fixed"}
        assert item.auto_eval_policy_id == "policy-1"
        assert item.created_at.tzinfo == UTC

    def test_review_item_extra_forbid_rejects_unknown_field(self) -> None:
        with pytest.raises(ValidationError):
            ReviewItem(**make_review_item_data(), unexpected="nope")


@pytest.mark.unit
class TestReviewItemCreate:
    def test_create_defaults(self) -> None:
        payload = ReviewItemCreate(subject_id="trace-1", project_id="proj-1")
        assert payload.subject_type == "trace"
        assert payload.severity == "medium"
        assert payload.reason == "manual_addition"
        assert payload.reason_detail == {}
        assert payload.automatic_scores == {}


@pytest.mark.unit
class TestReviewItemResolve:
    def test_override_requires_reviewer_score(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            ReviewItemResolve(decision="override", reviewer_score=None)
        assert "reviewer_score" in str(excinfo.value)

    @pytest.mark.parametrize("decision", ["approve", "dismiss", "add_to_dataset"])
    def test_non_override_allows_missing_reviewer_score(self, decision: str) -> None:
        payload = ReviewItemResolve(decision=decision, reviewer_score=None)
        assert payload.decision == decision
        assert payload.reviewer_score is None

    @pytest.mark.parametrize("score", [-0.1, 1.1])
    def test_reviewer_score_out_of_range_rejected(self, score: float) -> None:
        with pytest.raises(ValidationError):
            ReviewItemResolve(decision="override", reviewer_score=score)

    @pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
    def test_reviewer_score_boundary_values_allowed(self, score: float) -> None:
        payload = ReviewItemResolve(decision="override", reviewer_score=score)
        assert payload.reviewer_score == score


@pytest.mark.unit
class TestReviewReport:
    @pytest.mark.parametrize("missing_field", ["trace_id", "project_id", "reason"])
    def test_required_fields(self, missing_field: str) -> None:
        payload = {
            "trace_id": "trace-1",
            "project_id": "proj-1",
            "reason": "needs review",
        }
        payload.pop(missing_field)
        with pytest.raises(ValidationError) as excinfo:
            ReviewReport(**payload)
        assert missing_field in str(excinfo.value)

    def test_defaults(self) -> None:
        report = ReviewReport(trace_id="trace-1", project_id="proj-1", reason="wrong answer")
        assert report.severity == "medium"
        assert report.subject_type == "trace"

    @pytest.mark.parametrize("subject_type", ["experiment_item", "submission"])
    def test_allows_non_trace_subject_types(self, subject_type: str) -> None:
        report = ReviewReport(
            trace_id="subject-1",
            project_id="proj-1",
            reason="needs review",
            subject_type=subject_type,
        )
        assert report.subject_type == subject_type


@pytest.mark.unit
class TestReviewConstants:
    @pytest.mark.parametrize(
        ("severity", "expected"),
        [("low", 1), ("medium", 2), ("high", 3)],
    )
    def test_severity_score_mapping(self, severity: str, expected: int) -> None:
        assert SEVERITY_SCORE[severity] == expected

    @pytest.mark.parametrize(
        "reason",
        [
            "auto_eval_low_score",
            "judge_low_confidence",
            "evaluator_disagreement",
            "user_report",
            "manual_addition",
        ],
    )
    def test_review_reasons_contains_expected_values(self, reason: str) -> None:
        assert reason in REVIEW_REASONS

    def test_review_reasons_exact_size(self) -> None:
        assert len(REVIEW_REASONS) == 5


@pytest.mark.unit
class TestSerialization:
    def test_reviewer_stats_round_trip(self) -> None:
        stats = ReviewerStats(
            user_id="reviewer-1",
            open_count=3,
            in_review_count=2,
            resolved_today=4,
            avg_resolution_time_min=12.5,
            decisions_breakdown={"approve": 2, "override": 1, "dismiss": 1},
        )
        restored = ReviewerStats.model_validate_json(stats.model_dump_json())
        assert restored == stats

    def test_review_queue_summary_serialization(self) -> None:
        summary = ReviewQueueSummary(
            open=5,
            in_review=2,
            resolved_today=7,
            dismissed_today=1,
            avg_resolution_time_min=8.25,
        )
        restored = ReviewQueueSummary.model_validate(summary.model_dump())
        assert restored.avg_resolution_time_min == 8.25
        assert restored.model_dump() == summary.model_dump()

    def test_review_item_list_response_serialization(self) -> None:
        item = ReviewItem(**make_review_item_data())
        response = ReviewItemListResponse(items=[item], total=1, page=1, page_size=20)
        restored = ReviewItemListResponse.model_validate_json(response.model_dump_json())
        assert restored.total == 1
        assert restored.items[0].id == item.id

    def test_disagreement_response_serialization(self) -> None:
        response = EvaluatorDisagreementResponse(
            items=[
                EvaluatorDisagreementStat(
                    evaluator="llm_judge",
                    total_resolved=10,
                    override_count=3,
                    override_rate=0.3,
                )
            ]
        )
        restored = EvaluatorDisagreementResponse.model_validate_json(
            response.model_dump_json()
        )
        assert restored.items[0].evaluator == "llm_judge"
        assert restored.items[0].override_rate == 0.3


@pytest.mark.unit
class TestEvaluatorDisagreementStat:
    @pytest.mark.parametrize("rate", [0.0, 0.5, 1.0])
    def test_override_rate_within_bounds_is_valid(self, rate: float) -> None:
        stat = EvaluatorDisagreementStat(
            evaluator="llm_judge",
            total_resolved=10,
            override_count=5,
            override_rate=rate,
        )
        assert stat.override_rate == rate

    @pytest.mark.parametrize("rate", [-0.1, 1.1])
    def test_override_rate_out_of_bounds_rejected(self, rate: float) -> None:
        with pytest.raises(ValidationError):
            EvaluatorDisagreementStat(
                evaluator="llm_judge",
                total_resolved=10,
                override_count=5,
                override_rate=rate,
            )
