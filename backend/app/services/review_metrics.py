"""Review Queue Prometheus 메트릭 정의 + 갱신 헬퍼 (Phase 8-C-8).

본 모듈은 ``docs/AGENT_EVAL.md`` §29 Review Queue 메트릭 카탈로그를
``prometheus_client`` Counter / Gauge / Histogram 으로 등록하고, ReviewQueueService
스코프에서 호출 가능한 헬퍼를 export 한다.

설계 원칙
---------
- 메트릭은 모듈 import 시 자동 등록된다 (``ax_review_*`` / ``ax_evaluator_*`` prefix).
- 헬퍼는 None-safe — 부재한 라벨은 갱신 skip 하여 graceful 동작.
- 라벨 카디널리티: evaluator 라벨은 정책 evaluator 카탈로그 외에는 등장하지 않으므로
  자연스럽게 제어된다.

본 모듈은 ``observability.py`` 의 setup 흐름에 의존하지 않는다 — import 만으로
모든 메트릭이 등록된다.
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ---------------------------------------------------------------------------
# Metric definitions (AGENT_EVAL.md §29)
# ---------------------------------------------------------------------------

review_items_total: Gauge = Gauge(
    "ax_review_items",
    "Review Queue 현재 항목 수 (상태별 게이지)",
    labelnames=("type", "status", "severity"),
)
"""현재 큐의 항목 수 — Gauge.

상태 전이마다 ``inc/dec`` 로 갱신한다 (open → in_review 시 open=-1, in_review=+1).

라벨:
    type: ReviewItemType
    status: ReviewStatus (open | in_review | resolved | dismissed)
    severity: ReviewSeverity
"""

review_items_created_total: Counter = Counter(
    "ax_review_items_created_total",
    "Review Queue 진입 누적 (생성 시점)",
    labelnames=("type", "source"),
)
"""ReviewItem 생성 누적 카운터.

라벨:
    type: ReviewItemType
    source: ``auto`` (AutoEvalEngine 자동 진입) | ``manual`` | ``user_report``
"""

review_resolution_duration_seconds: Histogram = Histogram(
    "ax_review_resolution_duration_seconds",
    "Review 항목이 open → resolved/dismissed 까지 걸린 시간 (초)",
    labelnames=("decision",),
    buckets=(60, 300, 600, 1800, 3600, 7200, 14400, 28800, 86400),
)
"""resolve 시점 처리 시간 히스토그램.

라벨:
    decision: ReviewDecision (approve | override | dismiss | add_to_dataset)
"""

evaluator_disagreement_total: Counter = Counter(
    "ax_evaluator_disagreement_total",
    "Reviewer 결정별 evaluator 정확도 학습 카운터",
    labelnames=("evaluator", "decision"),
)
"""evaluator 별 reviewer 결정 누적 — override 비율이 높은 evaluator 는 rubric 재조정 후보.

라벨:
    evaluator: evaluator 이름 (e.g. ``llm_judge_factuality``, ``exact_match``)
    decision: ReviewDecision
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def record_review_created(type_: str, source: str, severity: str = "medium") -> None:
    """ReviewItem 생성 시점 — 생성 카운터 증분 + 게이지(open) 증분.

    Args:
        type_: ReviewItemType 값
        source: ``auto`` | ``manual`` | ``user_report``
        severity: ReviewSeverity 값
    """
    review_items_created_total.labels(type=str(type_), source=str(source)).inc()
    review_items_total.labels(type=str(type_), status="open", severity=str(severity)).inc()


def record_review_status_change(
    type_: str,
    severity: str,
    *,
    from_status: str | None,
    to_status: str,
) -> None:
    """상태 전이 — from_status 게이지 -1, to_status 게이지 +1.

    open → in_review, in_review → resolved 등 모든 전이에서 호출한다.
    """
    if from_status:
        review_items_total.labels(
            type=str(type_), status=str(from_status), severity=str(severity)
        ).dec()
    review_items_total.labels(type=str(type_), status=str(to_status), severity=str(severity)).inc()


def record_review_resolved(
    decision: str,
    duration_sec: float | None,
    *,
    automatic_scores: dict[str, float | None] | None = None,
) -> None:
    """resolve 시점 — duration histogram + evaluator_disagreement 일괄 갱신.

    Args:
        decision: ReviewDecision
        duration_sec: open → resolved 까지 초 (None 이면 histogram skip)
        automatic_scores: 진입 snapshot — evaluator 별로 disagreement 카운터 증분
    """
    if duration_sec is not None and duration_sec >= 0:
        review_resolution_duration_seconds.labels(decision=str(decision)).observe(
            float(duration_sec)
        )

    if automatic_scores:
        for evaluator_name in automatic_scores:
            if not evaluator_name:
                continue
            evaluator_disagreement_total.labels(
                evaluator=str(evaluator_name), decision=str(decision)
            ).inc()


def record_evaluator_disagreement(evaluator: str, decision: str) -> None:
    """단발 — 단일 evaluator + 결정 카운터 증분.

    ``record_review_resolved`` 가 자동 호출하므로 일반적으로 직접 호출할 필요는 없으나,
    외부 진단 도구에서 ad-hoc 갱신용으로 사용 가능.
    """
    evaluator_disagreement_total.labels(evaluator=str(evaluator), decision=str(decision)).inc()


__all__ = [
    "evaluator_disagreement_total",
    "record_evaluator_disagreement",
    "record_review_created",
    "record_review_resolved",
    "record_review_status_change",
    "review_items_created_total",
    "review_items_total",
    "review_resolution_duration_seconds",
]
