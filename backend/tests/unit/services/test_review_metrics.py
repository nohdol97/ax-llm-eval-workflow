"""Review Queue Prometheus 메트릭 헬퍼 단위 테스트."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
import pytest

from app.services import review_metrics


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch) -> CollectorRegistry:
    """각 테스트마다 독립 registry/metric 인스턴스를 사용한다."""
    registry = CollectorRegistry()

    monkeypatch.setattr(
        review_metrics,
        "review_items_total",
        Gauge(
            "ax_review_items",
            "Review Queue 현재 항목 수 (상태별 게이지)",
            labelnames=("type", "status", "severity"),
            registry=registry,
        ),
    )
    monkeypatch.setattr(
        review_metrics,
        "review_items_created_total",
        Counter(
            "ax_review_items_created_total",
            "Review Queue 진입 누적 (생성 시점)",
            labelnames=("type", "source"),
            registry=registry,
        ),
    )
    monkeypatch.setattr(
        review_metrics,
        "review_resolution_duration_seconds",
        Histogram(
            "ax_review_resolution_duration_seconds",
            "Review 항목이 open → resolved/dismissed 까지 걸린 시간 (초)",
            labelnames=("decision",),
            buckets=(60, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400),
            registry=registry,
        ),
    )
    monkeypatch.setattr(
        review_metrics,
        "evaluator_disagreement_total",
        Counter(
            "ax_evaluator_disagreement_total",
            "Reviewer 결정별 evaluator 정확도 학습 카운터",
            labelnames=("evaluator", "decision"),
            registry=registry,
        ),
    )
    return registry


@pytest.mark.unit
class TestReviewMetrics:
    def test_record_review_created_increments_counter_and_open_gauge(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_created("auto_eval_flagged", "auto", "high")

        created = isolated_registry.get_sample_value(
            "ax_review_items_created_total",
            {"type": "auto_eval_flagged", "source": "auto"},
        )
        open_items = isolated_registry.get_sample_value(
            "ax_review_items",
            {"type": "auto_eval_flagged", "status": "open", "severity": "high"},
        )

        assert created == 1.0
        assert open_items == 1.0

    def test_record_review_status_change_decrements_from_and_increments_to(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_created("user_report", "manual", "medium")

        review_metrics.record_review_status_change(
            "user_report",
            "medium",
            from_status="open",
            to_status="in_review",
        )

        open_items = isolated_registry.get_sample_value(
            "ax_review_items",
            {"type": "user_report", "status": "open", "severity": "medium"},
        )
        in_review_items = isolated_registry.get_sample_value(
            "ax_review_items",
            {"type": "user_report", "status": "in_review", "severity": "medium"},
        )

        assert open_items == 0.0
        assert in_review_items == 1.0

    def test_record_review_status_change_with_none_from_status_increments_only_target(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_status_change(
            "manual_addition",
            "low",
            from_status=None,
            to_status="open",
        )

        open_items = isolated_registry.get_sample_value(
            "ax_review_items",
            {"type": "manual_addition", "status": "open", "severity": "low"},
        )
        resolved_items = isolated_registry.get_sample_value(
            "ax_review_items",
            {"type": "manual_addition", "status": "resolved", "severity": "low"},
        )

        assert open_items == 1.0
        assert resolved_items is None

    def test_record_review_resolved_observes_duration_histogram(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved("approve", 120.0)

        count = isolated_registry.get_sample_value(
            "ax_review_resolution_duration_seconds_count",
            {"decision": "approve"},
        )
        total = isolated_registry.get_sample_value(
            "ax_review_resolution_duration_seconds_sum",
            {"decision": "approve"},
        )

        assert count == 1.0
        assert total == pytest.approx(120.0)

    def test_record_review_resolved_skips_histogram_when_duration_is_none(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved("override", None)

        count = isolated_registry.get_sample_value(
            "ax_review_resolution_duration_seconds_count",
            {"decision": "override"},
        )
        total = isolated_registry.get_sample_value(
            "ax_review_resolution_duration_seconds_sum",
            {"decision": "override"},
        )

        assert count is None
        assert total is None

    def test_record_review_resolved_increments_disagreement_for_each_evaluator(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved(
            "dismiss",
            30.0,
            automatic_scores={"judge_a": 0.1, "judge_b": 0.2},
        )

        ev1 = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "judge_a", "decision": "dismiss"},
        )
        ev2 = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "judge_b", "decision": "dismiss"},
        )

        assert ev1 == 1.0
        assert ev2 == 1.0

    def test_record_review_resolved_with_empty_scores_skips_disagreement_counter(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved("approve", 10.0, automatic_scores={})

        sample = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "judge_a", "decision": "approve"},
        )

        assert sample is None

    def test_record_evaluator_disagreement_increments_single_counter(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_evaluator_disagreement("exact_match", "override")

        value = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "exact_match", "decision": "override"},
        )

        assert value == 1.0

    def test_record_review_resolved_skips_histogram_when_duration_is_negative(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved("approve", -5.0)

        count = isolated_registry.get_sample_value(
            "ax_review_resolution_duration_seconds_count",
            {"decision": "approve"},
        )

        assert count is None

    def test_record_review_resolved_skips_empty_evaluator_name(
        self, isolated_registry: CollectorRegistry
    ) -> None:
        review_metrics.record_review_resolved(
            "add_to_dataset",
            15.0,
            automatic_scores={"": 0.7, "judge_valid": 0.8},
        )

        empty_name = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "", "decision": "add_to_dataset"},
        )
        valid_name = isolated_registry.get_sample_value(
            "ax_evaluator_disagreement_total",
            {"evaluator": "judge_valid", "decision": "add_to_dataset"},
        )

        assert empty_name is None
        assert valid_name == 1.0

