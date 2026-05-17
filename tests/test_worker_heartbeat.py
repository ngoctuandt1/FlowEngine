"""Tests for the worker's background heartbeat task.

The claim loop blocks in ``wait_for_capacity`` for the full duration of
a long-running Flow job (extend: 600-900s) when MAX_CONCURRENT_JOBS
slots are consumed. A dedicated heartbeat task must keep firing
regardless so the server's stale-claim reaper does not yank the active
job back to ``pending`` and duplicate the Chrome session on reclaim.
"""

import asyncio

import pytest

import worker.main as worker_main


class _FakeAPI:
    def __init__(self) -> None:
        self.beats = 0
        self.event = asyncio.Event()

    async def heartbeat(self) -> None:
        self.beats += 1
        self.event.set()


@pytest.fixture(autouse=True)
def _reset_shutdown():
    """Each test starts with a fresh shutdown flag."""
    worker_main._shutdown = asyncio.Event()
    yield
    worker_main._shutdown.set()


async def test_heartbeat_loop_fires_periodically():
    """The background task must call api.heartbeat() at least once per interval."""
    api = _FakeAPI()
    task = asyncio.create_task(worker_main._heartbeat_loop(api, interval_sec=0.05))
    try:
        await asyncio.wait_for(api.event.wait(), timeout=1.0)
        # Let it tick a few more times.
        await asyncio.sleep(0.2)
        assert api.beats >= 2, f"expected ≥2 beats, got {api.beats}"
    finally:
        worker_main._shutdown.set()
        await asyncio.wait_for(task, timeout=1.0)


async def test_heartbeat_loop_continues_after_failure():
    """A failing heartbeat must not kill the task — it logs + retries."""
    failures = {"count": 0}

    class FlakyAPI:
        async def heartbeat(self) -> None:
            failures["count"] += 1
            if failures["count"] <= 2:
                raise RuntimeError("network unreachable")

    api = FlakyAPI()
    task = asyncio.create_task(worker_main._heartbeat_loop(api, interval_sec=0.02))
    try:
        await asyncio.sleep(0.2)
        # Past the first two failures, subsequent calls must succeed —
        # i.e. the task is still alive.
        assert failures["count"] >= 3
        assert not task.done()
    finally:
        worker_main._shutdown.set()
        await asyncio.wait_for(task, timeout=1.0)


async def test_heartbeat_loop_exits_on_shutdown():
    """Setting the shutdown flag must end the loop promptly."""
    api = _FakeAPI()
    task = asyncio.create_task(worker_main._heartbeat_loop(api, interval_sec=10))
    # Let the first beat go out.
    await asyncio.wait_for(api.event.wait(), timeout=1.0)
    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=1.0)
    assert task.done()


async def test_heartbeat_keeps_firing_while_claim_loop_at_capacity():
    """Regression guard for the credit-burn race.

    Simulates the steady state: claim loop blocked on a slow job,
    heartbeat task running independently. The heartbeat must keep
    firing every interval — if it stopped, the server reaper would
    mark the worker dead at 180s and reset the active job, opening a
    duplicate Chrome session on the next claim cycle.
    """
    api = _FakeAPI()

    async def slow_claim_loop_substitute() -> None:
        # Simulate a long-running in-flight job — the claim loop is
        # parked in wait_for_capacity for the whole duration.
        await asyncio.sleep(0.3)

    hb_task = asyncio.create_task(worker_main._heartbeat_loop(api, interval_sec=0.05))
    claim_sub = asyncio.create_task(slow_claim_loop_substitute())
    try:
        await claim_sub
        # During those 300ms with a 50ms interval we expect ≥4 beats.
        assert api.beats >= 4, f"expected ≥4 beats while busy, got {api.beats}"
    finally:
        worker_main._shutdown.set()
        await asyncio.wait_for(hb_task, timeout=1.0)
