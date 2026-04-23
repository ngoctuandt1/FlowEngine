class _ProfileManagerStub:
    def __init__(self):
        self.busy = []
        self.available = []

    def mark_busy(self, profile, job_id):
        self.busy.append((profile, job_id))

    def mark_available(self, profile):
        self.available.append(profile)


class _ProjectLockStub:
    def __init__(self):
        self.acquired = []
        self.released = []

    def acquire(self, project_url, job_id):
        self.acquired.append((project_url, job_id))
        return True

    def release(self, project_url):
        self.released.append(project_url)


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "type": "camera-move",
        "profile": "profile-a",
        "project_url": "https://flow/project/1",
        "job_level": 2,
        "direction": "Dolly in",
    }


class _FakeProjectSession:
    def __init__(self, _profile, _project_url, *, submit_errors=None, download_errors=None):
        self.submit_errors = submit_errors or {}
        self.download_errors = download_errors or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def submit_many(self, jobs):
        submitted = []
        for job in jobs:
            if job["id"] not in self.submit_errors:
                submitted.append((job, {"project_id": f"p-{job['id']}", "locale": "en"}))
        return submitted

    async def download_all(self, submitted):
        results = []
        for job, _ctx in submitted:
            if job["id"] not in self.download_errors:
                results.append((job, {
                    "media_id": f"mid-{job['id']}",
                    "output_files": [f"{job['id']}.mp4"],
                    "project_url": job["project_url"],
                }))
        return results


async def test_dispatch_batch_returns_completed_dicts(monkeypatch):
    from worker import dispatcher

    jobs = [_job("job-1"), _job("job-2"), _job("job-3")]
    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()

    monkeypatch.setattr(
        dispatcher,
        "ProjectSession",
        lambda profile, project_url: _FakeProjectSession(profile, project_url),
    )

    results = await dispatcher.dispatch_batch(jobs, profile_mgr, project_lock)

    assert [r["status"] for r in results] == ["completed", "completed", "completed"]
    assert [r["media_id"] for r in results] == ["mid-job-1", "mid-job-2", "mid-job-3"]
    assert profile_mgr.busy == [("profile-a", "job-1")]
    assert profile_mgr.available == ["profile-a"]
    assert project_lock.acquired == [("https://flow/project/1", "job-1")]
    assert project_lock.released == ["https://flow/project/1"]


async def test_dispatch_batch_submit_failure_does_not_abort_others(monkeypatch):
    from worker import dispatcher

    jobs = [_job("job-1"), _job("job-2"), _job("job-3")]
    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()

    monkeypatch.setattr(
        dispatcher,
        "ProjectSession",
        lambda profile, project_url: _FakeProjectSession(
            profile,
            project_url,
            submit_errors={"job-2": RuntimeError("submit exploded")},
        ),
    )

    results = await dispatcher.dispatch_batch(jobs, profile_mgr, project_lock)

    assert [r["status"] for r in results] == ["completed", "failed", "completed"]
    assert results[1]["error"] == "submit exploded"
    assert results[0]["media_id"] == "mid-job-1"
    assert results[2]["media_id"] == "mid-job-3"


async def test_dispatch_batch_download_failure_does_not_abort_others(monkeypatch):
    from worker import dispatcher

    jobs = [_job("job-1"), _job("job-2"), _job("job-3")]
    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()

    monkeypatch.setattr(
        dispatcher,
        "ProjectSession",
        lambda profile, project_url: _FakeProjectSession(
            profile,
            project_url,
            download_errors={"job-2": RuntimeError("download exploded")},
        ),
    )

    results = await dispatcher.dispatch_batch(jobs, profile_mgr, project_lock)

    assert [r["status"] for r in results] == ["completed", "failed", "completed"]
    assert results[1]["error"] == "download exploded"
    assert results[0]["media_id"] == "mid-job-1"
    assert results[2]["media_id"] == "mid-job-3"
