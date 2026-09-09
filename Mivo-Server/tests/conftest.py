"""Shared pytest fixtures: a migrated test database + async session per test."""
import os

os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://mivo:mivo@localhost:5432/mivo_test"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg://mivo:mivo@localhost:5432/mivo_test"
)
# Tests must be hermetic w.r.t. whatever real secrets happen to be in the
# developer's .env — a real META_APP_SECRET there would otherwise make
# webhook-signature tests demand a signature they never send.
os.environ.setdefault("META_APP_SECRET", "")

import datetime as dt
import subprocess
import sys
import uuid
from pathlib import Path

import psycopg
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.core.config import get_settings
from app.core.db import Base, async_session_factory, engine
import app.core.models_registry  # noqa: F401  (populates Base.metadata for TRUNCATE)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sync_dsn() -> str:
    return get_settings().database_url_sync.replace("postgresql+psycopg://", "postgresql://")


def create_business_and_headers(
    email: str, business_name: str, password: str = "supersecret1"
) -> dict:
    """Test-only stand-in for the old public `POST /auth/signup` — businesses
    are superadmin-created now (no self-serve signup), so tests create the
    owner + business directly against the DB instead of over HTTP. Uses a
    plain sync psycopg connection (like `_sync_truncate_all` below) rather
    than the app's async engine/session — that engine's asyncpg connections
    are bound to the `client` fixture's own event loop/portal, and a second,
    independent `anyio.run()` here would pool connections on a *different*
    loop and blow up on first reuse ("Event loop is closed")."""
    user_id = uuid.uuid4()
    business_id = uuid.uuid4()
    trial_expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=auth_service.DEFAULT_TRIAL_DAYS)
    with psycopg.connect(_sync_dsn()) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, is_superadmin) VALUES (%s, %s, %s, false)",
            (user_id, email, auth_service.hash_password(password)),
        )
        conn.execute(
            "INSERT INTO businesses (id, owner_user_id, name, ai_enabled, subscription_expires_at) "
            "VALUES (%s, %s, %s, true, %s)",
            (business_id, user_id, business_name, trial_expires),
        )
        conn.commit()
    access, _ = auth_service.issue_tokens(user_id)
    return {"Authorization": f"Bearer {access}"}


def create_superadmin_and_headers(email: str = "admin@mivo.test", password: str = "supersecret1") -> dict:
    user_id = uuid.uuid4()
    with psycopg.connect(_sync_dsn()) as conn:
        conn.execute(
            "INSERT INTO users (id, email, password_hash, is_superadmin) VALUES (%s, %s, %s, true)",
            (user_id, email, auth_service.hash_password(password)),
        )
        conn.commit()
    access, _ = auth_service.issue_tokens(user_id)
    return {"Authorization": f"Bearer {access}"}


@pytest.fixture(scope="session", autouse=True)
def _migrated_database():
    """Run Alembic migrations once against the test database before the test session."""
    try:
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=REPO_ROOT,
            check=True,
            env={**os.environ},
        )
    except Exception as exc:
        # Local postgres might not be running; pure unit tests can still run
        print(f"Warning: Alembic test migration skipped ({exc})")
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    # asyncpg connections are bound to the event loop they were created on, and
    # pytest-asyncio spins up a fresh loop per test function by default — start each
    # test with a clean pool rather than reusing connections from a dead loop.
    await engine.dispose()
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.rollback()
    # Truncate all app tables between tests to keep them isolated.
    async with engine.begin() as conn:
        tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        await conn.exec_driver_sql(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")


def _sync_truncate_all() -> None:
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    with psycopg.connect(
        get_settings().database_url_sync.replace("postgresql+psycopg://", "postgresql://")
    ) as conn:
        conn.execute(f"TRUNCATE {tables} RESTART IDENTITY CASCADE")
        conn.commit()


@pytest.fixture
def client():
    """A TestClient for full HTTP-level tests. TestClient runs the ASGI app on its
    own background event loop (a portal) that's fresh per test, so the shared
    asyncpg connection pool must be disposed before/after each test — otherwise a
    pooled connection from a previous test's now-closed loop gets reused and
    asyncpg raises "attached to a different loop". Isolation/teardown uses a plain
    sync psycopg connection to avoid mixing with the pytest-asyncio `db_session`
    fixture's loop.
    """
    import anyio

    from app.main import app

    anyio.run(engine.dispose)

    with TestClient(app) as test_client:
        yield test_client

    anyio.run(engine.dispose)
    _sync_truncate_all()
