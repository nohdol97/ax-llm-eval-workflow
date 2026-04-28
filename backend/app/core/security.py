"""JWKS 기반 JWT 검증 + RBAC.

사내 Auth 서비스가 발급한 JWT를 본 백엔드에서 검증한다. 키는 ``AUTH_JWKS_URL``의
JWKS endpoint에서 fetch하여 인메모리 캐시(TTL 1시간)한다.

- ``JWKSValidator``: 키 fetch + 캐시 + 토큰 검증 로직
- ``verify_jwt(token)``: 토큰 → claims dict (예외 시 ``HTTPException(401)``)
- ``get_current_user``: FastAPI 의존성. ``Authorization: Bearer ...`` 헤더 파싱
- ``require_role(role)``: 의존성 팩토리. 부족 시 ``HTTPException(403)``
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, cast

import httpx
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import Settings, get_settings
from app.core.errors import AuthError, ForbiddenError
from app.models.auth import RBACRole, User

# JWKS 캐시 TTL (초)
JWKS_CACHE_TTL_SEC = 3600

# RBAC 클레임 후보 (둘 다 지원)
_ROLE_CLAIMS = ("roles", "groups", "labs_role", "role")
_VALID_ROLES: tuple[RBACRole, ...] = ("admin", "user", "viewer")


# ---------- JWKSValidator ----------
class JWKSValidator:
    """JWKS endpoint 기반 JWT 검증기.

    - 키 캐시 TTL: 1시간
    - 알고리즘: 환경변수 ``AUTH_JWT_ALGORITHMS`` (기본 ``RS256``)
    - 검증 항목: 서명, ``exp``, ``iss``, ``aud``
    - ``fetcher`` 인자로 httpx 클라이언트 주입 가능 (테스트용)
    """

    def __init__(
        self,
        jwks_url: str,
        *,
        audience: str,
        issuer: str,
        algorithms: list[str],
        cache_ttl: int = JWKS_CACHE_TTL_SEC,
        fetcher: Callable[[str], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.jwks_url = jwks_url
        self.audience = audience
        self.issuer = issuer
        self.algorithms = algorithms
        self.cache_ttl = cache_ttl
        self._fetcher = fetcher
        self._cache_keys: dict[str, Any] | None = None
        self._cache_expires_at: float = 0.0

    async def _fetch_jwks(self) -> dict[str, Any]:
        """JWKS endpoint 호출. ``fetcher`` 주입 시 그것을 사용."""
        if self._fetcher is not None:
            return await self._fetcher(self.jwks_url)
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(self.jwks_url)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def _get_jwks(self) -> dict[str, Any]:
        """캐시된 JWKS 반환 (만료 시 재fetch)."""
        now = time.monotonic()
        if self._cache_keys is None or now >= self._cache_expires_at:
            self._cache_keys = await self._fetch_jwks()
            self._cache_expires_at = now + self.cache_ttl
        return self._cache_keys

    async def verify(self, token: str) -> dict[str, Any]:
        """JWT 토큰 검증 후 claims 반환. 실패 시 ``AuthError``."""
        if not self.jwks_url:
            raise AuthError(detail="JWKS URL이 설정되지 않았습니다.")

        try:
            unverified_header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(detail=f"잘못된 토큰 헤더: {exc}") from exc

        kid = unverified_header.get("kid")
        if not kid:
            raise AuthError(detail="토큰에 kid가 없습니다.")

        jwks = await self._get_jwks()
        signing_key = self._select_key(jwks, kid)
        if signing_key is None:
            # kid 매치 실패 → 캐시를 무효화하고 한 번 더 시도
            self._cache_keys = None
            jwks = await self._get_jwks()
            signing_key = self._select_key(jwks, kid)
        if signing_key is None:
            raise AuthError(detail=f"kid={kid!r}에 매치되는 키 없음.")

        try:
            claims = jwt.decode(
                token,
                signing_key,
                algorithms=self.algorithms,
                audience=self.audience or None,
                issuer=self.issuer or None,
                options={
                    "require": ["exp"],
                    "verify_aud": bool(self.audience),
                    "verify_iss": bool(self.issuer),
                },
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthError(detail="토큰이 만료되었습니다.") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthError(detail=f"토큰 검증 실패: {exc}") from exc

        return cast(dict[str, Any], claims)

    @staticmethod
    def _select_key(jwks: dict[str, Any], kid: str) -> Any:
        """JWKS dict에서 ``kid``에 매치되는 검증 키를 반환."""
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                return jwt.PyJWK(key).key
        return None


# ---------- 글로벌 validator (lazy 초기화) ----------
_validator_instance: JWKSValidator | None = None


def get_jwks_validator(settings: Settings | None = None) -> JWKSValidator:
    """프로세스 단위 ``JWKSValidator`` 싱글턴."""
    global _validator_instance
    if _validator_instance is None:
        s = settings or get_settings()
        _validator_instance = JWKSValidator(
            jwks_url=s.AUTH_JWKS_URL,
            audience=s.AUTH_JWT_AUDIENCE,
            issuer=s.AUTH_JWT_ISSUER,
            algorithms=list(s.AUTH_JWT_ALGORITHMS),
        )
    return _validator_instance


def reset_jwks_validator() -> None:
    """싱글턴 리셋 (테스트용)."""
    global _validator_instance
    _validator_instance = None


def set_jwks_validator(validator: JWKSValidator) -> None:
    """싱글턴 강제 주입 (테스트/통합용)."""
    global _validator_instance
    _validator_instance = validator


# ---------- 토큰 검증 + User 매핑 ----------
async def verify_jwt(token: str) -> dict[str, Any]:
    """토큰 검증 후 raw claims 반환. 실패 시 ``AuthError``."""
    validator = get_jwks_validator()
    return await validator.verify(token)


def _extract_role(claims: dict[str, Any]) -> RBACRole:
    """claims에서 RBAC 역할 추출.

    ``roles`` 또는 ``groups``(list) 또는 ``labs_role``/``role``(str)을 우선순위로 검사.
    매치 실패 시 기본 ``viewer``.
    """
    found_roles: list[str] = []
    for claim in _ROLE_CLAIMS:
        value = claims.get(claim)
        if value is None:
            continue
        if isinstance(value, str):
            found_roles.append(value.lower())
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, str):
                    found_roles.append(v.lower())

    # 우선순위 admin > user > viewer
    for candidate in ("admin", "user", "viewer"):
        if candidate in found_roles:
            return cast(RBACRole, candidate)
    return "viewer"


def _claims_to_user(claims: dict[str, Any]) -> User:
    """claims dict → ``User`` 모델."""
    sub = claims.get("sub")
    if not sub:
        raise AuthError(detail="토큰에 sub 클레임이 없습니다.")
    role = _extract_role(claims)
    groups_claim = claims.get("groups", [])
    groups = [str(g) for g in groups_claim] if isinstance(groups_claim, list) else []
    return User(
        id=str(sub),
        email=claims.get("email"),
        role=role,
        name=claims.get("name"),
        groups=groups,
    )


# ---------- FastAPI 의존성 ----------
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> User:
    """``Authorization: Bearer ...`` 헤더에서 토큰 추출 → 검증 → User 반환."""
    if credentials is None or not credentials.credentials:
        raise AuthError(detail="Authorization 헤더가 없습니다.")
    if credentials.scheme.lower() != "bearer":
        raise AuthError(detail="Bearer 스킴이 아닙니다.")

    claims = await verify_jwt(credentials.credentials)
    user = _claims_to_user(claims)
    # request.state에 보관 — observability 등에서 사용
    request.state.user = user
    return user


def require_role(
    role: RBACRole,
) -> Callable[[User], User]:
    """RBAC 역할 가드. 의존성으로 사용.

    ``Depends(require_role("admin"))`` 형태로 라우터에 주입.
    """

    def _checker(current_user: User = Depends(get_current_user)) -> User:
        if not current_user.has_role(role):
            raise ForbiddenError(detail=f"권한 부족: required={role}, actual={current_user.role}")
        return current_user

    return _checker
