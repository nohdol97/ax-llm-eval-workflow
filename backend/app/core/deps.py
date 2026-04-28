"""FastAPI 의존성 주입.

``app.state``에 lifespan에서 attach한 클라이언트 인스턴스를 라우터에서 꺼내쓴다.
테스트에서는 ``app.dependency_overrides[...]``로 주입을 교체할 수 있다.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import Depends, Request

from app.core.config import Settings, get_settings
from app.models.project import ProjectConfig
from app.services.clickhouse_client import (
    ClickHouseClient,
    LangfusePublicAPIFallbackClient,
)
from app.services.context_engine import ContextEngine
from app.services.langfuse_client import LangfuseClient
from app.services.litellm_client import LiteLLMClient
from app.services.redis_client import RedisClient
from app.services.single_test_runner import SingleTestRunner


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


# ---------- Context Engine / Single Test Runner ----------
@lru_cache(maxsize=1)
def get_context_engine() -> ContextEngine:
    """프로세스 단위 ``ContextEngine`` 싱글턴 (무상태)."""
    return ContextEngine()


def get_single_test_runner(
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    context_engine: ContextEngine = Depends(get_context_engine),
) -> SingleTestRunner:
    """``SingleTestRunner`` 의존성 — 요청마다 가벼운 인스턴스 생성."""
    return SingleTestRunner(
        langfuse=langfuse,
        litellm=litellm,
        context_engine=context_engine,
    )


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


# ---------- 실험 조회/제어 (Phase 4) ----------
def get_experiment_control(
    redis: RedisClient = Depends(get_redis_client),
) -> Any:
    """``ExperimentControl`` 의존성. lazy import로 순환 회피."""
    from app.services.experiment_control import ExperimentControl

    return ExperimentControl(redis=redis)


def get_experiment_query(
    redis: RedisClient = Depends(get_redis_client),
    langfuse: LangfuseClient = Depends(get_langfuse_client),
) -> Any:
    """``ExperimentQuery`` 의존성. lazy import로 순환 회피."""
    from app.services.experiment_query import ExperimentQuery

    return ExperimentQuery(redis=redis, langfuse=langfuse)


# ---------- 배치 실험 실행기 (Phase 4) ----------
def get_batch_runner(
    request: Request,
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
    redis: RedisClient = Depends(get_redis_client),
    context_engine: ContextEngine = Depends(get_context_engine),
) -> Any:
    """``BatchExperimentRunner`` 의존성.

    실 환경: ``app.state``의 클라이언트 + ``ContextEngine`` 싱글턴 합성.
    테스트: ``app.dependency_overrides[get_batch_runner]``로 mock 주입.

    Phase 8-A: ``app.state.trace_fetcher`` 가 있으면 주입 (mode=trace_eval에서 사용).
    """
    from app.services.batch_runner import BatchExperimentRunner
    from app.services.evaluator_governance import EvaluatorGovernanceService

    pipeline = _build_evaluation_pipeline(langfuse, litellm)
    governance = EvaluatorGovernanceService(redis=redis)
    trace_fetcher = getattr(request.app.state, "trace_fetcher", None)
    return BatchExperimentRunner(
        langfuse=langfuse,
        litellm=litellm,
        redis=redis,
        context_engine=context_engine,
        evaluation_pipeline=pipeline,
        governance=governance,
        trace_fetcher=trace_fetcher,
    )


# ---------- Evaluation Pipeline (Phase 5) ----------
def _build_evaluation_pipeline(
    langfuse: LangfuseClient,
    litellm: LiteLLMClient,
) -> Any:
    """``EvaluationPipeline`` 인스턴스 생성 — lazy import로 순환 회피."""
    from app.evaluators.pipeline import EvaluationPipeline

    return EvaluationPipeline(langfuse=langfuse, litellm_client=litellm)


def get_evaluation_pipeline(
    langfuse: LangfuseClient = Depends(get_langfuse_client),
    litellm: LiteLLMClient = Depends(get_litellm_client),
) -> Any:
    """``EvaluationPipeline`` FastAPI 의존성 — Phase 5 evaluator 통합.

    각 요청마다 가벼운 인스턴스를 생성한다 (state는 langfuse/litellm 위임).
    """
    return _build_evaluation_pipeline(langfuse, litellm)


def get_governance_service(
    redis: RedisClient = Depends(get_redis_client),
) -> Any:
    """``EvaluatorGovernanceService`` 의존성 — Phase 5 거버넌스."""
    from app.services.evaluator_governance import EvaluatorGovernanceService

    return EvaluatorGovernanceService(redis=redis)


# ---------- Phase 8-A-1 — Trace Fetcher ----------
def get_trace_fetcher(request: Request) -> Any:
    """``TraceFetcher`` 의존성.

    - ``app.state.clickhouse`` 가 ``None`` 이면 자동으로 폴백 모드로 진입한다
      (``LangfuseClient`` SDK 사용).
    - ``Settings.USE_LANGFUSE_PUBLIC_API_FALLBACK`` 이 True면 ClickHouse가 있어도
      폴백 모드 강제.
    """
    from app.services.trace_fetcher import TraceFetcher

    settings = get_settings()
    langfuse = getattr(request.app.state, "langfuse", None)
    if langfuse is None:
        raise RuntimeError("LangfuseClient가 app.state에 초기화되지 않았습니다.")
    clickhouse = getattr(request.app.state, "clickhouse", None)
    return TraceFetcher(
        clickhouse=clickhouse,
        langfuse=langfuse,
        use_fallback=settings.USE_LANGFUSE_PUBLIC_API_FALLBACK,
    )


# ---------- Phase 6 — 분석 서비스 ----------
def get_analysis_service(request: Request) -> Any:
    """``AnalysisService`` 의존성.

    ``app.state.clickhouse`` 가 ``None`` 이면 503 ``service_unavailable`` 반환.
    그 외 직접 모드/폴백 모드 모두 동일한 ``query(sql, parameters)`` 인터페이스를
    사용하므로 ``AnalysisService`` 가 그대로 받는다.
    """
    from fastapi import HTTPException, status

    from app.services.analysis_service import AnalysisService

    clickhouse = getattr(request.app.state, "clickhouse", None)
    if clickhouse is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "ClickHouse 미설정 — 분석 API를 사용하려면 CLICKHOUSE_HOST 또는"
                " USE_LANGFUSE_PUBLIC_API_FALLBACK 설정이 필요합니다."
            ),
        )
    return AnalysisService(clickhouse=clickhouse)
