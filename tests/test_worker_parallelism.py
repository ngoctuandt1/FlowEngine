import asyncio
import os

from worker.profile_manager import ProfileManager
from worker.project_lock import ProjectLock


def _job(job_id: str, profile: str) -> dict:
    return {
        "id": job_id,
        "type": "text-to-video",
        "profile": profile,
        "job_level": 1,
    }


class _FakeAPI:
    def __init__(self, jobs):
        self.jobs = list(jobs)
        self.claim_calls = []
        self.updates = []

    async def claim_job(self, profiles):
        self.claim_calls.append(list(profiles))
        for index, job in enumerate(self.jobs):
            if job["profile"] in profiles:
                return self.jobs.pop(index)
        return None

    async def update_job(self, job_id, result):
        self.updates.append((job_id, result))
        return {"id": job_id}

    async def heartbeat(self):
        return None


async def _run_loop(monkeypatch, profiles, jobs, dispatch, *, max_jobs=10):
    from worker import main as worker_main

    api = _FakeAPI(jobs)
    profile_mgr = ProfileManager("./chrome-profiles", profiles)
    project_lock = ProjectLock()

    monkeypatch.setattr(worker_main, "MAX_CONCURRENT_JOBS", max_jobs)
    monkeypatch.setattr(worker_main, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(worker_main, "_shutdown", asyncio.Event())
    monkeypatch.setattr(worker_main, "dispatch_job", dispatch)

    task = asyncio.create_task(worker_main.claim_loop(api, profile_mgr, project_lock))
    return worker_main, task, api, profile_mgr


async def test_claim_loop_dispatches_jobs_concurrently_when_profiles_allow(monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0
    started_jobs = []

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        nonlocal active, max_active
        assert manage_profile is False
        assert profile_mgr.get_current_job(job["profile"]) == job["id"]
        active += 1
        max_active = max(max_active, active)
        started_jobs.append(job["id"])
        if len(started_jobs) == 3:
            started.set()
        await release.wait()
        active -= 1
        return {"status": "completed"}

    worker_main, task, api, profile_mgr = await _run_loop(
        monkeypatch,
        ["p1", "p2", "p3"],
        [_job("j1", "p1"), _job("j2", "p2"), _job("j3", "p3")],
        dispatch,
        max_jobs=3,
    )

    await asyncio.wait_for(started.wait(), timeout=1)
    worker_main._shutdown.set()
    release.set()
    await asyncio.wait_for(task, timeout=1)

    assert max_active == 3
    assert set(started_jobs) == {"j1", "j2", "j3"}
    assert {job_id for job_id, _ in api.updates} == {"j1", "j2", "j3"}
    assert sorted(profile_mgr.get_available()) == ["p1", "p2", "p3"]


async def test_claim_loop_drains_in_flight_job_on_shutdown(monkeypatch):
    dispatch_started = asyncio.Event()
    release = asyncio.Event()

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        dispatch_started.set()
        await release.wait()
        return {"status": "completed"}

    worker_main, task, api, profile_mgr = await _run_loop(
        monkeypatch,
        ["p1"],
        [_job("j1", "p1")],
        dispatch,
        max_jobs=1,
    )

    await asyncio.wait_for(dispatch_started.wait(), timeout=1)
    worker_main._shutdown.set()
    await asyncio.sleep(0.03)

    assert not task.done()
    assert profile_mgr.get_current_job("p1") == "j1"

    release.set()
    await asyncio.wait_for(task, timeout=1)

    assert api.updates == [("j1", {"status": "completed"})]
    assert profile_mgr.is_available("p1")


async def test_slow_job_on_one_profile_does_not_block_other_profile_claim(monkeypatch):
    p1_started = asyncio.Event()
    p2_started = asyncio.Event()
    release_p1 = asyncio.Event()

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        if job["profile"] == "p1":
            p1_started.set()
            await release_p1.wait()
        else:
            p2_started.set()
        return {"status": "completed"}

    worker_main, task, api, _ = await _run_loop(
        monkeypatch,
        ["p1", "p2"],
        [_job("slow", "p1"), _job("fast", "p2")],
        dispatch,
        max_jobs=2,
    )

    await asyncio.wait_for(p1_started.wait(), timeout=1)
    await asyncio.wait_for(p2_started.wait(), timeout=1)

    worker_main._shutdown.set()
    release_p1.set()
    await asyncio.wait_for(task, timeout=1)

    assert {job_id for job_id, _ in api.updates} == {"slow", "fast"}


async def test_claim_loop_respects_max_concurrent_jobs_cap(monkeypatch):
    two_started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0
    started_jobs = []

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started_jobs.append(job["id"])
        if len(started_jobs) == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return {"status": "completed"}

    worker_main, task, api, _ = await _run_loop(
        monkeypatch,
        ["p1", "p2", "p3", "p4", "p5"],
        [_job(f"j{i}", f"p{i}") for i in range(1, 6)],
        dispatch,
        max_jobs=2,
    )

    await asyncio.wait_for(two_started.wait(), timeout=1)
    await asyncio.sleep(0.03)

    assert max_active == 2
    assert len(started_jobs) == 2

    worker_main._shutdown.set()
    release.set()
    await asyncio.wait_for(task, timeout=1)

    assert len(api.updates) == 2


# ---------------------------------------------------------------------------
# ALLOW_SAME_PROFILE_CONCURRENCY paths
# ---------------------------------------------------------------------------

async def _run_loop_same_profile(monkeypatch, profiles, jobs, dispatch, *, max_jobs=4):
    from worker import main as worker_main

    api = _FakeAPI(jobs)
    profile_mgr = ProfileManager("./chrome-profiles", profiles)
    project_lock = ProjectLock()

    monkeypatch.setattr(worker_main, "MAX_CONCURRENT_JOBS", max_jobs)
    monkeypatch.setattr(worker_main, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(worker_main, "_shutdown", asyncio.Event())
    monkeypatch.setattr(worker_main, "dispatch_job", dispatch)
    monkeypatch.setattr(worker_main, "ALLOW_SAME_PROFILE_CONCURRENCY", True)

    task = asyncio.create_task(worker_main.claim_loop(api, profile_mgr, project_lock))
    return worker_main, task, api, profile_mgr


async def test_same_profile_concurrency_runs_multiple_jobs_on_one_profile(monkeypatch):
    """ALLOW_SAME_PROFILE_CONCURRENCY: multiple jobs dispatched concurrently
    even though all share a single profile name."""
    two_started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        if active == 2:
            two_started.set()
        await release.wait()
        active -= 1
        return {"status": "completed"}

    worker_main, task, api, _ = await _run_loop_same_profile(
        monkeypatch,
        ["p1"],
        [_job("j1", "p1"), _job("j2", "p1")],
        dispatch,
        max_jobs=2,
    )

    await asyncio.wait_for(two_started.wait(), timeout=1)
    assert max_active == 2, "two concurrent jobs on same profile must reach max_active=2"

    worker_main._shutdown.set()
    release.set()
    await asyncio.wait_for(task, timeout=1)
    assert {job_id for job_id, _ in api.updates} == {"j1", "j2"}


async def test_same_profile_concurrency_requeue_unblocks_profile(monkeypatch):
    """Requeue path must not permanently drain the profile (deadlock fix):
    after a wipe-rewarm job is requeued, the profile must be available again
    for the next claim cycle."""
    from worker import main as worker_main

    release = asyncio.Event()
    requeued_once = False

    async def dispatch(job, profile_mgr, project_lock, *, manage_profile=True):
        nonlocal requeued_once
        await release.wait()
        if not requeued_once and job["id"] == "j-burn":
            requeued_once = True
            return {"status": "failed", "requeue": True, "error_message": "wipe-rewarm"}
        return {"status": "completed"}

    jobs = [_job("j-burn", "p1"), _job("j-follow", "p1")]
    api = _FakeAPI(jobs)
    profile_mgr = ProfileManager("./chrome-profiles", ["p1"])
    project_lock = ProjectLock()

    monkeypatch.setattr(worker_main, "MAX_CONCURRENT_JOBS", 1)
    monkeypatch.setattr(worker_main, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(worker_main, "_shutdown", asyncio.Event())
    monkeypatch.setattr(worker_main, "dispatch_job", dispatch)
    monkeypatch.setattr(worker_main, "ALLOW_SAME_PROFILE_CONCURRENCY", True)

    task = asyncio.create_task(worker_main.claim_loop(api, profile_mgr, project_lock))

    # Let j-burn run and requeue, then put j-burn back in the API queue.
    release.set()
    await asyncio.sleep(0.05)

    # Requeued job is back in the server queue; j-follow is also pending.
    api.jobs.append(_job("j-burn", "p1"))

    await asyncio.sleep(0.1)
    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=1)

    # j-follow must have been dispatched (profile was NOT stuck draining).
    dispatched_ids = {job_id for job_id, _ in api.updates}
    assert "j-follow" in dispatched_ids, (
        "profile must not stay in _draining after requeue — j-follow was never dispatched"
    )


async def test_env_var_case_insensitive_true_yes(monkeypatch):
    """ALLOW_SAME_PROFILE_CONCURRENCY / FLOW_USE_BASE_PROFILE / FLOW_BROWSER_POOL
    must accept 'True', 'YES', 'TRUE' in addition to lowercase."""
    import importlib
    from worker import main as worker_main

    for truthy in ("True", "YES", "TRUE", "1"):
        monkeypatch.setenv("ALLOW_SAME_PROFILE_CONCURRENCY", truthy)
        val = os.getenv("ALLOW_SAME_PROFILE_CONCURRENCY", "0").strip().lower() in (
            "1", "true", "yes"
        )
        assert val is True, f"Expected True for ALLOW_SAME_PROFILE_CONCURRENCY={truthy!r}"
