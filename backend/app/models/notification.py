"""알림(Notification Inbox) 도메인 모델.

저장소: Redis Hash ``ax:notification:{user_id}:{notification_id}`` (TTL 30일).
보조 인덱스 Sorted Set ``ax:notification:{user_id}:index``는 최신순 조회를 위해 사용한다.

본 모델은 API 응답/내부 직렬화 형태로만 사용되며, Redis 직렬화는 서비스 계층에서 수행한다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

NotificationType = Literal[
    "experiment_complete",
    "experiment_failed",
    "experiment_cancelled",
    "evaluator_approved",
    "evaluator_rejected",
    "evaluator_deprecated",
    "evaluator_submission_pending",
    "auto_eval_regression",
    "auto_eval_cost_limit",
    "auto_eval_run_completed",
]
"""알림 타입.

- ``experiment_*``: 실험 완료/실패/취소 훅에서 생성
- ``evaluator_*``: Custom Evaluator 거버넌스 이벤트
- ``evaluator_submission_pending``: 신규 제출 — admin에게 전송
- ``auto_eval_regression``: Auto-Eval 정책의 회귀 임계 충족 시
- ``auto_eval_cost_limit``: Auto-Eval 정책의 일일 비용 한도 초과 시
- ``auto_eval_run_completed``: Auto-Eval run 완료 알림 (선택)
"""


class Notification(BaseModel):
    """단일 알림."""

    id: str = Field(..., description="알림 ID (sha1 결정적 — 멱등)")
    user_id: str = Field(..., description="알림 수신자 user_id")
    type: NotificationType = Field(..., description="알림 타입")
    title: str = Field(..., description="알림 제목")
    body: str = Field(..., description="알림 본문 (저장소 ``message`` 필드와 호환)")
    link: str | None = Field(None, description="클릭 시 이동할 URL (저장소 ``target_url`` 필드)")
    read: bool = Field(False, description="읽음 여부")
    created_at: datetime = Field(..., description="생성 시각 (UTC)")
    read_at: datetime | None = Field(None, description="읽음 처리 시각 (UTC). 미열람이면 None.")


class NotificationListResponse(BaseModel):
    """``GET /api/v1/notifications`` 응답."""

    items: list[Notification] = Field(default_factory=list, description="알림 목록 (최신순)")
    total: int = Field(0, description="필터 적용 후 전체 개수")
    unread_count: int = Field(0, description="미열람 개수 (필터 무관)")
    page: int = Field(1, description="현재 페이지 (1-based)")
    page_size: int = Field(20, description="페이지 크기")


class MarkReadResponse(BaseModel):
    """``PATCH /api/v1/notifications/{id}/read`` 응답 데이터."""

    id: str
    read: bool = True


class MarkAllReadResponse(BaseModel):
    """``POST /api/v1/notifications/read-all`` 응답 데이터."""

    marked_count: int = Field(0, description="읽음 처리된 알림 수")


# ---------- 상수 ----------
NOTIFICATION_TTL_SECONDS: int = 30 * 24 * 60 * 60  # 30일
"""알림 TTL — IMPLEMENTATION.md §1.5 준수."""

NOTIFICATION_MAX_PER_USER: int = 1000
"""사용자당 인덱스 최대 보유 수 — ZREMRANGEBYRANK 0 -1001."""
