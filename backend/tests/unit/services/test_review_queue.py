"""ReviewQueueService 단위 테스트 (Phase 8-C)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.models.review import ReviewItem, ReviewItemCreate, ReviewItemResolve
from app.services.review_queue import (
    CLAIM_AUTO_UNASSIGN_SEC,
    InvalidStatusTransitionError,
    ReviewClaimConflictError,
    ReviewETagMismatchError,
    ReviewItemNotFoundError,
    ReviewQueueService,
    _queue_score,
)
from tests.fixtures.mock_redis import MockRedisClient


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def __call__(self) -> datetime:
        return self.current

    def set(self, now: datetime) -> None:
        self.current = now

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


def make_manual_payload(
    *,
    subject_id: str = "trace-1",
    project_id: str = "proj-1",
    severity: str = "medium",
    subject_type: str = "trace",
    reason: str = "manual_addition",
) -> ReviewItemCreate:
    return ReviewItemCreate(
        subject_type=subject_type,
        subject_id=subject_id,
        project_id=project_id,
        severity=severity,
        reason=reason,
    )


async def make_open_item(
    service: ReviewQueueService,
    *,
    subject_id: str = "trace-1",
    project_id: str = "proj-1",
    severity: str = "medium",
    subject_type: str = "trace",
    reason: str = "manual_addition",
) -> ReviewItem:
    return await service.create_manual(
        make_manual_payload(
            subject_id=subject_id,
            project_id=project_id,
            severity=severity,
            subject_type=subject_type,
            reason=reason,
        )
    )


async def overwrite_item(
    service: ReviewQueueService,
    redis: MockRedisClient,
    item: ReviewItem,
) -> None:
    await redis.set(service._item_key(item.id), service._to_payload(item), ex=service.ITEM_TTL_SEC)


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(datetime(2026, 4, 29, 9, 0, tzinfo=UTC))


@pytest.fixture
async def mock_redis(redis_client: MockRedisClient) -> MockRedisClient:
    return redis_client


@pytest.fixture
async def service(mock_redis: MockRedisClient, clock: MutableClock) -> ReviewQueueService:
    return ReviewQueueService(mock_redis, clock=clock)


@pytest.mark.unit
class TestEnqueue:
    async def test_enqueue_weighted_score_below_half_returns_true(
        self, service: ReviewQueueService
    ) -> None:
        enqueued = await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"weighted_score": 0.49, "llm_judge": 0.6},
        )
        items, total = await service.list_items(status="open")
        assert enqueued is True
        assert total == 1
        assert items[0].reason == "auto_eval_low_score"
        assert items[0].severity == "medium"

    async def test_enqueue_weighted_score_below_point_three_sets_high_severity(
        self, service: ReviewQueueService
    ) -> None:
        await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"weighted_score": 0.29, "llm_judge": 0.2},
        )
        items, _ = await service.list_items(status="open")
        assert items[0].severity == "high"

    async def test_enqueue_weighted_score_between_point_three_and_half_sets_medium_severity(
        self, service: ReviewQueueService
    ) -> None:
        await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"weighted_score": 0.31, "llm_judge": 0.3},
        )
        items, _ = await service.list_items(status="open")
        assert items[0].severity == "medium"

    async def test_enqueue_evaluator_disagreement_branch_creates_high_priority_item(
        self, service: ReviewQueueService
    ) -> None:
        enqueued = await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"a": -1.0, "b": 1.0, "weighted_score": 0.9},
        )
        items, total = await service.list_items(status="open")
        assert enqueued is True
        assert total == 1
        assert items[0].reason == "evaluator_disagreement"
        assert items[0].severity == "high"
        assert items[0].reason_detail["variance"] == pytest.approx(1.0)

    async def test_enqueue_returns_false_when_no_trigger_matches(
        self, service: ReviewQueueService
    ) -> None:
        enqueued = await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"exact_match": 1.0, "llm_judge": 0.0, "weighted_score": 0.5},
        )
        items, total = await service.list_items()
        assert enqueued is False
        assert total == 0
        assert items == []

    async def test_enqueue_returns_false_when_weighted_and_variance_are_none(
        self, service: ReviewQueueService
    ) -> None:
        enqueued = await service.enqueue(
            SimpleNamespace(id="policy-1", project_id="proj-1"),
            SimpleNamespace(id="run-1"),
            SimpleNamespace(id="trace-1"),
            {"weighted_score": None, "llm_judge": None},
        )
        assert enqueued is False

    def test_compute_variance_returns_none_for_single_value(self) -> None:
        assert ReviewQueueService._compute_variance({"a": 1.0}) is None

    def test_compute_variance_returns_exact_value(self) -> None:
        variance = ReviewQueueService._compute_variance({"a": 0.0, "b": 1.0})
        assert variance == pytest.approx(0.25)

    def test_compute_variance_excludes_weighted_score(self) -> None:
        variance = ReviewQueueService._compute_variance(
            {"a": 0.0, "b": 1.0, "weighted_score": 0.99}
        )
        assert variance == pytest.approx(0.25)


@pytest.mark.unit
class TestCreateEntryPoints:
    async def test_create_auto_creates_review_item_with_generated_id(
        self, service: ReviewQueueService
    ) -> None:
        item = await service.create_auto(
            project_id="proj-1",
            trace_id="trace-1",
            type_="auto_eval_flagged",
            severity="high",
            reason="auto_eval_low_score",
            reason_detail={"weighted_score": 0.2},
            automatic_scores={"weighted_score": 0.2},
            auto_eval_policy_id="policy-1",
            auto_eval_run_id="run-1",
        )
        assert re.fullmatch(r"review_[0-9a-f]{12}", item.id)
        assert item.subject_type == "trace"
        assert item.auto_eval_policy_id == "policy-1"

    async def test_create_manual_forces_manual_addition_type(
        self, service: ReviewQueueService
    ) -> None:
        payload = make_manual_payload(reason="manual_addition")
        item = await service.create_manual(payload)
        assert item.type == "manual_addition"
        assert item.reason == "manual_addition"

    async def test_create_user_report_sets_type_reason_and_detail(
        self, service: ReviewQueueService
    ) -> None:
        item = await service.create_user_report(
            trace_id="trace-1",
            project_id="proj-1",
            reporter_user_id="user-1",
            reason_text="answer is unsafe",
            severity="high",
        )
        assert item.type == "user_report"
        assert item.reason == "user_report"
        assert item.reason_detail == {
            "reporter_user_id": "user-1",
            "reason_text": "answer is unsafe",
        }

    async def test_create_user_report_allows_experiment_item_subject_type(
        self, service: ReviewQueueService
    ) -> None:
        item = await service.create_user_report(
            trace_id="exp-item-1",
            project_id="proj-1",
            reporter_user_id="user-1",
            reason_text="compare row issue",
            subject_type="experiment_item",
        )
        assert item.subject_type == "experiment_item"

    async def test_create_user_report_invalid_subject_type_falls_back_to_trace(
        self, service: ReviewQueueService
    ) -> None:
        item = await service.create_user_report(
            trace_id="trace-1",
            project_id="proj-1",
            reporter_user_id="user-1",
            reason_text="bad output",
            subject_type="invalid",
        )
        assert item.subject_type == "trace"


@pytest.mark.unit
class TestPersistenceAndIndexes:
    async def test_persist_new_stores_json_and_ttl(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await service.create_auto(
            project_id="proj-1",
            trace_id="trace-1",
            type_="auto_eval_flagged",
            severity="medium",
            reason="auto_eval_low_score",
            reason_detail={"weighted_score": 0.4},
            automatic_scores={"weighted_score": 0.4},
            auto_eval_policy_id="policy-1",
            auto_eval_run_id="run-1",
        )
        raw = await mock_redis.get(service._item_key(item.id))
        ttl = await mock_redis.ttl(service._item_key(item.id))
        assert raw is not None
        assert '"id"' in str(raw)
        assert ttl in {service.ITEM_TTL_SEC, service.ITEM_TTL_SEC - 1}

    async def test_persist_new_registers_open_queue_score(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await service.create_auto(
            project_id="proj-1",
            trace_id="trace-1",
            type_="auto_eval_flagged",
            severity="high",
            reason="auto_eval_low_score",
            reason_detail={"weighted_score": 0.2},
            automatic_scores={"weighted_score": 0.2},
            auto_eval_policy_id=None,
            auto_eval_run_id=None,
        )
        score = await mock_redis._client.zscore(service.KEY_OPEN, item.id)
        assert score == pytest.approx(_queue_score(item.severity, item.created_at))

    async def test_persist_new_registers_by_policy_index_when_policy_id_present(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await service.create_auto(
            project_id="proj-1",
            trace_id="trace-1",
            type_="auto_eval_flagged",
            severity="medium",
            reason="auto_eval_low_score",
            reason_detail={"weighted_score": 0.4},
            automatic_scores={"weighted_score": 0.4},
            auto_eval_policy_id="policy-1",
            auto_eval_run_id="run-1",
        )
        ids = await mock_redis._client.zrange(service._by_policy_key("policy-1"), 0, -1)
        assert item.id in ids

    async def test_persist_new_registers_by_subject_index(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await service.create_user_report(
            trace_id="trace-9",
            project_id="proj-1",
            reporter_user_id="user-1",
            reason_text="bad output",
        )
        ids = await mock_redis.smembers(service._by_subject_key("trace", "trace-9"))
        assert item.id in ids

    def test_queue_score_orders_high_before_medium_before_low(self) -> None:
        created_at = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)
        high = _queue_score("high", created_at)
        medium = _queue_score("medium", created_at)
        low = _queue_score("low", created_at)
        assert high < medium < low

    def test_queue_score_older_item_wins_when_same_severity(self) -> None:
        older = datetime(2026, 4, 29, 8, 0, tzinfo=UTC)
        newer = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)
        assert _queue_score("medium", older) < _queue_score("medium", newer)


@pytest.mark.unit
class TestTransitions:
    @pytest.mark.parametrize(
        ("current", "target"),
        [
            ("open", "in_review"),
            ("open", "dismissed"),
            ("in_review", "open"),
            ("in_review", "resolved"),
            ("in_review", "dismissed"),
        ],
    )
    def test_allowed_transitions(
        self, current: str, target: str
    ) -> None:
        ReviewQueueService._check_transition(current, target)

    @pytest.mark.parametrize(
        ("current", "target"),
        [
            ("open", "resolved"),
            ("resolved", "open"),
            ("resolved", "in_review"),
            ("resolved", "dismissed"),
            ("dismissed", "open"),
            ("dismissed", "in_review"),
            ("dismissed", "resolved"),
        ],
    )
    def test_disallowed_transitions_raise(
        self, current: str, target: str
    ) -> None:
        with pytest.raises(InvalidStatusTransitionError):
            ReviewQueueService._check_transition(current, target)


@pytest.mark.unit
class TestClaim:
    async def test_claim_moves_open_to_in_review_and_records_assignment(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await make_open_item(service)
        claimed = await service.claim(item.id, "reviewer-1")
        open_ids = await mock_redis._client.zrange(service.KEY_OPEN, 0, -1)
        in_review_ids = await mock_redis.smembers(service._in_review_key("reviewer-1"))
        assert claimed.status == "in_review"
        assert claimed.assigned_to == "reviewer-1"
        assert claimed.assigned_at is not None
        assert item.id not in open_ids
        assert item.id in in_review_ids

    async def test_claim_is_idempotent_for_same_user(self, service: ReviewQueueService) -> None:
        item = await make_open_item(service)
        first = await service.claim(item.id, "reviewer-1")
        second = await service.claim(item.id, "reviewer-1")
        assert second.id == first.id
        assert second.assigned_to == "reviewer-1"

    async def test_claim_by_different_user_raises_conflict(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        with pytest.raises(ReviewClaimConflictError):
            await service.claim(item.id, "reviewer-2")

    async def test_claim_resolved_item_raises_transition_error(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="approve"))
        with pytest.raises(InvalidStatusTransitionError):
            await service.claim(item.id, "reviewer-2")

    async def test_claim_dismissed_item_raises_transition_error(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="dismiss"))
        with pytest.raises(InvalidStatusTransitionError):
            await service.claim(item.id, "reviewer-2")


@pytest.mark.unit
class TestRelease:
    async def test_release_returns_item_to_open_for_owner(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        released = await service.release(item.id, "reviewer-1")
        assert released.status == "open"
        assert released.assigned_to is None
        assert released.assigned_at is None

    async def test_release_by_different_user_without_force_raises_conflict(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        with pytest.raises(ReviewClaimConflictError):
            await service.release(item.id, "reviewer-2")

    async def test_release_force_true_allows_admin_override(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        released = await service.release(item.id, "admin-1", force=True)
        assert released.status == "open"
        assert released.assigned_to is None

    async def test_release_open_item_raises_transition_error(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        with pytest.raises(InvalidStatusTransitionError):
            await service.release(item.id, "reviewer-1")


@pytest.mark.unit
class TestResolve:
    async def test_resolve_approve_marks_item_resolved(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        resolved = await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="approve"))
        assert resolved.status == "resolved"
        assert resolved.decision == "approve"
        assert resolved.resolved_by == "reviewer-1"
        assert resolved.resolved_at is not None

    async def test_resolve_override_persists_reviewer_score(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        resolved = await service.resolve(
            item.id,
            "reviewer-1",
            ReviewItemResolve(decision="override", reviewer_score=0.95),
        )
        assert resolved.status == "resolved"
        assert resolved.reviewer_score == 0.95

    async def test_resolve_dismiss_from_in_review_marks_item_dismissed(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        dismissed = await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="dismiss"))
        assert dismissed.status == "dismissed"
        assert dismissed.decision == "dismiss"

    async def test_resolve_dismiss_from_open_is_allowed(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        dismissed = await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="dismiss"))
        assert dismissed.status == "dismissed"
        assert dismissed.assigned_to is None

    async def test_resolve_add_to_dataset_persists_expected_output(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        resolved = await service.resolve(
            item.id,
            "reviewer-1",
            ReviewItemResolve(
                decision="add_to_dataset",
                expected_output={"final_answer": "42"},
            ),
        )
        assert resolved.status == "resolved"
        assert resolved.expected_output == {"final_answer": "42"}

    async def test_resolve_if_match_exact_etag_passes(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        current = await service.get_item(item.id)
        resolved = await service.resolve(
            item.id,
            "reviewer-1",
            ReviewItemResolve(decision="approve"),
            if_match=service.compute_etag(current),
        )
        assert resolved.status == "resolved"

    async def test_resolve_if_match_mismatch_raises(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        with pytest.raises(ReviewETagMismatchError):
            await service.resolve(
                item.id,
                "reviewer-1",
                ReviewItemResolve(decision="approve"),
                if_match="open:0",
            )

    async def test_resolve_if_match_star_passes(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        resolved = await service.resolve(
            item.id,
            "reviewer-1",
            ReviewItemResolve(decision="approve"),
            if_match="*",
        )
        assert resolved.status == "resolved"

    async def test_resolve_if_match_with_quotes_passes(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        current = await service.get_item(item.id)
        resolved = await service.resolve(
            item.id,
            "reviewer-1",
            ReviewItemResolve(decision="approve"),
            if_match=f'"{service.compute_etag(current)}"',
        )
        assert resolved.status == "resolved"

    async def test_resolve_without_if_match_skips_etag_check(
        self, service: ReviewQueueService
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        resolved = await service.resolve(item.id, "reviewer-1", ReviewItemResolve(decision="approve"))
        assert resolved.status == "resolved"


@pytest.mark.unit
class TestExpireStaleClaims:
    async def test_expire_stale_claims_releases_old_assignments(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        clock.advance(seconds=CLAIM_AUTO_UNASSIGN_SEC)
        recovered = await service.expire_stale_claims()
        refreshed = await service.get_item(item.id)
        assert recovered == 1
        assert refreshed.status == "open"
        assert refreshed.assigned_to is None

    async def test_expire_stale_claims_keeps_recent_assignments(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        clock.advance(minutes=59)
        recovered = await service.expire_stale_claims()
        refreshed = await service.get_item(item.id)
        assert recovered == 0
        assert refreshed.status == "in_review"

    async def test_expire_stale_claims_skips_items_without_assigned_at(
        self,
        service: ReviewQueueService,
        mock_redis: MockRedisClient,
        clock: MutableClock,
    ) -> None:
        item = await make_open_item(service)
        await service.claim(item.id, "reviewer-1")
        claimed = await service.get_item(item.id)
        claimed.assigned_at = None
        await overwrite_item(service, mock_redis, claimed)
        clock.advance(hours=2)
        recovered = await service.expire_stale_claims()
        refreshed = await service.get_item(item.id)
        assert recovered == 0
        assert refreshed.status == "in_review"
        assert refreshed.assigned_at is None


@pytest.mark.unit
class TestSummaryAndStats:
    async def test_get_summary_counts_open_in_review_and_today_resolutions(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        await make_open_item(service, subject_id="open-a", project_id="proj-A")
        in_review = await make_open_item(service, subject_id="review-a", project_id="proj-A")
        await service.claim(in_review.id, "reviewer-1")

        resolved = await make_open_item(service, subject_id="resolved-a", project_id="proj-A")
        await service.claim(resolved.id, "reviewer-1")
        clock.advance(minutes=30)
        await service.resolve(resolved.id, "reviewer-1", ReviewItemResolve(decision="approve"))

        dismissed = await make_open_item(service, subject_id="dismissed-b", project_id="proj-B")
        clock.advance(minutes=15)
        await service.resolve(dismissed.id, "reviewer-2", ReviewItemResolve(decision="dismiss"))

        summary = await service.get_summary()
        assert summary.open == 1
        assert summary.in_review == 1
        assert summary.resolved_today == 1
        assert summary.dismissed_today == 1
        assert summary.avg_resolution_time_min == pytest.approx(22.5)

    async def test_get_summary_filters_by_project(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        await make_open_item(service, subject_id="open-a", project_id="proj-A")
        await make_open_item(service, subject_id="open-b", project_id="proj-B")

        resolved = await make_open_item(service, subject_id="resolved-a", project_id="proj-A")
        await service.claim(resolved.id, "reviewer-1")
        clock.advance(minutes=10)
        await service.resolve(resolved.id, "reviewer-1", ReviewItemResolve(decision="approve"))

        dismissed = await make_open_item(service, subject_id="dismissed-b", project_id="proj-B")
        clock.advance(minutes=5)
        await service.resolve(dismissed.id, "reviewer-2", ReviewItemResolve(decision="dismiss"))

        summary = await service.get_summary(project_id="proj-A")
        assert summary.open == 1
        assert summary.in_review == 0
        assert summary.resolved_today == 1
        assert summary.dismissed_today == 0
        assert summary.avg_resolution_time_min == pytest.approx(10.0)

    async def test_get_summary_empty_queue_returns_zeroes(
        self, service: ReviewQueueService
    ) -> None:
        summary = await service.get_summary()
        assert summary.open == 0
        assert summary.in_review == 0
        assert summary.resolved_today == 0
        assert summary.dismissed_today == 0
        assert summary.avg_resolution_time_min is None

    async def test_get_reviewer_stats_includes_in_review_and_daily_breakdown(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        claimed = await make_open_item(service, subject_id="claimed-1")
        await service.claim(claimed.id, "reviewer-1")

        first = await make_open_item(service, subject_id="resolved-1")
        await service.claim(first.id, "reviewer-1")
        clock.advance(minutes=10)
        await service.resolve(first.id, "reviewer-1", ReviewItemResolve(decision="approve"))

        second = await make_open_item(service, subject_id="resolved-2")
        await service.claim(second.id, "reviewer-1")
        clock.advance(minutes=20)
        await service.resolve(
            second.id,
            "reviewer-1",
            ReviewItemResolve(decision="override", reviewer_score=0.9),
        )

        stats = await service.get_reviewer_stats("reviewer-1")
        assert stats["in_review_count"] == 1
        assert stats["resolved_today"] == 2
        assert stats["decisions_breakdown"] == {"approve": 1, "override": 1}
        assert stats["avg_resolution_time_min"] == pytest.approx(15.0)

    async def test_get_disagreement_stats_computes_override_rate_exactly(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        key = service._disagreement_key("llm_judge")
        await mock_redis.hincrby(key, "override", 3)
        await mock_redis.hincrby(key, "approve", 1)
        stats = await service.get_disagreement_stats()
        assert len(stats.items) == 1
        assert stats.items[0].override_count == 3
        assert stats.items[0].total_resolved == 4
        assert stats.items[0].override_rate == pytest.approx(0.75)

    async def test_get_disagreement_stats_sorts_by_override_rate_desc(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        await mock_redis.hincrby(service._disagreement_key("eval-high"), "override", 3)
        await mock_redis.hincrby(service._disagreement_key("eval-high"), "approve", 1)
        await mock_redis.hincrby(service._disagreement_key("eval-low"), "override", 1)
        await mock_redis.hincrby(service._disagreement_key("eval-low"), "approve", 4)
        stats = await service.get_disagreement_stats()
        assert [item.evaluator for item in stats.items] == ["eval-high", "eval-low"]


@pytest.mark.unit
class TestEdgeCases:
    def test_compute_etag_uses_status_and_updated_at_millis(self) -> None:
        item = ReviewItem(
            id="review_123456abcdef",
            type="manual_addition",
            severity="medium",
            subject_type="trace",
            subject_id="trace-1",
            project_id="proj-1",
            reason="manual_addition",
            created_at=datetime(2026, 4, 29, 9, 0, tzinfo=UTC),
            updated_at=datetime(2026, 4, 29, 9, 1, 2, 345000, tzinfo=UTC),
        )
        assert ReviewQueueService.compute_etag(item) == "open:1777453262345"

    def test_compute_etag_handles_naive_datetime(self) -> None:
        item = ReviewItem(
            id="review_123456abcdef",
            type="manual_addition",
            severity="medium",
            subject_type="trace",
            subject_id="trace-1",
            project_id="proj-1",
            reason="manual_addition",
            created_at=datetime(2026, 4, 29, 9, 0),
            updated_at=datetime(2026, 4, 29, 9, 1, 2, 345000),
        )
        assert ReviewQueueService.compute_etag(item) == "open:1777453262345"

    async def test_delete_removes_all_indexes(
        self, service: ReviewQueueService, mock_redis: MockRedisClient
    ) -> None:
        item = await service.create_auto(
            project_id="proj-1",
            trace_id="trace-del",
            type_="auto_eval_flagged",
            severity="high",
            reason="auto_eval_low_score",
            reason_detail={"weighted_score": 0.2},
            automatic_scores={"weighted_score": 0.2},
            auto_eval_policy_id="policy-del",
            auto_eval_run_id="run-del",
        )
        await service.claim(item.id, "reviewer-1")
        await service.delete(item.id)
        assert await mock_redis.get(service._item_key(item.id)) is None
        assert item.id not in await mock_redis._client.zrange(service.KEY_OPEN, 0, -1)
        assert item.id not in await mock_redis.smembers(service._in_review_key("reviewer-1"))
        assert item.id not in await mock_redis._client.zrange(
            service._by_policy_key("policy-del"), 0, -1
        )
        assert item.id not in await mock_redis.smembers(
            service._by_subject_key("trace", "trace-del")
        )

    async def test_list_items_open_uses_priority_order(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        high = await make_open_item(service, subject_id="high", severity="high")
        clock.advance(minutes=1)
        medium_older = await make_open_item(service, subject_id="medium-old", severity="medium")
        clock.advance(minutes=1)
        low = await make_open_item(service, subject_id="low", severity="low")
        clock.advance(minutes=1)
        medium_newer = await make_open_item(service, subject_id="medium-new", severity="medium")
        items, _ = await service.list_items(status="open")
        assert [item.id for item in items] == [
            high.id,
            medium_older.id,
            medium_newer.id,
            low.id,
        ]

    async def test_list_items_in_review_filters_by_assigned_user(
        self, service: ReviewQueueService
    ) -> None:
        first = await make_open_item(service, subject_id="trace-1")
        second = await make_open_item(service, subject_id="trace-2")
        await service.claim(first.id, "reviewer-1")
        await service.claim(second.id, "reviewer-2")
        items, total = await service.list_items(status="in_review", assigned_to="reviewer-1")
        assert total == 1
        assert items[0].id == first.id

    async def test_list_items_supports_pagination(
        self, service: ReviewQueueService, clock: MutableClock
    ) -> None:
        for idx in range(5):
            await make_open_item(service, subject_id=f"trace-{idx}", severity="low")
            clock.advance(minutes=1)
        page1, total = await service.list_items(status="open", page=1, page_size=2)
        page2, _ = await service.list_items(status="open", page=2, page_size=2)
        page3, _ = await service.list_items(status="open", page=3, page_size=2)
        assert total == 5
        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    async def test_get_item_missing_raises_not_found(
        self, service: ReviewQueueService
    ) -> None:
        with pytest.raises(ReviewItemNotFoundError):
            await service.get_item("review_missing")
