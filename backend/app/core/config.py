"""애플리케이션 설정 (pydantic-settings).

환경변수에서 설정을 로드하며, 사내 endpoint(Langfuse, LiteLLM, ClickHouse,
Prometheus, OTel, Loki) 등은 모두 graceful 기본값을 가진다. 비밀은 ``SecretStr``로
래핑하여 로깅/repr 노출을 방지한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

LabsEnv = Literal["dev", "staging", "demo", "prod"]
LogFormat = Literal["json", "console"]


class Settings(BaseSettings):
    """본 프로젝트 환경 설정.

    모든 필드는 환경변수에서 주입되며, 미설정 시에도 부팅 가능한 기본값을 가진다.
    실제 외부 호출은 endpoint가 비어 있으면 graceful warn을 반환한다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ---------- 메타 ----------
    LABS_ENV: LabsEnv = Field(default="dev", description="실행 환경")
    LABS_LOG_LEVEL: str = Field(default="INFO", description="structlog 로그 레벨")
    LABS_LOG_FORMAT: LogFormat = Field(default="json", description="로그 포맷")

    # ---------- 사내 Langfuse ----------
    LANGFUSE_HOST: str = Field(
        default="https://langfuse.internal.example.com",
        description="사내 Langfuse host URL",
    )
    LANGFUSE_PUBLIC_KEY: str = Field(default="", description="Langfuse public key")
    LANGFUSE_SECRET_KEY: SecretStr = Field(
        default=SecretStr(""), description="Langfuse secret key"
    )

    # ---------- 사내 LiteLLM ----------
    LITELLM_BASE_URL: str = Field(
        default="https://litellm.internal.example.com",
        description="사내 LiteLLM Proxy base URL",
    )
    LITELLM_VIRTUAL_KEY: SecretStr = Field(
        default=SecretStr(""), description="LiteLLM Virtual Key"
    )

    # ---------- ClickHouse (선택, fallback 모드 지원) ----------
    USE_LANGFUSE_PUBLIC_API_FALLBACK: bool = Field(
        default=False,
        description="True면 ClickHouse 직접 쿼리 대신 Langfuse public API 사용",
    )
    CLICKHOUSE_HOST: str = Field(default="", description="ClickHouse host")
    CLICKHOUSE_PORT: int = Field(default=8443, description="ClickHouse port (HTTPS)")
    CLICKHOUSE_SECURE: bool = Field(default=True, description="ClickHouse TLS 사용")
    CLICKHOUSE_DB: str = Field(default="langfuse", description="ClickHouse 데이터베이스명")
    CLICKHOUSE_READONLY_USER: str = Field(
        default="", description="ClickHouse readonly 계정"
    )
    CLICKHOUSE_READONLY_PASSWORD: SecretStr = Field(
        default=SecretStr(""), description="ClickHouse readonly 비밀번호"
    )

    # ---------- Redis ----------
    REDIS_URL: str = Field(
        default="redis://localhost:6379/0", description="Redis 접속 URL"
    )
    LABS_REDIS_DB: int = Field(default=0, description="Redis DB 번호")

    # ---------- JWT (사내 Auth) ----------
    AUTH_JWKS_URL: str = Field(default="", description="JWKS endpoint URL")
    AUTH_JWT_AUDIENCE: str = Field(default="labs", description="JWT audience")
    AUTH_JWT_ISSUER: str = Field(default="", description="JWT issuer")
    AUTH_JWT_ALGORITHMS: list[str] = Field(
        default_factory=lambda: ["RS256"], description="허용 JWT 서명 알고리즘"
    )

    # ---------- OpenTelemetry ----------
    OTEL_EXPORTER_OTLP_ENDPOINT: str = Field(
        default="", description="OTLP/HTTP exporter endpoint"
    )
    OTEL_EXPORTER_OTLP_HEADERS: str = Field(
        default="", description="OTLP exporter 추가 헤더 (key=value,...)"
    )
    OTEL_SERVICE_NAME: str = Field(
        default="ax-llm-eval-workflow-backend", description="OTel service name"
    )
    OTEL_RESOURCE_ATTRIBUTES: str = Field(
        default="service.namespace=labs",
        description="OTel resource attributes (k=v 컴마 구분)",
    )
    OTEL_TRACES_SAMPLER: str = Field(
        default="parentbased_traceidratio", description="OTel sampler 이름"
    )
    OTEL_TRACES_SAMPLER_ARG: float = Field(
        default=0.1, description="OTel sampler ratio (0.0~1.0)"
    )

    # ---------- Prometheus ----------
    LABS_METRICS_ENABLED: bool = Field(
        default=True, description="Prometheus /metrics 엔드포인트 노출"
    )
    LABS_METRICS_PATH: str = Field(default="/metrics", description="metrics 엔드포인트 경로")
    PROMETHEUS_QUERY_URL: str = Field(
        default="", description="사내 Prometheus query URL (헬스체크용)"
    )

    # ---------- Loki ----------
    LABS_LOG_LOKI_LABELS: str = Field(
        default="service=ax-llm-eval-workflow-backend",
        description="Loki 라벨 (k=v 컴마 구분, stdout 자체 점검용)",
    )

    # ---------- 동작 ----------
    LABS_SHUTDOWN_GRACE_SEC: int = Field(
        default=30, description="graceful shutdown 대기 시간"
    )
    LABS_EXPERIMENT_STATE_TTL: int = Field(
        default=86400, description="실험 상태 Redis TTL (초)"
    )
    LABS_HEALTH_CHECK_TIMEOUT_SEC: float = Field(
        default=3.0, description="헬스 체크 타임아웃 (초)"
    )

    # ---------- CORS ----------
    LABS_CORS_ORIGINS: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"],
        description="허용 CORS origin 목록",
    )

    # ---------- 멀티 프로젝트 (IMPLEMENTATION.md §3) ----------
    LABS_PROJECTS_JSON: str = Field(
        default="",
        description=(
            "프로젝트 정의 JSON 배열. "
            "각 항목은 `{id, name, description?, langfuse_host?, "
            "langfuse_public_key?, langfuse_secret_key?}`. "
            "비어 있으면 단일 기본 프로젝트(`default`) 1건이 자동 등록된다."
        ),
    )

    # ---------- 파생 ----------
    @property
    def is_production(self) -> bool:
        """prod 환경 여부."""
        return self.LABS_ENV == "prod"

    @property
    def langfuse_configured(self) -> bool:
        """Langfuse 자격증명 설정 여부."""
        return bool(self.LANGFUSE_PUBLIC_KEY) and bool(
            self.LANGFUSE_SECRET_KEY.get_secret_value()
        )

    @property
    def litellm_configured(self) -> bool:
        """LiteLLM 자격증명 설정 여부."""
        return bool(self.LITELLM_VIRTUAL_KEY.get_secret_value())

    @property
    def clickhouse_configured(self) -> bool:
        """ClickHouse 직접 모드 사용 가능 여부."""
        return bool(self.CLICKHOUSE_HOST) and bool(self.CLICKHOUSE_READONLY_USER)

    # ---------- 프로젝트 카탈로그 ----------
    def projects(self) -> list[dict[str, Any]]:
        """``LABS_PROJECTS_JSON``을 파싱하여 dict 리스트로 반환.

        - 미설정 시 단일 기본 프로젝트(``default``) 1건을 반환한다.
        - 잘못된 JSON 또는 list가 아닌 형태는 ``ValueError``로 raise하여
          기동 시점에 명시적으로 실패시킨다.
        """
        raw = (self.LABS_PROJECTS_JSON or "").strip()
        if not raw:
            return [
                {
                    "id": "default",
                    "name": "Default Project",
                    "description": "Default project (no LABS_PROJECTS_JSON configured)",
                    "langfuse_host": self.LANGFUSE_HOST or None,
                    "langfuse_public_key": self.LANGFUSE_PUBLIC_KEY or None,
                    "langfuse_secret_key": (
                        self.LANGFUSE_SECRET_KEY.get_secret_value()
                        if self.LANGFUSE_SECRET_KEY.get_secret_value()
                        else None
                    ),
                }
            ]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"LABS_PROJECTS_JSON 파싱 실패: {exc}"
            ) from exc
        if not isinstance(parsed, list):
            raise ValueError("LABS_PROJECTS_JSON은 JSON 배열이어야 합니다.")
        result: list[dict[str, Any]] = []
        for entry in parsed:
            if not isinstance(entry, dict):
                raise ValueError(
                    "LABS_PROJECTS_JSON 각 항목은 객체(dict)여야 합니다."
                )
            result.append(dict(entry))
        return result


@lru_cache
def get_settings() -> Settings:
    """프로세스 단위 캐시된 설정 인스턴스 반환.

    환경변수 변경 시 ``get_settings.cache_clear()``를 호출해야 한다.
    """
    return Settings()
