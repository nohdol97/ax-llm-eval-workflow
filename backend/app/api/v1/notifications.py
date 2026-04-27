"""알림 수신함 라우터.

엔드포인트:
- ``GET /api/v1/notifications`` — 본인 알림 목록 (viewer+)
- ``PATCH /api/v1/notifications/{id}/read`` — 읽음 처리 (본인 것만, ETag/If-Match)
- ``POST /api/v1/notifications/read-all`` — 전체 읽음 (본인 것만)
- ``DELETE /api/v1/notifications/{id}`` — 삭제 (본인 것만)

본 프로젝트 정책: 타 사용자 알림은 존재 여부를 노출하지 않기 위해 404로 통일 응답한다.
"""

from __future__ import annotations

import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.deps import get_redis_client
from app.core.security import get_current_user
from app.models.auth import User
from app.models.notification import (
    MarkAllReadResponse,
    MarkReadResponse,
    Notification,
    NotificationListResponse,
)
from app.services.notification_service import (
    NotificationNotFoundError,
    delete_notification,
    list_notifications,
    mark_all_read,
    mark_read,
)
from app.services.redis_client import RedisClient

router = APIRouter(tags=["notifications"])


def _etag(notification: Notification) -> str:
    """알림 객체의 ETag 계산 — JSON 직렬화 + sha256 prefix 16."""
    payload = notification.model_dump_json()
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f'"{digest}"'


@router.get(
    "/notifications",
    response_model=NotificationListResponse,
    summary="본인 알림 목록 조회",
)
async def list_my_notifications(
    unread_only: bool = Query(False, description="미열람만 반환"),
    page: int = Query(1, ge=1, description="페이지 (1-based)"),
    page_size: int = Query(20, ge=1, le=100, description="페이지 크기"),
    user: User = Depends(get_current_user),
    redis: RedisClient = Depends(get_redis_client),
) -> NotificationListResponse:
    """본인 알림 목록을 최신순으로 반환한다."""
    return await list_notifications(
        user_id=user.id,
        redis=redis,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/notifications/{notification_id}/read",
    response_model=MarkReadResponse,
    summary="알림 읽음 처리",
)
async def patch_read(
    notification_id: str,
    response: Response,
    if_match: str | None = Header(None, alias="If-Match"),
    user: User = Depends(get_current_user),
    redis: RedisClient = Depends(get_redis_client),
) -> MarkReadResponse:
    """단건 읽음 처리. ETag 불일치 시 412 Precondition Failed."""
    try:
        notification = await mark_read(
            user_id=user.id,
            notification_id=notification_id,
            redis=redis,
        )
    except NotificationNotFoundError:
        # 본 프로젝트 정책: 타 사용자 알림도 동일하게 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="알림을 찾을 수 없습니다.",
        ) from None

    etag = _etag(notification)
    if if_match is not None and if_match.strip() not in ("*", etag):
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail="ETag가 일치하지 않습니다.",
        )
    response.headers["ETag"] = etag
    return MarkReadResponse(id=notification.id, read=True)


@router.post(
    "/notifications/read-all",
    response_model=MarkAllReadResponse,
    summary="모든 알림 읽음 처리",
)
async def post_read_all(
    user: User = Depends(get_current_user),
    redis: RedisClient = Depends(get_redis_client),
) -> MarkAllReadResponse:
    """본인의 미열람 알림을 모두 읽음 처리한다."""
    marked = await mark_all_read(user_id=user.id, redis=redis)
    return MarkAllReadResponse(marked_count=marked)


@router.delete(
    "/notifications/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="알림 삭제",
)
async def delete_my_notification(
    notification_id: str,
    user: User = Depends(get_current_user),
    redis: RedisClient = Depends(get_redis_client),
) -> Response:
    """본인 알림을 삭제한다. 미존재/타사용자 알림은 404."""
    try:
        await delete_notification(
            user_id=user.id,
            notification_id=notification_id,
            redis=redis,
        )
    except NotificationNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="알림을 찾을 수 없습니다.",
        ) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)
