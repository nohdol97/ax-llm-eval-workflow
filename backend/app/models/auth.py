"""인증/인가 도메인 모델.

사내 Auth 서비스가 발급한 JWT 클레임을 본 프로젝트 도메인 모델로 매핑한다.
RBAC 역할은 ``admin`` / ``user`` / ``viewer`` 3종으로 단순화한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RBACRole = Literal["admin", "reviewer", "user", "viewer"]
"""RBAC 역할.

- ``admin``: 시스템 설정, score config 등록, 위험 작업 가능
- ``reviewer`` (Phase 8-C 신설): Review Queue claim/decide + 통계 조회
- ``user``: 실험 생성/실행, 프롬프트 편집 가능
- ``viewer``: 읽기 전용

권한 hierarchy: ``viewer < user < reviewer < admin``.
"""


# 역할 우선순위 — require_role에서 비교용 (높을수록 강한 권한)
ROLE_PRIORITY: dict[str, int] = {
    "viewer": 10,
    "user": 20,
    "reviewer": 25,
    "admin": 30,
}


class User(BaseModel):
    """인증된 사용자 도메인 모델.

    JWT 클레임에서 추출한 최소 정보를 담는다. 본 프로젝트는 사용자 정보를
    영속 저장하지 않으며, 매 요청마다 토큰에서 재구성한다.
    """

    id: str = Field(..., description="사용자 고유 ID (sub 클레임)")
    email: str | None = Field(None, description="이메일 (선택)")
    role: RBACRole = Field(..., description="RBAC 역할")
    name: str | None = Field(None, description="표시 이름 (선택)")
    groups: list[str] = Field(default_factory=list, description="소속 그룹 (선택)")

    def has_role(self, required: RBACRole) -> bool:
        """현재 사용자의 역할이 ``required`` 이상인지 확인."""
        return ROLE_PRIORITY[self.role] >= ROLE_PRIORITY[required]
