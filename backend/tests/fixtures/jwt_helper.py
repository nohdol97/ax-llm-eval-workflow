"""JWT 테스트 helper.

PyJWT + ``cryptography`` 라이브러리로 RS256 키페어 생성 및 JWT 발행.
JWKS endpoint mock(공개키 JSON)도 제공하여 ``security.py``의 JWKS 검증 흐름을 테스트할 수 있다.
"""

from __future__ import annotations

import base64
import time
import uuid
from typing import Any, Literal

import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey, RSAPublicKey

Role = Literal["admin", "user", "viewer"]


def _int_to_base64url(value: int) -> str:
    """정수를 base64url(no-pad) 문자열로 변환 (JWKS n/e 인코딩)."""
    byte_length = (value.bit_length() + 7) // 8
    raw = value.to_bytes(byte_length, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class JWTTestHelper:
    """RS256 JWT 발행 + JWKS endpoint mock helper.

    부팅 시 RSA 2048 키페어를 생성하고, ``create_token()``으로 역할별 JWT를 발행한다.
    공개키는 ``get_public_pem()`` / ``get_public_jwks()``로 노출되어
    Backend의 JWKS 기반 검증 코드를 테스트할 수 있다.
    """

    def __init__(
        self,
        issuer: str = "https://auth.test.local",
        audience: str = "labs",
        kid: str | None = None,
    ) -> None:
        self.issuer = issuer
        self.audience = audience
        self.kid = kid or "test-key-1"

        # RSA 2048 키페어 생성
        self._private_key: RSAPrivateKey = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self._public_key: RSAPublicKey = self._private_key.public_key()

    # ---------- 토큰 발행 ----------
    def create_token(
        self,
        role: Role,
        sub: str = "user-1",
        expires_in: int = 3600,
        **extra_claims: Any,
    ) -> str:
        """역할 기반 JWT 발행.

        Args:
            role: ``admin`` / ``user`` / ``viewer`` 중 하나
            sub: subject claim (사용자 식별자, dummy 권장)
            expires_in: 만료 시각 (초)
            **extra_claims: 추가 claim (kid 외)

        Returns:
            서명된 JWT 문자열 (RS256)
        """
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": self.issuer,
            "aud": self.audience,
            "sub": sub,
            "iat": now,
            "exp": now + expires_in,
            "nbf": now,
            "jti": str(uuid.uuid4()),
            "roles": [role],
        }
        payload.update(extra_claims)

        private_pem = self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )

        token = pyjwt.encode(
            payload,
            private_pem,
            algorithm="RS256",
            headers={"kid": self.kid, "alg": "RS256", "typ": "JWT"},
        )
        return token

    def create_expired_token(self, role: Role, sub: str = "user-1") -> str:
        """만료된 JWT 발행 (테스트용)."""
        return self.create_token(role=role, sub=sub, expires_in=-3600)

    # ---------- 공개키 노출 ----------
    def get_public_pem(self) -> str:
        """공개키 PEM 문자열."""
        pem_bytes = self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem_bytes.decode("utf-8")

    def get_public_jwks(self) -> dict[str, Any]:
        """JWKS JSON (RSA 공개키 한 개)."""
        numbers = self._public_key.public_numbers()
        return {
            "keys": [
                {
                    "kty": "RSA",
                    "use": "sig",
                    "alg": "RS256",
                    "kid": self.kid,
                    "n": _int_to_base64url(numbers.n),
                    "e": _int_to_base64url(numbers.e),
                }
            ]
        }
