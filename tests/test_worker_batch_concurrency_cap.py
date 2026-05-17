"""Regression test: batch claim must not over-claim past MAX_CONCURRENT_JOBS.

Before the fix, the claim loop counted in-flight work at asyncio.Task
granularity. A single batch task carrying N jobs only counted as 1, so the
loop would happily claim another batch (and another) until N * iterations
exceeded MAX_CONCURRENT_JOBS. The fix tracks in-flight job count and gates
``slots = max_concurrent - jobs_in_flight`` on the real job total.
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


class _GatedBatchAPI:
    """First claim_batch returns 2 jobs; further claims block until released."""

    def __init__(self, first_payload: list[dict]) -> None:
        self.first_payload = first_payload
        self.batch_calls: list[tuple[list[str], int]] = []
        self.single_calls: list[list[str]] = []
        self.updates: list[tuple[str, dict]] = []
        self._served = False

    async def claim_job(self, profiles):
        self.single_calls.append(list(profiles))
        return None

    async def claim_batch(self, profiles, batch_size):
        self.batch_calls.append((list(profiles), batch_size))
        if self._served:
            return []
        self._served = True
        return list(self.first_payload)

    async def update_job(self, job_id, result):
        self.updates.append((job_id, result))
        return {"id": job_id}

    async def heartbeat(self):
        return None


async def test_batch_claim_respects_max_concurrent_at_job_granularity(monkeypatch):
    """With MAX_CONCURRENT_JOBS=2 and an in-flight 2-job batch, the loop
    must NOT issue another claim until at least one of those jobs finishes.

    Prior bug: ``len(in_flight)`` treated the batch task as 1 occupant, so
    a second claim_batch call would fire with slots=1 immediately."""
    from worker import main as worker_main

    payload = [_job("a", "p1"), _job("b", "p2")]
    api = _GatedBatchAPI(first_payload=payload)

    dispatch_gate = asyncio.Event()

    async def dispatch_batch(jobs, profile_mgr, project_lock):
        # Block until the test releases the gate so the 2-job batch
        # stays in-flight long enough to observe over-claim attempts.
        await dispatch_gate.wait()
        return [{"status": "completed", "job_id": j["id"]} for j in jobs]

    async def dispatch_job(job, *args, **kwargs):  # pragma: no cover - guard
        raise AssertionError("dispatch_job must not run for batch payload")

    profile_mgr = ProfileManager("./chrome-profiles", ["p1", "p2"])
    project_lock = ProjectLock()

    monkeypatch.setattr(worker_main, "MAX_CONCURRENT_JOBS", 2)
    monkeypatch.setattr(worker_main, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(worker_main, "_shutdown", asyncio.Event())
    monkeypatch.setattr(worker_main, "FLOW_CLAIM_BATCH_ENABLED", True)
    monkeypatch.setattr(worker_main, "dispatch_batch", dispatch_batch)
    monkeypatch.setattr(worker_main, "dispatch_job", dispatch_job)

    task = asyncio.create_task(worker_main.claim_loop(api, profile_mgr, project_lock))

    # Give the loop ample time to (a) make the first batch claim, then
    # (b) loop several iterations attempting to claim more capacity.
    for _ in range(50):
        if api.batch_calls:
            break
        await asyncio.sleep(0.01)

    initial_batch_count = len(api.batch_calls)
    initial_single_count = len(api.single_calls)
    assert initial_batch_count >= 1, "first claim_batch did not fire"

    # Let the loop spin ~30 iterations of POLL_INTERVAL_SEC=0.01s without
    # releasing the dispatch gate. With the bug present, the loop would
    # see ``len(in_flight) == 1 < max_concurrent=2`` and call claim_batch
    # repeatedly. With the fix, jobs_in_flight=2 == max_concurrent, so it
    # must stay at the original count.
    await asyncio.sleep(0.3)

    extra_batches = len(api.batch_calls) - initial_batch_count
    extra_singles = len(api.single_calls) - initial_single_count

    # Release dispatch + drain.
    dispatch_gate.set()
    for _ in range(200):
        if len(api.updates) >= 2:
            break
        await asyncio.sleep(0.01)
    worker_main._shutdown.set()
    await asyncio.wait_for(task, timeout=2)

    assert extra_batches == 0, (
        f"loop over-claimed: {extra_batches} extra claim_batch calls while "
        f"2 jobs were already in-flight (max_concurrent=2)"
    )
    assert extra_singles == 0, (
        f"loop over-claimed via single path: {extra_singles} extra claim_job "
        f"calls while 2 jobs already in-flight"
    )
