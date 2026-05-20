"""Shared test fixtures for FlowEngine (B9 test foundation).

- `temp_db_path` — isolates every test to a fresh SQLite file under a tempdir,
  AND re-points the already-imported `DATABASE_PATH` bindings. Needed because
  `server.config` reads the env var at import time and `server.db.database`
  copies the value via `from server.config import DATABASE_PATH`.
- `db` — runs `init_db()` so schema exists before the test body.
- `api_client` — httpx AsyncClient bound to the FastAPI app via ASGITransport
  (no real socket). Depends on `db` so the schema is ready when routes run.
"""
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def temp_db_path(monkeypatch):
    """Point every DATABASE_PATH binding at a fresh temp file for this test."""
    with tempfile.TemporaryDirectory() as tmp:
        db_path = str(Path(tmp) / "test.db")

        # Future imports read the env var.
        monkeypatch.setenv("DATABASE_PATH", db_path)

        # Already-imported modules copy the value at import time — patch both
        # bindings so no test can hit the real dev DB.
        import server.config
        monkeypatch.setattr(server.config, "DATABASE_PATH", db_path, raising=False)
        import server.db.database
        monkeypatch.setattr(server.db.database, "DATABASE_PATH", db_path, raising=False)

        yield db_path


@pytest_asyncio.fixture
async def db(temp_db_path):
    """Initialise a fresh schema in the temp DB before the test runs."""
    from server.db.database import init_db
    await init_db()
    yield
    # tempdir cleanup removes the file on fixture exit


@pytest_asyncio.fixture
async def api_client(db):
    """HTTP client bound to the FastAPI app — no real server / port needed."""
    from server.app import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def sample_job_payload() -> dict:
    return {
        "type": "text-to-video",
        "prompt": "test prompt",
        "model": "veo-3.1-fast",
        "aspect_ratio": "16:9",
    }


@pytest.fixture
def sample_profile():
    from datetime import UTC, datetime

    from server.models.profile import Profile, ProfileStatus

    return Profile(
        name="test-profile",
        google_account="test@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )
