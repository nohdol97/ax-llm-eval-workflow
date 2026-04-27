"""헬스 체크 응답 스키마."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

ServiceStatus = Literal["ok", "warn", "error"]
OverallStatus = Literal["ok", "degraded", "down"]


class ServiceHealth(BaseModel):
    """단일 외부 서비스의 헬스 상태."""

    status: ServiceStatus = Field(..., description="서비스 상태 (ok / warn / error)")
    latency_ms: float | None = Field(
        None, description="헬스 체크 호출 응답 시간 (ms)"
    )
    endpoint: str | None = Field(None, description="대상 endpoint URL (있으면)")
    detail: str | None = Field(None, description="추가 정보 또는 에러 메시지")
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="체크 시각 (UTC)",
    )


class HealthResponse(BaseModel):
    """``GET /api/v1/health`` 응답."""

    status: OverallStatus = Field(..., description="전체 상태 (ok / degraded / down)")
    version: str = Field(..., description="백엔드 빌드 버전")
    environment: str = Field(..., description="실행 환경 (dev / staging / demo / prod)")
    services: dict[str, ServiceHealth] = Field(
        ..., description="개별 서비스 상태 매핑"
    )
    checked_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="응답 생성 시각 (UTC)",
    )
