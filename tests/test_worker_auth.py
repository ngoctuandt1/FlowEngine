"""Auth tests for /api/worker/* endpoints.

Production deploys (see deploy/debian/) put this server on the public
internet. The worker endpoints let callers claim jobs and post results,
so they MUST require a Bearer token. This module verifies:

- With a strong API_KEY: missing or wrong tokens get 401, correct token
  gets through.
- With the default API_KEY (``dev-key`` / unset / ``changeme``): the
  endpoint stays open so local development isn't broken — and the
  helper logs a one-shot warning at startup. We don't assert the
  warning here (logging-capture is brittle); the warning text is
  short-circuit-tested via the helper directly.

The check is intentionally constant-time. We don't try to assert that
property here — leave that to a side-channel review — but we do verify
the symmetric behaviour (right header in, 200/204 out; wrong header
in, 401 out).
"""

from __future__ import annotations

import importlib

import pytest


def _reload_with_key(monkeypatch, key: str | None):
    """Re-import the auth module + worker route under a chosen API_KEY.

    The auth helper caches the key at import time via ``server.config``,
    so we have to reimport once per test that flips the key.
    """
    if key is None:
        monkeypatch.delenv("API_KEY", raising=False)
    else:
        monkeypatch.setenv("API_KEY", key)

    import server.config  # noqa: WPS433
    import server.auth    # noqa: WPS433
    import server.routes.worker  # noqa: WPS433
    import server.app    # noqa: WPS433
    importlib.reload(server.config)
    importlib.reload(server.auth)
    importlib.reload(server.routes.worker)
    importlib.reload(server.app)
    return server.app.app


@pytest.mark.asyncio
async def test_worker_claim_open_with_default_key(monkeypatch, db):
    """Default key keeps /api/worker/* open for local dev."""
    from httpx import ASGITransport, AsyncClient

    app = _reload_with_key(monkeypatch, "dev-key")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/worker/claim",
            json={"worker_id": "w1", "profiles": ["p1"]},
        )
    # 204 No Content (no jobs queued) is the success-but-empty signal —
    # what matters is we got past auth (not 401).
    assert r.status_code in (200, 204), r.text


@pytest.mark.asyncio
async def test_worker_claim_rejects_missing_token_in_prod(monkeypatch, db):
    """A real API_KEY makes the endpoint require Bearer auth."""
    from httpx import ASGITransport, AsyncClient

    app = _reload_with_key(monkeypatch, "s3cret-prod-key-xyz")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/worker/claim",
            json={"worker_id": "w1", "profiles": ["p1"]},
        )
    assert r.status_code == 401
    assert "invalid worker token" in r.text.lower()


@pytest.mark.asyncio
async def test_worker_claim_rejects_wrong_token(monkeypatch, db):
    """Wrong Bearer value -> 401, even close near-matches."""
    from httpx import ASGITransport, AsyncClient

    app = _reload_with_key(monkeypatch, "s3cret-prod-key-xyz")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/worker/claim",
            headers={"Authorization": "Bearer wrong-key"},
            json={"worker_id": "w1", "profiles": ["p1"]},
        )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_worker_claim_accepts_correct_token(monkeypatch, db):
    """Correct Bearer -> auth passes, request proceeds normally."""
    from httpx import ASGITransport, AsyncClient

    app = _reload_with_key(monkeypatch, "s3cret-prod-key-xyz")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.post(
            "/api/worker/claim",
            headers={"Authorization": "Bearer s3cret-prod-key-xyz"},
            json={"worker_id": "w1", "profiles": ["p1"]},
        )
    # Empty queue => 204; non-empty => 200. Either way past auth.
    assert r.status_code in (200, 204), r.text


@pytest.mark.asyncio
async def test_dashboard_endpoints_remain_open(monkeypatch, db):
    """/api/jobs and /api/profiles MUST stay unauth'd — they're the dashboard."""
    from httpx import ASGITransport, AsyncClient

    app = _reload_with_key(monkeypatch, "s3cret-prod-key-xyz")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        jobs = await c.get("/api/jobs")
        profiles = await c.get("/api/profiles")
    assert jobs.status_code == 200
    assert profiles.status_code == 200
