"""FastAPI application: ``uvicorn --factory materials_db.api.app:create_app``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import __version__
from ..config import RII_CITATION, Settings, get_settings
from ..db import Database, schema_status
from ..references import DoiResolver
from .. import site
from . import routes_datasets, routes_materials, routes_submissions

DESCRIPTION = f"""
{{brand}}: an open database of optical constants (refractive index n, extinction coefficient k,
permittivity) in which every dataset is tied to its source publication.

* Units: vacuum wavelength in nm unless an endpoint says otherwise; energies in eV,
  wavenumbers in cm^-1. Time dependence exp(-iωt): absorbing media have k > 0, Im(ε) > 0.
* IDs: every material and dataset has a stable public ID (`mat_…`, `ds_…`) and a slug
  (`au`, `au/johnson-christy-1972`); both work in every path.
* The bulk of the data is imported from the refractiveindex.info database (CC0 1.0) at a
  pinned commit; cite the dataset's own reference and {RII_CITATION}.
* Exports target the simulation library: `/v1/datasets/{{id}}/lightsmith` returns its `tabulated` or
  `drude_lorentz` material kinds; `/v1/datasets/{{id}}/model` gives FDTD/FDFD-ready dispersive models.
* Writes (`/v1/submissions`) need a bearer JWT issued by the community platform (HS256).
"""


def _error_body(status: int, detail) -> dict:
    if isinstance(detail, dict) and "error" in detail:
        return detail
    code = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed",
            409: "conflict", 413: "too_large", 415: "unsupported_media_type", 422: "invalid_request"}.get(
        status, "error")
    return {"error": code, "message": str(detail), "detail": None}


def create_app(settings: Settings | None = None, database: Database | None = None,
               resolver: DoiResolver | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.brand_name, version=__version__,
                  description=DESCRIPTION.replace("{brand}", settings.brand_name),
                  openapi_url="/openapi.json", docs_url="/docs", redoc_url="/redoc")
    app.state.settings = settings
    app.state.db = database or Database(settings.database_url)
    if resolver is None and settings.crossref_enabled:
        resolver = DoiResolver(settings.data_dir / "doi-cache", mailto=settings.crossref_mailto, timeout=8.0)
    app.state.resolver = resolver
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=True,
                       allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["Authorization", "Content-Type"],
                       expose_headers=["X-Dataset-Id", "X-Dataset-Slug", "X-Dataset-Version", "X-Warnings",
                                       "Content-Disposition"])

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        html_page = not request.url.path.startswith("/v1/") and site.wants_html(request)
        if settings.site_enabled and exc.status_code == 404 and html_page:
            return site.not_found_page(request)
        return JSONResponse(_error_body(exc.status_code, exc.detail), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        msg = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in errors)
        return JSONResponse({"error": "invalid_request", "message": msg, "detail": errors}, status_code=422)

    @app.get("/healthz", tags=["meta"], summary="Liveness and database check")
    def healthz():
        try:
            schema = schema_status(app.state.db.engine)
        except Exception as e:  # noqa: BLE001 - report any database failure as unhealthy
            return JSONResponse({"status": "error", "database": type(e).__name__, "version": __version__},
                                status_code=503)
        body = {"status": "ok" if schema == "ok" else "error", "schema": schema, "version": __version__}
        return JSONResponse(body, status_code=200 if schema == "ok" else 503)

    if not settings.site_enabled:
        @app.get("/", include_in_schema=False)
        def root():
            return {"service": settings.brand_name, "version": __version__, "openapi": "/openapi.json",
                    "docs": "/docs", "api": "/v1"}

    app.include_router(routes_materials.router)
    app.include_router(routes_submissions.router)
    app.include_router(routes_datasets.router)
    if settings.site_enabled:
        # The website: server-rendered pages at /, over the same database (see materials_db.site).
        app.include_router(site.router)
        app.mount("/static", site.CachedStaticFiles(directory=site.STATIC_DIR), name="static")
    return app
