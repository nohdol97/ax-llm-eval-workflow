"""ReviewQueueService — Review 항목 영속화 + 상태 머신 + 통계 (Phase 8-C-2).

본 모듈은 ``docs/AGENT_EVAL.md`` Part III §15~§18 명세를 그대로 구현한다.

Redis 키
--------
::

    ax:review_item:{id}                       Hash + JSON payload (data 필드)
    ax:review_queue:open                      Sorted Set (severity_score desc, created_at asc)
    ax:review_queue:in_review:{user_id}       Set (해당 reviewer 가 claim 한 항목)
    ax:review_queue:by_policy:{policy_id}     Sorted Set (created_at)
    ax:review_queue:by_subject:{type}:{id}    Set
    ax:review_stats:{user_id}:{date}          Hash (일일 집계)

규칙
----
- ``open``       → 큐 ``open`` ZSet 등록 (score = severity_score * 1e10 - created_ts)
- ``in_review``  → ``open`` ZSet 제거, ``in_review:{user}`` Set 등록
- ``resolved``   → 모든 인덱스 제거 (item 본체는 보존)
- ``dismissed``  → 모든 인덱스 제거

상태 전이는 :meth:`_check_transition` 가 강제한다 — 위반 시 :class:`InvalidStatusTransitionError`.

claim 후 1시간 미해결이면 :meth:`expire_stale_claims` 가 자동 unassign 한다 (외부 스케줄러 hook).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.review import (
    SEVERITY_SCORE,
    EvaluatorDisagreementResponse,
    EvaluatorDisagreementStat,
    ReviewDecision,
    ReviewItem,
    ReviewItemCreate,
    ReviewItemResolve,
    ReviewItemType,
    ReviewQueueSummary,
    ReviewSeverity,
    ReviewStatus,
)
from app.services.redis_client import RedisClient
from app.services.review_metrics import (
    record_evaluator_disagreement,
    record_review_created,
    record_review_resolved,
    record_review_status_change,
)

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 도메인 예외
# ---------------------------------------------------------------------------
class ReviewItemNotFoundError(LabsError):
    """ReviewItem 미존재."""

    code = "review_item_not_found"
    status_code = 404
    title = "Review item not found"


class InvalidStatusTransitionError(LabsError):
    """상태 전이 규칙 위반."""

    code = "review_invalid_status_transition"
    status_code = 409
    title = "Invalid review status transition"


class ReviewClaimConflictError(LabsError):
    """이미 다른 reviewer 가 claim 한 항목."""

    code = "review_claim_conflict"
    status_code = 409
    title = "Review item already claimed"


class ReviewETagMismatchError(LabsError):
    """ETag 불일치 — resolve 동시성 보호."""

    code = "review_etag_mismatch"
    status_code = 412
    title = "Review item ETag mismatch"


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------
def _get_underlying(redis: RedisClient | Any) -> Any:
    """``RedisClient.underlying`` 또는 mock 의 ``_client`` 반환."""
    if hasattr(redis, "underlying"):
        return redis.underlying
    if hasattr(redis, "_client"):
        return redis._client
    return redis


def _to_datetime(value: Any) -> datetime | None:
    """ISO 문자열 또는 datetime → UTC-aware datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


# 큐 정렬 점수: severity 가 높을수록 우선, 같으면 더 오래된 것이 우선.
# Redis ZSet 은 score 오름차순으로 ZRANGE — score 가 작을수록 앞.
# 그래서 score = -(severity * 1e10) + created_ts 형태로 두면 zrange 0..N 이 우선순위 높은 순서.
_SEVERITY_WEIGHT = 1e11


def _queue_score(severity: ReviewSeverity, created_at: datetime) -> float:
    """``ax:review_queue:open`` ZSet score 계산.

    severity 가 높을수록(=score 작을수록) ZRANGE 0..N 결과의 앞에 등장.
    같은 severity 면 created_at 이 오래된 것이 우선.
    """
    sev = SEVERITY_SCORE.get(severity, 2)
    ts = created_at.timestamp()
    return -float(sev) * _SEVERITY_WEIGHT + ts


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------
CLAIM_AUTO_UNASSIGN_SEC = 3600
"""claim 후 자동 unassign 임계 (1시간) — AGENT_EVAL.md Part VII #7 결정."""


class ReviewQueueService:
    """Review Queue CRUD + 상태 머신 + 통계.

    의존:
        redis: ``RedisClient`` 또는 mock (``underlying`` 또는 ``_client``)
        clock: 테스트용 datetime provider (기본 ``datetime.now(UTC)``)
    """

    KEY_ITEM = "ax:review_item:{id}"
    KEY_OPEN = "ax:review_queue:open"
    KEY_IN_REVIEW = "ax:review_queue:in_review:{user_id}"
    KEY_BY_POLICY = "ax:review_queue:by_policy:{policy_id}"
    KEY_BY_SUBJECT = "ax:review_queue:by_subject:{type}:{id}"
    KEY_STATS_DAILY = "ax:review_stats:{user_id}:{date}"
    # decision 별 누적 (결정 시점 evaluator_disagreement 학습)
    KEY_DISAGREEMENT = "ax:review_disagreement:{evaluator}"

    ITEM_TTL_SEC = 365 * 86400  # 1년 (감사용)

    def __init__(
        self,
        redis: RedisClient | Any,
        *,
        clock: Any | None = None,
    ) -> None:
        self._redis = redis
        self._u = _get_underlying(redis)
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ #
    # 직렬화
    # ------------------------------------------------------------------ #
    @staticmethod
    def _to_payload(item: ReviewItem) -> str:
        return item.model_dump_json()

    @staticmethod
    def _from_payload(raw: str | bytes | None) -> ReviewItem | None:
        if not raw:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return None
        try:
            return ReviewItem.model_validate(data)
        except Exception:  # noqa: BLE001
            return None

    @classmethod
    def _item_key(cls, item_id: str) -> str:
        return cls.KEY_ITEM.format(id=item_id)

    @classmethod
    def _in_review_key(cls, user_id: str) -> str:
        return cls.KEY_IN_REVIEW.format(user_id=user_id)

    @classmethod
    def _by_policy_key(cls, policy_id: str) -> str:
        return cls.KEY_BY_POLICY.format(policy_id=policy_id)

    @classmethod
    def _by_subject_key(cls, subject_type: str, subject_id: str) -> str:
        return cls.KEY_BY_SUBJECT.format(type=subject_type, id=subject_id)

    @classmethod
    def _stats_key(cls, user_id: str, day: date) -> str:
        return cls.KEY_STATS_DAILY.format(user_id=user_id, date=day.isoformat())

    @classmethod
    def _disagreement_key(cls, evaluator: str) -> str:
        return cls.KEY_DISAGREEMENT.format(evaluator=evaluator)

    @staticmethod
    def compute_etag(item: ReviewItem) -> str:
        """resolve If-Match 용 ETag — ``updated_at`` ISO 시각 기반.

        형식: ``<status>:<updated_at_ts>``. 라이트한 비교만 필요.
        """
        ts = item.updated_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return f"{item.status}:{int(ts.timestamp() * 1000)}"

    # ------------------------------------------------------------------ #
    # 상태 머신
    # ------------------------------------------------------------------ #
    @staticmethod
    def _check_transition(current: ReviewStatus, target: ReviewStatus) -> None:
        """상태 전이 규칙 검사.

        규칙:
            open → in_review | dismissed
            in_review → open | resolved | dismissed
            resolved | dismissed → (전이 불가)
        """
        allowed: dict[ReviewStatus, set[ReviewStatus]] = {
            "open": {"in_review", "dismissed"},
            "in_review": {"open", "resolved", "dismissed"},
            "resolved": set(),
            "dismissed": set(),
        }
        if target not in allowed[current]:
            raise InvalidStatusTransitionError(
                detail=f"상태 전이 불가: {current} → {target}",
            )

    # ------------------------------------------------------------------ #
    # 진입 (자동 enqueue + 수동 추가 + 사용자 신고)
    # ------------------------------------------------------------------ #
    async def enqueue(
        self,
        policy: Any,
        run: Any,
        trace: Any,
        scores: dict[str, float | None],
    ) -> bool:
        """AutoEvalEngine 자동 진입 (5 trigger 중 자동 3가지).

        조건:
            1. weighted_score < 0.5  → reason=``auto_eval_low_score``
            2. evaluator variance > 0.3 → reason=``evaluator_disagreement``
            3. judge low confidence (uncertain 키워드) → reason=``judge_low_confidence``

        하나라도 매칭되면 ReviewItem 생성. 매칭 없으면 ``False`` 반환 (engine 통계용).
        """
        weighted = scores.get("weighted_score")
        reason: str | None = None
        severity: ReviewSeverity = "medium"
        type_: ReviewItemType = "auto_eval_flagged"
        detail: dict[str, Any] = {
            "policy_id": getattr(policy, "id", None),
            "run_id": getattr(run, "id", None),
        }

        # 1) low score
        if weighted is not None and weighted < 0.5:
            reason = "auto_eval_low_score"
            severity = "high" if weighted < 0.3 else "medium"
            detail["weighted_score"] = float(weighted)
        # 2) evaluator disagreement (variance)
        else:
            variance = self._compute_variance(scores)
            if variance is not None and variance > 0.3:
                reason = "evaluator_disagreement"
                severity = "high"
                detail["variance"] = float(variance)
                detail["scores_snapshot"] = {k: v for k, v in scores.items() if v is not None}

        if reason is None:
            return False

        await self.create_auto(
            project_id=getattr(policy, "project_id", "") or "",
            trace_id=getattr(trace, "id", "") or "",
            type_=type_,
            severity=severity,
            reason=reason,
            reason_detail=detail,
            automatic_scores=scores,
            auto_eval_policy_id=getattr(policy, "id", None),
            auto_eval_run_id=getattr(run, "id", None),
        )
        return True

    @staticmethod
    def _compute_variance(scores: dict[str, float | None]) -> float | None:
        """evaluator score 분산 (None / weighted_score 제외)."""
        values = [
            float(v) for k, v in scores.items() if v is not None and k != "weighted_score"
        ]
        if len(values) < 2:
            return None
        mean = sum(values) / len(values)
        return sum((v - mean) ** 2 for v in values) / len(values)

    async def create_auto(
        self,
        *,
        project_id: str,
        trace_id: str,
        type_: ReviewItemType,
        severity: ReviewSeverity,
        reason: str,
        reason_detail: dict[str, Any],
        automatic_scores: dict[str, float | None],
        auto_eval_policy_id: str | None,
        auto_eval_run_id: str | None,
    ) -> ReviewItem:
        """자동 진입 (AutoEvalEngine / judge_low_confidence) 단축 진입점."""
        return await self._persist_new(
            ReviewItem(
                id=self._new_id(),
                type=type_,
                severity=severity,
                subject_type="trace",
                subject_id=trace_id,
                project_id=project_id,
                reason=reason,
                reason_detail=reason_detail,
                automatic_scores=automatic_scores,
                auto_eval_policy_id=auto_eval_policy_id,
                auto_eval_run_id=auto_eval_run_id,
                created_at=self._now(),
                updated_at=self._now(),
            ),
            source="auto",
        )

    async def create_manual(
        self,
        payload: ReviewItemCreate,
    ) -> ReviewItem:
        """수동 추가 — ``POST /api/v1/reviews/items``."""
        return await self._persist_new(
            ReviewItem(
                id=self._new_id(),
                type="manual_addition",
                severity=payload.severity,
                subject_type=payload.subject_type,
                subject_id=payload.subject_id,
                project_id=payload.project_id,
                reason=payload.reason or "manual_addition",
                reason_detail=payload.reason_detail,
                automatic_scores=payload.automatic_scores,
                auto_eval_policy_id=payload.auto_eval_policy_id,
                auto_eval_run_id=payload.auto_eval_run_id,
                created_at=self._now(),
                updated_at=self._now(),
            ),
            source="manual",
        )

    async def create_user_report(
        self,
        *,
        trace_id: str,
        project_id: str,
        reporter_user_id: str,
        reason_text: str,
        severity: ReviewSeverity = "medium",
        subject_type: str = "trace",
    ) -> ReviewItem:
        """사용자 신고 — ``POST /api/v1/reviews/report``.

        ``subject_type`` 기본 ``trace``. Compare 페이지의 행 신고는 ``experiment_item``.
        """
        from app.models.review import ReviewSubjectType  # noqa: WPS433

        valid_subjects = {"trace", "experiment_item", "submission"}
        st: ReviewSubjectType = (
            subject_type if subject_type in valid_subjects else "trace"  # type: ignore[assignment]
        )
        return await self._persist_new(
            ReviewItem(
                id=self._new_id(),
                type="user_report",
                severity=severity,
                subject_type=st,
                subject_id=trace_id,
                project_id=project_id,
                reason="user_report",
                reason_detail={
                    "reporter_user_id": reporter_user_id,
                    "reason_text": reason_text,
                },
                automatic_scores={},
                auto_eval_policy_id=None,
                auto_eval_run_id=None,
                created_at=self._now(),
                updated_at=self._now(),
            ),
            source="user_report",
        )

    async def _persist_new(self, item: ReviewItem, *, source: str) -> ReviewItem:
        """신규 ReviewItem 저장 + 인덱스 등록 + 메트릭."""
        await self._u.set(self._item_key(item.id), self._to_payload(item), ex=self.ITEM_TTL_SEC)
        # open ZSet
        await self._u.zadd(
            self.KEY_OPEN, {item.id: _queue_score(item.severity, item.created_at)}
        )
        # by_policy
        if item.auto_eval_policy_id:
            await self._u.zadd(
                self._by_policy_key(item.auto_eval_policy_id),
                {item.id: item.created_at.timestamp()},
            )
        # by_subject
        await self._u.sadd(
            self._by_subject_key(item.subject_type, item.subject_id), item.id
        )
        record_review_created(item.type, source, item.severity)
        logger.info(
            "review_item_created",
            review_id=item.id,
            type=item.type,
            reason=item.reason,
            severity=item.severity,
            project_id=item.project_id,
            source=source,
        )
        return item

    # ------------------------------------------------------------------ #
    # 조회
    # ------------------------------------------------------------------ #
    async def get_item(self, item_id: str) -> ReviewItem:
        """item 조회. 미존재 시 :class:`ReviewItemNotFoundError`."""
        raw = await self._u.get(self._item_key(item_id))
        item = self._from_payload(raw)
        if item is None:
            raise ReviewItemNotFoundError(detail=f"리뷰 항목을 찾을 수 없습니다: id={item_id!r}")
        return item

    async def list_items(
        self,
        *,
        project_id: str | None = None,
        status: ReviewStatus | None = None,
        type_: ReviewItemType | None = None,
        severity: ReviewSeverity | None = None,
        assigned_to: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[list[ReviewItem], int]:
        """필터 + 페이지네이션 + 우선순위 정렬.

        - ``status=open`` 인 경우 ``ax:review_queue:open`` ZSet 우선순위 사용
        - 그 외는 ``scan_iter`` (실 운영에서는 별도 인덱스 필요 — v1 충분)
        - 다른 필터는 in-memory 후처리
        """
        page = max(1, page)
        page_size = max(1, min(100, page_size))

        ids: list[str] = []
        if status == "open":
            raw = await self._u.zrange(self.KEY_OPEN, 0, -1)
            ids = [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in raw]
        elif status == "in_review" and assigned_to:
            raw = await self._u.smembers(self._in_review_key(assigned_to))
            ids = sorted(
                [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in raw]
            )
        else:
            async for key in self._u.scan_iter(match="ax:review_item:*"):
                k = key.decode("utf-8") if isinstance(key, bytes) else key
                ids.append(k.split(":", 2)[2])

        items: list[ReviewItem] = []
        for iid in ids:
            raw = await self._u.get(self._item_key(iid))
            item = self._from_payload(raw)
            if item is None:
                continue
            if status is not None and item.status != status:
                continue
            if project_id is not None and item.project_id != project_id:
                continue
            if type_ is not None and item.type != type_:
                continue
            if severity is not None and item.severity != severity:
                continue
            if assigned_to is not None and item.assigned_to != assigned_to:
                continue
            items.append(item)

        # status!=open 일 때 created_at desc 정렬
        if status != "open":
            items.sort(key=lambda i: i.created_at, reverse=True)

        total = len(items)
        start = (page - 1) * page_size
        end = start + page_size
        return items[start:end], total

    # ------------------------------------------------------------------ #
    # claim / release
    # ------------------------------------------------------------------ #
    async def claim(self, item_id: str, user_id: str) -> ReviewItem:
        """open → in_review 전이 + assigned_to/at 기록.

        - 이미 in_review 면 :class:`ReviewClaimConflictError`
        - resolved/dismissed 면 :class:`InvalidStatusTransitionError`
        """
        item = await self.get_item(item_id)
        if item.status == "in_review":
            if item.assigned_to == user_id:
                # idempotent — 본인 재claim
                return item
            raise ReviewClaimConflictError(
                detail=f"이미 {item.assigned_to!r} 가 claim 중입니다.",
            )
        self._check_transition(item.status, "in_review")

        from_status = item.status
        item.status = "in_review"
        item.assigned_to = user_id
        item.assigned_at = self._now()
        item.updated_at = self._now()

        await self._u.set(self._item_key(item.id), self._to_payload(item), ex=self.ITEM_TTL_SEC)
        await self._u.zrem(self.KEY_OPEN, item.id)
        await self._u.sadd(self._in_review_key(user_id), item.id)
        record_review_status_change(
            item.type, item.severity, from_status=from_status, to_status="in_review"
        )
        logger.info("review_item_claimed", review_id=item.id, user_id=user_id)
        return item

    async def release(self, item_id: str, user_id: str, *, force: bool = False) -> ReviewItem:
        """in_review → open. ``force=True`` (admin) 가 아니면 본인만 가능."""
        item = await self.get_item(item_id)
        if item.status != "in_review":
            raise InvalidStatusTransitionError(
                detail=f"in_review 가 아닌 항목은 release 불가: status={item.status}",
            )
        if not force and item.assigned_to != user_id:
            raise ReviewClaimConflictError(
                detail="본인이 claim 한 항목만 release 가능합니다 (admin 은 force 사용).",
            )

        prev_user = item.assigned_to
        item.status = "open"
        item.assigned_to = None
        item.assigned_at = None
        item.updated_at = self._now()

        await self._u.set(self._item_key(item.id), self._to_payload(item), ex=self.ITEM_TTL_SEC)
        if prev_user:
            await self._u.srem(self._in_review_key(prev_user), item.id)
        await self._u.zadd(
            self.KEY_OPEN, {item.id: _queue_score(item.severity, item.created_at)}
        )
        record_review_status_change(
            item.type, item.severity, from_status="in_review", to_status="open"
        )
        logger.info("review_item_released", review_id=item.id, prev_user=prev_user)
        return item

    # ------------------------------------------------------------------ #
    # resolve
    # ------------------------------------------------------------------ #
    async def resolve(
        self,
        item_id: str,
        user_id: str,
        payload: ReviewItemResolve,
        *,
        if_match: str | None = None,
    ) -> ReviewItem:
        """결정 적용 — in_review → resolved/dismissed.

        - dismiss 결정은 open 에서도 가능 (false positive 즉시 처리)
        - approve / override / add_to_dataset 는 in_review 만 가능
        - ETag 불일치 시 :class:`ReviewETagMismatchError`
        """
        item = await self.get_item(item_id)

        # ETag 검증
        if if_match is not None:
            current_etag = self.compute_etag(item)
            raw = if_match.strip()
            if raw == "*":
                pass
            else:
                if raw.startswith('"') and raw.endswith('"'):
                    raw = raw[1:-1]
                if raw != current_etag:
                    raise ReviewETagMismatchError(
                        detail="ETag 불일치 — 동시 수정이 감지되었습니다.",
                    )

        # 결정별 target status
        if payload.decision == "dismiss":
            target_status: ReviewStatus = "dismissed"
        else:
            target_status = "resolved"

        # 전이 규칙 — dismiss 는 open 에서도 허용
        if item.status == "open" and payload.decision == "dismiss":
            pass  # 명시적 허용
        else:
            self._check_transition(item.status, target_status)

        # 결정 적용
        from_status = item.status
        prev_user = item.assigned_to
        now = self._now()
        item.status = target_status
        item.decision = payload.decision
        item.reviewer_score = payload.reviewer_score
        item.reviewer_comment = payload.reviewer_comment
        item.expected_output = payload.expected_output
        item.resolved_by = user_id
        item.resolved_at = now
        item.updated_at = now

        # 인덱스 정리
        await self._u.set(self._item_key(item.id), self._to_payload(item), ex=self.ITEM_TTL_SEC)
        await self._u.zrem(self.KEY_OPEN, item.id)
        if prev_user:
            await self._u.srem(self._in_review_key(prev_user), item.id)

        # 통계 + 메트릭
        duration_sec = (now - item.created_at).total_seconds()
        record_review_resolved(
            payload.decision,
            duration_sec,
            automatic_scores=item.automatic_scores,
        )
        record_review_status_change(
            item.type, item.severity, from_status=from_status, to_status=target_status
        )
        await self._bump_daily_stats(user_id, payload.decision, duration_sec)
        if payload.decision == "override":
            await self._bump_disagreement(item.automatic_scores)

        logger.info(
            "review_item_resolved",
            review_id=item.id,
            decision=payload.decision,
            user_id=user_id,
            duration_sec=duration_sec,
        )
        return item

    # ------------------------------------------------------------------ #
    # admin — 삭제
    # ------------------------------------------------------------------ #
    async def delete(self, item_id: str) -> None:
        """admin 전용 — item 본체 + 모든 인덱스 정리."""
        item = await self.get_item(item_id)
        await self._u.delete(self._item_key(item_id))
        await self._u.zrem(self.KEY_OPEN, item_id)
        if item.assigned_to:
            await self._u.srem(self._in_review_key(item.assigned_to), item_id)
        if item.auto_eval_policy_id:
            await self._u.zrem(self._by_policy_key(item.auto_eval_policy_id), item_id)
        await self._u.srem(self._by_subject_key(item.subject_type, item.subject_id), item_id)
        logger.info("review_item_deleted", review_id=item_id)

    # ------------------------------------------------------------------ #
    # 자동 unassign — 1시간 미해결 claim 회수
    # ------------------------------------------------------------------ #
    async def expire_stale_claims(self, *, threshold_sec: int = CLAIM_AUTO_UNASSIGN_SEC) -> int:
        """``in_review`` 상태로 ``threshold_sec`` 이상 미해결인 항목을 open 으로 회수.

        외부 스케줄러 (auto_eval_scheduler tick 또는 cron) 가 호출.
        """
        now = self._now()
        threshold = timedelta(seconds=threshold_sec)
        recovered = 0

        async for key in self._u.scan_iter(match="ax:review_queue:in_review:*"):
            k = key.decode("utf-8") if isinstance(key, bytes) else key
            user_id = k.rsplit(":", 1)[-1]
            ids_raw = await self._u.smembers(k)
            ids = [(r.decode("utf-8") if isinstance(r, bytes) else str(r)) for r in ids_raw]
            for iid in ids:
                try:
                    item = await self.get_item(iid)
                except ReviewItemNotFoundError:
                    await self._u.srem(k, iid)
                    continue
                if item.status != "in_review":
                    await self._u.srem(k, iid)
                    continue
                if item.assigned_at is None:
                    continue
                age = now - item.assigned_at
                if age >= threshold:
                    try:
                        await self.release(item.id, user_id, force=True)
                        recovered += 1
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "review_expire_release_failed",
                            review_id=item.id,
                            error=str(exc),
                        )
        if recovered:
            logger.info("review_stale_claims_recovered", count=recovered)
        return recovered

    # ------------------------------------------------------------------ #
    # 통계
    # ------------------------------------------------------------------ #
    async def _bump_daily_stats(
        self, user_id: str, decision: ReviewDecision, duration_sec: float
    ) -> None:
        """``ax:review_stats:{user_id}:{date}`` Hash 갱신."""
        day = self._now().date()
        key = self._stats_key(user_id, day)
        try:
            await self._u.hincrby(key, "resolved", 1)
            await self._u.hincrby(key, f"decision:{decision}", 1)
            await self._u.hincrbyfloat(key, "total_duration_sec", float(duration_sec))
            await self._u.expire(key, 90 * 86400)  # 90일 보존
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "review_stats_bump_failed", user_id=user_id, error=str(exc)
            )

    async def _bump_disagreement(self, scores: dict[str, float | None]) -> None:
        """override 결정 시 evaluator 별 누적 카운터 증가 + 메트릭."""
        for evaluator_name in scores:
            if evaluator_name == "weighted_score" or not evaluator_name:
                continue
            try:
                await self._u.hincrby(
                    self._disagreement_key(evaluator_name), "override", 1
                )
                await self._u.expire(self._disagreement_key(evaluator_name), 365 * 86400)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "review_disagreement_bump_failed",
                    evaluator=evaluator_name,
                    error=str(exc),
                )
            record_evaluator_disagreement(evaluator_name, "override")

    async def get_summary(
        self,
        *,
        project_id: str | None = None,
    ) -> ReviewQueueSummary:
        """전체 큐 요약 — open/in_review/today resolved/dismissed + 평균 처리 시간."""
        open_count = 0
        in_review_count = 0
        resolved_today = 0
        dismissed_today = 0
        durations: list[float] = []

        today = self._now().date()
        async for key in self._u.scan_iter(match="ax:review_item:*"):
            k = key.decode("utf-8") if isinstance(key, bytes) else key
            iid = k.split(":", 2)[2]
            raw = await self._u.get(self._item_key(iid))
            item = self._from_payload(raw)
            if item is None:
                continue
            if project_id is not None and item.project_id != project_id:
                continue
            if item.status == "open":
                open_count += 1
            elif item.status == "in_review":
                in_review_count += 1
            elif item.status in {"resolved", "dismissed"} and item.resolved_at is not None:
                if item.resolved_at.date() == today:
                    if item.status == "resolved":
                        resolved_today += 1
                    else:
                        dismissed_today += 1
                    durations.append(
                        (item.resolved_at - item.created_at).total_seconds() / 60.0
                    )

        avg = sum(durations) / len(durations) if durations else None
        return ReviewQueueSummary(
            open=open_count,
            in_review=in_review_count,
            resolved_today=resolved_today,
            dismissed_today=dismissed_today,
            avg_resolution_time_min=avg,
        )

    async def get_reviewer_stats(self, user_id: str) -> dict[str, Any]:
        """reviewer 개인 통계 (오늘 + 누적 in_review)."""
        in_review_raw = await self._u.smembers(self._in_review_key(user_id))
        in_review_count = (
            len(in_review_raw)
            if hasattr(in_review_raw, "__len__")
            else sum(1 for _ in in_review_raw)
        )

        today = self._now().date()
        stats_raw = await self._u.hgetall(self._stats_key(user_id, today)) or {}
        # decode bytes
        stats: dict[str, str] = {}
        for k, v in stats_raw.items():
            kk = k.decode("utf-8") if isinstance(k, bytes) else str(k)
            vv = v.decode("utf-8") if isinstance(v, bytes) else str(v)
            stats[kk] = vv

        resolved_today = int(stats.get("resolved", 0) or 0)
        total_dur = float(stats.get("total_duration_sec", 0.0) or 0.0)
        avg_min = (total_dur / resolved_today / 60.0) if resolved_today > 0 else None

        decisions: dict[str, int] = {}
        for key, val in stats.items():
            if key.startswith("decision:"):
                decisions[key.split(":", 1)[1]] = int(val or 0)

        # open_count 는 큐 전체에서 미할당 — 본 사용자 관점에서는 0 또는 의미상 인용
        return {
            "user_id": user_id,
            "open_count": 0,
            "in_review_count": int(in_review_count),
            "resolved_today": resolved_today,
            "avg_resolution_time_min": avg_min,
            "decisions_breakdown": decisions,
        }

    async def get_disagreement_stats(self) -> EvaluatorDisagreementResponse:
        """evaluator 별 override 비율 — 통계 페이지용."""
        results: list[EvaluatorDisagreementStat] = []
        # 모든 disagreement 카운터 + resolved 총수 합산
        async for key in self._u.scan_iter(match="ax:review_disagreement:*"):
            k = key.decode("utf-8") if isinstance(key, bytes) else key
            evaluator = k.rsplit(":", 1)[-1]
            stats_raw = await self._u.hgetall(k) or {}
            override_count = 0
            total_resolved = 0
            for kk, vv in stats_raw.items():
                k2 = kk.decode("utf-8") if isinstance(kk, bytes) else str(kk)
                v2 = int(vv.decode("utf-8") if isinstance(vv, bytes) else vv or 0)
                total_resolved += v2
                if k2 == "override":
                    override_count = v2
            rate = (override_count / total_resolved) if total_resolved > 0 else 0.0
            results.append(
                EvaluatorDisagreementStat(
                    evaluator=evaluator,
                    total_resolved=total_resolved,
                    override_count=override_count,
                    override_rate=round(rate, 4),
                )
            )
        results.sort(key=lambda s: s.override_rate, reverse=True)
        return EvaluatorDisagreementResponse(items=results)

    # ------------------------------------------------------------------ #
    # 헬퍼
    # ------------------------------------------------------------------ #
    @staticmethod
    def _new_id() -> str:
        return f"review_{uuid.uuid4().hex[:12]}"

    def _now(self) -> datetime:
        result = self._clock()
        if isinstance(result, datetime):
            return result if result.tzinfo else result.replace(tzinfo=UTC)
        return datetime.now(UTC)


__all__ = [
    "CLAIM_AUTO_UNASSIGN_SEC",
    "InvalidStatusTransitionError",
    "ReviewClaimConflictError",
    "ReviewETagMismatchError",
    "ReviewItemNotFoundError",
    "ReviewQueueService",
]
