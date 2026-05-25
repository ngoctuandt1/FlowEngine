from contextlib import asynccontextmanager
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

    def release(self, project_url, job_id=None):
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


async def test_omni_flash_text_to_video_dispatches_paid_mode(monkeypatch):
    from flow.operations import generate
    from worker import dispatcher

    captured = {}

    async def fake_text_to_video(client, **kwargs):
        captured.update(kwargs)
        return {"output_files": ["out.mp4"], "media_id": "mid"}

    class _ClientStub:
        pass

    @asynccontextmanager
    async def fake_client_lease(profile):
        yield _ClientStub()

    monkeypatch.setattr(generate, "text_to_video", fake_text_to_video)
    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)

    result = await dispatcher.handle_text_to_video({
        "id": "job-omni",
        "type": "text-to-video",
        "profile": "profile-paid",
        "prompt": "make omni video",
        "model": "omni-flash",
        "aspect_ratio": "16:9",
    })

    assert result["media_id"] == "mid"
    assert captured["model"] == "omni-flash"
    assert captured["free_mode"] is False


async def test_omni_flash_insert_dispatches_paid_mode(monkeypatch):
    from flow.operations import insert
    from worker import dispatcher

    captured = {}

    async def fake_insert_object(client, **kwargs):
        captured.update(kwargs)
        return {"output_files": ["out.mp4"], "media_id": "mid"}

    class _ClientStub:
        pass

    @asynccontextmanager
    async def fake_client_lease(profile):
        yield _ClientStub()

    monkeypatch.setattr(insert, "insert_object", fake_insert_object)
    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)

    result = await dispatcher.handle_insert({
        "id": "job-insert-omni",
        "type": "insert-object",
        "profile": "profile-paid",
        "prompt": "add neon helmet",
        "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
        "model": "omni-flash",
        "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent-mid",
    })

    assert result["media_id"] == "mid"
    assert captured["model"] == "omni-flash"
    assert captured["free_mode"] is False


async def test_insert_dispatches_default_lite_free_mode(monkeypatch):
    from flow.operations import insert
    from worker import dispatcher

    captured = {}

    async def fake_insert_object(client, **kwargs):
        captured.update(kwargs)
        return {"output_files": ["out.mp4"], "media_id": "mid"}

    class _ClientStub:
        pass

    @asynccontextmanager
    async def fake_client_lease(profile):
        yield _ClientStub()

    monkeypatch.setattr(insert, "insert_object", fake_insert_object)
    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)

    result = await dispatcher.handle_insert({
        "id": "job-insert-default",
        "type": "insert-object",
        "profile": "profile-free",
        "prompt": "add small sign",
        "bbox": {"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.4},
        "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent-mid",
    })

    assert result["media_id"] == "mid"
    assert captured["model"] == "veo-3.1-lite"
    assert captured["free_mode"] is True


async def test_batch_multitab_preserves_model_for_extend_jobs(monkeypatch):
    from worker import dispatcher
    import flow.operations._multitab as multitab_mod

    captured = {}

    async def fake_batch_dispatch_ops_multitab(client, op_jobs):
        captured["op_jobs"] = op_jobs
        return [
            {"job_id": job["id"], "status": "completed", "media_id": f"mid-{job['id']}"}
            for job in op_jobs
        ]

    class _ClientStub:
        pass

    @asynccontextmanager
    async def fake_client_lease(profile):
        yield _ClientStub()

    monkeypatch.setattr(
        multitab_mod, "batch_dispatch_ops_multitab", fake_batch_dispatch_ops_multitab,
    )
    monkeypatch.setattr(dispatcher, "_client_lease", fake_client_lease)

    jobs = [
        {
            "id": "batch-paid",
            "type": "extend-video",
            "profile": "profile-paid",
            "job_level": 2,
            "project_url": "https://labs.google/fx/tools/flow/project/p-paid",
            "parent_edit_url": "https://labs.google/fx/tools/flow/project/p-paid/edit/parent-mid",
            "parent_media_id": "parent-mid",
            "prompt": "extend paid",
            "model": "omni-flash",
        },
        {
            "id": "batch-default",
            "type": "extend-video",
            "profile": "profile-paid",
            "job_level": 2,
            "project_url": "https://labs.google/fx/tools/flow/project/p-paid",
            "parent_edit_url": "https://labs.google/fx/tools/flow/project/p-paid/edit/parent-mid-2",
            "parent_media_id": "parent-mid-2",
            "prompt": "extend default",
        },
    ]

    result = await dispatcher.dispatch_batch_multitab(
        jobs, _ProfileManagerStub(), _ProjectLockStub(),
    )

    assert [r["job_id"] for r in result] == ["batch-paid", "batch-default"]
    assert captured["op_jobs"][0]["model"] == "omni-flash"
    assert captured["op_jobs"][1].get("model") is None

# Upload-path resolver security contract tests live in
# `tests/test_upload_resolution.py` (shipped separately via the
# worker-upload-hardening PR).
