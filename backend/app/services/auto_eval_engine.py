"""Auto-Eval Engine — 단일 정책 실행 (Phase 8-B-1).

본 모듈은 ``docs/AGENT_EVAL.md`` §10 명세를 그대로 구현한다.

흐름 (run_policy 13단계):
    1. 정책 로드
    2. 일일 비용 한도 체크 → 초과 시 status=skipped + 알림
    3. AutoEvalRun 생성 (status=running)
    4. trace 검색 (sample_size 적용)
    5. trace 단건 fetch (병렬, semaphore=10)
    6. expected dataset 매칭 (선택)
    7. 각 trace 에 대해 ``EvaluationPipeline.evaluate_trace`` 병렬 실행
    8. 집계 (avg_score / pass_rate / scores_by_evaluator / duration)
    9. 비용 추정 + ``record_cost``
    10. 회귀 감지 (alert_thresholds 평가, baseline 대비 drop_pct 포함)
    11. 알림 발송 (in-app NotificationService)
    12. Review Queue 진입 (Phase 8-C 통합 시 활성)
    13. run 완료 + 정책 reschedule
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.evaluators.pipeline import WEIGHTED_SCORE_NAME, EvaluationPipeline
from app.models.auto_eval import (
    AlertThreshold,
    AutoEvalPolicy,
    AutoEvalRun,
    AutoEvalRunStatus,
)
from app.models.trace import TraceTree
from app.services.auto_eval_repo import AutoEvalRepo
from app.services.langfuse_client import LangfuseClient
from app.services.notification_service import create_notification
from app.services.redis_client import RedisClient
from app.services.trace_fetcher import TraceFetcher

logger = get_logger(__name__)


PASS_RATE_THRESHOLD = 0.7
"""pass_rate 산정 시 weighted_score 임계 (>=0.7 통과)."""

LLM_JUDGE_COST_PER_TRACE_USD = 0.005
"""LLM Judge evaluator 1회 평균 비용 (heuristic — pipeline에서 cost 노출 시 교체)."""

TRACE_FETCH_CONCURRENCY = 10
"""trace 단건 fetch 병렬도."""


class AutoEvalEngineError(Exception):
    """엔진 실행 일반 에러."""


class AutoEvalEngine:
    """단일 정책 실행 엔진."""

    def __init__(
        self,
        repo: AutoEvalRepo,
        trace_fetcher: TraceFetcher,
        pipeline: EvaluationPipeline,
        langfuse: LangfuseClient | Any,
        redis: RedisClient | Any,
        review_queue: Any | None = None,
    ) -> None:
        self._repo = repo
        self._trace_fetcher = trace_fetcher
        self._pipeline = pipeline
        self._langfuse = langfuse
        self._redis = redis
        self._review_queue = review_queue

    # ------------------------------------------------------------------ #
    # Public — 정책 1회 실행
    # ------------------------------------------------------------------ #
    async def run_policy(self, policy_id: str) -> AutoEvalRun:
        """정책 1회 실행. 실패 시 :class:`AutoEvalEngineError` raise."""
        # 1. 정책 로드
        policy = await self._repo.get_policy(policy_id)

        if policy.status != "active":
            raise AutoEvalEngineError(f"policy {policy_id} is not active")

        # 2. 일일 비용 한도 체크
        if policy.daily_cost_limit_usd is not None:
            current_cost = await self._repo.get_daily_cost(policy_id)
            if current_cost >= policy.daily_cost_limit_usd:
                run = self._build_run(
                    policy,
                    status="skipped",
                    skip_reason="daily_cost_limit_exceeded",
                )
                run.completed_at = datetime.now(UTC)
                await self._repo.create_run(run)
                await self._notify_cost_limit(policy)
                logger.info(
                    "auto_eval_run_skipped_cost_limit",
                    policy_id=policy_id,
                    run_id=run.id,
                    current_cost=current_cost,
                    limit=policy.daily_cost_limit_usd,
                )
                return run

        # 3. AutoEvalRun 생성
        run = self._build_run(policy, status="running")
        await self._repo.create_run(run)

        try:
            t_start = datetime.now(UTC)

            # 4. trace 검색
            summaries, total = await self._trace_fetcher.search(policy.trace_filter)
            run.traces_total = total

            if not summaries:
                run.status = "completed"
                run.completed_at = datetime.now(UTC)
                run.duration_ms = (run.completed_at - t_start).total_seconds() * 1000.0
                await self._repo.update_run(run)
                await self._repo.reschedule(policy)
                logger.info(
                    "auto_eval_run_completed_empty",
                    policy_id=policy_id,
                    run_id=run.id,
                )
                return run

            # 5. 단건 fetch (병렬)
            sem = asyncio.Semaphore(TRACE_FETCH_CONCURRENCY)

            async def _fetch_one(summary_id: str) -> TraceTree | None:
                async with sem:
                    try:
                        return await self._trace_fetcher.get(summary_id, policy.project_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "auto_eval_trace_fetch_failed",
                            trace_id=summary_id,
                            error=str(exc),
                        )
                        return None

            fetched = await asyncio.gather(
                *[_fetch_one(s.id) for s in summaries], return_exceptions=False
            )
            traces: list[TraceTree] = [t for t in fetched if t is not None]

            # 6. expected dataset 매칭
            expecteds = await self._match_expected(traces, policy.expected_dataset_name)

            # 7. evaluator pipeline 병렬
            async def _eval_one(
                trace: TraceTree,
            ) -> dict[str, float | None]:
                try:
                    return await self._pipeline.evaluate_trace(
                        policy.evaluators, trace, expecteds.get(trace.id)
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "auto_eval_trace_eval_failed",
                        trace_id=trace.id,
                        error=str(exc),
                    )
                    return {}

            results = await asyncio.gather(*[_eval_one(t) for t in traces], return_exceptions=False)

            # 8. 집계
            run.traces_evaluated = len(traces)
            run.avg_score = self._compute_avg(results, WEIGHTED_SCORE_NAME)
            run.pass_rate = self._compute_pass_rate(results, PASS_RATE_THRESHOLD)
            run.scores_by_evaluator = self._compute_per_evaluator(results)

            now_after_eval = datetime.now(UTC)
            run.duration_ms = (now_after_eval - t_start).total_seconds() * 1000.0

            # 9. 비용 추정 — pipeline 응답에 cost 정보가 없어 heuristic 사용
            judge_count = sum(1 for ev in policy.evaluators if ev.type == "judge")
            estimated_cost = run.traces_evaluated * judge_count * LLM_JUDGE_COST_PER_TRACE_USD
            run.cost_usd = round(estimated_cost, 6)
            if estimated_cost > 0:
                await self._repo.record_cost(policy_id, estimated_cost)

            # 10. 회귀 감지
            triggered = await self._check_alerts(policy, run)
            run.triggered_alerts = triggered

            # 11. 알림 발송
            if triggered:
                await self._notify_regression(policy, run, triggered)

            # 12. Review Queue 진입 (Phase 8-C 통합 시 활성)
            if self._review_queue is not None:
                try:
                    review_count = await self._enqueue_for_review(policy, run, traces, results)
                    run.review_items_created = review_count
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "auto_eval_review_enqueue_failed",
                        policy_id=policy_id,
                        run_id=run.id,
                        error=str(exc),
                    )

            # 13. 완료 + reschedule
            run.status = "completed"
            run.completed_at = datetime.now(UTC)
            await self._repo.update_run(run)
            await self._repo.reschedule(policy)

            logger.info(
                "auto_eval_run_completed",
                policy_id=policy_id,
                run_id=run.id,
                traces_evaluated=run.traces_evaluated,
                avg_score=run.avg_score,
                pass_rate=run.pass_rate,
                triggered_alerts=run.triggered_alerts,
                cost_usd=run.cost_usd,
                duration_ms=run.duration_ms,
            )
            return run

        except Exception as exc:
            logger.exception(
                "auto_eval_run_failed",
                policy_id=policy_id,
                run_id=run.id,
                error=str(exc),
            )
            run.status = "failed"
            run.error_message = str(exc)
            run.completed_at = datetime.now(UTC)
            try:
                await self._repo.update_run(run)
            except Exception as upd_exc:  # noqa: BLE001
                # update 실패는 swallow — 원 예외 우선
                logger.warning(
                    "auto_eval_run_update_after_failure_failed",
                    policy_id=policy_id,
                    run_id=run.id,
                    error=str(upd_exc),
                )
            raise AutoEvalEngineError(str(exc)) from exc

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _build_run(
        policy: AutoEvalPolicy,
        status: AutoEvalRunStatus,
        skip_reason: str | None = None,
    ) -> AutoEvalRun:
        """초기 AutoEvalRun 생성."""
        return AutoEvalRun(
            id=f"run_{uuid.uuid4().hex[:12]}",
            policy_id=policy.id,
            started_at=datetime.now(UTC),
            status=status,
            skip_reason=skip_reason,
        )

    async def _match_expected(
        self,
        traces: list[TraceTree],
        dataset_name: str | None,
    ) -> dict[str, dict[str, Any]]:
        """expected_dataset_name 이 있으면 trace.input → expected_output 매칭.

        매칭 시그니처는 ``json.dumps(input, sort_keys=True)`` 정확 일치.
        실패는 graceful — 빈 dict 반환.
        """
        if not dataset_name:
            return {}
        try:
            from app.services.dataset_service import list_dataset_items_via_client

            items = list_dataset_items_via_client(self._langfuse, dataset_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "auto_eval_dataset_match_failed",
                dataset=dataset_name,
                error=str(exc),
            )
            return {}

        index: dict[str, Any] = {}
        for it in items or []:
            sig = self._signature(it.get("input"))
            if sig:
                index[sig] = it.get("expected_output")

        result: dict[str, dict[str, Any]] = {}
        for trace in traces:
            sig = self._signature(trace.input)
            if sig and sig in index:
                result[trace.id] = {"expected_output": index[sig]}
        return result

    @staticmethod
    def _signature(value: Any) -> str:
        """input/expected 매칭용 시그니처 — JSON 정렬 dump."""
        if value is None:
            return ""
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)

    @staticmethod
    def _compute_avg(results: list[dict[str, float | None]], key: str) -> float | None:
        """결과 리스트에서 특정 key 평균 (None 제외). 빈 결과면 None."""
        values = [r.get(key) for r in results if r.get(key) is not None]
        # mypy: values 는 float | None 이지만 None 은 위에서 제외됨
        floats: list[float] = [float(v) for v in values if v is not None]
        return sum(floats) / len(floats) if floats else None

    @staticmethod
    def _compute_pass_rate(
        results: list[dict[str, float | None]], threshold: float
    ) -> float | None:
        """weighted_score >= threshold 비율."""
        values = [
            r.get(WEIGHTED_SCORE_NAME) for r in results if r.get(WEIGHTED_SCORE_NAME) is not None
        ]
        floats: list[float] = [float(v) for v in values if v is not None]
        if not floats:
            return None
        return sum(1 for v in floats if v >= threshold) / len(floats)

    @staticmethod
    def _compute_per_evaluator(
        results: list[dict[str, float | None]],
    ) -> dict[str, float | None]:
        """evaluator 이름 → 평균 점수. weighted_score 도 포함."""
        sums: dict[str, list[float]] = defaultdict(list)
        for r in results:
            for k, v in r.items():
                if v is not None:
                    sums[k].append(float(v))
        return {k: (sum(vs) / len(vs)) for k, vs in sums.items() if vs}

    # ------------------------------------------------------------------ #
    # Alerting
    # ------------------------------------------------------------------ #
    async def _check_alerts(
        self,
        policy: AutoEvalPolicy,
        run: AutoEvalRun,
    ) -> list[str]:
        """alert_thresholds 평가 → 발화된 metric 식별자 리스트."""
        if not policy.alert_thresholds:
            return []
        baseline = await self._repo.get_latest_completed_run(policy.id)
        triggered: list[str] = []
        for threshold in policy.alert_thresholds:
            if self._evaluate_alert(threshold, run, baseline):
                triggered.append(self._alert_id(threshold))
        return triggered

    @staticmethod
    def _alert_id(threshold: AlertThreshold) -> str:
        """alert 식별자 — ``{metric}:{evaluator_name}`` 또는 ``{metric}``."""
        if threshold.evaluator_name:
            return f"{threshold.metric}:{threshold.evaluator_name}"
        return threshold.metric

    @staticmethod
    def _evaluate_alert(
        threshold: AlertThreshold,
        run: AutoEvalRun,
        baseline: AutoEvalRun | None,
    ) -> bool:
        """절대값 임계 + drop_pct 평가."""
        current = AutoEvalEngine._get_metric(threshold.metric, threshold.evaluator_name, run)
        if current is None:
            return False

        # 절대값
        if threshold.operator == "lt" and current < threshold.value:
            return True
        if threshold.operator == "lte" and current <= threshold.value:
            return True
        if threshold.operator == "gt" and current > threshold.value:
            return True
        if threshold.operator == "gte" and current >= threshold.value:
            return True

        # 상대 (drop_pct)
        if threshold.drop_pct is not None and baseline is not None:
            base = AutoEvalEngine._get_metric(threshold.metric, threshold.evaluator_name, baseline)
            if base is not None and base > 0:
                drop = (base - current) / base
                if drop >= threshold.drop_pct:
                    return True
        return False

    @staticmethod
    def _get_metric(
        metric: str,
        evaluator_name: str | None,
        run: AutoEvalRun,
    ) -> float | None:
        """metric 이름 + evaluator_name 으로 run의 값 추출."""
        if metric == "avg_score":
            return run.avg_score
        if metric == "pass_rate":
            return run.pass_rate
        if metric == "evaluator_score" and evaluator_name:
            value = run.scores_by_evaluator.get(evaluator_name)
            return value
        return None

    # ------------------------------------------------------------------ #
    # Notifications
    # ------------------------------------------------------------------ #
    async def _notify_regression(
        self,
        policy: AutoEvalPolicy,
        run: AutoEvalRun,
        triggered: list[str],
    ) -> None:
        """회귀 감지 in-app 알림 — best-effort."""
        avg_str = f"{run.avg_score:.2f}" if run.avg_score is not None else "N/A"
        pass_str = f"{run.pass_rate:.2f}" if run.pass_rate is not None else "N/A"
        body = f"{', '.join(triggered)} 임계 매칭. avg_score={avg_str}, pass_rate={pass_str}"
        for user_id in policy.notification_targets:
            try:
                await create_notification(
                    user_id=user_id,
                    type_="auto_eval_regression",
                    title=f"[Auto-Eval] {policy.name} — 회귀 감지",
                    body=body,
                    link=f"/auto-eval/{policy.id}",
                    redis=self._redis,
                    resource_id=run.id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_eval_notification_regression_failed",
                    policy_id=policy.id,
                    user_id=user_id,
                    error=str(exc),
                )

    async def _notify_cost_limit(self, policy: AutoEvalPolicy) -> None:
        """일일 비용 한도 초과 알림 — best-effort."""
        body = f"한도 ${policy.daily_cost_limit_usd}. 이 정책의 오늘 실행은 자동 스킵됩니다."
        for user_id in policy.notification_targets:
            try:
                await create_notification(
                    user_id=user_id,
                    type_="auto_eval_cost_limit",
                    title=f"[Auto-Eval] {policy.name} — 일일 비용 한도 초과",
                    body=body,
                    link=f"/auto-eval/{policy.id}",
                    redis=self._redis,
                    resource_id=f"{policy.id}:cost_limit",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_eval_notification_cost_limit_failed",
                    policy_id=policy.id,
                    user_id=user_id,
                    error=str(exc),
                )

    # ------------------------------------------------------------------ #
    # Review Queue (Phase 8-C 통합용 placeholder)
    # ------------------------------------------------------------------ #
    async def _enqueue_for_review(
        self,
        policy: AutoEvalPolicy,
        run: AutoEvalRun,
        traces: list[TraceTree],
        results: list[dict[str, float | None]],
    ) -> int:
        """Phase 8-C ReviewQueue 통합 시 활성. 현재는 0 반환.

        review_queue 객체가 ``enqueue(policy, run, trace, scores)`` 메서드를 노출하면
        호출하도록 작성. 미존재 시 0.
        """
        if self._review_queue is None:
            return 0
        enqueue = getattr(self._review_queue, "enqueue", None)
        if not callable(enqueue):
            return 0
        count = 0
        for trace, scores in zip(traces, results, strict=True):
            try:
                ok = await enqueue(policy, run, trace, scores)
                if ok:
                    count += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "auto_eval_review_enqueue_item_failed",
                    trace_id=trace.id,
                    error=str(exc),
                )
        return count


__all__ = [
    "AutoEvalEngine",
    "AutoEvalEngineError",
    "LLM_JUDGE_COST_PER_TRACE_USD",
    "PASS_RATE_THRESHOLD",
    "TRACE_FETCH_CONCURRENCY",
]
