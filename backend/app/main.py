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
from app.api.v1 import analysis as analysis_router
from app.api.v1 import auto_eval as auto_eval_router
from app.api.v1 import datasets as datasets_router
from app.api.v1 import evaluators as evaluators_router
from app.api.v1 import experiments as experiments_router
from app.api.v1 import health as health_router
from app.api.v1 import models as models_router
from app.api.v1 import notifications as notifications_router
from app.api.v1 import projects as projects_router
from app.api.v1 import prompts as prompts_router
from app.api.v1 import reviews as reviews_router
from app.api.v1 import search as search_router
from app.api.v1 import tests as tests_router
from app.api.v1 import traces as traces_router
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

    # ---------- Auto-Eval Engine + Scheduler (Phase 8-B) ----------
    # 본 단계는 best-effort — repo/engine 미구현 시 graceful degradation 으로
    # 라우터는 동작하되 lifespan 백그라운드 task 만 비활성화된다.
    try:
        await _setup_auto_eval(app, settings)
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto_eval_lifespan_setup_failed", error=str(exc))

    yield

    # ---------- Auto-Eval Scheduler graceful shutdown ----------
    scheduler = getattr(app.state, "auto_eval_scheduler", None)
    if scheduler is not None:
        try:
            await scheduler.stop(timeout_sec=settings.LABS_SHUTDOWN_GRACE_SEC)
            logger.info("auto_eval_scheduler_stopped")
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_eval_scheduler_stop_failed", error=str(exc))

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
    app.include_router(evaluators_router.router, prefix="/api/v1")
    app.include_router(analysis_router.router, prefix="/api/v1")
    app.include_router(traces_router.router, prefix="/api/v1")
    app.include_router(auto_eval_router.router, prefix="/api/v1")
    app.include_router(reviews_router.router, prefix="/api/v1")

    return app


async def _setup_auto_eval(app: FastAPI, settings: Settings) -> None:
    """Auto-Eval Repo / Engine / Scheduler 를 ``app.state`` 에 부착한다.

    Phase 8-B-1 의 서비스 모듈이 없으면 ``ImportError`` 가 발생하며 호출자가
    swallow 하여 graceful 부팅이 보장된다 (라우터는 ``app.state.redis`` 만으로
    repo 를 즉석 생성 가능).
    """
    from app.evaluators.pipeline import EvaluationPipeline
    from app.services.auto_eval_engine import AutoEvalEngine
    from app.services.auto_eval_repo import AutoEvalRepo
    from app.services.trace_fetcher import TraceFetcher

    # Repo
    repo = AutoEvalRepo(redis=app.state.redis)
    app.state.auto_eval_repo = repo

    # TraceFetcher (lifespan 단계에서 한 번 생성)
    trace_fetcher = TraceFetcher(
        clickhouse=getattr(app.state, "clickhouse", None),
        langfuse=app.state.langfuse,
        use_fallback=settings.USE_LANGFUSE_PUBLIC_API_FALLBACK,
    )
    app.state.trace_fetcher = trace_fetcher

    # EvaluationPipeline
    pipeline = EvaluationPipeline(
        langfuse=app.state.langfuse,
        litellm_client=app.state.litellm,
    )

    # Review Queue (Phase 8-C-2) — engine 보다 먼저 만들어 주입
    from app.services.review_queue import ReviewQueueService  # noqa: WPS433

    review_queue = ReviewQueueService(redis=app.state.redis)
    app.state.review_queue = review_queue

    # Engine
    engine = AutoEvalEngine(
        repo=repo,
        trace_fetcher=trace_fetcher,
        pipeline=pipeline,
        langfuse=app.state.langfuse,
        redis=app.state.redis,
        review_queue=review_queue,
    )
    app.state.auto_eval_engine = engine

    # Scheduler (Phase 8-B-1) — lazy import + graceful skip 만약 미구현
    try:
        from app.services.auto_eval_scheduler import AutoEvalScheduler  # noqa: WPS433
    except ImportError as exc:
        logger.info(
            "auto_eval_scheduler_not_available",
            reason=str(exc),
            hint="Phase 8-B-1 미구현 — repo/engine 만 활성화",
        )
        app.state.auto_eval_scheduler = None
        return

    scheduler = AutoEvalScheduler(repo=repo, engine=engine, redis=app.state.redis)
    app.state.auto_eval_scheduler = scheduler
    await scheduler.start()
    logger.info("auto_eval_scheduler_started")


app = create_app()
