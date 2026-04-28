"""AutoEval 도메인 모델 단위 테스트 (Phase 8-B-1).

검증:
- :class:`AutoEvalSchedule` validation (cron / interval / event 분기 + 잘못된 cron)
- :class:`AlertThreshold` validation (evaluator_score 시 evaluator_name 필수)
- :class:`AutoEvalPolicyCreate` evaluators 비어 있으면 거부
- :class:`AutoEvalPolicy` 필수 필드 + status 기본값
- :class:`AutoEvalRun` 기본 필드 + 카운터 비음수
- :class:`AutoEvalPolicyUpdate` 부분 업데이트 허용
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.models.auto_eval import (
    AlertThreshold,
    AutoEvalPolicy,
    AutoEvalPolicyCreate,
    AutoEvalPolicyUpdate,
    AutoEvalRun,
    AutoEvalSchedule,
)
from app.models.experiment import EvaluatorConfig
from app.models.trace import TraceFilter


# ---------- 테스트 헬퍼 ----------
def make_filter() -> TraceFilter:
    return TraceFilter(project_id="proj-1")


def make_evaluator(name: str = "ev1") -> EvaluatorConfig:
    return EvaluatorConfig(type="builtin", name=name, weight=1.0)


# ---------- AutoEvalSchedule ----------
@pytest.mark.unit
class TestAutoEvalSchedule:
    """스케줄 모델 검증 — type별 필수 필드 + cron 표현식."""

    def test_cron_schedule_valid(self) -> None:
        s = AutoEvalSchedule(type="cron", cron_expression="0 */1 * * *")
        assert s.type == "cron"
        assert s.cron_expression == "0 */1 * * *"
        assert s.timezone == "Asia/Seoul"

    def test_cron_schedule_missing_expression_raises(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AutoEvalSchedule(type="cron")
        assert "cron_expression" in str(excinfo.value)

    def test_cron_schedule_invalid_expression_raises(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AutoEvalSchedule(type="cron", cron_expression="not-a-cron")
        assert "invalid cron expression" in str(excinfo.value)

    def test_interval_schedule_valid(self) -> None:
        s = AutoEvalSchedule(type="interval", interval_seconds=3600)
        assert s.interval_seconds == 3600

    def test_interval_schedule_missing_seconds_raises(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AutoEvalSchedule(type="interval")
        assert "interval_seconds" in str(excinfo.value)

    def test_interval_seconds_minimum_60(self) -> None:
        """60 미만은 ge 제약으로 거부."""
        with pytest.raises(ValidationError):
            AutoEvalSchedule(type="interval", interval_seconds=30)

    def test_event_schedule_requires_trigger(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AutoEvalSchedule(type="event")
        assert "event_trigger" in str(excinfo.value)

    def test_event_schedule_valid(self) -> None:
        s = AutoEvalSchedule(type="event", event_trigger="new_traces", event_threshold=100)
        assert s.event_trigger == "new_traces"
        assert s.event_threshold == 100


# ---------- AlertThreshold ----------
@pytest.mark.unit
class TestAlertThreshold:
    """임계 모델 — metric별 필수 필드."""

    def test_avg_score_threshold_valid(self) -> None:
        t = AlertThreshold(metric="avg_score", operator="lt", value=0.7)
        assert t.metric == "avg_score"
        assert t.window_minutes == 60  # 기본

    def test_pass_rate_threshold_valid(self) -> None:
        t = AlertThreshold(metric="pass_rate", operator="lt", value=0.8, drop_pct=0.15)
        assert t.drop_pct == 0.15

    def test_evaluator_score_requires_evaluator_name(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AlertThreshold(metric="evaluator_score", operator="lt", value=0.5)
        assert "evaluator_name" in str(excinfo.value)

    def test_evaluator_score_with_name_valid(self) -> None:
        t = AlertThreshold(
            metric="evaluator_score",
            evaluator_name="grounding",
            operator="lt",
            value=0.5,
        )
        assert t.evaluator_name == "grounding"

    def test_value_range_validation(self) -> None:
        with pytest.raises(ValidationError):
            AlertThreshold(metric="avg_score", operator="lt", value=1.5)
        with pytest.raises(ValidationError):
            AlertThreshold(metric="avg_score", operator="lt", value=-0.1)

    def test_drop_pct_range(self) -> None:
        with pytest.raises(ValidationError):
            AlertThreshold(metric="avg_score", operator="lt", value=0.5, drop_pct=1.5)

    def test_window_minutes_minimum(self) -> None:
        with pytest.raises(ValidationError):
            AlertThreshold(metric="avg_score", operator="lt", value=0.5, window_minutes=0)


# ---------- AutoEvalPolicyCreate ----------
@pytest.mark.unit
class TestAutoEvalPolicyCreate:
    """입력 모델 — evaluators 1개 이상 필수."""

    def test_create_with_evaluators_valid(self) -> None:
        c = AutoEvalPolicyCreate(
            name="qa-policy",
            project_id="proj-1",
            trace_filter=make_filter(),
            evaluators=[make_evaluator()],
            schedule=AutoEvalSchedule(type="interval", interval_seconds=3600),
        )
        assert c.name == "qa-policy"

    def test_create_no_evaluators_raises(self) -> None:
        with pytest.raises(ValidationError) as excinfo:
            AutoEvalPolicyCreate(
                name="qa-policy",
                project_id="proj-1",
                trace_filter=make_filter(),
                evaluators=[],
                schedule=AutoEvalSchedule(type="interval", interval_seconds=3600),
            )
        assert "at least one evaluator required" in str(excinfo.value)

    def test_create_default_status_active(self) -> None:
        c = AutoEvalPolicyCreate(
            name="x",
            project_id="proj-1",
            trace_filter=make_filter(),
            evaluators=[make_evaluator()],
            schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
        )
        assert c.status == "active"

    def test_create_with_alert_thresholds(self) -> None:
        c = AutoEvalPolicyCreate(
            name="x",
            project_id="proj-1",
            trace_filter=make_filter(),
            evaluators=[make_evaluator()],
            schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
            alert_thresholds=[AlertThreshold(metric="avg_score", operator="lt", value=0.7)],
            notification_targets=["user-1", "user-2"],
        )
        assert len(c.alert_thresholds) == 1
        assert c.notification_targets == ["user-1", "user-2"]

    def test_negative_cost_limit_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutoEvalPolicyCreate(
                name="x",
                project_id="proj-1",
                trace_filter=make_filter(),
                evaluators=[make_evaluator()],
                schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
                daily_cost_limit_usd=-1.0,
            )


# ---------- AutoEvalPolicy ----------
@pytest.mark.unit
class TestAutoEvalPolicy:
    """정책 본체 — 필수 필드 + 기본값."""

    def test_policy_construction(self) -> None:
        now = datetime.now(UTC)
        p = AutoEvalPolicy(
            id="policy_abc123",
            name="qa-policy",
            project_id="proj-1",
            trace_filter=make_filter(),
            evaluators=[make_evaluator()],
            schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
            owner="user-1",
            created_at=now,
            updated_at=now,
        )
        assert p.id == "policy_abc123"
        assert p.status == "active"
        assert p.alert_thresholds == []
        assert p.notification_targets == []
        assert p.last_run_at is None
        assert p.next_run_at is None

    def test_policy_requires_evaluators(self) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            AutoEvalPolicy(
                id="p1",
                name="x",
                project_id="proj-1",
                trace_filter=make_filter(),
                evaluators=[],  # min_length=1
                schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
                owner="user-1",
                created_at=now,
                updated_at=now,
            )

    def test_policy_paused_status(self) -> None:
        now = datetime.now(UTC)
        p = AutoEvalPolicy(
            id="p1",
            name="x",
            project_id="proj-1",
            trace_filter=make_filter(),
            evaluators=[make_evaluator()],
            schedule=AutoEvalSchedule(type="interval", interval_seconds=60),
            owner="user-1",
            created_at=now,
            updated_at=now,
            status="paused",
        )
        assert p.status == "paused"


# ---------- AutoEvalPolicyUpdate ----------
@pytest.mark.unit
class TestAutoEvalPolicyUpdate:
    """PATCH용 — 모든 필드 선택."""

    def test_empty_update_valid(self) -> None:
        u = AutoEvalPolicyUpdate()
        assert u.name is None
        assert u.status is None

    def test_partial_update_status(self) -> None:
        u = AutoEvalPolicyUpdate(status="paused")
        assert u.status == "paused"

    def test_partial_update_schedule(self) -> None:
        u = AutoEvalPolicyUpdate(schedule=AutoEvalSchedule(type="interval", interval_seconds=120))
        assert u.schedule is not None
        assert u.schedule.interval_seconds == 120

    def test_model_dump_excludes_unset(self) -> None:
        u = AutoEvalPolicyUpdate(name="new-name")
        data = u.model_dump(exclude_unset=True)
        assert data == {"name": "new-name"}


# ---------- AutoEvalRun ----------
@pytest.mark.unit
class TestAutoEvalRun:
    """run 모델 — 카운터 + 상태 + skip_reason."""

    def test_running_run(self) -> None:
        r = AutoEvalRun(
            id="run_xyz",
            policy_id="policy_abc",
            started_at=datetime.now(UTC),
            status="running",
        )
        assert r.status == "running"
        assert r.traces_evaluated == 0
        assert r.cost_usd == 0.0
        assert r.scores_by_evaluator == {}

    def test_skipped_run_with_reason(self) -> None:
        r = AutoEvalRun(
            id="run_xyz",
            policy_id="policy_abc",
            started_at=datetime.now(UTC),
            status="skipped",
            skip_reason="daily_cost_limit_exceeded",
        )
        assert r.skip_reason == "daily_cost_limit_exceeded"

    def test_completed_run_with_metrics(self) -> None:
        r = AutoEvalRun(
            id="run_xyz",
            policy_id="policy_abc",
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            status="completed",
            traces_evaluated=10,
            traces_total=10,
            avg_score=0.85,
            pass_rate=0.9,
            cost_usd=0.05,
            duration_ms=1234.5,
            scores_by_evaluator={"grounding": 0.8, "weighted_score": 0.85},
        )
        assert r.avg_score == 0.85
        assert r.scores_by_evaluator["grounding"] == 0.8

    def test_negative_traces_rejected(self) -> None:
        with pytest.raises(ValidationError):
            AutoEvalRun(
                id="run_xyz",
                policy_id="policy_abc",
                started_at=datetime.now(UTC),
                status="completed",
                traces_evaluated=-1,
            )

    def test_failed_run_with_error(self) -> None:
        r = AutoEvalRun(
            id="run_xyz",
            policy_id="policy_abc",
            started_at=datetime.now(UTC),
            status="failed",
            error_message="something broke",
        )
        assert r.status == "failed"
        assert r.error_message == "something broke"
