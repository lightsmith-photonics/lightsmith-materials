"""FastAPI dependencies: settings, sessions, authentication, pagination."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..auth import AuthError, Principal, decode_token
from ..config import Settings


class ApiError(HTTPException):
    def __init__(self, status: int, error: str, message: str, detail=None):
        super().__init__(status_code=status, detail={"error": error, "message": message, "detail": detail})


def settings(request: Request) -> Settings:
    return request.app.state.settings


def session(request: Request) -> Iterator[Session]:
    with request.app.state.db.session() as s:
        yield s


def base_url(request: Request) -> str:
    return request.app.state.settings.public_base_url


def brand(request: Request) -> str:
    return request.app.state.settings.brand_name


def principal(request: Request, cfg: Settings = Depends(settings)) -> Principal:
    if not cfg.jwt_secret:
        raise ApiError(503, "auth_not_configured", "PLATFORM_JWT_SECRET is not set on the server")
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ApiError(401, "unauthorized", "send 'Authorization: Bearer <platform JWT>'")
    try:
        return decode_token(token.strip(), cfg.jwt_secret, audience=cfg.jwt_audience, issuer=cfg.jwt_issuer)
    except AuthError as e:
        raise ApiError(401, "unauthorized", str(e)) from None


def moderator(p: Principal = Depends(principal)) -> Principal:
    if not p.is_moderator:
        raise ApiError(403, "forbidden", "moderator role required")
    return p


@dataclass
class Paging:
    limit: int
    offset: int

    def envelope(self, items: list, total: int) -> dict:
        return {"items": items, "total": total, "limit": self.limit, "offset": self.offset,
                "page": self.offset // self.limit + 1, "per_page": self.limit}


def paging(limit: int | None = Query(None, ge=1, le=200, description="Page size (default 50)"),
           offset: int | None = Query(None, ge=0),
           page: int | None = Query(None, ge=1, description="1-based page (alternative to offset)"),
           per_page: int | None = Query(None, ge=1, le=200, description="Alias of limit")) -> Paging:
    size = limit or per_page or 50
    start = offset if offset is not None else ((page - 1) * size if page else 0)
    return Paging(limit=size, offset=start)
