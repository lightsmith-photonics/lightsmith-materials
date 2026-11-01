from __future__ import annotations

import dataclasses
import os
from pathlib import Path

os.environ.setdefault("MATERIALS_DB_TESTING", "1")  # the service refuses SQLite outside the tests

import pytest
from fastapi.testclient import TestClient

from materials_db.api.app import create_app
from materials_db.auth import make_token
from materials_db.config import get_settings
from materials_db.db import Database, upgrade
from materials_db.importer import DirectorySource, import_rii

FIXTURE = Path(__file__).parent / "fixtures" / "rii-mini"
COMMIT = "c5c2f188e848453def5970e347399d653df2ffc2"
SECRET = "test-secret-at-least-32-bytes-long!!"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture(scope="session")
def fixture_source() -> DirectorySource:
    return DirectorySource(FIXTURE)


def fresh_database_url(tmp_path: Path) -> str:
    """SQLite per test, or the Postgres in TEST_DATABASE_URL with an emptied public schema."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        return sqlite_url(tmp_path / "test.sqlite")
    from sqlalchemy import create_engine, text
    engine = create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    engine.dispose()
    return url


@pytest.fixture()
def db(tmp_path, fixture_source) -> Database:
    url = fresh_database_url(tmp_path)
    upgrade(url)
    database = Database(url)
    with database.session() as s:
        import_rii(s, fixture_source, COMMIT)
    return database


class FakeResolver:
    """Stands in for Crossref in tests."""

    def __init__(self):
        self.calls = []

    def resolve(self, doi):
        self.calls.append(doi)
        if doi.startswith("10.9999/"):
            return None
        return {"_source": "crossref", "title": ["A measured optical constant"], "DOI": doi,
                "author": [{"given": "Ada", "family": "Lovelace"}, {"given": "Alan", "family": "Turing"}],
                "container-title": ["J. Test. Opt."], "issued": {"date-parts": [[2025, 3]]}, "volume": "12",
                "page": "1-9"}


@pytest.fixture()
def resolver():
    return FakeResolver()


@pytest.fixture()
def client(db, tmp_path, resolver) -> TestClient:
    settings = dataclasses.replace(get_settings(), database_url=str(db.engine.url), jwt_secret=SECRET,
                                   crossref_enabled=False, data_dir=tmp_path / "data",
                                   public_base_url="http://testserver")
    return TestClient(create_app(settings, database=db, resolver=resolver))


def auth(role: str = "user", sub: str = "u1", name: str = "Test User") -> dict:
    return {"Authorization": f"Bearer {make_token(SECRET, sub=sub, name=name, role=role)}"}
