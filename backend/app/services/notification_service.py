"""알림 수신함 서비스.

Redis Hash + 보조 Sorted Set으로 알림을 영속화한다.

- Hash 키: ``ax:notification:{user_id}:{notification_id}``
- Index Sorted Set: ``ax:notification:{user_id}:index``
  (score=created_at_ms, member=notification_id)
- TTL: 30일 (Hash, index 모두)

본 서비스는 ``RedisClient`` (실제) / ``MockRedisClient`` (테스트) 모두에서 동작하도록
``set/get/expire``류 직접 호출을 피하고, ``hset/hget/hgetall/zadd/zrevrange``를 사용한다.
``MockRedisClient.__getattr__``가 fakeredis로 위임하므로 실 환경과 mock 모두 동일 인터페이스로
호출 가능하다.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any, cast

from app.core.errors import LabsError
from app.core.logging import get_logger
from app.models.notification import (
    NOTIFICATION_MAX_PER_USER,
    NOTIFICATION_TTL_SECONDS,
    Notification,
    NotificationListResponse,
    NotificationType,
)

logger = get_logger(__name__)


class NotificationNotFoundError(LabsError):
    """알림이 존재하지 않을 때 발생.

    본 프로젝트 정책: 타 사용자 알림 접근도 동일한 404로 통일하여 정보 노출을 방지한다.
    """

    code = "notification_not_found"
    status_code = 404
    title = "Notification not found"


# ---------- 키 헬퍼 ----------
def _hash_key(user_id: str, notification_id: str) -> str:
    """알림 Hash 키 (prefix 없이 — RedisClient가 자동 부착)."""
    return f"notification:{user_id}:{notification_id}"


def _index_key(user_id: str) -> str:
    """알림 인덱스 Sorted Set 키."""
    return f"notification:{user_id}:index"


def _full_key(user_id: str, notification_id: str) -> str:
    """``ax:`` prefix 포함 전체 Hash 키 — 직접 redis 호출용."""
    return f"ax:notification:{user_id}:{notification_id}"


def _full_index_key(user_id: str) -> str:
    """``ax:`` prefix 포함 인덱스 키."""
    return f"ax:notification:{user_id}:index"


def _now_utc() -> datetime:
    """현재 UTC 시각 (테스트 hook 용으로 분리)."""
    return datetime.now(UTC)


def make_notification_id(user_id: str, type_: str, resource_id: str) -> str:
    """결정적 알림 ID 생성 — 멱등 보장.

    ``sha1(user_id + ":" + type + ":" + resource_id)``의 hex 16자.
    """
    seed = f"{user_id}:{type_}:{resource_id}".encode()
    return hashlib.sha1(seed, usedforsecurity=False).hexdigest()[:16]


# ---------- 직렬화 ----------
def _to_storage(notification: Notification) -> dict[str, str]:
    """``Notification`` → Redis Hash 필드 dict.

    저장 필드는 IMPLEMENTATION.md §1.5와 호환되도록 ``message``/``target_url`` 사용.
    """
    return {
        "id": notification.id,
        "user_id": notification.user_id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.body,
        "target_url": notification.link or "",
        "read": "1" if notification.read else "0",
        "created_at": notification.created_at.isoformat().replace("+00:00", "Z"),
        "read_at": (
            notification.read_at.isoformat().replace("+00:00", "Z")
            if notification.read_at is not None
            else ""
        ),
    }


def _from_storage(raw: dict[Any, Any]) -> Notification | None:
    """Redis Hash → ``Notification``. 결손 필드는 best-effort로 보정."""
    if not raw:
        return None

    def _get(key: str) -> str | None:
        # fakeredis(decode_responses=True)는 str, 일부 환경은 bytes — 모두 수용
        v = raw.get(key)
        if v is None:
            v = raw.get(key.encode("utf-8") if isinstance(key, str) else key)
        if v is None:
            return None
        if isinstance(v, bytes):
            return v.decode("utf-8")
        return str(v)

    nid = _get("id")
    user_id = _get("user_id")
    type_ = _get("type")
    title = _get("title")
    message = _get("message") or _get("body") or ""
    target_url = _get("target_url") or _get("link") or ""
    read_raw = _get("read") or "0"
    created_at_raw = _get("created_at")
    read_at_raw = _get("read_at")

    if not (nid and user_id and type_ and title and created_at_raw):
        return None

    try:
        created_at = datetime.fromisoformat(created_at_raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    read_at: datetime | None = None
    if read_at_raw:
        try:
            read_at = datetime.fromisoformat(read_at_raw.replace("Z", "+00:00"))
        except ValueError:
            read_at = None

    return Notification(
        id=nid,
        user_id=user_id,
        type=cast(NotificationType, type_),
        title=title,
        body=message,
        link=target_url or None,
        read=read_raw == "1",
        created_at=created_at,
        read_at=read_at,
    )


# ---------- 헬퍼: redis 객체에서 underlying client / hset 일관 호출 ----------
def _get_underlying(redis: Any) -> Any:
    """``RedisClient.underlying`` 또는 ``MockRedisClient._client``에서 실제 redis 인스턴스를 추출.

    실제 redis 명령(특히 ``zadd``, ``zrevrange`` 등)을 호출할 때, prefix 자동 부착이 없는
    underlying 인스턴스를 사용해야 키 충돌 없이 동작한다.
    """
    if hasattr(redis, "underlying"):
        return redis.underlying
    if hasattr(redis, "_client"):
        return redis._client
    return redis


# ---------- 알림 생성 ----------
async def create_notification(
    user_id: str,
    type_: NotificationType,
    title: str,
    body: str,
    link: str | None,
    redis: Any,
    *,
    resource_id: str | None = None,
    now: datetime | None = None,
) -> Notification:
    """알림 1건 생성. 실패는 ``LabsError`` 변형으로 raise되며, 호출자가 best-effort로 swallow한다.

    멱등: ``resource_id`` 제공 시 ``sha1(user_id+type+resource_id)`` 결정적 ID 사용.
    이미 존재하면 기존 알림을 그대로 반환한다.
    """
    if not user_id:
        raise LabsError(detail="user_id가 비어 있습니다.")

    rid = resource_id or str(uuid.uuid4())
    nid = make_notification_id(user_id, type_, rid)
    created_at = now or _now_utc()

    notification = Notification(
        id=nid,
        user_id=user_id,
        type=type_,
        title=title,
        body=body,
        link=link,
        read=False,
        created_at=created_at,
        read_at=None,
    )

    underlying = _get_underlying(redis)
    hash_key = _full_key(user_id, nid)
    index_key = _full_index_key(user_id)

    # 멱등: 이미 존재하면 기존 객체 반환
    exists = await underlying.exists(hash_key)
    if exists:
        existing = await underlying.hgetall(hash_key)
        existing_obj = _from_storage(existing)
        if existing_obj is not None:
            return existing_obj
        # 손상 → 덮어쓰기 진행

    payload = _to_storage(notification)
    score = created_at.timestamp() * 1000.0  # ms

    # 원자 보장은 Lua가 이상적이나, 본 단계에서는 pipeline으로 충분
    pipe = underlying.pipeline()
    pipe.hset(hash_key, mapping=payload)
    pipe.expire(hash_key, NOTIFICATION_TTL_SECONDS)
    pipe.zadd(index_key, {nid: score})
    pipe.expire(index_key, NOTIFICATION_TTL_SECONDS)
    # 최신 N개만 유지 (오래된 항목 제거)
    pipe.zremrangebyrank(index_key, 0, -(NOTIFICATION_MAX_PER_USER + 1))
    try:
        await pipe.execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "notification_create_failed",
            user_id=user_id,
            type=type_,
            error=str(exc),
        )
        raise

    return notification


# ---------- 목록 ----------
async def list_notifications(
    user_id: str,
    redis: Any,
    *,
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> NotificationListResponse:
    """본인 알림 목록 조회. 최신순 정렬, 페이지네이션 적용.

    Args:
        user_id: 본인 ID
        redis: redis client (실 또는 mock)
        unread_only: True면 읽지 않은 알림만 반환
        page: 1-based
        page_size: 1~100
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))

    underlying = _get_underlying(redis)
    index_key = _full_index_key(user_id)

    # 1) 인덱스 전체 → 최신순 ID 목록
    raw_ids = await underlying.zrevrange(index_key, 0, -1)
    ids: list[str] = [
        rid.decode("utf-8") if isinstance(rid, bytes) else str(rid) for rid in raw_ids
    ]

    # 2) 각 ID → Hash fetch (lazy cleanup)
    notifications: list[Notification] = []
    stale_ids: list[str] = []
    for nid in ids:
        raw = await underlying.hgetall(_full_key(user_id, nid))
        obj = _from_storage(raw)
        if obj is None:
            stale_ids.append(nid)
            continue
        notifications.append(obj)

    # 3) lazy cleanup — 손상/만료된 항목은 인덱스에서 제거
    if stale_ids:
        try:
            await underlying.zrem(index_key, *stale_ids)
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning(
                "notification_lazy_cleanup_failed",
                user_id=user_id,
                error=str(exc),
            )

    unread_count = sum(1 for n in notifications if not n.read)

    # 4) 필터링
    if unread_only:
        filtered = [n for n in notifications if not n.read]
    else:
        filtered = notifications

    total = len(filtered)
    start = (page - 1) * page_size
    end = start + page_size
    page_items = filtered[start:end]

    return NotificationListResponse(
        items=page_items,
        total=total,
        unread_count=unread_count,
        page=page,
        page_size=page_size,
    )


# ---------- 읽음 처리 ----------
async def mark_read(
    user_id: str,
    notification_id: str,
    redis: Any,
    *,
    now: datetime | None = None,
) -> Notification:
    """본인 알림 읽음 처리. 미존재/타사용자 알림은 ``NotificationNotFoundError``."""
    underlying = _get_underlying(redis)
    key = _full_key(user_id, notification_id)
    raw = await underlying.hgetall(key)
    obj = _from_storage(raw)
    if obj is None:
        raise NotificationNotFoundError(detail=f"알림을 찾을 수 없습니다: id={notification_id!r}")

    if obj.read:
        # 이미 읽음 — 응답은 그대로 반환 (멱등)
        return obj

    timestamp = now or _now_utc()
    obj.read = True
    obj.read_at = timestamp
    await underlying.hset(
        key,
        mapping={
            "read": "1",
            "read_at": timestamp.isoformat().replace("+00:00", "Z"),
        },
    )
    return obj


# ---------- 전체 읽음 ----------
async def mark_all_read(
    user_id: str,
    redis: Any,
    *,
    now: datetime | None = None,
) -> int:
    """본인 알림 전체 읽음 처리. 처리한 개수 반환."""
    underlying = _get_underlying(redis)
    index_key = _full_index_key(user_id)
    raw_ids = await underlying.zrevrange(index_key, 0, -1)
    ids: Iterable[str] = (
        rid.decode("utf-8") if isinstance(rid, bytes) else str(rid) for rid in raw_ids
    )

    timestamp = now or _now_utc()
    ts_iso = timestamp.isoformat().replace("+00:00", "Z")
    marked = 0
    for nid in ids:
        key = _full_key(user_id, nid)
        raw = await underlying.hgetall(key)
        obj = _from_storage(raw)
        if obj is None or obj.read:
            continue
        await underlying.hset(
            key,
            mapping={"read": "1", "read_at": ts_iso},
        )
        marked += 1
    return marked


# ---------- 삭제 ----------
async def delete_notification(
    user_id: str,
    notification_id: str,
    redis: Any,
) -> None:
    """본인 알림 삭제. 미존재/타사용자 → ``NotificationNotFoundError``."""
    underlying = _get_underlying(redis)
    key = _full_key(user_id, notification_id)
    exists = await underlying.exists(key)
    if not exists:
        raise NotificationNotFoundError(detail=f"알림을 찾을 수 없습니다: id={notification_id!r}")
    pipe = underlying.pipeline()
    pipe.delete(key)
    pipe.zrem(_full_index_key(user_id), notification_id)
    await pipe.execute()
