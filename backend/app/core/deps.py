"""FastAPI 의존성 주입.

``app.state``에 lifespan에서 attach한 클라이언트 인스턴스를 라우터에서 꺼내쓴다.
테스트에서는 ``app.dependency_overrides[...]``로 주입을 교체할 수 있다.
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.models.project import ProjectConfig
from app.services.clickhouse_client import (
    ClickHouseClient,
    LangfusePublicAPIFallbackClient,
)
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient
from app.services.redis_client import RedisClient


def get_app_settings() -> Settings:
    """현재 프로세스의 ``Settings`` 인스턴스 반환."""
    return get_settings()


def get_langfuse_client(request: Request) -> LangfuseClient:
    """``app.state.langfuse``에 보관된 클라이언트 반환."""
    client = getattr(request.app.state, "langfuse", None)
    if client is None:
        raise RuntimeError("LangfuseClient가 app.state에 초기화되지 않았습니다.")
    return client  # type: ignore[no-any-return]


def get_litellm_client(request: Request) -> LiteLLMClient:
    """``app.state.litellm``에 보관된 클라이언트 반환."""
    client = getattr(request.app.state, "litellm", None)
    if client is None:
        raise RuntimeError("LiteLLMClient가 app.state에 초기화되지 않았습니다.")
    return client  # type: ignore[no-any-return]


def get_redis_client(request: Request) -> RedisClient:
    """``app.state.redis``에 보관된 클라이언트 반환."""
    client = getattr(request.app.state, "redis", None)
    if client is None:
        raise RuntimeError("RedisClient가 app.state에 초기화되지 않았습니다.")
    return client  # type: ignore[no-any-return]


def get_clickhouse_client(
    request: Request,
) -> ClickHouseClient | LangfusePublicAPIFallbackClient | None:
    """``app.state.clickhouse``에 보관된 클라이언트 반환.

    설정에 따라 ``None``일 수 있음 (직접 모드 미사용 + 폴백 미구성).
    """
    return getattr(request.app.state, "clickhouse", None)  # type: ignore[no-any-return]


# ---------- 프로젝트 ----------
def get_project_configs(
    settings: Settings = Depends(get_app_settings),
) -> list[ProjectConfig]:
    """``Settings.projects()``를 ``ProjectConfig`` 리스트로 매핑.

    파싱 실패 시 ``ValueError``가 그대로 전파되어 422/500으로 변환된다.
    """
    return [ProjectConfig.model_validate(entry) for entry in settings.projects()]


# ---------- 합성 의존성 ----------
def get_clients(
    settings: Settings = Depends(get_app_settings),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    redis: RedisClient = Depends(get_redis_client),
) -> dict[str, Any]:
    """주요 클라이언트 묶음 반환 (헬스 체크 등에서 사용)."""
    return {
        "settings": settings,
        "langfuse": langfuse,
        "litellm": litellm,
        "redis": redis,
    }
