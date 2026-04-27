"""JWT 검증 + RBAC 단위 테스트.

자체적으로 RSA 키 쌍을 생성하여 JWT를 발행/검증한다. Phase 0 fixture에 의존하지 않는다.

검증 항목:
- 정상 토큰 → ``User`` 매핑
- 만료 토큰 → ``AuthError``
- 잘못된 audience/issuer → ``AuthError``
- ``roles``/``groups`` 클레임 둘 다 지원
- ``require_role`` 권한 비교
"""

from __future__ import annotations

import time
from typing import Any

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.errors import AuthError, ForbiddenError
from app.core.security import (
    JWKSValidator,
    _claims_to_user,
    _extract_role,
    require_role,
    set_jwks_validator,
)
from app.models.auth import User


# ---------- 키 / 토큰 헬퍼 ----------
def _rsa_keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """테스트용 RSA-2048 키 쌍 + JWK 딕셔너리 반환."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_numbers = private_key.public_key().public_numbers()

    def _b64uint(n: int) -> str:
        import base64

        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return base64.urlsafe_b64encode(b).rstrip(b"=").decode()

    jwk = {
        "kty": "RSA",
        "kid": "test-key-1",
        "use": "sig",
        "alg": "RS256",
        "n": _b64uint(public_numbers.n),
        "e": _b64uint(public_numbers.e),
    }
    return private_key, jwk


def _make_token(
    private_key: rsa.RSAPrivateKey,
    *,
    sub: str = "user-1",
    aud: str = "labs",
    iss: str = "https://auth.internal.example.com",
    roles: list[str] | None = None,
    groups: list[str] | None = None,
    exp_offset: int = 3600,
    extra: dict[str, Any] | None = None,
) -> str:
    """RSA 비밀키로 RS256 JWT 발행."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "iat": now,
        "exp": now + exp_offset,
    }
    if roles is not None:
        payload["roles"] = roles
    if groups is not None:
        payload["groups"] = groups
    if extra:
        payload.update(extra)

    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return pyjwt.encode(
        payload, pem, algorithm="RS256", headers={"kid": "test-key-1"}
    )


@pytest.fixture
def keypair() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    """테스트당 한 번 RSA 키 쌍 생성."""
    return _rsa_keypair()


@pytest.fixture
def validator(
    keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
) -> JWKSValidator:
    """JWKS endpoint를 stub fetcher로 대체한 ``JWKSValidator``."""
    _, jwk = keypair

    async def _stub_fetch(_url: str) -> dict[str, Any]:
        return {"keys": [jwk]}

    v = JWKSValidator(
        jwks_url="https://stub/jwks",
        audience="labs",
        issuer="https://auth.internal.example.com",
        algorithms=["RS256"],
        fetcher=_stub_fetch,
    )
    set_jwks_validator(v)
    return v


# ---------- 검증 테스트 ----------
@pytest.mark.unit
class TestJWKSValidator:
    """JWKSValidator 핵심 동작."""

    async def test_valid_token_returns_claims(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        validator: JWKSValidator,
    ) -> None:
        """정상 토큰은 claims dict 반환."""
        priv, _ = keypair
        token = _make_token(priv, sub="alice", roles=["user"])
        claims = await validator.verify(token)
        assert claims["sub"] == "alice"
        assert claims["roles"] == ["user"]

    async def test_expired_token_raises_autherror(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        validator: JWKSValidator,
    ) -> None:
        """만료 토큰은 ``AuthError``."""
        priv, _ = keypair
        token = _make_token(priv, exp_offset=-10)  # 이미 만료됨
        with pytest.raises(AuthError):
            await validator.verify(token)

    async def test_wrong_audience_raises_autherror(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        validator: JWKSValidator,
    ) -> None:
        """audience 불일치 시 ``AuthError``."""
        priv, _ = keypair
        token = _make_token(priv, aud="other-service")
        with pytest.raises(AuthError):
            await validator.verify(token)

    async def test_wrong_issuer_raises_autherror(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
        validator: JWKSValidator,
    ) -> None:
        """issuer 불일치 시 ``AuthError``."""
        priv, _ = keypair
        token = _make_token(priv, iss="https://malicious.example.com")
        with pytest.raises(AuthError):
            await validator.verify(token)

    async def test_jwks_caches(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
    ) -> None:
        """JWKS는 캐시됨 — 첫 호출 후 fetcher가 다시 호출되지 않음."""
        _, jwk = keypair
        call_count = 0

        async def _counting_fetch(_url: str) -> dict[str, Any]:
            nonlocal call_count
            call_count += 1
            return {"keys": [jwk]}

        v = JWKSValidator(
            jwks_url="https://stub/jwks",
            audience="labs",
            issuer="https://auth.internal.example.com",
            algorithms=["RS256"],
            fetcher=_counting_fetch,
        )
        priv, _ = keypair
        token = _make_token(priv)

        await v.verify(token)
        await v.verify(token)
        await v.verify(token)
        assert call_count == 1

    async def test_unknown_kid_raises(
        self,
        keypair: tuple[rsa.RSAPrivateKey, dict[str, Any]],
    ) -> None:
        """매치되지 않는 kid는 ``AuthError``."""
        _, jwk = keypair

        # JWKS는 다른 키만 등록
        other_jwk = dict(jwk)
        other_jwk["kid"] = "other-key"

        async def _fetch(_url: str) -> dict[str, Any]:
            return {"keys": [other_jwk]}

        v = JWKSValidator(
            jwks_url="https://stub/jwks",
            audience="labs",
            issuer="https://auth.internal.example.com",
            algorithms=["RS256"],
            fetcher=_fetch,
        )
        priv, _ = keypair
        token = _make_token(priv)
        with pytest.raises(AuthError):
            await v.verify(token)


# ---------- RBAC ----------
@pytest.mark.unit
class TestRoleExtraction:
    """roles / groups 클레임에서 RBAC 역할 추출."""

    def test_admin_in_roles(self) -> None:
        """``roles=['admin']`` → admin."""
        assert _extract_role({"roles": ["admin"]}) == "admin"

    def test_user_in_groups(self) -> None:
        """``groups=['user']`` → user."""
        assert _extract_role({"groups": ["user"]}) == "user"

    def test_admin_priority_over_user(self) -> None:
        """admin > user 우선순위."""
        assert _extract_role({"roles": ["user", "admin"]}) == "admin"

    def test_unknown_role_falls_back_to_viewer(self) -> None:
        """알 수 없는 값은 viewer로 fallback."""
        assert _extract_role({"roles": ["unknown_role"]}) == "viewer"

    def test_no_role_claim_default_viewer(self) -> None:
        """역할 클레임이 없으면 viewer."""
        assert _extract_role({"sub": "anyone"}) == "viewer"

    def test_string_role_claim(self) -> None:
        """``role`` 클레임이 string인 경우도 인식."""
        assert _extract_role({"role": "admin"}) == "admin"


@pytest.mark.unit
class TestRequireRole:
    """require_role 의존성 동작."""

    def test_admin_passes_admin_check(self) -> None:
        """admin 사용자는 admin 체크 통과."""
        admin = User(id="a", email="a@x.com", role="admin")
        checker = require_role("admin")
        assert checker(current_user=admin) is admin

    def test_admin_passes_viewer_check(self) -> None:
        """admin은 viewer 권한도 보유."""
        admin = User(id="a", email="a@x.com", role="admin")
        checker = require_role("viewer")
        assert checker(current_user=admin) is admin

    def test_viewer_fails_admin_check(self) -> None:
        """viewer는 admin 권한 부족 → ForbiddenError."""
        viewer = User(id="v", email="v@x.com", role="viewer")
        checker = require_role("admin")
        with pytest.raises(ForbiddenError):
            checker(current_user=viewer)

    def test_user_fails_admin_check(self) -> None:
        """user는 admin 권한 부족."""
        user = User(id="u", email="u@x.com", role="user")
        checker = require_role("admin")
        with pytest.raises(ForbiddenError):
            checker(current_user=user)

    def test_user_passes_user_check(self) -> None:
        """user는 user 체크 통과."""
        user = User(id="u", email="u@x.com", role="user")
        checker = require_role("user")
        assert checker(current_user=user) is user


# ---------- claims → User 매핑 ----------
@pytest.mark.unit
class TestClaimsToUser:
    """``_claims_to_user`` 변환."""

    def test_minimal_claims(self) -> None:
        """sub만 있는 최소 claims."""
        u = _claims_to_user({"sub": "alice"})
        assert u.id == "alice"
        assert u.role == "viewer"
        assert u.email is None

    def test_full_claims(self) -> None:
        """이메일/이름/그룹/역할 모두 있는 경우."""
        u = _claims_to_user(
            {
                "sub": "bob",
                "email": "bob@example.com",
                "name": "Bob",
                "roles": ["admin"],
                "groups": ["eng", "labs"],
            }
        )
        assert u.id == "bob"
        assert u.email == "bob@example.com"
        assert u.name == "Bob"
        assert u.role == "admin"
        assert u.groups == ["eng", "labs"]

    def test_missing_sub_raises(self) -> None:
        """sub가 없으면 AuthError."""
        with pytest.raises(AuthError):
            _claims_to_user({"email": "no-sub@x.com"})
