"""Server-side camera-move direction validator (issue #39).

Before this validator, a typo like "Pan left" or "Tilt up" would pass
request validation, get claimed by a worker, then silently no-op in the
Flow UI (those preset names don't exist in the DOM). The claim cycle is
wasted and the chain stalls without a clear error.

These tests lock in the fail-fast behavior at POST /api/jobs and
POST /api/chains time, plus a drift guard that keeps the server's
preset list in sync with the browser-side source of truth.
"""

import pytest

from server.models.job import CAMERA_PRESETS, JobCreate, JobType


# --- Direct Pydantic validation (no HTTP layer) -----------------------------

def test_camera_move_accepts_valid_motion_preset():
    req = JobCreate(type=JobType.CAMERA_MOVE, direction="Dolly in")
    assert req.direction == "Dolly in"


def test_camera_move_accepts_valid_position_preset():
    req = JobCreate(type=JobType.CAMERA_MOVE, direction="Center")
    assert req.direction == "Center"


@pytest.mark.parametrize("bogus", ["Pan left", "Tilt up", "Zoom in", "", "dolly in"])
def test_camera_move_rejects_unknown_direction(bogus):
    with pytest.raises(ValueError, match="camera preset|requires 'direction'"):
        JobCreate(type=JobType.CAMERA_MOVE, direction=bogus)


def test_camera_move_without_direction_rejected():
    with pytest.raises(ValueError, match="requires 'direction'"):
        JobCreate(type=JobType.CAMERA_MOVE)


def test_non_camera_type_ignores_direction():
    """direction field on a non-camera-move job is accepted (backward compat)."""
    req = JobCreate(type=JobType.TEXT_TO_VIDEO, prompt="hello", direction="Pan left")
    assert req.direction == "Pan left"


# --- HTTP integration ------------------------------------------------------

async def test_post_jobs_rejects_invalid_camera_direction(api_client):
    payload = {"type": "camera-move", "direction": "Pan left"}
    resp = await api_client.post("/api/jobs", json=payload)
    assert resp.status_code == 422
    body = resp.json()
    # Pydantic surfaces validator errors under detail[].msg
    joined = str(body)
    assert "Pan left" in joined or "camera preset" in joined


async def test_post_jobs_accepts_valid_camera_direction(api_client):
    # Seed an L1 parent so the L2 camera-move has something to attach to;
    # the validator runs BEFORE the parent lookup so we can skip the parent
    # link entirely here — camera-move without a parent simply sits as L1
    # pending, which is fine for this test.
    payload = {"type": "camera-move", "direction": "Orbit left"}
    resp = await api_client.post("/api/jobs", json=payload)
    assert resp.status_code == 201, resp.text
    assert resp.json()["direction"] == "Orbit left"


async def test_post_chains_rejects_invalid_camera_direction(api_client):
    payload = {
        "jobs": [
            {"type": "text-to-video", "prompt": "seed"},
            {"type": "camera-move", "direction": "Tilt up"},
        ]
    }
    resp = await api_client.post("/api/chains", json=payload)
    assert resp.status_code == 422
    assert "Tilt up" in resp.text or "camera preset" in resp.text


# --- Drift guard ----------------------------------------------------------

def test_preset_list_matches_flow_operations_camera():
    """Server's CAMERA_PRESETS must stay in sync with flow/operations/camera.py.

    If either list changes, this test fails loudly — forcing the author
    to update both (or intentionally diverge and update this test).
    """
    from flow.operations.camera import ALL_PRESETS

    assert CAMERA_PRESETS == frozenset(ALL_PRESETS), (
        "CAMERA_PRESETS in server/models/job.py drifted from "
        "flow.operations.camera.ALL_PRESETS. Keep both in sync."
    )
