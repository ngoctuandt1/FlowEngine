"""Bug #3 — Store project_url after Level-2 operations.

Acceptance criteria:
- After Level-2 job completes, job.project_url is non-empty and equal to the
  project used by the pipeline.
- Level-3 job with parent_job_id=B resolves project_url directly from B — no
  grandparent traversal required.
- Applies to extend-video, insert-object, remove-object, camera-move.
"""

import asyncio
import os
import tempfile
from datetime import datetime

import pytest


# ---------------------------------------------------------------------------
# Shared temp-DB fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db(monkeypatch):
    """Point the server at a throwaway SQLite file and init the schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", path)

    # Reload modules that snapshot DATABASE_PATH at import time
    import importlib

    import server.config as cfg
    importlib.reload(cfg)
    import server.db.database as dbmod
    importlib.reload(dbmod)
    import server.db.job_store as js
    importlib.reload(js)

    asyncio.run(dbmod.init_db())
    yield path

    try:
        os.remove(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Unit test: dispatcher fills project_url for Level-2+ if handler omitted it
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("job_type", [
    "extend-video", "insert-object", "remove-object", "camera-move",
])
def test_dispatcher_backfills_project_url_for_level2(monkeypatch, job_type):
    """If a Level-2 handler result lacks project_url, dispatch_job must
    inject it from the claimed job so the server persists it."""
    from worker import dispatcher as disp
    from worker.profile_manager import ProfileManager
    from worker.project_lock import ProjectLock

    project_url = "https://labs.google/fx/tools/flow/project/proj-123"

    job = {
        "id": "job-child",
        "type": job_type,
        "profile": "profile-a",
        "project_url": project_url,
        "media_id": "media-1",
        "job_level": 2,
    }

    # Stub the operation handler: returns result WITHOUT project_url, to
    # simulate a buggy operation (the old engine's bg_* behavior).
    async def fake_handler(j):
        return {
            "media_id": j["media_id"],
            "edit_url": f"{project_url}/edit/{j['media_id']}",
            "output_files": ["/tmp/out.mp4"],
            "generation_id": "gen-1",
        }

    monkeypatch.setitem(disp.HANDLER_MAP, job_type, fake_handler)

    profile_mgr = ProfileManager(
        tempfile.mkdtemp(), ["profile-a"]
    )
    lock = ProjectLock()

    result = asyncio.run(disp.dispatch_job(job, profile_mgr, lock))

    assert result["status"] == "completed"
    assert result["project_url"] == project_url, (
        f"{job_type}: dispatch_job must persist project_url on Level-2+ result"
    )


def test_dispatcher_preserves_handler_project_url(monkeypatch):
    """If the handler already returned project_url, dispatch_job must not
    overwrite it (handler wins — it knows the true URL from the live page)."""
    from worker import dispatcher as disp
    from worker.profile_manager import ProfileManager
    from worker.project_lock import ProjectLock

    handler_url = "https://labs.google/fx/tools/flow/project/from-handler"
    job_url = "https://labs.google/fx/tools/flow/project/from-job"

    job = {
        "id": "job-child",
        "type": "extend-video",
        "profile": "profile-a",
        "project_url": job_url,
        "media_id": "media-1",
        "job_level": 2,
    }

    async def fake_handler(j):
        return {
            "project_url": handler_url,
            "media_id": j["media_id"],
            "output_files": [],
        }

    monkeypatch.setitem(disp.HANDLER_MAP, "extend-video", fake_handler)

    profile_mgr = ProfileManager(tempfile.mkdtemp(), ["profile-a"])
    lock = ProjectLock()

    result = asyncio.run(disp.dispatch_job(job, profile_mgr, lock))
    assert result["project_url"] == handler_url


# ---------------------------------------------------------------------------
# Integration: update_job persists project_url, Level-3 inherits from parent
# ---------------------------------------------------------------------------

def test_level3_inherits_project_url_directly_from_level2(temp_db):
    """After Level-2 completes with project_url stored, a new Level-3 child
    must resolve project_url from its immediate parent (B), not grandparent A.
    """
    import importlib
    import server.db.job_store as js
    importlib.reload(js)
    from server.models.job import (
        Job, JobCreate, JobStatus, JobType, JobUpdate,
    )

    project_url = "https://labs.google/fx/tools/flow/project/proj-abc"

    async def scenario():
        # --- Level-1: completed t2v with project_url set ---
        level1 = Job(
            type=JobType.TEXT_TO_VIDEO,
            status=JobStatus.COMPLETED,
            profile="profile-x",
            project_url=project_url,
            media_id="media-A",
            job_level=1,
        )
        await js.create_job(level1)

        # --- Level-2: create child of Level-1 (inherits project_url) ---
        from server.routes.jobs import create_single_job
        level2_req = JobCreate(
            type=JobType.EXTEND_VIDEO,
            parent_job_id=level1.id,
        )
        level2 = await create_single_job(level2_req)
        assert level2.project_url == project_url
        assert level2.job_level == 2

        # Simulate worker reporting Level-2 completion WITH project_url
        # (this is what dispatch_job returns — bug #3 ensures this field
        # is present for all Level-2 ops: extend/insert/remove/camera).
        await js.update_job(
            level2.id,
            JobUpdate(
                status=JobStatus.COMPLETED,
                project_url=project_url,
                media_id="media-A",
                edit_url=f"{project_url}/edit/media-A",
                output_files=["/tmp/ext.mp4"],
                profile="profile-x",
            ),
        )

        level2_db = await js.get_job(level2.id)
        assert level2_db.project_url == project_url, (
            "Level-2 must persist project_url after completion"
        )
        assert level2_db.status == JobStatus.COMPLETED

        # --- Level-3: parent = Level-2 B (NOT grandparent A) ---
        level3_req = JobCreate(
            type=JobType.INSERT_OBJECT,
            parent_job_id=level2.id,
            prompt="add seagulls",
        )
        level3 = await create_single_job(level3_req)

        # Acceptance: resolved directly from B, no grandparent lookup needed
        assert level3.project_url == project_url
        assert level3.parent_job_id == level2.id
        assert level3.job_level == 3

    asyncio.run(scenario())
