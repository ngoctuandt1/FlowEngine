"""Tests for flow-bugs issue #6 — camera-move direction lost.

The bug: frontend emitted `type: 'camera'` (not `'camera-move'`) and a
`camera_direction` field that the backend never read. As a result, any
camera-move job silently turned into a plain extend, and the preset
selection was lost.

Acceptance criteria verified:
  AC1. A camera-move job persisted via the normal pipeline keeps its
       `direction` value intact.
  AC2. The worker dispatcher routes `type='camera-move'` to the dedicated
       `handle_camera` handler (NOT to `handle_extend`), and the handler
       reads `job['direction']`.
  AC3. The frontend create-job and chain-builder pages now send the
       contract the backend expects: `type: 'camera-move'` and a
       top-level `direction` field.
"""

from pathlib import Path

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# DB bootstrap (mirrors tests/test_profile_pinning.py)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def db_path(tmp_path):
    import server.db.database as _db_module

    path = str(tmp_path / "test.db")
    old = _db_module.DATABASE_PATH
    _db_module.DATABASE_PATH = path

    from server.db.database import init_db
    await init_db()

    yield path

    _db_module.DATABASE_PATH = old


# ===========================================================================
# AC1 — `direction` survives the create → store → read round trip
# ===========================================================================

@pytest.mark.asyncio
async def test_camera_move_job_preserves_direction(db_path):
    """A JobCreate with type=camera-move and direction='Dolly in' must be
    stored and retrieved with the direction intact."""
    from server.models.job import JobCreate, JobType
    from server.routes.jobs import _build_job
    from server.db.job_store import create_job, get_job

    req = JobCreate(
        type=JobType.CAMERA_MOVE,
        direction="Dolly in",
        parent_job_id=None,
        project_url="https://labs.google.com/fx/proj/xyz",
    )

    job = _build_job(req, profile="alpha", job_level=2)
    await create_job(job)

    stored = await get_job(job.id)
    assert stored is not None
    assert stored.type == JobType.CAMERA_MOVE
    assert stored.direction == "Dolly in", (
        "camera-move job must persist the `direction` preset exactly"
    )


# ===========================================================================
# AC2 — Dispatcher routes camera-move to handle_camera (direction read)
# ===========================================================================

def test_handler_map_routes_camera_move_to_handle_camera():
    """Regression guard: camera-move MUST NOT be routed through
    handle_extend. If this mapping regresses, the direction field is
    silently dropped again."""
    from worker.dispatcher import HANDLER_MAP, handle_camera, handle_extend

    assert "camera-move" in HANDLER_MAP, "camera-move must be a registered job type"
    assert HANDLER_MAP["camera-move"] is handle_camera
    assert HANDLER_MAP["camera-move"] is not handle_extend


@pytest.mark.asyncio
async def test_handle_camera_reads_direction_from_job():
    """handle_camera must pass `job['direction']` into camera_move().
    We stub out FlowClient and the camera_move operation to capture the
    kwargs the handler forwards."""
    from unittest.mock import patch, MagicMock

    from worker import dispatcher

    captured = {}

    async def fake_camera_move(client, job, direction):
        captured["direction"] = direction
        captured["job"] = job
        return {"output_files": [], "media_id": "media-xyz"}

    # _make_client returns an async context manager wrapping a fake client
    fake_client = MagicMock()

    class _AsyncCM:
        async def __aenter__(self_inner):
            return fake_client

        async def __aexit__(self_inner, *a):
            return False

    with patch.object(dispatcher, "_make_client", return_value=_AsyncCM()), \
         patch("flow.operations.camera.camera_move", new=fake_camera_move):
        job = {
            "id": "job-1",
            "type": "camera-move",
            "profile": "alpha",
            "direction": "Orbit Left",
            "project_url": "https://labs.google.com/fx/proj/xyz",
        }
        result = await dispatcher.handle_camera(job)

    assert captured["direction"] == "Orbit Left", (
        "handle_camera must forward job['direction'] verbatim to camera_move()"
    )
    assert result["media_id"] == "media-xyz"


# ===========================================================================
# AC3 — Frontend sends the backend contract (camera-move + direction)
# ===========================================================================

FRONTEND_PAGES = Path(__file__).resolve().parent.parent / "frontend" / "js" / "pages"


def test_create_job_page_uses_camera_move_type_and_direction_field():
    """create-job.js must submit type='camera-move' and a `direction`
    field (not 'camera' / 'camera_direction')."""
    src = (FRONTEND_PAGES / "create-job.js").read_text(encoding="utf-8")

    # Correct contract present
    assert "'camera-move'" in src, "job type id must be 'camera-move'"
    assert "data.direction = camDir" in src, (
        "create-job.js must set the top-level `direction` field on the payload"
    )

    # Old, broken contract gone
    assert "data.camera_direction = camDir" not in src, (
        "legacy `camera_direction` payload field must be removed"
    )
    # A bare {id: 'camera', ...} JOB_TYPES entry would re-introduce the bug
    assert "{ id: 'camera'," not in src


def test_chain_builder_page_uses_camera_move_type_and_direction_field():
    """chain-builder.js must emit type='camera-move' and a `direction`
    field for each camera step."""
    src = (FRONTEND_PAGES / "chain-builder.js").read_text(encoding="utf-8")

    assert "'camera-move'" in src
    assert "step.direction = s.direction" in src, (
        "chain-builder.js must copy `direction` into the submitted step"
    )

    assert "s.camera_direction" not in src, (
        "legacy `camera_direction` state field must be removed"
    )
    assert "step.camera_direction" not in src
    assert "{ id: 'camera'," not in src
