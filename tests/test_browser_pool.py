"""Unit tests for :mod:`worker.browser_pool`.

Uses a fake FlowClient so we don't launch real Chrome. Asserts:
  - first lease on a profile calls ``start()`` once
  - second lease reuses the same client (no extra ``start()``)
  - an unhealthy client is torn down and replaced on the next lease
  - buffers are cleared between leases via ``reset_for_next_job``
  - a handler exception that corrupts the client discards it
  - ``close_all`` stops every client it knows about
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from worker import browser_pool as bp


class FakeClient:
    def __init__(self, **_kwargs) -> None:
        self.started = 0
        self.stopped = 0
        self.resets = 0
        self._healthy = True
        self.reset_target: str | None = None

    async def start(self) -> None:
        self.started += 1

    async def stop(self) -> None:
        self.stopped += 1
        self._healthy = False

    def is_healthy(self) -> bool:
        return self._healthy

    async def reset_for_next_job(self, target_url: str | None = None) -> None:
        self.resets += 1
        self.reset_target = target_url


@pytest.fixture
def pool(monkeypatch):
    instances: list[FakeClient] = []

    def _factory(self, profile: str) -> FakeClient:
        c = FakeClient()
        instances.append(c)
        return c

    monkeypatch.setattr(bp.BrowserPool, "_make_client", _factory)
    p = bp.BrowserPool(profile_base_dir="/tmp/profiles", download_dir="/tmp/dl")
    p._instances = instances  # type: ignore[attr-defined]
    return p


@pytest.mark.asyncio
async def test_first_lease_starts_client(pool):
    async with pool.lease("A") as c:
        assert c.started == 1
        assert c.resets == 1
    # still alive afterwards
    assert c.stopped == 0


@pytest.mark.asyncio
async def test_second_lease_reuses_client(pool):
    async with pool.lease("A") as c1:
        pass
    async with pool.lease("A") as c2:
        pass
    assert c1 is c2
    assert c1.started == 1
    assert c1.resets == 2


@pytest.mark.asyncio
async def test_unhealthy_client_is_replaced(pool):
    async with pool.lease("A") as c1:
        pass
    c1._healthy = False  # simulate crash between jobs

    async with pool.lease("A") as c2:
        pass

    assert c1 is not c2
    assert c1.stopped == 1
    assert c2.started == 1


@pytest.mark.asyncio
async def test_reset_url_forwarded(pool):
    async with pool.lease("A", reset_url="https://example.com/home"):
        pass
    c = pool._instances[0]  # type: ignore[attr-defined]
    assert c.reset_target == "https://example.com/home"


@pytest.mark.asyncio
async def test_handler_exception_discards_when_unhealthy(pool):
    with pytest.raises(RuntimeError):
        async with pool.lease("A") as c:
            c._healthy = False
            raise RuntimeError("boom")
    assert c.stopped == 1
    assert "A" not in pool._clients


@pytest.mark.asyncio
async def test_handler_exception_keeps_healthy_client(pool):
    with pytest.raises(RuntimeError):
        async with pool.lease("A") as c:
            raise RuntimeError("boom")
    # Healthy client survives a handler raise.
    assert c.stopped == 0
    assert pool._clients["A"] is c


@pytest.mark.asyncio
async def test_close_all_stops_every_client(pool):
    async with pool.lease("A"):
        pass
    async with pool.lease("B"):
        pass
    await pool.close_all()
    assert all(c.stopped == 1 for c in pool._instances)  # type: ignore[attr-defined]
    assert pool._clients == {}


@pytest.mark.asyncio
async def test_reset_failure_discards_client(pool):
    async with pool.lease("A"):
        pass
    c = pool._clients["A"]

    async def boom(target_url=None):
        raise RuntimeError("nav failed")

    c.reset_for_next_job = boom  # type: ignore[assignment]

    with pytest.raises(RuntimeError):
        async with pool.lease("A"):
            pass

    assert "A" not in pool._clients


def test_init_pool_gated_by_env(monkeypatch):
    monkeypatch.delenv("FLOW_BROWSER_POOL", raising=False)
    bp._pool = None
    assert bp.init_pool(profile_base_dir="/x", download_dir="/y") is None
    assert bp.get_pool() is None

    monkeypatch.setenv("FLOW_BROWSER_POOL", "1")
    bp._pool = None
    p = bp.init_pool(profile_base_dir="/x", download_dir="/y")
    assert p is not None
    assert bp.get_pool() is p
    # Idempotent.
    assert bp.init_pool(profile_base_dir="/x", download_dir="/y") is p
    bp._pool = None
