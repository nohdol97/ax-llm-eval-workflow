"""알림 라우터 + 서비스 단위 테스트.

검증:
- ``create_notification`` 멱등 보장 (동일 user/type/resource → 같은 id)
- TTL 30일 적용
- 인덱스 Sorted Set 등록 + 정렬 (최신순)
- ``list_notifications`` 페이지네이션 + ``unread_only`` 필터
- ``mark_read`` read_at 타임스탬프 갱신
- ``mark_all_read`` 전체 처리 카운트
- ``delete_notification`` 삭제 + 인덱스 정리
- 본인 외 알림 접근 → 404
- 라우터: 인증 필요, RBAC viewer+
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.core.deps import get_redis_client
from app.core.security import get_current_user
from app.main import create_app
from app.models.auth import User
from app.models.notification import NOTIFICATION_TTL_SECONDS
from app.services.notification_service import (
    NotificationNotFoundError,
    create_notification,
    delete_notification,
    list_notifications,
    make_notification_id,
    mark_all_read,
    mark_read,
)
from tests.fixtures.mock_redis import MockRedisClient


# ---------- 단위: ID 결정성 ----------
@pytest.mark.unit
class TestMakeNotificationId:
    """결정적 ID — 동일 입력에 대해 항상 동일 결과."""

    def test_deterministic(self) -> None:
        a = make_notification_id("u1", "experiment_complete", "exp-1")
        b = make_notification_id("u1", "experiment_complete", "exp-1")
        assert a == b

    def test_different_inputs_yield_different_ids(self) -> None:
        a = make_notification_id("u1", "experiment_complete", "exp-1")
        b = make_notification_id("u1", "experiment_complete", "exp-2")
        assert a != b

    def test_user_isolation(self) -> None:
        a = make_notification_id("u1", "experiment_complete", "exp-1")
        b = make_notification_id("u2", "experiment_complete", "exp-1")
        assert a != b


# ---------- 서비스: create / list / mark / delete ----------
@pytest.mark.unit
class TestNotificationServiceCreate:
    """``create_notification``."""

    async def test_creates_notification_with_correct_fields(
        self, redis_client: MockRedisClient
    ) -> None:
        notif = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="실험 완료",
            body="감성분석 v3 실험이 완료되었습니다.",
            link="/experiments/exp-1",
            redis=redis_client,
            resource_id="exp-1",
        )
        assert notif.user_id == "user-1"
        assert notif.type == "experiment_complete"
        assert notif.title == "실험 완료"
        assert notif.body == "감성분석 v3 실험이 완료되었습니다."
        assert notif.link == "/experiments/exp-1"
        assert notif.read is False
        assert notif.read_at is None

    async def test_idempotent_with_same_resource(
        self, redis_client: MockRedisClient
    ) -> None:
        """동일 resource_id로 두 번 호출해도 1건만 생성."""
        a = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="A",
            body="A",
            link=None,
            redis=redis_client,
            resource_id="exp-1",
        )
        b = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="B (different)",
            body="B body",
            link=None,
            redis=redis_client,
            resource_id="exp-1",
        )
        assert a.id == b.id
        # 두 번째 호출이 새 레코드를 만들지 않았으므로 title은 그대로 A
        assert b.title == "A"

    async def test_ttl_30_days_applied(
        self, redis_client: MockRedisClient
    ) -> None:
        """알림 Hash와 인덱스 모두 TTL 30일 적용."""
        notif = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        underlying = redis_client._client
        hash_ttl = await underlying.ttl(f"ax:notification:user-1:{notif.id}")
        index_ttl = await underlying.ttl("ax:notification:user-1:index")
        # TTL은 음수면 미설정/만료. 양수여야 하며 NOTIFICATION_TTL_SECONDS 이하
        assert 0 < hash_ttl <= NOTIFICATION_TTL_SECONDS
        assert 0 < index_ttl <= NOTIFICATION_TTL_SECONDS

    async def test_index_zset_records_creation(
        self, redis_client: MockRedisClient
    ) -> None:
        """인덱스 Sorted Set에 등록되어 ZCARD = 1."""
        await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        zcard = await redis_client._client.zcard("ax:notification:user-1:index")
        assert zcard == 1


@pytest.mark.unit
class TestNotificationServiceList:
    """``list_notifications``."""

    async def test_returns_empty_for_new_user(
        self, redis_client: MockRedisClient
    ) -> None:
        result = await list_notifications(
            user_id="brand-new", redis=redis_client
        )
        assert result.total == 0
        assert result.unread_count == 0
        assert result.items == []

    async def test_orders_newest_first(
        self, redis_client: MockRedisClient
    ) -> None:
        """가장 최근 생성된 알림이 첫 번째."""
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="oldest",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
            now=t0,
        )
        await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="newest",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r2",
            now=t0 + timedelta(hours=1),
        )
        result = await list_notifications(user_id="u1", redis=redis_client)
        assert len(result.items) == 2
        assert result.items[0].title == "newest"
        assert result.items[1].title == "oldest"

    async def test_unread_only_filter(
        self, redis_client: MockRedisClient
    ) -> None:
        """``unread_only=True``면 읽지 않은 알림만."""
        n1 = await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="read-me",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        await create_notification(
            user_id="u1",
            type_="experiment_failed",
            title="unread",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r2",
        )
        await mark_read(user_id="u1", notification_id=n1.id, redis=redis_client)
        result = await list_notifications(
            user_id="u1", redis=redis_client, unread_only=True
        )
        assert result.total == 1
        assert result.items[0].title == "unread"
        # unread_count는 필터 무관
        assert result.unread_count == 1

    async def test_pagination(self, redis_client: MockRedisClient) -> None:
        """page/page_size."""
        for i in range(5):
            await create_notification(
                user_id="u1",
                type_="experiment_complete",
                title=f"t{i}",
                body="b",
                link=None,
                redis=redis_client,
                resource_id=f"r{i}",
                now=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=i),
            )
        page1 = await list_notifications(
            user_id="u1", redis=redis_client, page=1, page_size=2
        )
        page2 = await list_notifications(
            user_id="u1", redis=redis_client, page=2, page_size=2
        )
        assert len(page1.items) == 2
        assert len(page2.items) == 2
        # 두 페이지에 동일 알림이 등장하지 않아야 함
        ids_p1 = {n.id for n in page1.items}
        ids_p2 = {n.id for n in page2.items}
        assert ids_p1.isdisjoint(ids_p2)

    async def test_isolation_between_users(
        self, redis_client: MockRedisClient
    ) -> None:
        """user_id별로 알림이 분리됨."""
        await create_notification(
            user_id="alice",
            type_="experiment_complete",
            title="alice",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        await create_notification(
            user_id="bob",
            type_="experiment_complete",
            title="bob",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        alice_list = await list_notifications(user_id="alice", redis=redis_client)
        bob_list = await list_notifications(user_id="bob", redis=redis_client)
        assert len(alice_list.items) == 1
        assert len(bob_list.items) == 1
        assert alice_list.items[0].title == "alice"
        assert bob_list.items[0].title == "bob"


@pytest.mark.unit
class TestMarkRead:
    """``mark_read``."""

    async def test_sets_read_and_read_at(
        self, redis_client: MockRedisClient
    ) -> None:
        notif = await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        ts = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
        result = await mark_read(
            user_id="u1", notification_id=notif.id, redis=redis_client, now=ts
        )
        assert result.read is True
        assert result.read_at == ts

    async def test_idempotent(self, redis_client: MockRedisClient) -> None:
        """이미 읽음 처리된 알림을 다시 호출해도 read_at이 유지됨."""
        notif = await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        ts1 = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)
        await mark_read(
            user_id="u1", notification_id=notif.id, redis=redis_client, now=ts1
        )
        result2 = await mark_read(
            user_id="u1",
            notification_id=notif.id,
            redis=redis_client,
            now=datetime(2026, 5, 1, tzinfo=UTC),
        )
        # 멱등 — read_at은 처음 시각 유지
        assert result2.read_at == ts1

    async def test_unknown_id_raises_not_found(
        self, redis_client: MockRedisClient
    ) -> None:
        with pytest.raises(NotificationNotFoundError):
            await mark_read(
                user_id="u1", notification_id="nonexistent", redis=redis_client
            )

    async def test_other_user_notification_not_visible(
        self, redis_client: MockRedisClient
    ) -> None:
        """타 사용자 알림은 mark_read 시 NotFound — 정보 노출 방지."""
        notif = await create_notification(
            user_id="alice",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        # bob이 alice의 알림 ID로 접근
        with pytest.raises(NotificationNotFoundError):
            await mark_read(
                user_id="bob", notification_id=notif.id, redis=redis_client
            )


@pytest.mark.unit
class TestMarkAllRead:
    """``mark_all_read``."""

    async def test_marks_all_unread(self, redis_client: MockRedisClient) -> None:
        for i in range(3):
            await create_notification(
                user_id="u1",
                type_="experiment_complete",
                title=f"t{i}",
                body="b",
                link=None,
                redis=redis_client,
                resource_id=f"r{i}",
            )
        marked = await mark_all_read(user_id="u1", redis=redis_client)
        assert marked == 3
        result = await list_notifications(user_id="u1", redis=redis_client)
        assert result.unread_count == 0

    async def test_skips_already_read(
        self, redis_client: MockRedisClient
    ) -> None:
        n1 = await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="t2",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r2",
        )
        await mark_read(user_id="u1", notification_id=n1.id, redis=redis_client)
        marked = await mark_all_read(user_id="u1", redis=redis_client)
        assert marked == 1


@pytest.mark.unit
class TestDeleteNotification:
    """``delete_notification``."""

    async def test_deletes_and_removes_from_index(
        self, redis_client: MockRedisClient
    ) -> None:
        notif = await create_notification(
            user_id="u1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        await delete_notification(
            user_id="u1", notification_id=notif.id, redis=redis_client
        )
        # 인덱스에서 제거됨
        zcard = await redis_client._client.zcard("ax:notification:u1:index")
        assert zcard == 0

    async def test_unknown_id_raises(
        self, redis_client: MockRedisClient
    ) -> None:
        with pytest.raises(NotificationNotFoundError):
            await delete_notification(
                user_id="u1", notification_id="missing", redis=redis_client
            )

    async def test_cannot_delete_other_users(
        self, redis_client: MockRedisClient
    ) -> None:
        notif = await create_notification(
            user_id="alice",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        with pytest.raises(NotificationNotFoundError):
            await delete_notification(
                user_id="bob", notification_id=notif.id, redis=redis_client
            )


# ---------- 라우터 통합 ----------
@pytest.fixture
def viewer_user() -> User:
    return User(id="user-1", email="v@x.com", role="viewer")


@pytest.fixture
def app_with_notifications(
    viewer_user: User, redis_client: MockRedisClient
) -> TestClient:
    app = create_app()
    app.dependency_overrides[get_redis_client] = lambda: redis_client
    app.dependency_overrides[get_current_user] = lambda: viewer_user
    return TestClient(app)


@pytest.mark.unit
class TestNotificationRouter:
    """라우터 통합."""

    def test_get_empty_list(self, app_with_notifications: TestClient) -> None:
        resp = app_with_notifications.get("/api/v1/notifications")
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["unread_count"] == 0

    async def test_get_after_create(
        self,
        app_with_notifications: TestClient,
        redis_client: MockRedisClient,
    ) -> None:
        await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        resp = app_with_notifications.get("/api/v1/notifications")
        body = resp.json()
        assert body["total"] == 1
        assert body["unread_count"] == 1

    async def test_patch_read_endpoint(
        self,
        app_with_notifications: TestClient,
        redis_client: MockRedisClient,
    ) -> None:
        notif = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        resp = app_with_notifications.patch(
            f"/api/v1/notifications/{notif.id}/read"
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == notif.id
        assert body["read"] is True
        # ETag 헤더 부착
        assert "etag" in {k.lower() for k in resp.headers}

    def test_patch_unknown_returns_404(
        self, app_with_notifications: TestClient
    ) -> None:
        resp = app_with_notifications.patch("/api/v1/notifications/missing/read")
        assert resp.status_code == 404

    async def test_post_read_all(
        self,
        app_with_notifications: TestClient,
        redis_client: MockRedisClient,
    ) -> None:
        for i in range(3):
            await create_notification(
                user_id="user-1",
                type_="experiment_complete",
                title=f"t{i}",
                body="b",
                link=None,
                redis=redis_client,
                resource_id=f"r{i}",
            )
        resp = app_with_notifications.post("/api/v1/notifications/read-all")
        assert resp.status_code == 200
        body = resp.json()
        assert body["marked_count"] == 3

    async def test_delete_endpoint(
        self,
        app_with_notifications: TestClient,
        redis_client: MockRedisClient,
    ) -> None:
        notif = await create_notification(
            user_id="user-1",
            type_="experiment_complete",
            title="t",
            body="b",
            link=None,
            redis=redis_client,
            resource_id="r1",
        )
        resp = app_with_notifications.delete(
            f"/api/v1/notifications/{notif.id}"
        )
        assert resp.status_code == 204

    def test_delete_unknown_returns_404(
        self, app_with_notifications: TestClient
    ) -> None:
        resp = app_with_notifications.delete("/api/v1/notifications/missing")
        assert resp.status_code == 404

    def test_unauthenticated_request_rejected(
        self, redis_client: MockRedisClient
    ) -> None:
        app = create_app()
        app.dependency_overrides[get_redis_client] = lambda: redis_client
        client = TestClient(app)
        resp = client.get("/api/v1/notifications")
        assert resp.status_code == 401
