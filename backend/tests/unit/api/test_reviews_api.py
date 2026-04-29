"""Reviews API 라우터 단위 테스트."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.reviews import get_review_queue, router
from app.core.errors import register_exception_handlers
from app.core.security import get_current_user
from app.models.auth import User
from app.models.review import ReviewItem, ReviewItemCreate, ReviewItemResolve
from app.services.review_queue import ReviewQueueService
from tests.fixtures.mock_redis import MockRedisClient

T0 = datetime(2026, 4, 29, 9, 0, tzinfo=UTC)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.current = now

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: int) -> None:
        self.current += timedelta(**kwargs)


def arun(awaitable: object) -> object:
    return asyncio.run(awaitable)  # type: ignore[arg-type]


def make_manual_payload(
    *,
    subject_id: str = "trace-1",
    project_id: str = "proj-1",
    severity: str = "medium",
    subject_type: str = "trace",
    reason: str = "manual_addition",
    automatic_scores: dict[str, float | None] | None = None,
) -> ReviewItemCreate:
    return ReviewItemCreate(
        subject_type=subject_type,
        subject_id=subject_id,
        project_id=project_id,
        severity=severity,  # type: ignore[arg-type]
        reason=reason,
        automatic_scores=automatic_scores or {},
    )


def create_manual(
    queue: ReviewQueueService,
    *,
    subject_id: str = "trace-1",
    project_id: str = "proj-1",
    severity: str = "medium",
    subject_type: str = "trace",
    reason: str = "manual_addition",
    automatic_scores: dict[str, float | None] | None = None,
) -> ReviewItem:
    return arun(
        queue.create_manual(
            make_manual_payload(
                subject_id=subject_id,
                project_id=project_id,
                severity=severity,
                subject_type=subject_type,
                reason=reason,
                automatic_scores=automatic_scores,
            )
        )
    )


def claim(queue: ReviewQueueService, item_id: str, user_id: str) -> ReviewItem:
    return arun(queue.claim(item_id, user_id))


def resolve(
    queue: ReviewQueueService,
    item_id: str,
    user_id: str,
    *,
    decision: str,
    reviewer_score: float | None = None,
    reviewer_comment: str | None = None,
    expected_output: object | None = None,
    if_match: str | None = None,
) -> ReviewItem:
    return arun(
        queue.resolve(
            item_id,
            user_id,
            ReviewItemResolve(
                decision=decision,  # type: ignore[arg-type]
                reviewer_score=reviewer_score,
                reviewer_comment=reviewer_comment,
                expected_output=expected_output,  # type: ignore[arg-type]
            ),
            if_match=if_match,
        )
    )


@pytest.fixture
def clock() -> MutableClock:
    return MutableClock(T0)


@pytest.fixture
def review_queue(clock: MutableClock) -> ReviewQueueService:
    redis = MockRedisClient(decode_responses=True)
    service = ReviewQueueService(redis, clock=clock)
    yield service
    arun(redis.aclose())


@pytest.fixture
def admin_user() -> User:
    return User(id="user-admin", email="admin@test.local", role="admin")


@pytest.fixture
def reviewer_user() -> User:
    return User(id="user-reviewer", email="reviewer@test.local", role="reviewer")


@pytest.fixture
def reviewer_user_2() -> User:
    return User(id="user-reviewer-2", email="reviewer2@test.local", role="reviewer")


@pytest.fixture
def user_user() -> User:
    return User(id="user-plain", email="user@test.local", role="user")


@pytest.fixture
def viewer_user() -> User:
    return User(id="user-viewer", email="viewer@test.local", role="viewer")


@pytest.fixture
def client_factory(review_queue: ReviewQueueService) -> object:
    clients: list[TestClient] = []

    def _make(
        user: User | None,
        *,
        langfuse: object | None = None,
        trace_fetcher: object | None = None,
    ) -> TestClient:
        app = FastAPI()
        register_exception_handlers(app)
        app.include_router(router, prefix="/api/v1")
        app.state.langfuse = langfuse if langfuse is not None else MagicMock()
        app.state.trace_fetcher = trace_fetcher
        app.dependency_overrides[get_review_queue] = lambda: review_queue
        if user is not None:
            app.dependency_overrides[get_current_user] = lambda: user
        client = TestClient(app)
        clients.append(client)
        return client

    yield _make

    for client in clients:
        client.close()


def get_etag(resp: object) -> str:
    value = resp.headers.get("ETag") or resp.headers.get("etag")  # type: ignore[union-attr]
    assert value is not None
    return value


@pytest.mark.unit
class TestListItems:
    def test_lists_items_with_pagination(self, client_factory: object, review_queue: ReviewQueueService) -> None:
        create_manual(review_queue, subject_id="trace-a")
        create_manual(review_queue, subject_id="trace-b")
        client = client_factory(User(id="viewer-1", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items?page=1&page_size=20")

        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        assert body["page"] == 1
        assert body["page_size"] == 20
        assert len(body["items"]) == 2

    def test_filters_by_status_open(self, client_factory: object, review_queue: ReviewQueueService) -> None:
        open_item = create_manual(review_queue, subject_id="trace-open")
        claimed = create_manual(review_queue, subject_id="trace-claimed")
        claim(review_queue, claimed.id, "user-reviewer")
        client = client_factory(User(id="user-viewer", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items?status=open")

        assert resp.status_code == 200
        assert [item["subject_id"] for item in resp.json()["items"]] == [open_item.subject_id]

    def test_filters_by_type_user_report(self, client_factory: object, review_queue: ReviewQueueService) -> None:
        create_manual(review_queue, subject_id="trace-manual")
        arun(
            review_queue.create_user_report(
                trace_id="trace-report",
                project_id="proj-1",
                reporter_user_id="user-1",
                reason_text="unsafe answer",
            )
        )
        client = client_factory(User(id="user-viewer", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items?type=user_report")

        assert resp.status_code == 200
        assert resp.json()["items"][0]["type"] == "user_report"

    def test_filters_by_severity_high(self, client_factory: object, review_queue: ReviewQueueService) -> None:
        create_manual(review_queue, subject_id="trace-low", severity="low")
        create_manual(review_queue, subject_id="trace-high", severity="high")
        client = client_factory(User(id="user-viewer", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items?severity=high")

        assert resp.status_code == 200
        assert [item["subject_id"] for item in resp.json()["items"]] == ["trace-high"]

    def test_filters_by_assigned_to(self, client_factory: object, review_queue: ReviewQueueService) -> None:
        item_1 = create_manual(review_queue, subject_id="trace-1")
        item_2 = create_manual(review_queue, subject_id="trace-2")
        claim(review_queue, item_1.id, "user-reviewer")
        claim(review_queue, item_2.id, "user-reviewer-2")
        client = client_factory(User(id="user-viewer", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items?assigned_to=user-reviewer")

        assert resp.status_code == 200
        assert [item["subject_id"] for item in resp.json()["items"]] == ["trace-1"]

    def test_requires_authentication(self, client_factory: object) -> None:
        client = client_factory(None)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items")

        assert resp.status_code == 401

    def test_viewer_role_can_list_items(self, client_factory: object) -> None:
        client = client_factory(User(id="user-viewer", role="viewer"))  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items")

        assert resp.status_code == 200


@pytest.mark.unit
class TestGetItem:
    def test_gets_single_item_and_returns_etag(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        viewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-detail")
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get(f"/api/v1/reviews/items/{item.id}")

        assert resp.status_code == 200
        assert resp.json()["id"] == item.id
        assert get_etag(resp) == f'"{review_queue.compute_etag(item)}"'

    def test_get_missing_item_returns_404(self, client_factory: object, viewer_user: User) -> None:
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/items/review_missing")

        assert resp.status_code == 404

    def test_viewer_role_can_get_single_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        viewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-viewer")
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get(f"/api/v1/reviews/items/{item.id}")

        assert resp.status_code == 200


@pytest.mark.unit
class TestCreateItem:
    def test_user_can_create_manual_item_and_type_is_forced(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/items",
            json={
                "subject_type": "trace",
                "subject_id": "trace-manual",
                "project_id": "proj-1",
                "severity": "high",
                "reason": "manual_addition",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["type"] == "manual_addition"
        assert get_etag(resp).startswith('"open:')

    def test_viewer_cannot_create_manual_item(
        self,
        client_factory: object,
        viewer_user: User,
    ) -> None:
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/items",
            json={"subject_type": "trace", "project_id": "proj-1", "reason": "manual_addition"},
        )

        assert resp.status_code == 403

    def test_missing_subject_id_returns_422(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/items",
            json={"subject_type": "trace", "project_id": "proj-1", "reason": "manual_addition"},
        )

        assert resp.status_code == 422


@pytest.mark.unit
class TestClaimItem:
    def test_reviewer_can_claim_open_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-claim")
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/claim")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "in_review"
        assert body["assigned_to"] == reviewer_user.id

    def test_user_role_cannot_claim(self, client_factory: object, user_user: User) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.patch("/api/v1/reviews/items/review_x/claim")

        assert resp.status_code == 403

    def test_claim_conflicts_when_other_user_already_claimed(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
        reviewer_user_2: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-conflict")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user_2)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/claim")

        assert resp.status_code == 409

    def test_resolved_item_cannot_be_claimed(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
        reviewer_user_2: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-resolved")
        claim(review_queue, item.id, reviewer_user.id)
        resolve(review_queue, item.id, reviewer_user.id, decision="approve")
        client = client_factory(reviewer_user_2)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/claim")

        assert resp.status_code == 409

    def test_reclaim_by_same_user_is_idempotent(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-reclaim")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/claim")

        assert resp.status_code == 200
        assert resp.json()["assigned_to"] == reviewer_user.id


@pytest.mark.unit
class TestReleaseItem:
    def test_reviewer_can_release_own_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-release")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/release")

        assert resp.status_code == 200
        assert resp.json()["status"] == "open"
        assert resp.json()["assigned_to"] is None

    def test_release_other_users_item_returns_409(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
        reviewer_user_2: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-other-release")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user_2)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/release")

        assert resp.status_code == 409

    def test_admin_can_force_release_other_users_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        admin_user: User,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-admin-release")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(admin_user)  # type: ignore[operator]

        resp = client.patch(f"/api/v1/reviews/items/{item.id}/release")

        assert resp.status_code == 200
        assert resp.json()["status"] == "open"


@pytest.mark.unit
class TestResolveItem:
    def test_approve_from_in_review_returns_200_and_etag(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-approve")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "approve"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "resolved"
        assert get_etag(resp).startswith('"resolved:')

    def test_override_with_reviewer_score_returns_200(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-override")
        claim(review_queue, item.id, reviewer_user.id)
        langfuse = MagicMock()
        client = client_factory(reviewer_user, langfuse=langfuse)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "override", "reviewer_score": 0.9},
        )

        assert resp.status_code == 200
        assert resp.json()["decision"] == "override"
        langfuse.score.assert_called_once()

    def test_override_without_reviewer_score_returns_422(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-override-422")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "override"},
        )

        assert resp.status_code == 422

    def test_dismiss_from_open_is_allowed(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-dismiss-open")
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "dismiss"},
        )

        assert resp.status_code == 200
        assert resp.json()["status"] == "dismissed"

    def test_add_to_dataset_with_expected_output_returns_200(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-dataset")
        claim(review_queue, item.id, reviewer_user.id)
        langfuse = MagicMock()
        trace_fetcher = AsyncMock()
        trace_fetcher.get.return_value = MagicMock(
            name="qa-agent-v3",
            input={"question": "hello"},
            output={"answer": "fallback"},
        )
        client = client_factory(reviewer_user, langfuse=langfuse, trace_fetcher=trace_fetcher)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "add_to_dataset", "expected_output": {"answer": "gold"}},
        )

        assert resp.status_code == 200
        assert resp.json()["expected_output"] == {"answer": "gold"}
        langfuse.create_dataset_item.assert_called_once()

    def test_if_match_exact_value_is_accepted(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-if-match")
        claimed = claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "approve"},
            headers={"If-Match": review_queue.compute_etag(claimed)},
        )

        assert resp.status_code == 200

    def test_if_match_mismatch_returns_412(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-if-match-mismatch")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "approve"},
            headers={"If-Match": '"deadbeef00000000"'},
        )

        assert resp.status_code == 412

    def test_if_match_star_is_accepted(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-if-match-star")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "approve"},
            headers={"If-Match": "*"},
        )

        assert resp.status_code == 200

    def test_if_match_quoted_etag_is_accepted(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-if-match-quoted")
        claim(review_queue, item.id, reviewer_user.id)
        client = client_factory(reviewer_user)  # type: ignore[operator]
        detail_resp = client.get(f"/api/v1/reviews/items/{item.id}")
        etag = get_etag(detail_resp)

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "approve"},
            headers={"If-Match": etag},
        )

        assert resp.status_code == 200

    def test_user_role_cannot_resolve(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        user_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-user-resolve")
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            f"/api/v1/reviews/items/{item.id}/resolve",
            json={"decision": "dismiss"},
        )

        assert resp.status_code == 403


@pytest.mark.unit
class TestDeleteItem:
    def test_admin_can_delete_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        admin_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-delete")
        client = client_factory(admin_user)  # type: ignore[operator]

        resp = client.delete(f"/api/v1/reviews/items/{item.id}")

        assert resp.status_code == 204

    def test_reviewer_cannot_delete_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-delete-reviewer")
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.delete(f"/api/v1/reviews/items/{item.id}")

        assert resp.status_code == 403

    def test_user_cannot_delete_item(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        user_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-delete-user")
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.delete(f"/api/v1/reviews/items/{item.id}")

        assert resp.status_code == 403

    def test_delete_missing_item_returns_404(
        self,
        client_factory: object,
        admin_user: User,
    ) -> None:
        client = client_factory(admin_user)  # type: ignore[operator]

        resp = client.delete("/api/v1/reviews/items/review_missing")

        assert resp.status_code == 404


@pytest.mark.unit
class TestSummaryStats:
    def test_viewer_can_get_summary_counts(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        clock: MutableClock,
        viewer_user: User,
        reviewer_user: User,
    ) -> None:
        create_manual(review_queue, subject_id="trace-open", project_id="proj-1")
        item_in_review = create_manual(review_queue, subject_id="trace-in-review", project_id="proj-1")
        claim(review_queue, item_in_review.id, reviewer_user.id)
        item_resolved = create_manual(review_queue, subject_id="trace-resolved", project_id="proj-1")
        claim(review_queue, item_resolved.id, reviewer_user.id)
        clock.advance(minutes=5)
        resolve(review_queue, item_resolved.id, reviewer_user.id, decision="approve")
        item_dismissed = create_manual(review_queue, subject_id="trace-dismissed", project_id="proj-1")
        resolve(review_queue, item_dismissed.id, reviewer_user.id, decision="dismiss")
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/summary")

        assert resp.status_code == 200
        assert resp.json()["open"] == 1
        assert resp.json()["in_review"] == 1
        assert resp.json()["resolved_today"] == 1
        assert resp.json()["dismissed_today"] == 1

    def test_summary_supports_project_filter(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        viewer_user: User,
    ) -> None:
        create_manual(review_queue, subject_id="trace-proj-1", project_id="proj-1")
        create_manual(review_queue, subject_id="trace-proj-2", project_id="proj-2")
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/summary?project_id=proj-2")

        assert resp.status_code == 200
        assert resp.json()["open"] == 1


@pytest.mark.unit
class TestReviewerStats:
    def test_reviewer_can_get_own_stats(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        clock: MutableClock,
        reviewer_user: User,
    ) -> None:
        item = create_manual(review_queue, subject_id="trace-own-stats")
        claim(review_queue, item.id, reviewer_user.id)
        clock.advance(minutes=3)
        resolve(review_queue, item.id, reviewer_user.id, decision="approve")
        client = client_factory(reviewer_user)  # type: ignore[operator]

        resp = client.get(f"/api/v1/reviews/stats/reviewer/{reviewer_user.id}")

        assert resp.status_code == 200
        assert resp.json()["user_id"] == reviewer_user.id
        assert resp.json()["resolved_today"] == 1

    def test_non_reviewer_cannot_get_other_users_stats(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/reviewer/user-reviewer")

        assert resp.status_code == 403

    def test_reviewer_can_get_other_users_stats(
        self,
        client_factory: object,
        reviewer_user_2: User,
    ) -> None:
        client = client_factory(reviewer_user_2)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/reviewer/user-reviewer")

        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user-reviewer"

    def test_admin_can_get_other_users_stats(
        self,
        client_factory: object,
        admin_user: User,
    ) -> None:
        client = client_factory(admin_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/reviewer/user-reviewer")

        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user-reviewer"


@pytest.mark.unit
class TestDisagreementStats:
    def test_viewer_can_get_disagreement_stats(
        self,
        client_factory: object,
        review_queue: ReviewQueueService,
        reviewer_user: User,
        viewer_user: User,
    ) -> None:
        item = create_manual(
            review_queue,
            subject_id="trace-disagreement",
            automatic_scores={"evaluator_a": 0.1, "evaluator_b": 0.9, "weighted_score": 0.5},
        )
        claim(review_queue, item.id, reviewer_user.id)
        resolve(review_queue, item.id, reviewer_user.id, decision="override", reviewer_score=0.2)
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.get("/api/v1/reviews/stats/disagreement")

        assert resp.status_code == 200
        assert resp.json()["items"]
        assert resp.json()["items"][0]["evaluator"] in {"evaluator_a", "evaluator_b"}


@pytest.mark.unit
class TestReportTrace:
    def test_user_can_create_report_item(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/report",
            json={
                "trace_id": "trace-report",
                "project_id": "proj-1",
                "reason": "unsafe output",
                "severity": "high",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["type"] == "user_report"
        assert get_etag(resp).startswith('"open:')

    def test_report_allows_experiment_item_subject_type(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/report",
            json={
                "trace_id": "exp-item-1",
                "project_id": "proj-1",
                "reason": "bad compare row",
                "severity": "medium",
                "subject_type": "experiment_item",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["subject_type"] == "experiment_item"

    def test_report_invalid_subject_type_returns_422(
        self,
        client_factory: object,
        user_user: User,
    ) -> None:
        client = client_factory(user_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/report",
            json={
                "trace_id": "trace-invalid",
                "project_id": "proj-1",
                "reason": "bad output",
                "severity": "medium",
                "subject_type": "invalid",
            },
        )

        assert resp.status_code == 422

    def test_viewer_cannot_report(self, client_factory: object, viewer_user: User) -> None:
        client = client_factory(viewer_user)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/report",
            json={"trace_id": "trace-x", "project_id": "proj-1", "reason": "bad output"},
        )

        assert resp.status_code == 403

    def test_report_requires_authentication(self, client_factory: object) -> None:
        client = client_factory(None)  # type: ignore[operator]

        resp = client.post(
            "/api/v1/reviews/report",
            json={"trace_id": "trace-x", "project_id": "proj-1", "reason": "bad output"},
        )

        assert resp.status_code == 401
