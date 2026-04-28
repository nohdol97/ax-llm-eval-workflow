"""Auto-Eval Prometheus 메트릭 단위 테스트 (Phase 8-B-2).

검증 항목:
- 8 종 메트릭이 ``prometheus_client.REGISTRY`` 에 등록되었는지
- 각 헬퍼 호출 시 Counter / Gauge / Histogram 값이 올바르게 갱신되는지
- ``record_run_from_run_obj`` 가 ``AutoEvalRun`` 객체를 받아 일괄 갱신
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY

from app.models.auto_eval import AutoEvalRun
from app.services.auto_eval_metrics import (
    auto_eval_alerts_triggered_total,
    auto_eval_avg_score,
    auto_eval_cost_usd_total,
    auto_eval_evaluator_score,
    auto_eval_pass_rate,
    auto_eval_run_duration_seconds,
    auto_eval_runs_total,
    auto_eval_traces_evaluated_total,
    record_alert_triggered,
    record_cost,
    record_run_completed,
    record_run_from_run_obj,
    record_run_started,
)


@pytest.mark.unit
class TestMetricRegistration:
    """모듈 import 만으로 모든 메트릭이 REGISTRY 에 등록되어야 한다."""

    @pytest.mark.parametrize(
        "metric_name",
        [
            "ax_auto_eval_runs_total",
            "ax_auto_eval_run_duration_seconds",
            "ax_auto_eval_traces_evaluated_total",
            "ax_auto_eval_avg_score",
            "ax_auto_eval_pass_rate",
            "ax_auto_eval_evaluator_score",
            "ax_auto_eval_cost_usd_total",
            "ax_auto_eval_alerts_triggered_total",
        ],
    )
    def test_metric_registered(self, metric_name: str) -> None:
        """REGISTRY 에서 metric_name 으로 검색하여 발견되어야 한다."""
        # Counter 는 ``_total`` 가 자동 부착되므로 base name 으로도 조회
        base = metric_name.removesuffix("_total")
        names = {m.name for m in REGISTRY.collect()}
        assert base in names or metric_name in names, (
            f"{metric_name} 가 REGISTRY 에 등록되지 않았습니다. available={sorted(names)[:20]}"
        )


@pytest.mark.unit
class TestRecordRunStarted:
    """``record_run_started`` 동작."""

    def test_increments_running_counter(self) -> None:
        before = auto_eval_runs_total.labels(policy_id="p-rs1", status="running")._value.get()
        record_run_started("p-rs1")
        after = auto_eval_runs_total.labels(policy_id="p-rs1", status="running")._value.get()
        assert after == before + 1


@pytest.mark.unit
class TestRecordRunCompleted:
    """``record_run_completed`` 동작."""

    def test_completed_status_increments_counter(self) -> None:
        before = auto_eval_runs_total.labels(policy_id="p-rc1", status="completed")._value.get()
        record_run_completed(
            policy_id="p-rc1",
            status="completed",
            duration_sec=12.5,
            traces_evaluated=10,
            avg_score=0.85,
            pass_rate=0.9,
            scores_by_evaluator={"exact_match": 0.8, "llm_judge": 0.9},
            cost_usd=0.05,
            triggered_alerts=[],
        )
        after = auto_eval_runs_total.labels(policy_id="p-rc1", status="completed")._value.get()
        assert after == before + 1

    def test_traces_evaluated_increments(self) -> None:
        before = auto_eval_traces_evaluated_total.labels(policy_id="p-te1")._value.get()
        record_run_completed(
            policy_id="p-te1",
            status="completed",
            duration_sec=1.0,
            traces_evaluated=7,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator=None,
            cost_usd=0.0,
        )
        after = auto_eval_traces_evaluated_total.labels(policy_id="p-te1")._value.get()
        assert after == before + 7

    def test_avg_score_gauge(self) -> None:
        record_run_completed(
            policy_id="p-as1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=0.73,
            pass_rate=None,
            scores_by_evaluator=None,
            cost_usd=0.0,
        )
        value = auto_eval_avg_score.labels(policy_id="p-as1")._value.get()
        assert value == pytest.approx(0.73)

    def test_pass_rate_gauge(self) -> None:
        record_run_completed(
            policy_id="p-pr1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=0.91,
            scores_by_evaluator=None,
            cost_usd=0.0,
        )
        value = auto_eval_pass_rate.labels(policy_id="p-pr1")._value.get()
        assert value == pytest.approx(0.91)

    def test_evaluator_breakdown_gauge(self) -> None:
        record_run_completed(
            policy_id="p-eb1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator={"exact_match": 1.0, "llm_judge": 0.5},
            cost_usd=0.0,
        )
        v1 = auto_eval_evaluator_score.labels(
            policy_id="p-eb1", evaluator="exact_match"
        )._value.get()
        v2 = auto_eval_evaluator_score.labels(policy_id="p-eb1", evaluator="llm_judge")._value.get()
        assert v1 == pytest.approx(1.0)
        assert v2 == pytest.approx(0.5)

    def test_evaluator_none_value_skipped(self) -> None:
        """evaluator value 가 None 이면 갱신 skip — 기존 값 유지/초기 0."""
        # None 만 포함된 dict
        record_run_completed(
            policy_id="p-en1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator={"only_none": None},
            cost_usd=0.0,
        )
        # 갱신되지 않았으므로 라벨이 등록되지 않거나 0
        # (Gauge 는 set 호출이 없으면 라벨 자체가 없을 수 있음 → 호출 시 0 으로 생성됨)
        v = auto_eval_evaluator_score.labels(policy_id="p-en1", evaluator="only_none")._value.get()
        assert v == 0.0

    def test_cost_increments(self) -> None:
        before = auto_eval_cost_usd_total.labels(policy_id="p-c1")._value.get()
        record_run_completed(
            policy_id="p-c1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator=None,
            cost_usd=2.5,
        )
        after = auto_eval_cost_usd_total.labels(policy_id="p-c1")._value.get()
        assert after == pytest.approx(before + 2.5)

    def test_alerts_triggered_increments(self) -> None:
        before = auto_eval_alerts_triggered_total.labels(
            policy_id="p-al1", metric="pass_rate"
        )._value.get()
        record_run_completed(
            policy_id="p-al1",
            status="completed",
            duration_sec=0.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator=None,
            cost_usd=0.0,
            triggered_alerts=["pass_rate"],
        )
        after = auto_eval_alerts_triggered_total.labels(
            policy_id="p-al1", metric="pass_rate"
        )._value.get()
        assert after == before + 1

    def test_duration_histogram_observed(self) -> None:
        # Histogram 의 sum 이 증가하는지로 검증
        sample_before = sum(
            s.value
            for m in auto_eval_run_duration_seconds.collect()
            for s in m.samples
            if s.name == "ax_auto_eval_run_duration_seconds_sum"
            and s.labels.get("policy_id") == "p-h1"
        )
        record_run_completed(
            policy_id="p-h1",
            status="completed",
            duration_sec=42.0,
            traces_evaluated=0,
            avg_score=None,
            pass_rate=None,
            scores_by_evaluator=None,
            cost_usd=0.0,
        )
        sample_after = sum(
            s.value
            for m in auto_eval_run_duration_seconds.collect()
            for s in m.samples
            if s.name == "ax_auto_eval_run_duration_seconds_sum"
            and s.labels.get("policy_id") == "p-h1"
        )
        assert sample_after - sample_before == pytest.approx(42.0)


@pytest.mark.unit
class TestRecordSingleHelpers:
    """단발 헬퍼 — ``record_alert_triggered`` / ``record_cost``."""

    def test_record_alert_triggered(self) -> None:
        before = auto_eval_alerts_triggered_total.labels(
            policy_id="p-sa1", metric="evaluator_score:exact_match"
        )._value.get()
        record_alert_triggered("p-sa1", "evaluator_score:exact_match")
        after = auto_eval_alerts_triggered_total.labels(
            policy_id="p-sa1", metric="evaluator_score:exact_match"
        )._value.get()
        assert after == before + 1

    def test_record_cost_zero_skipped(self) -> None:
        """cost=0 이면 increment 가 호출되지 않아야 한다."""
        before = auto_eval_cost_usd_total.labels(policy_id="p-rc-zero")._value.get()
        record_cost("p-rc-zero", 0.0)
        after = auto_eval_cost_usd_total.labels(policy_id="p-rc-zero")._value.get()
        assert after == before

    def test_record_cost_positive(self) -> None:
        before = auto_eval_cost_usd_total.labels(policy_id="p-rc-pos")._value.get()
        record_cost("p-rc-pos", 1.25)
        after = auto_eval_cost_usd_total.labels(policy_id="p-rc-pos")._value.get()
        assert after == pytest.approx(before + 1.25)


@pytest.mark.unit
class TestRecordRunFromRunObj:
    """``record_run_from_run_obj`` — AutoEvalRun 객체로부터 일괄 갱신."""

    def test_increments_all_metrics(self) -> None:
        from datetime import UTC, datetime

        run = AutoEvalRun(
            id="run_obj_001",
            policy_id="p-obj1",
            started_at=datetime(2026, 4, 1, tzinfo=UTC),
            completed_at=datetime(2026, 4, 1, 0, 0, 30, tzinfo=UTC),
            status="completed",
            traces_evaluated=5,
            avg_score=0.6,
            pass_rate=0.7,
            cost_usd=0.1,
            duration_ms=30000.0,
            scores_by_evaluator={"x": 0.6},
            triggered_alerts=["pass_rate"],
        )
        before_runs = auto_eval_runs_total.labels(
            policy_id="p-obj1", status="completed"
        )._value.get()
        before_traces = auto_eval_traces_evaluated_total.labels(policy_id="p-obj1")._value.get()
        record_run_from_run_obj("p-obj1", run)
        after_runs = auto_eval_runs_total.labels(
            policy_id="p-obj1", status="completed"
        )._value.get()
        after_traces = auto_eval_traces_evaluated_total.labels(policy_id="p-obj1")._value.get()
        assert after_runs == before_runs + 1
        assert after_traces == before_traces + 5
        # avg_score 게이지 30 초 = 30000 ms
        assert auto_eval_avg_score.labels(policy_id="p-obj1")._value.get() == pytest.approx(0.6)
