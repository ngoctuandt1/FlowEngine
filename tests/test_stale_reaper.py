import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

import server.app as app_module
from server.db.database import get_db
from server.db.job_store import create_job
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType
from server.models.profile import Profile, ProfileStatus


def _job(job_id: str, status: JobStatus, updated_at: datetime) -> Job:
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=status,
        job_level=1,
        profile=f"profile-{job_id}",
        prompt="test prompt",
        worker_id=f"worker-{job_id}",
        claimed_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
        completed_at=updated_at if status == JobStatus.COMPLETED else None,
        error=f"existing-error-{job_id}",
    )


async def _create_profile_for_job(job: Job) -> None:
    await create_profile(
        Profile(
            name=job.profile,
            google_account=f"{job.profile}@example.com",
            locale="en",
            tier="ultra",
            status=ProfileStatus.AVAILABLE,
            current_job_id=job.id,
            worker_id=job.worker_id,
            created_at=datetime.now(UTC),
        )
    )


async def _fetch_job_row(job_id: str) -> dict:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return dict(row)


async def _fetch_profile_row(profile: str) -> dict:
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM profiles WHERE name = ?", (profile,))
        row = await cursor.fetchone()
        return dict(row)


async def test_old_running_job_is_reaped_to_pending_and_claim_cleared(db):
    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("stale-running", JobStatus.RUNNING, old)
    await create_job(job)
    await _create_profile_for_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    profile = await _fetch_profile_row(job.profile)
    assert reaped == [job.id]
    assert row["status"] == "pending"
    assert row["worker_id"] is None
    assert row["claimed_at"] is None
    assert "existing-error-stale-running" in row["error"]
    assert "stale_claim_reaped: previous_worker=worker-stale-running" in row["error"]
    assert profile["current_job_id"] is None
    assert profile["worker_id"] is None


async def test_old_claimed_job_is_reaped_to_pending_and_claim_cleared(db):
    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("stale-claimed", JobStatus.CLAIMED, old)
    await create_job(job)
    await _create_profile_for_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    profile = await _fetch_profile_row(job.profile)
    assert reaped == [job.id]
    assert row["status"] == "pending"
    assert row["worker_id"] is None
    assert row["claimed_at"] is None
    assert "existing-error-stale-claimed" in row["error"]
    assert "stale_claim_reaped: previous_worker=worker-stale-claimed" in row["error"]
    assert profile["current_job_id"] is None
    assert profile["worker_id"] is None


async def test_recent_running_job_is_not_reaped(db):
    recent = datetime.now(UTC) - timedelta(seconds=30)
    job = _job("recent-running", JobStatus.RUNNING, recent)
    await create_job(job)
    await _create_profile_for_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    profile = await _fetch_profile_row(job.profile)
    assert reaped == []
    assert row["status"] == "running"
    assert row["worker_id"] == "worker-recent-running"
    assert row["claimed_at"] == recent.isoformat()
    assert row["error"] == "existing-error-recent-running"
    assert profile["current_job_id"] == job.id
    assert profile["worker_id"] == "worker-recent-running"


async def test_old_pending_job_is_not_touched(db):
    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("old-pending", JobStatus.PENDING, old)
    await create_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    assert reaped == []
    assert row["status"] == "pending"
    assert row["worker_id"] == "worker-old-pending"
    assert row["claimed_at"] == old.isoformat()
    assert row["error"] == "existing-error-old-pending"
    assert row["updated_at"] == old.isoformat()


async def test_completed_job_is_not_touched(db):
    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("completed", JobStatus.COMPLETED, old)
    await create_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    assert reaped == []
    assert row["status"] == "completed"
    assert row["worker_id"] == "worker-completed"
    assert row["claimed_at"] == old.isoformat()
    assert row["error"] == "existing-error-completed"
    assert row["updated_at"] == old.isoformat()


async def test_cancelled_job_is_not_touched(db):
    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("cancelled", JobStatus.CANCELLED, old)
    await create_job(job)

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    assert reaped == []
    assert row["status"] == "cancelled"
    assert row["worker_id"] == "worker-cancelled"
    assert row["claimed_at"] == old.isoformat()
    assert row["error"] == "existing-error-cancelled"
    assert row["updated_at"] == old.isoformat()


async def test_reaper_interval_and_threshold_honor_env(monkeypatch):
    monkeypatch.setenv("FLOW_STALE_REAPER_INTERVAL_SEC", "2")
    monkeypatch.setenv("FLOW_STALE_RUNNING_THRESHOLD_SEC", "3")
    thresholds: list[int] = []
    sleeps: list[int] = []
    sleep_calls = 0

    async def fake_reap(threshold_sec: int | None = None) -> list[str]:
        thresholds.append(threshold_sec)
        return []

    async def fake_sleep(interval_sec: int) -> None:
        nonlocal sleep_calls
        sleeps.append(interval_sec)
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    monkeypatch.setattr(app_module, "_reap_stale_claims", fake_reap)
    monkeypatch.setattr(app_module.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        # Skip the startup grace window; this test exercises the
        # steady-state interval/threshold env wiring only.
        await app_module._stale_claim_reaper(startup_grace_sec=0)

    # Loop order is reap → sleep; the second sleep call raises so reap
    # has been invoked twice with the env-overridden threshold.
    assert thresholds == [3, 3]
    assert sleeps == [2, 2]


async def test_reaper_logs_single_row_db_error_and_continues(db, monkeypatch, caplog):
    old_first = datetime.now(UTC) - timedelta(seconds=1000)
    old_second = datetime.now(UTC) - timedelta(seconds=900)
    first = _job("stale-error", JobStatus.RUNNING, old_first)
    second = _job("stale-ok", JobStatus.RUNNING, old_second)
    await create_job(first)
    await create_job(second)
    original_reset = app_module._reset_stale_running_job

    async def flaky_reset(job_id: str, threshold_sec: int, observed_at: datetime):
        if job_id == first.id:
            raise sqlite3.OperationalError("synthetic row failure")
        return await original_reset(job_id, threshold_sec, observed_at)

    monkeypatch.setattr(app_module, "_reset_stale_running_job", flaky_reset)
    with caplog.at_level(logging.ERROR, logger=app_module.__name__):
        reaped = await app_module._reap_stale_claims(threshold_sec=600)

    first_row = await _fetch_job_row(first.id)
    second_row = await _fetch_job_row(second.id)
    assert reaped == [second.id]
    assert first_row["status"] == "running"
    assert second_row["status"] == "pending"
    assert "failed to reap stale claim job stale-error" in caplog.text
    assert "synthetic row failure" in caplog.text


async def test_lifespan_defers_first_sweep_to_periodic_task(
    db,
    monkeypatch,
):
    """Server boot must NOT eager-reap.

    The in-memory ``_workers`` heartbeat map is empty until workers
    re-register, so an immediate sweep treats every live claimed job
    as orphaned and the next claim cycle opens duplicate Chrome
    sessions. Only the periodic reaper runs at startup; that task
    owns its own grace-period before the first pass.
    """
    import server.config

    events: list[tuple[str, int | None]] = []
    started = asyncio.Event()

    async def fake_reap(threshold_sec: int | None = None) -> list[str]:
        events.append(("startup-sweep", threshold_sec))
        return []

    async def fake_reaper() -> None:
        events.append(("periodic-task", None))
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(server.config, "setup_logging", lambda service: None)
    monkeypatch.setattr(app_module, "_reap_stale_claims", fake_reap)
    monkeypatch.setattr(app_module, "_stale_claim_reaper", fake_reaper)

    async with app_module.lifespan(app_module.app):
        await asyncio.wait_for(started.wait(), timeout=1)

    # Only the periodic task should have run; no eager sweep.
    assert events == [("periodic-task", None)]


async def test_lifespan_starts_and_cancels_reaper_task(db, monkeypatch, caplog):
    import server.config

    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def fake_reaper() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    monkeypatch.setattr(server.config, "setup_logging", lambda service: None)
    monkeypatch.setattr(app_module, "_stale_claim_reaper", fake_reaper)
    with caplog.at_level(logging.INFO, logger=app_module.__name__):
        async with app_module.lifespan(app_module.app):
            await asyncio.wait_for(started.wait(), timeout=1)

    assert cancelled.is_set()
    assert "stale claim reaper task started" in caplog.text
    assert "stale claim reaper task stopped" in caplog.text


# -- New regression tests: heartbeat join + threshold default ------------------


async def test_default_threshold_is_thirty_minutes():
    """Flow extend jobs run 600-900s; the reaper threshold must outlast that.

    Regression guard for the credit-burn bug where a still-active worker
    would have its job yanked back to ``pending`` mid-generation, then
    reclaimed in a second Chrome session. 1800s comfortably exceeds the
    longest measured extend cycle.
    """
    assert app_module.STALE_RUNNING_THRESHOLD_SEC >= 1800


async def test_alive_worker_with_old_running_job_is_not_reaped(db, monkeypatch):
    """Heartbeating worker on a long-running job is protected."""
    import server.routes.worker as worker_route

    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("alive-running", JobStatus.RUNNING, old)
    await create_job(job)
    await _create_profile_for_job(job)

    # Worker heartbeat is fresh — this is exactly the live-Flow case we
    # used to break.
    monkeypatch.setitem(
        worker_route._workers,
        job.worker_id,
        datetime.now(UTC),
    )

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    profile = await _fetch_profile_row(job.profile)
    assert reaped == []
    assert row["status"] == "running"
    assert row["worker_id"] == job.worker_id
    assert profile["current_job_id"] == job.id


async def test_dead_worker_with_old_running_job_is_reaped(db, monkeypatch):
    """Stale heartbeat (worker died mid-job) still gets reaped."""
    import server.routes.worker as worker_route

    old = datetime.now(UTC) - timedelta(seconds=900)
    job = _job("dead-worker", JobStatus.RUNNING, old)
    await create_job(job)
    await _create_profile_for_job(job)

    # Heartbeat older than the heartbeat-stale window (default 180s).
    monkeypatch.setitem(
        worker_route._workers,
        job.worker_id,
        datetime.now(UTC) - timedelta(seconds=600),
    )

    reaped = await app_module._reap_stale_claims(threshold_sec=600)

    row = await _fetch_job_row(job.id)
    assert reaped == [job.id]
    assert row["status"] == "pending"
    assert row["worker_id"] is None


# -- Server-restart startup grace period ---------------------------------------


async def test_default_startup_grace_at_least_twice_heartbeat_interval():
    """Grace must outlast 2× the worker heartbeat interval (60s default)."""
    assert app_module.STALE_REAPER_STARTUP_GRACE_SEC >= 120


async def test_stale_claim_reaper_skips_first_sweep_during_grace(db, monkeypatch):
    """During the boot grace window the reaper must NOT call _reap_stale_claims.

    Simulates the server-restart edge case: in-memory ``_workers`` map is
    empty, so a sweep would treat every claimed row as orphaned and
    duplicate Chrome sessions on the next claim. The task must wait
    out the grace period before its first pass.
    """
    calls: list[int] = []

    async def fake_reap(threshold_sec: int | None = None) -> list[str]:
        calls.append(1)
        return []

    monkeypatch.setattr(app_module, "_reap_stale_claims", fake_reap)
    monkeypatch.setenv("FLOW_STALE_REAPER_INTERVAL_SEC", "30")

    # Grace > test timeout so the first sweep never lands.
    task = asyncio.create_task(app_module._stale_claim_reaper(startup_grace_sec=60))
    try:
        await asyncio.sleep(0.2)
        assert calls == []  # still in grace window
    finally:
        task.cancel()
        with suppress_cancel():
            await task


async def test_stale_claim_reaper_runs_after_grace(db, monkeypatch):
    """Once grace expires the periodic sweep starts firing normally."""
    calls = asyncio.Event()

    async def fake_reap(threshold_sec: int | None = None) -> list[str]:
        calls.set()
        return []

    monkeypatch.setattr(app_module, "_reap_stale_claims", fake_reap)
    monkeypatch.setenv("FLOW_STALE_REAPER_INTERVAL_SEC", "30")

    task = asyncio.create_task(app_module._stale_claim_reaper(startup_grace_sec=0))
    try:
        await asyncio.wait_for(calls.wait(), timeout=1.0)
    finally:
        task.cancel()
        with suppress_cancel():
            await task


def suppress_cancel():
    from contextlib import suppress

    return suppress(asyncio.CancelledError, Exception)
