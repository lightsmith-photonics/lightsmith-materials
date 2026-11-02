"""Settings, read from the environment (see ``.env.example``)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[2]

# refractiveindex.info database snapshot imported by default. Same commit as the
# lightsmith library's bundled data (src/lightsmith/data/materials/manifest.json).
RII_REPOSITORY = "https://github.com/polyanskiy/refractiveindex.info-database"
RII_DEFAULT_COMMIT = "c5c2f188e848453def5970e347399d653df2ffc2"
RII_ARCHIVE_URL = "https://codeload.github.com/polyanskiy/refractiveindex.info-database/zip/{commit}"
RII_RAW_URL = "https://raw.githubusercontent.com/polyanskiy/refractiveindex.info-database/{commit}/database/{path}"
RII_PAGE_URL = "https://refractiveindex.info/?shelf={shelf}&book={book}&page={page}"
RII_CITATION = "M. N. Polyanskiy, Sci. Data 11, 94 (2024), doi:10.1038/s41597-023-02898-2"
RII_DOI = "10.1038/s41597-023-02898-2"

DEV_CORS_ORIGINS = (
    "http://localhost:3000", "http://127.0.0.1:3000",  # apps/web
    "http://localhost:5173", "http://127.0.0.1:5173",  # apps/studio
)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> tuple[str, ...]:
    return tuple(s.strip() for s in os.environ.get(name, "").split(",") if s.strip())


def _url(name: str, default: str) -> str:
    return (os.environ.get(name) or default).rstrip("/")


@dataclass(frozen=True)
class Settings:
    brand_name: str  # product name shown in the API, exports and citations
    library_name: str  # the simulation library the export endpoint targets
    database_url: str | None
    data_dir: Path
    jwt_secret: str | None
    jwt_issuer: str | None
    jwt_audience: str | None
    cors_origins: tuple[str, ...]
    public_base_url: str
    crossref_enabled: bool
    crossref_mailto: str | None
    rii_commit: str
    max_upload_bytes: int
    # public website (server-rendered pages on the same app; see materials_db.site)
    site_enabled: bool = True
    docs_url: str = "https://docs.lightsmith.dev"
    studio_url: str = "https://studio.lightsmith.dev"
    community_url: str = "https://lightsmith.dev"
    library_repo_url: str = "https://github.com/lightsmith-photonics/lightsmith"
    library_install: str = "pip install lightsmith"


def get_settings() -> Settings:
    data_dir = Path(os.environ.get("MATERIALS_DB_DATA_DIR") or SERVICE_ROOT / ".data")
    return Settings(
        brand_name=os.environ.get("BRAND_NAME") or "Lightsmith Materials",
        library_name=os.environ.get("LIBRARY_NAME") or "Lightsmith",
        database_url=os.environ.get("DATABASE_URL") or None,
        data_dir=data_dir,
        jwt_secret=os.environ.get("PLATFORM_JWT_SECRET") or None,
        jwt_issuer=os.environ.get("PLATFORM_JWT_ISSUER") or None,
        jwt_audience=os.environ.get("PLATFORM_JWT_AUDIENCE") or "materials-db",
        cors_origins=DEV_CORS_ORIGINS + _list("CORS_ORIGINS"),
        public_base_url=(os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8100").rstrip("/"),
        crossref_enabled=_bool("CROSSREF_ENABLED", True),
        crossref_mailto=os.environ.get("CROSSREF_MAILTO") or None,
        rii_commit=os.environ.get("RII_COMMIT") or RII_DEFAULT_COMMIT,
        max_upload_bytes=int(os.environ.get("MAX_UPLOAD_BYTES") or 5_000_000),
        site_enabled=_bool("SITE_ENABLED", True),
        docs_url=_url("DOCS_URL", "https://docs.lightsmith.dev"),
        studio_url=_url("STUDIO_URL", "https://studio.lightsmith.dev"),
        community_url=_url("COMMUNITY_URL", "https://lightsmith.dev"),
        library_repo_url=_url("LIBRARY_REPO_URL", "https://github.com/lightsmith-photonics/lightsmith"),
        library_install=os.environ.get("LIBRARY_INSTALL") or "pip install lightsmith",
    )
