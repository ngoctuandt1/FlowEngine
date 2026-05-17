"""Project-lock contention must requeue (not fail) the contending job.

Previously the dispatcher returned `status=failed` when `ProjectLock.acquire()`
returned False, which cascade-failed every descendant in the chain. Requeue
the job (status=pending, claimed_at=None) so it can be re-claimed on the next
worker cycle. Cap retries to avoid infinite churn on a permanently stuck
project.
"""
from __future__ import annotations

from unittest.mock import AsyncMock


class _ProfileManagerStub:
    def __init__(self):
        self.busy = []
        self.available = []

    def mark_busy(self, profile, job_id):
        self.busy.append((profile, job_id))

    def mark_available(self, profile):
        self.available.append(profile)


class _ContendingLock:
    """Always reports a contention hit for the target project_url."""

    def __init__(self, target_url: str):
        self._target = target_url
        self.acquired = []
        self.released = []

    def acquire(self, project_url, job_id):
        self.acquired.append((project_url, job_id))
        return False if project_url == self._target else True

    def release(self, project_url, job_id=None):
        self.released.append((project_url, job_id))


def _l2_job(job_id: str, project_url: str) -> dict:
    return {
        "id": job_id,
        "type": "extend-video",
        "profile": "profile-A",
        "project_url": project_url,
        "job_level": 2,
        "parent_job_id": "parent-x",
    }


async def test_lock_contention_requeues_instead_of_failing(monkeypatch):
    from worker import dispatcher

    # Reset module-level history so test isolation holds.
    dispatcher._lock_contention_history.clear()

    handler = AsyncMock()
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "extend-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_url = "https://flow.example/project/contended"
    lock = _ContendingLock(project_url)
    job = _l2_job("job-1", project_url)

    result = await dispatcher.dispatch_job(job, profile_mgr, lock)

    assert result["status"] == "pending"
    # claim ownership cleared so claim loop can pick it up again
    assert result.get("claimed_at") is None
    assert result.get("worker_id") is None
    assert "project_lock_busy" in result.get("error", "")
    # Profile lock returned to the pool.
    assert profile_mgr.available == ["profile-A"]
    # Handler was never invoked — we never held the lock.
    handler.assert_not_called()


async def test_lock_contention_caps_out_to_failure(monkeypatch):
    from worker import dispatcher

    dispatcher._lock_contention_history.clear()
    monkeypatch.setitem(
        dispatcher.HANDLER_MAP, "extend-video", AsyncMock(),
    )

    profile_mgr = _ProfileManagerStub()
    project_url = "https://flow.example/project/exhausted"
    lock = _ContendingLock(project_url)

    # First N hits requeue.
    last_result = None
    for i in range(dispatcher.LOCK_RETRY_MAX_PER_WINDOW):
        job = _l2_job(f"job-{i}", project_url)
        last_result = await dispatcher.dispatch_job(job, profile_mgr, lock)
        assert last_result["status"] == "pending", (i, last_result)

    # One past the cap fails terminally.
    job_final = _l2_job("job-final", project_url)
    final_result = await dispatcher.dispatch_job(job_final, profile_mgr, lock)
    assert final_result["status"] == "failed"
    assert "project_lock_exhausted" in final_result["error"]
    # Counter cleared after terminal failure so a recovered project can run again.
    assert project_url not in dispatcher._lock_contention_history


async def test_successful_acquire_clears_contention_counter(monkeypatch):
    from worker import dispatcher

    dispatcher._lock_contention_history.clear()

    handler = AsyncMock(return_value={"output_files": ["x.mp4"]})
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "extend-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_url = "https://flow.example/project/recovered"

    # Pre-seed contention history.
    dispatcher._record_lock_contention(project_url)
    dispatcher._record_lock_contention(project_url)
    assert len(dispatcher._lock_contention_history[project_url]) == 2

    class _PermissiveLock:
        def acquire(self, project_url, job_id):  # noqa: D401
            return True

        def release(self, project_url, job_id=None):  # noqa: D401
            pass

    job = _l2_job("job-recover", project_url)
    result = await dispatcher.dispatch_job(job, profile_mgr, _PermissiveLock())

    assert result["status"] == "completed"
    assert project_url not in dispatcher._lock_contention_history
