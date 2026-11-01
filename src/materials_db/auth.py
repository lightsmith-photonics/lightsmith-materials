"""Bearer JWTs issued by the web platform (HS256, shared secret ``PLATFORM_JWT_SECRET``).

Claims used: ``sub`` (platform user id, required), ``name``, ``role``
(``user`` | ``moderator`` | ``admin``). ``exp`` is enforced when present. When
the token carries ``aud`` it must include ``PLATFORM_JWT_AUDIENCE`` (default
``materials-db``); ``iss`` is checked only if ``PLATFORM_JWT_ISSUER`` is set.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt

DEFAULT_AUDIENCE = "materials-db"
MODERATOR_ROLES = ("moderator", "admin")


class AuthError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    sub: str
    name: str | None
    role: str

    @property
    def is_moderator(self) -> bool:
        return self.role in MODERATOR_ROLES


def decode_token(token: str, secret: str, *, audience: str | None = None, issuer: str | None = None) -> Principal:
    try:
        claims = jwt.decode(token, secret, algorithms=["HS256"], leeway=30,
                            options={"verify_aud": False, "require": ["sub"]})
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid token: {e}") from None
    aud = claims.get("aud")
    if aud is not None:
        auds = [aud] if isinstance(aud, str) else list(aud)
        if (audience or DEFAULT_AUDIENCE) not in auds:
            raise AuthError(f"token audience {auds} does not include {audience or DEFAULT_AUDIENCE!r}")
    if issuer and claims.get("iss") != issuer:
        raise AuthError(f"token issuer {claims.get('iss')!r} is not {issuer!r}")
    role = str(claims.get("role") or "user")
    return Principal(sub=str(claims["sub"]), name=claims.get("name"), role=role)


def make_token(secret: str, *, sub: str, name: str | None = None, role: str = "user", ttl: int = 3600,
               audience: str | None = DEFAULT_AUDIENCE, issuer: str | None = None) -> str:
    """Mint a token the way the web platform does (for development and tests)."""
    now = int(time.time())
    claims = {"sub": sub, "name": name, "role": role, "iat": now, "exp": now + ttl}
    if audience:
        claims["aud"] = audience
    if issuer:
        claims["iss"] = issuer
    return jwt.encode(claims, secret, algorithm="HS256")
