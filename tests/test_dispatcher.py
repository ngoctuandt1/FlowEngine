from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock


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


def _make_local_tmp() -> Path:
    root = Path("tests") / "_tmp" / f"path_{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


async def test_dispatch_frames_to_video_routes_to_handler(monkeypatch):
    from worker import dispatcher

    handler = AsyncMock(return_value={"output_files": ["out.mp4"], "media_id": "mid"})
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "frames-to-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-1",
        "type": "frames-to-video",
        "profile": "profile-a",
        "job_level": 1,
    }

    result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    handler.assert_awaited_once_with(job)
    assert result["status"] == "completed"
    assert result["profile"] == "profile-a"
    assert profile_mgr.busy == [("profile-a", "job-1")]
    assert profile_mgr.available == ["profile-a"]
    assert project_lock.acquired == []
    assert project_lock.released == []


async def test_dispatch_text_to_image_routes_to_handler(monkeypatch):
    from worker import dispatcher

    handler = AsyncMock(return_value={"output_files": ["out.png"], "media_id": "mid"})
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "text-to-image", handler)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-2",
        "type": "text-to-image",
        "profile": "profile-b",
        "job_level": 1,
    }

    result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    handler.assert_awaited_once_with(job)
    assert result["status"] == "completed"
    assert result["profile"] == "profile-b"
    assert profile_mgr.busy == [("profile-b", "job-2")]
    assert profile_mgr.available == ["profile-b"]


async def test_dispatch_ingredients_to_video_routes_to_handler(monkeypatch):
    from worker import dispatcher

    handler = AsyncMock(return_value={"output_files": ["out.mp4"], "media_id": "mid"})
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "ingredients-to-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-3",
        "type": "ingredients-to-video",
        "profile": "profile-c",
        "job_level": 1,
        "ingredient_image_paths": ["uploads/a.png", "uploads/b.png"],
    }

    result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    handler.assert_awaited_once_with(job)
    assert result["status"] == "completed"
    assert result["profile"] == "profile-c"
    assert profile_mgr.busy == [("profile-c", "job-3")]
    assert profile_mgr.available == ["profile-c"]


async def test_dispatch_audio_to_video_returns_stub_failure():
    from worker import dispatcher

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-4",
        "type": "audio-to-video",
        "profile": "profile-d",
        "job_level": 1,
        "prompt": "Sync visuals to the beat",
        "audio_path": "uploads/track.wav",
    }

    result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    assert result == {
        "status": "failed",
        "error": "audio-to-video driver not implemented",
    }
    assert profile_mgr.busy == [("profile-d", "job-4")]
    assert profile_mgr.available == ["profile-d"]
    assert project_lock.acquired == []
    assert project_lock.released == []


# Upload-path resolver security contract tests live in
# `tests/test_upload_resolution.py` (shipped separately via the
# worker-upload-hardening PR).
