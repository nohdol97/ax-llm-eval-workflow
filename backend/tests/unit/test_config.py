"""Settings 단위 테스트.

핵심 검증:
- 모든 필드의 graceful 기본값
- 환경변수 오버라이드 동작
- ``SecretStr`` 비밀 노출 방지
- 파생 프로퍼티(``langfuse_configured`` 등)
"""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import Settings, get_settings


@pytest.mark.unit
class TestSettingsDefaults:
    """기본값 — 사내 endpoint 미설정 시 graceful 부팅."""

    def test_default_env_is_dev(self) -> None:
        """기본 LABS_ENV는 ``dev``."""
        s = Settings()
        assert s.LABS_ENV == "dev"
        assert s.is_production is False

    def test_default_log_format_is_json(self) -> None:
        """기본 로그 포맷은 ``json`` (Loki 호환)."""
        s = Settings()
        assert s.LABS_LOG_FORMAT == "json"

    def test_secrets_default_empty_secretstr(self) -> None:
        """비밀 필드는 모두 빈 ``SecretStr``."""
        s = Settings()
        assert isinstance(s.LANGFUSE_SECRET_KEY, SecretStr)
        assert s.LANGFUSE_SECRET_KEY.get_secret_value() == ""
        assert isinstance(s.LITELLM_VIRTUAL_KEY, SecretStr)
        assert s.LITELLM_VIRTUAL_KEY.get_secret_value() == ""
        assert isinstance(s.CLICKHOUSE_READONLY_PASSWORD, SecretStr)

    def test_health_check_timeout_is_3_seconds(self) -> None:
        """헬스 체크 타임아웃 기본 3초."""
        s = Settings()
        assert s.LABS_HEALTH_CHECK_TIMEOUT_SEC == pytest.approx(3.0)

    def test_clickhouse_default_secure(self) -> None:
        """ClickHouse 기본 TLS=True / port=8443."""
        s = Settings()
        assert s.CLICKHOUSE_SECURE is True
        assert s.CLICKHOUSE_PORT == 8443

    def test_default_jwt_algorithms_rs256(self) -> None:
        """기본 JWT 알고리즘은 RS256만."""
        s = Settings()
        assert s.AUTH_JWT_ALGORITHMS == ["RS256"]

    def test_default_cors_localhost_3000(self) -> None:
        """기본 CORS origin은 frontend dev 서버(3000)."""
        s = Settings()
        assert "http://localhost:3000" in s.LABS_CORS_ORIGINS


@pytest.mark.unit
class TestSettingsDerivedProperties:
    """파생 프로퍼티 — 자격증명 설정 여부 판단."""

    def test_langfuse_not_configured_by_default(self) -> None:
        """기본 상태에서 Langfuse는 미설정."""
        s = Settings()
        assert s.langfuse_configured is False

    def test_langfuse_configured_with_both_keys(self) -> None:
        """public + secret key 둘 다 있으면 configured."""
        s = Settings(
            LANGFUSE_PUBLIC_KEY="pk-xxx",
            LANGFUSE_SECRET_KEY=SecretStr("sk-xxx"),
        )
        assert s.langfuse_configured is True

    def test_langfuse_not_configured_with_only_public(self) -> None:
        """public만 있으면 미설정."""
        s = Settings(LANGFUSE_PUBLIC_KEY="pk-xxx")
        assert s.langfuse_configured is False

    def test_litellm_configured_with_virtual_key(self) -> None:
        """Virtual Key가 있으면 configured."""
        s = Settings(LITELLM_VIRTUAL_KEY=SecretStr("vk-xxx"))
        assert s.litellm_configured is True

    def test_clickhouse_configured_requires_host_and_user(self) -> None:
        """ClickHouse는 host + readonly user 둘 다 필요."""
        s_partial = Settings(CLICKHOUSE_HOST="ch.internal")
        assert s_partial.clickhouse_configured is False
        s_full = Settings(
            CLICKHOUSE_HOST="ch.internal",
            CLICKHOUSE_READONLY_USER="ro_user",
        )
        assert s_full.clickhouse_configured is True

    def test_is_production_only_for_prod(self) -> None:
        """is_production은 LABS_ENV='prod'일 때만 True."""
        for env, expected in [
            ("dev", False),
            ("staging", False),
            ("demo", False),
            ("prod", True),
        ]:
            s = Settings(LABS_ENV=env)  # type: ignore[arg-type]
            assert s.is_production is expected, f"env={env}"


@pytest.mark.unit
class TestSettingsEnvOverride:
    """환경변수 오버라이드."""

    def test_env_var_overrides_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``LABS_ENV`` 환경변수가 기본값을 덮어쓴다."""
        monkeypatch.setenv("LABS_ENV", "staging")
        s = Settings()
        assert s.LABS_ENV == "staging"

    def test_env_var_overrides_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``LITELLM_VIRTUAL_KEY`` 환경변수 → SecretStr."""
        monkeypatch.setenv("LITELLM_VIRTUAL_KEY", "sk-litellm-test")
        s = Settings()
        assert s.LITELLM_VIRTUAL_KEY.get_secret_value() == "sk-litellm-test"
        # repr에 비밀이 노출되지 않아야 함
        assert "sk-litellm-test" not in repr(s.LITELLM_VIRTUAL_KEY)

    def test_get_settings_caches(self) -> None:
        """get_settings()는 lru_cache — 동일 인스턴스 반환."""
        get_settings.cache_clear()
        s1 = get_settings()
        s2 = get_settings()
        assert s1 is s2

    def test_get_settings_cache_clear(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cache_clear() 후엔 새 인스턴스가 환경변수를 다시 읽음."""
        get_settings.cache_clear()
        monkeypatch.setenv("LABS_ENV", "demo")
        s = get_settings()
        assert s.LABS_ENV == "demo"
        get_settings.cache_clear()


@pytest.mark.unit
class TestSettingsExtraIgnored:
    """알 수 없는 환경변수는 무시 (extra='ignore')."""

    def test_unknown_env_var_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """관계 없는 환경변수가 있어도 부팅 가능."""
        monkeypatch.setenv("SOME_UNRELATED_VAR", "value")
        # raise 안 나면 통과
        s = Settings()
        assert s.LABS_ENV == "dev"
