"""FastAPI 앱 엔트리포인트.

라이프사이클:
- startup: 외부 클라이언트 초기화 + Langfuse score config idempotent 등록
- shutdown: Redis/Langfuse graceful close

사내 endpoint 미설정 시에도 부팅은 성공하며, 헬스 체크에서 ``warn``으로 노출된다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1 import datasets as datasets_router
from app.api.v1 import experiments as experiments_router
from app.api.v1 import health as health_router
from app.api.v1 import models as models_router
from app.api.v1 import notifications as notifications_router
from app.api.v1 import projects as projects_router
from app.api.v1 import prompts as prompts_router
from app.api.v1 import search as search_router
from app.api.v1 import tests as tests_router
from app.core.config import Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import get_logger
from app.core.observability import setup_observability
from app.services.clickhouse_client import build_clickhouse_client
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient
from app.services.redis_client import RedisClient
from app.services.score_registry import register_score_configs_on_startup

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """앱 라이프사이클 — 외부 클라이언트 초기화 / 종료."""
    settings: Settings = get_settings()

    # ---------- 클라이언트 초기화 ----------
    app.state.langfuse = LangfuseClient(settings)
    app.state.litellm = LiteLLMClient(
        base_url=settings.LITELLM_BASE_URL,
        virtual_key=settings.LITELLM_VIRTUAL_KEY,
    )
    app.state.redis = RedisClient(settings.REDIS_URL)
    app.state.clickhouse = build_clickhouse_client(settings, app.state.langfuse)

    logger.info(
        "lifespan_startup",
        env=settings.LABS_ENV,
        langfuse_configured=settings.langfuse_configured,
        litellm_configured=settings.litellm_configured,
        clickhouse_configured=settings.clickhouse_configured,
        clickhouse_fallback=settings.USE_LANGFUSE_PUBLIC_API_FALLBACK,
    )

    # ---------- Score Config 등록 ----------
    if settings.langfuse_configured:
        try:
            registered = await register_score_configs_on_startup(app.state.langfuse)
            logger.info(
                "score_configs_registered",
                count=len(registered),
                names=list(registered.keys()),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("score_config_registration_failed", error=str(exc))
            if settings.is_production:
                raise
    else:
        logger.info(
            "score_config_registration_skipped",
            reason="Langfuse not configured (graceful)",
        )

    yield

    # ---------- 종료 ----------
    try:
        app.state.langfuse.flush()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover
        logger.warning("langfuse_flush_failed_on_shutdown", error=str(exc))

    try:
        await app.state.redis.aclose()
    except Exception as exc:  # noqa: BLE001  # pragma: no cover
        logger.warning("redis_close_failed", error=str(exc))

    if app.state.clickhouse is not None:
        try:
            await app.state.clickhouse.close()
        except Exception as exc:  # noqa: BLE001  # pragma: no cover
            logger.warning("clickhouse_close_failed", error=str(exc))

    logger.info("lifespan_shutdown")


def create_app() -> FastAPI:
    """FastAPI 앱 생성 + 라우터/미들웨어/관측성 구성."""
    settings: Settings = get_settings()

    app = FastAPI(
        title="GenAI Labs Backend",
        description="Langfuse v3 기반 LLM 프롬프트 실험/평가 워크플로우 백엔드",
        version=__version__,
        lifespan=lifespan,
    )

    # 관측성 (반드시 라우터 추가 전에 호출)
    setup_observability(app, settings)

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.LABS_CORS_ORIGINS),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 예외 핸들러
    register_exception_handlers(app)

    # 라우터
    app.include_router(health_router.router, prefix="/api/v1")
    app.include_router(prompts_router.router, prefix="/api/v1")
    app.include_router(projects_router.router, prefix="/api/v1")
    app.include_router(models_router.router, prefix="/api/v1")
    app.include_router(search_router.router, prefix="/api/v1")
    app.include_router(notifications_router.router, prefix="/api/v1")
    app.include_router(datasets_router.router, prefix="/api/v1")
    app.include_router(tests_router.router, prefix="/api/v1")
    app.include_router(experiments_router.router, prefix="/api/v1")

    return app


app = create_app()
