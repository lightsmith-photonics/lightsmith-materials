"""The public website (server-rendered HTML over the same database as the API)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.staticfiles import StaticFiles

from .routes import _not_found, router, wants_html

STATIC_DIR = Path(__file__).parent / "static"


class CachedStaticFiles(StaticFiles):
    """Static assets; pages link them with a content hash (``?v=``), so they can be cached for good."""

    async def get_response(self, path, scope):
        resp = await super().get_response(path, scope)
        if resp.status_code == 200:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


def not_found_page(request: Request):
    return _not_found(request, request.url.path)


__all__ = ["STATIC_DIR", "CachedStaticFiles", "not_found_page", "router", "wants_html"]
