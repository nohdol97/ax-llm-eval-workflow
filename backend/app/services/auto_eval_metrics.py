"""Auto-Eval Prometheus 메트릭 정의 + 갱신 헬퍼 (Phase 8-B-2).

본 모듈은 ``docs/AGENT_EVAL.md`` §11.2 / §29 에서 정의된 8 종 메트릭을
``prometheus_client`` Counter / Gauge / Histogram 으로 등록하고, AutoEvalEngine
스코프에서 호출 가능한 헬퍼를 export 한다.

설계 원칙
---------
- 메트릭은 모듈 import 시 자동 등록된다 (``ax_*`` prefix).
- 헬퍼 함수는 None-safe — value 가 None 이면 갱신 skip 하여 graceful 동작.
- 라벨 카디널리티 제어를 위해 ``policy_id`` 는 caller 가 직접 전달, evaluator 라벨
  세부 갱신은 ``record_run_completed`` 에서 ``scores_by_evaluator`` 를 순회한다.

본 모듈은 ``observability.py`` 의 setup 흐름에 의존하지 않는다 — 단순 import 만으로
모든 메트릭이 등록된다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Metric definitions (AGENT_EVAL.md §11.2 / §29)
# ---------------------------------------------------------------------------

auto_eval_runs_total: Counter = Counter(
    "ax_auto_eval_runs_total",
    "Auto-Eval 정책 실행 수",
    labelnames=("policy_id", "status"),
)
"""정책 1회 실행 결과 카운터.

라벨:
    policy_id: 정책 ID
    status: ``running`` | ``completed`` | ``failed`` | ``skipped``
"""

auto_eval_run_duration_seconds: Histogram = Histogram(
    "ax_auto_eval_run_duration_seconds",
    "Auto-Eval 단일 run 소요 시간 (초)",
    labelnames=("policy_id",),
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1800),
)
"""정책 1회 실행 소요 시간 히스토그램."""

auto_eval_traces_evaluated_total: Counter = Counter(
    "ax_auto_eval_traces_evaluated_total",
    "Auto-Eval 로 평가된 trace 누적",
    labelnames=("policy_id",),
)

auto_eval_avg_score: Gauge = Gauge(
    "ax_auto_eval_avg_score",
    "Auto-Eval 정책의 직전 run 평균 weighted_score",
    labelnames=("policy_id",),
)

auto_eval_pass_rate: Gauge = Gauge(
    "ax_auto_eval_pass_rate",
    "Auto-Eval 정책의 직전 run pass_rate",
    labelnames=("policy_id",),
)

auto_eval_evaluator_score: Gauge = Gauge(
    "ax_auto_eval_evaluator_score",
    "Auto-Eval evaluator 별 직전 run 평균",
    labelnames=("policy_id", "evaluator"),
)

auto_eval_cost_usd_total: Counter = Counter(
    "ax_auto_eval_cost_usd_total",
    "Auto-Eval 누적 비용 USD",
    labelnames=("policy_id",),
)

auto_eval_alerts_triggered_total: Counter = Counter(
    "ax_auto_eval_alerts_triggered_total",
    "Auto-Eval 회귀 알림 트리거 누적",
    labelnames=("policy_id", "metric"),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def record_run_started(policy_id: str) -> None:
    """run 시작 시점 — 진행 중 카운터 증가 (status=running)."""
    auto_eval_runs_total.labels(policy_id=policy_id, status="running").inc()


def record_run_completed(
    policy_id: str,
    *,
    status: str,
    duration_sec: float | None,
    traces_evaluated: int,
    avg_score: float | None,
    pass_rate: float | None,
    scores_by_evaluator: Mapping[str, float | None] | None,
    cost_usd: float,
    triggered_alerts: list[str] | None = None,
) -> None:
    """run 종료 시점 — 모든 메트릭 일괄 갱신.

    Args:
        policy_id: 정책 ID
        status: ``completed`` | ``failed`` | ``skipped``
        duration_sec: 소요 초 (None 이면 histogram skip)
        traces_evaluated: 평가한 trace 수
        avg_score / pass_rate: 직전 run 게이지 값
        scores_by_evaluator: evaluator 별 평균 점수 dict
        cost_usd: 이 run 의 비용 (counter 증분)
        triggered_alerts: 발화된 알림 metric ID 목록
    """
    auto_eval_runs_total.labels(policy_id=policy_id, status=status).inc()

    if duration_sec is not None and duration_sec >= 0:
        auto_eval_run_duration_seconds.labels(policy_id=policy_id).observe(duration_sec)

    if traces_evaluated > 0:
        auto_eval_traces_evaluated_total.labels(policy_id=policy_id).inc(traces_evaluated)

    if avg_score is not None:
        auto_eval_avg_score.labels(policy_id=policy_id).set(float(avg_score))
    if pass_rate is not None:
        auto_eval_pass_rate.labels(policy_id=policy_id).set(float(pass_rate))

    if scores_by_evaluator:
        for name, value in scores_by_evaluator.items():
            if value is None:
                continue
            auto_eval_evaluator_score.labels(policy_id=policy_id, evaluator=str(name)).set(
                float(value)
            )

    if cost_usd and cost_usd > 0:
        auto_eval_cost_usd_total.labels(policy_id=policy_id).inc(float(cost_usd))

    if triggered_alerts:
        for metric_id in triggered_alerts:
            auto_eval_alerts_triggered_total.labels(
                policy_id=policy_id, metric=str(metric_id)
            ).inc()


def record_alert_triggered(policy_id: str, metric_id: str) -> None:
    """알림 발화 시점 단발 갱신 (engine 외부에서 호출 가능)."""
    auto_eval_alerts_triggered_total.labels(policy_id=policy_id, metric=str(metric_id)).inc()


def record_cost(policy_id: str, cost_usd: float) -> None:
    """비용 누적 — record_run_completed 외에서 호출 가능."""
    if cost_usd and cost_usd > 0:
        auto_eval_cost_usd_total.labels(policy_id=policy_id).inc(float(cost_usd))


def record_run_from_run_obj(policy_id: str, run: Any) -> None:
    """``AutoEvalRun`` 객체로부터 메트릭 일괄 갱신.

    Engine 통합용 단축 헬퍼 — Phase 8-B-1 engine 이 본 함수만 호출하면 모든
    메트릭이 갱신된다.

    duration_sec 은 ``duration_ms / 1000`` 으로 변환된다.
    """
    duration_sec: float | None
    if getattr(run, "duration_ms", None) is not None:
        duration_sec = float(run.duration_ms) / 1000.0
    else:
        duration_sec = None
    record_run_completed(
        policy_id=policy_id,
        status=str(getattr(run, "status", "unknown")),
        duration_sec=duration_sec,
        traces_evaluated=int(getattr(run, "traces_evaluated", 0) or 0),
        avg_score=getattr(run, "avg_score", None),
        pass_rate=getattr(run, "pass_rate", None),
        scores_by_evaluator=getattr(run, "scores_by_evaluator", None),
        cost_usd=float(getattr(run, "cost_usd", 0.0) or 0.0),
        triggered_alerts=list(getattr(run, "triggered_alerts", []) or []),
    )


__all__ = [
    "auto_eval_alerts_triggered_total",
    "auto_eval_avg_score",
    "auto_eval_cost_usd_total",
    "auto_eval_evaluator_score",
    "auto_eval_pass_rate",
    "auto_eval_run_duration_seconds",
    "auto_eval_runs_total",
    "auto_eval_traces_evaluated_total",
    "record_alert_triggered",
    "record_cost",
    "record_run_completed",
    "record_run_from_run_obj",
    "record_run_started",
]
