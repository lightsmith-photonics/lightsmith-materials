"""The Alembic migrations produce exactly the ORM schema."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from materials_db.db import make_engine, upgrade
from materials_db.models import Base

from .conftest import fresh_database_url


def test_migrations_match_models(tmp_path):
    url = fresh_database_url(tmp_path)
    upgrade(url)
    engine = make_engine(url)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []
