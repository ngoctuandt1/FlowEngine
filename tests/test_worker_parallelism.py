import asyncio

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
