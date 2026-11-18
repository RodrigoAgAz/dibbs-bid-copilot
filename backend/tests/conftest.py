"""Test configuration.

Database selection, in order:
  1. TEST_DATABASE_URL (docker compose: postgres service, database `dibbs_test`)
  2. an embedded PostgreSQL 16 via `pgserver` (local dev without Docker)
  3. SQLite in a temp dir (last resort)
The schema is created by running the Alembic migrations, so migrations are tested too.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Test-safe environment BEFORE the app reads settings.
os.environ.update(
    {
        "DEMO_MODE": "true",
        "CONNECTOR_MODE": "fixture",
        "EMAIL_PROVIDER": "console",
        "LLM_PROVIDER": "mock",
        "VOICE_PROVIDER": "mock",
        "REDIS_URL": "",
        "COOKIE_SECURE": "false",
        "JWT_SECRET": "test-secret-0123456789abcdef0123456789abcdef",
        "INBOUND_EMAIL_SECRET": "test-inbound-secret",
        "ELEVENLABS_WEBHOOK_SECRET": "test-eleven-secret",
        "TWILIO_AUTH_TOKEN": "test-twilio-token",
    }
)
_tmp = Path(tempfile.mkdtemp(prefix="dibbs-test-"))
os.environ["RAW_CACHE_DIR"] = str(_tmp / "raw")
os.environ["MODEL_ARTIFACT_DIR"] = str(_tmp / "artifacts")

import pytest  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
BACKEND = REPO / "backend"
sys.path.insert(0, str(BACKEND / "scripts"))


def _database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        return url
    try:
        import pgserver

        srv = pgserver.get_server(str(REPO / "data" / "pgtest"), cleanup_mode=None)
        srv.psql("DROP DATABASE IF EXISTS dibbs_test WITH (FORCE);")
        srv.psql("CREATE DATABASE dibbs_test;")
        return srv.get_uri("dibbs_test").replace("postgresql://", "postgresql+psycopg://", 1)
    except Exception:
        return f"sqlite:///{(_tmp / 'test.db').as_posix()}"


TEST_DATABASE_URL = _database_url()
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from app.config import get_settings  # noqa: E402

get_settings.cache_clear()

from app.db import set_engine  # noqa: E402
from app.models import Base  # noqa: E402


def _migrate(engine) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


@pytest.fixture(scope="session")
def engine():
    if TEST_DATABASE_URL.startswith("sqlite"):
        eng = create_engine(TEST_DATABASE_URL, connect_args={"check_same_thread": False})
    else:
        eng = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    _migrate(eng)
    set_engine(eng)
    yield eng
    eng.dispose()


def _truncate(engine) -> None:
    tables = [t.name for t in reversed(Base.metadata.sorted_tables)]
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY CASCADE"))
        else:
            for t in tables:
                conn.execute(text(f'DELETE FROM "{t}"'))


@pytest.fixture(autouse=True)
def _clean_state(engine):
    from app.core.ratelimit import get_limiter
    from app.services.competitors import clear_competitor_index
    from app.services.email.providers import set_email_provider
    from app.services.item_index import clear_item_index
    from app.services.pricing import clear_model_cache

    _truncate(engine)
    get_limiter().reset()
    set_email_provider(None)
    # Process-wide caches built from an earlier test's rows (the model cache is keyed by a run id the truncate reuses).
    clear_competitor_index()
    clear_item_index()
    clear_model_cache()
    yield


@pytest.fixture()
def db(engine):
    from app.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def app():
    from app.main import create_app

    return create_app()


def make_client(app):
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://testserver")


@pytest.fixture()
def client(app):
    with make_client(app) as c:
        yield c


def csrf(client) -> dict[str, str]:
    return {"x-csrf-token": client.cookies.get("csrf_token", "")}


def signup(client, email: str, org: str = "Org", password: str = "correct-horse-1") -> dict:
    r = client.post(
        "/api/auth/signup", json={"organization_name": org, "email": email, "password": password}
    )
    assert r.status_code == 201, r.text
    return r.json()
