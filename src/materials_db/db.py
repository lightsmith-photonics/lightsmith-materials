"""Engine and session handling. Postgres only; SQLite is accepted solely for the unit tests."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings

TESTING_ENV = "MATERIALS_DB_TESTING"  # set by tests/conftest.py to allow SQLite


class ConfigurationError(RuntimeError):
    pass


def database_url(url: str | None = None) -> str:
    url = url or get_settings().database_url
    if not url:
        raise ConfigurationError("DATABASE_URL is not set; expected e.g. "
                                 "postgresql+psycopg://materials:password@localhost:5432/materials (see .env.example)")
    if url.startswith("sqlite") and not os.environ.get(TESTING_ENV):
        raise ConfigurationError("SQLite is only supported in the unit tests; use Postgres (docker compose up db)")
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


def make_engine(url: str | None = None) -> Engine:
    url = database_url(url)
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=10)


class Database:
    def __init__(self, url: str | None = None):
        self.engine = make_engine(url)
        self.sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.sessionmaker() as s:
            try:
                yield s
                s.commit()
            except BaseException:
                s.rollback()
                raise


def _alembic_config(url: str | None = None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", database_url(url).replace("%", "%%"))
    return cfg


def schema_status(engine: Engine) -> str:
    """"ok" when the database is at the latest migration, else "empty" or "outdated (<rev>)"."""
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    head = ScriptDirectory(str(Path(__file__).parent / "migrations")).get_current_head()
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    if current == head:
        return "ok"
    return "empty" if current is None else f"outdated ({current}, head {head})"


def upgrade(url: str | None = None, revision: str = "head") -> None:
    """Apply Alembic migrations (``materials-db migrate``)."""
    from alembic import command

    command.upgrade(_alembic_config(url), revision)
