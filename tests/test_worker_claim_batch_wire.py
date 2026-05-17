"""Wire-level tests for FLOW_CLAIM_BATCH_ENABLED in worker/main.py.

Validates the runtime branch added to ``worker.main.claim_loop``:

- When the env flag is OFF (default), the loop calls ``api.claim_job``
  (single-claim wire) regardless of capacity.
- When the env flag is ON and there's room for >1 job, the loop calls
  ``api.claim_batch`` and routes the returned jobs through
  ``dispatch_batch`` instead of ``dispatch_job``.
- A single-job batch result falls through to the legacy ``dispatch_job``
  path so the live-verified single-claim contract is preserved.
"""

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


class _BatchFakeAPI:
    """Tracks both claim_job and claim_batch invocations."""

    def __init__(self, batch_payload: list[dict]) -> None:
        self.batch_payload = batch_payload
        self.batch_calls: list[tuple[list[str], int]] = []
        self.single_calls: list[list[str]] = []
        self.updates: list[tuple[str, dict]] = []
        self._batch_served = False

    async def claim_job(self, profiles):
        self.single_calls.append(list(profiles))
        return None

    async def claim_batch(self, profiles, batch_size):
        self.batch_calls.append((list(profiles), batch_size))
        if self._batch_served:
            return []
        self._batch_served = True
        return list(self.batch_payload)

    async def update_job(self, job_id, result):
        self.updates.append((job_id, result))
        return {"id": job_id}

    async def heartbeat(self):
        return None


async def _start_loop(monkeypatch, profiles, api, *, max_jobs, batch_enabled,
                       dispatch_job=None, dispatch_batch=None):
    from worker import main as worker_main

    profile_mgr = ProfileManager("./chrome-profiles", profiles)
    project_lock = ProjectLock()

    monkeypatch.setattr(worker_main, "MAX_CONCURRENT_JOBS", max_jobs)
    monkeypatch.setattr(worker_main, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(worker_main, "_shutdown", asyncio.Event())
    monkeypatch.setattr(worker_main, "FLOW_CLAIM_BATCH_ENABLED", batch_enabled)
    if dispatch_job is not None:
        monkeypatch.setattr(worker_main, "dispatch_job", dispatch_job)
    if dispatch_batch is not None:
        monkeypatch.setattr(worker_main, "dispatch_batch", dispatch_batch)

    task = asyncio.create_task(worker_main.claim_loop(api, profile_mgr, project_lock))
    return worker_main, task, profile_mgr


async def test_claim_batch_disabled_uses_single_claim(monkeypatch):
    """Default (flag off): only api.claim_job is called."""
    api = _BatchFakeAPI(batch_payload=[])

    async def dispatch_job(job, *args, **kwargs):
        return {"status": "completed"}

    async def dispatch_batch(jobs, *args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("dispatch_batch must not run when flag is off")

    worker_main, task, _ = await _start_loop(
        monkeypatch,
        ["p1", "p2"],
        api,
        max_jobs=3,
        batch_enabled=False,
        dispatch_job=dispatch_job,
        dispatch_batch=dispatch_batch,
    )

    await asyncio.sleep(0.05)
    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=1)

    assert api.batch_calls == []
    assert api.single_calls, "claim_job must be polled at least once"


async def test_claim_batch_enabled_routes_through_dispatch_batch(monkeypatch):
    """Flag on + slots>1 + batch returns N>1: dispatch_batch is invoked."""
    payload = [_job("a", "p1"), _job("b", "p2")]
    api = _BatchFakeAPI(batch_payload=payload)

    dispatched: list[list[str]] = []
    single_dispatched: list[str] = []

    async def dispatch_job(job, *args, **kwargs):  # pragma: no cover - guard
        single_dispatched.append(job["id"])
        return {"status": "completed"}

    async def dispatch_batch(jobs, profile_mgr, project_lock):
        dispatched.append([j["id"] for j in jobs])
        return [
            {"status": "completed", "job_id": j["id"]}
            for j in jobs
        ]

    worker_main, task, _ = await _start_loop(
        monkeypatch,
        ["p1", "p2"],
        api,
        max_jobs=4,
        batch_enabled=True,
        dispatch_job=dispatch_job,
        dispatch_batch=dispatch_batch,
    )

    # Wait until results are reported back through update_job.
    for _ in range(200):
        if len(api.updates) >= 2:
            break
        await asyncio.sleep(0.01)

    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert api.batch_calls, "claim_batch must be called when flag on + slots>1"
    assert dispatched == [["a", "b"]]
    assert single_dispatched == [], "dispatch_job must not run for batch payload"
    assert sorted(job_id for job_id, _ in api.updates) == ["a", "b"]


async def test_claim_batch_single_job_payload_falls_back_to_dispatch_job(monkeypatch):
    """Batch claim returning 1 job uses the legacy single-job dispatch path."""
    payload = [_job("solo", "p1")]
    api = _BatchFakeAPI(batch_payload=payload)

    single_dispatched: list[str] = []
    batch_dispatched: list[list[str]] = []

    async def dispatch_job(job, *args, **kwargs):
        single_dispatched.append(job["id"])
        return {"status": "completed"}

    async def dispatch_batch(jobs, *args, **kwargs):  # pragma: no cover - guard
        batch_dispatched.append([j["id"] for j in jobs])
        return [{"status": "completed", "job_id": j["id"]} for j in jobs]

    worker_main, task, _ = await _start_loop(
        monkeypatch,
        ["p1", "p2"],
        api,
        max_jobs=4,
        batch_enabled=True,
        dispatch_job=dispatch_job,
        dispatch_batch=dispatch_batch,
    )

    for _ in range(200):
        if api.updates:
            break
        await asyncio.sleep(0.01)

    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert single_dispatched == ["solo"]
    assert batch_dispatched == [], "1-job payload must skip dispatch_batch"
