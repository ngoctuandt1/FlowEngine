"""Guard rails for L2 op types submitted via POST /api/jobs.

Without these checks, an L2 op (extend / insert / remove / camera-move) submitted
with neither `parent_job_id` nor a resolvable `project_url` would be created as
`job_level=1`. The dispatcher only acquires the project_lock for level>=2 jobs
(`worker/dispatcher.py`), so the L2 would run unlocked and bypass INV-4 (serial-
per-project) while also breaking INV-1 (same-profile chain) — its profile would
be picked by the unclaimed-L1 path instead of inherited from the L1 ancestor.
"""
from __future__ import annotations

import pytest


L2_OPS = ["extend-video", "camera-move", "insert-object", "remove-object"]


def _l2_payload(op_type: str) -> dict:
    base: dict = {
        "type": op_type,
        "prompt": "shared payload for L2 op gate",
    }
    if op_type == "camera-move":
        base["direction"] = "Dolly in"
    if op_type in {"insert-object", "remove-object"}:
        base["bbox"] = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}
    return base


@pytest.mark.parametrize("op_type", L2_OPS)
async def test_l2_op_without_parent_or_project_url_is_rejected(api_client, op_type):
    payload = _l2_payload(op_type)

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 422, response.text
    assert "L2" in response.json()["detail"]


@pytest.mark.parametrize("op_type", L2_OPS)
async def test_l2_op_with_project_url_but_no_completed_ancestor_rejected(
    api_client, op_type,
):
    payload = _l2_payload(op_type)
    payload["project_url"] = "https://labs.google/fx/tools/flow/project/orphan"

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 422, response.text
    assert "L2" in response.json()["detail"]


async def test_l2_op_resolves_parent_via_project_url_when_unambiguous(
    api_client,
):
    # Seed a completed L1 on a project owned by one profile.
    from datetime import UTC, datetime

    from server.db.job_store import create_job, update_job
    from server.models.job import Job, JobStatus, JobType, JobUpdate

    project_url = "https://labs.google/fx/tools/flow/project/resolve-me"
    parent = Job(
        type=JobType.TEXT_TO_VIDEO,
        prompt="seed parent",
        profile="profile-A",
        job_level=1,
        project_url=project_url,
        status=JobStatus.PENDING,
    )
    await create_job(parent)
    await update_job(
        parent.id,
        JobUpdate(
            status=JobStatus.COMPLETED,
            media_id="m-1",
            completed_at=datetime.now(UTC),
        ),
    )

    payload = _l2_payload("extend-video")
    payload["project_url"] = project_url

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["job_level"] == 2
    assert body["parent_job_id"] == parent.id
    assert body["profile"] == "profile-A"


async def test_l2_op_ambiguous_project_url_rejected(api_client):
    from datetime import UTC, datetime

    from server.db.job_store import create_job, update_job
    from server.models.job import Job, JobStatus, JobType, JobUpdate

    project_url = "https://labs.google/fx/tools/flow/project/ambiguous"
    for profile in ("profile-A", "profile-B"):
        l1 = Job(
            type=JobType.TEXT_TO_VIDEO,
            prompt="seed",
            profile=profile,
            job_level=1,
            project_url=project_url,
            status=JobStatus.PENDING,
        )
        await create_job(l1)
        await update_job(
            l1.id,
            JobUpdate(
                status=JobStatus.COMPLETED,
                media_id=f"m-{profile}",
                completed_at=datetime.now(UTC),
            ),
        )

    payload = _l2_payload("extend-video")
    payload["project_url"] = project_url

    response = await api_client.post("/api/jobs", json=payload)

    assert response.status_code == 422
    assert "ambiguous" in response.json()["detail"].lower() or \
        "multiple" in response.json()["detail"].lower()
