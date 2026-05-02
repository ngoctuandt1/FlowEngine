"""B4 regression — chains table must be persisted + aggregated status exposed.

Before B4: `chains` table was CREATE'd in `server/db/database.py:12-20` but never
INSERT'd. `POST /api/chains` created jobs with a shared `chain_id` but left the
chains row orphan (FK pointing into an empty table). Frontend had to GROUP BY
`jobs.chain_id` every render to compute chain-level status.

After B4 (Choice C — Hybrid):
- `POST /api/chains` INSERTs one row into `chains` with id + profile + timestamps
  (immutable metadata only).
- No `update_job` path writes back to chains.status → no two-table drift.
- `GET /api/chains/{id}` returns `{id, profile, created_at, status, progress, jobs}`
  where `status` and `progress` are **derived** on-demand from `jobs` GROUP BY.
- `chains.status` column stays at its DEFAULT value forever (vestigial, not surfaced).
"""

from server.db.chain_store import (
    compute_aggregated_status,
    create_chain,
    get_chain_aggregate,
    get_chain_row,
)
from server.db.database import get_db
from server.db.job_store import create_job, update_job
from server.models.chain import Chain
from server.models.job import Job, JobStatus, JobType, JobUpdate


from datetime import UTC, datetime


async def _create_project(api_client, name: str) -> str:
    response = await api_client.post("/api/projects", json={"name": name})
    assert response.status_code == 201
    return response.json()["id"]


def _make_pending_job(job_id: str, chain_id: str, level: int = 1) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=level,
        chain_id=chain_id,
        prompt="x",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Unit — compute_aggregated_status helper
# ---------------------------------------------------------------------------

def test_status_rule_all_pending():
    assert compute_aggregated_status(["pending", "pending"]) == "pending"


def test_status_rule_any_failed_wins():
    # Even with completed + running in the mix, any failed → failed.
    assert compute_aggregated_status(["completed", "running", "failed"]) == "failed"
    assert compute_aggregated_status(["failed"]) == "failed"


def test_status_rule_any_running_or_claimed():
    assert compute_aggregated_status(["pending", "running"]) == "running"
    assert compute_aggregated_status(["pending", "claimed"]) == "running"
    assert compute_aggregated_status(["completed", "running"]) == "running"


def test_status_rule_all_completed():
    assert compute_aggregated_status(["completed"]) == "completed"
    assert compute_aggregated_status(["completed", "completed"]) == "completed"


def test_status_rule_mixed_pending_completed_is_running():
    # In-progress chain — some done, some queued. Rule from prompt.
    assert compute_aggregated_status(["pending", "completed"]) == "running"


def test_status_rule_empty_list_is_pending():
    # Defensive — chain row with zero jobs. Treat as pending.
    assert compute_aggregated_status([]) == "pending"


# ---------------------------------------------------------------------------
# DB — create_chain + get_chain_row
# ---------------------------------------------------------------------------

async def test_create_chain_persists_row(db):
    """Choice C core: chain row is actually INSERT'd."""
    chain = Chain(id="b4-chain-a", profile="prof-a")
    await create_chain(chain)

    row = await get_chain_row("b4-chain-a")
    assert row is not None
    assert row.id == "b4-chain-a"
    assert row.profile == "prof-a"
    assert row.created_at is not None


async def test_get_chain_row_returns_none_for_unknown(db):
    assert await get_chain_row("nope") is None


# ---------------------------------------------------------------------------
# API — POST /api/chains INSERTs chain row
# ---------------------------------------------------------------------------

async def test_post_chains_inserts_chain_row(api_client):
    """POST /api/chains now writes to chains table — not just jobs table."""
    payload = {
        "profile": "prof-x",
        "jobs": [
            {"type": "text-to-video", "prompt": "hello"},
            {"type": "extend-video", "prompt": "more"},
        ],
    }
    r = await api_client.post("/api/chains", json=payload)
    assert r.status_code == 201
    chain_id = r.json()["chain_id"]

    # Direct DB check: chains row exists with the returned id.
    async with get_db() as conn:
        cursor = await conn.execute(
            "SELECT id, profile FROM chains WHERE id = ?", (chain_id,)
        )
        row = await cursor.fetchone()
    assert row is not None, "POST /api/chains must INSERT into chains table"
    assert row["id"] == chain_id
    assert row["profile"] == "prof-x"


async def test_post_chains_rejects_empty_jobs(api_client):
    response = await api_client.post("/api/chains", json={"jobs": []})

    assert response.status_code == 422


async def test_post_chain_propagates_first_step_project_id(api_client):
    project_id = await _create_project(api_client, "Chain Project")

    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "project-chain-profile",
            "jobs": [
                {
                    "type": "text-to-video",
                    "prompt": "Start a branded chain",
                    "project_id": project_id,
                },
                {
                    "type": "extend-video",
                    "prompt": "Continue the same project",
                },
            ],
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["jobs"][0]["project_id"] == project_id
    assert body["jobs"][1]["project_id"] == project_id


async def test_post_chain_rejects_mixed_step_project_ids(api_client):
    first_project_id = await _create_project(api_client, "Chain Project A")
    second_project_id = await _create_project(api_client, "Chain Project B")

    response = await api_client.post(
        "/api/chains",
        json={
            "profile": "project-chain-profile",
            "jobs": [
                {
                    "type": "text-to-video",
                    "prompt": "Start a branded chain",
                    "project_id": first_project_id,
                },
                {
                    "type": "extend-video",
                    "prompt": "Keep going",
                },
                {
                    "type": "extend-video",
                    "prompt": "Try to switch projects mid-chain",
                    "project_id": second_project_id,
                },
            ],
        },
    )

    assert response.status_code == 422
    assert "same project_id as the first step" in response.json()["detail"]


# ---------------------------------------------------------------------------
# API — GET /api/chains/{id} aggregated response
# ---------------------------------------------------------------------------

async def test_get_chain_returns_aggregated_all_pending(api_client):
    payload = {
        "profile": "prof-y",
        "jobs": [
            {"type": "text-to-video", "prompt": "p1"},
            {"type": "extend-video", "prompt": "p2"},
            {"type": "extend-video", "prompt": "p3"},
        ],
    }
    post = await api_client.post("/api/chains", json=payload)
    chain_id = post.json()["chain_id"]

    r = await api_client.get(f"/api/chains/{chain_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == chain_id
    assert body["profile"] == "prof-y"
    assert body["status"] == "pending"
    assert body["progress"] == {"completed": 0, "total": 3}
    assert len(body["jobs"]) == 3


async def test_get_chain_status_all_completed(api_client):
    payload = {
        "profile": "prof-c",
        "jobs": [
            {"type": "text-to-video", "prompt": "p1"},
            {"type": "extend-video", "prompt": "p2"},
        ],
    }
    post = await api_client.post("/api/chains", json=payload)
    body = post.json()
    chain_id = body["chain_id"]
    job_ids = [j["id"] for j in body["jobs"]]

    for jid in job_ids:
        await update_job(jid, JobUpdate(status=JobStatus.COMPLETED))

    r = await api_client.get(f"/api/chains/{chain_id}")
    body = r.json()
    assert body["status"] == "completed"
    assert body["progress"] == {"completed": 2, "total": 2}


async def test_get_chain_status_any_failed(api_client):
    payload = {
        "profile": "prof-f",
        "jobs": [
            {"type": "text-to-video", "prompt": "p1"},
            {"type": "extend-video", "prompt": "p2"},
        ],
    }
    post = await api_client.post("/api/chains", json=payload)
    body = post.json()
    chain_id = body["chain_id"]
    job_ids = [j["id"] for j in body["jobs"]]

    await update_job(job_ids[0], JobUpdate(status=JobStatus.COMPLETED))
    await update_job(job_ids[1], JobUpdate(status=JobStatus.FAILED, error="boom"))

    r = await api_client.get(f"/api/chains/{chain_id}")
    body = r.json()
    assert body["status"] == "failed"
    # progress counts completed only (not failed); total = all jobs
    assert body["progress"] == {"completed": 1, "total": 2}


async def test_get_chain_status_any_running(api_client):
    payload = {
        "profile": "prof-r",
        "jobs": [
            {"type": "text-to-video", "prompt": "p1"},
            {"type": "extend-video", "prompt": "p2"},
        ],
    }
    post = await api_client.post("/api/chains", json=payload)
    body = post.json()
    chain_id = body["chain_id"]
    job_ids = [j["id"] for j in body["jobs"]]

    await update_job(job_ids[0], JobUpdate(status=JobStatus.RUNNING))

    r = await api_client.get(f"/api/chains/{chain_id}")
    body = r.json()
    assert body["status"] == "running"


async def test_get_chain_status_mixed_pending_completed(api_client):
    """Mixed partial progress → running (chain is in-progress)."""
    payload = {
        "profile": "prof-m",
        "jobs": [
            {"type": "text-to-video", "prompt": "p1"},
            {"type": "extend-video", "prompt": "p2"},
            {"type": "extend-video", "prompt": "p3"},
        ],
    }
    post = await api_client.post("/api/chains", json=payload)
    body = post.json()
    chain_id = body["chain_id"]
    job_ids = [j["id"] for j in body["jobs"]]

    await update_job(job_ids[0], JobUpdate(status=JobStatus.COMPLETED))

    r = await api_client.get(f"/api/chains/{chain_id}")
    body = r.json()
    assert body["status"] == "running"
    assert body["progress"] == {"completed": 1, "total": 3}


async def test_get_chain_404_for_unknown_id(api_client):
    r = await api_client.get("/api/chains/does-not-exist")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Trip-wire — Choice C contract: update_job MUST NOT sync to chains.status
# ---------------------------------------------------------------------------

async def test_chain_status_not_synced_from_job_update(db):
    """Choice C trip-wire: chains.status column stays at INSERT default
    after job transitions. If a future change adds an UPDATE path, this
    test breaks — and the PR must either keep the no-sync invariant or
    update the test with rationale."""
    chain = Chain(id="b4-nosync", profile="prof-n")
    await create_chain(chain)

    job = _make_pending_job("b4-nosync-job", "b4-nosync")
    await create_job(job)

    # Baseline: whatever DEFAULT the schema chose.
    baseline_row = await get_chain_row("b4-nosync")
    baseline_status = baseline_row.status

    # Drive the job through all terminal states — chain row must not move.
    await update_job("b4-nosync-job", JobUpdate(status=JobStatus.RUNNING))
    await update_job("b4-nosync-job", JobUpdate(status=JobStatus.COMPLETED))

    after_row = await get_chain_row("b4-nosync")
    assert after_row.status == baseline_status, (
        "Choice C invariant: update_job must NOT write to chains.status. "
        "Aggregated status is derived on-demand to keep a single source of truth."
    )

    # But the derived aggregate must reflect the completion.
    aggregate = await get_chain_aggregate("b4-nosync")
    assert aggregate.status == "completed"


async def test_get_chain_aggregate_includes_job_ids_in_order(db):
    """Jobs listed by created_at ASC so chain builder can render in order."""
    chain = Chain(id="b4-order", profile="prof-o")
    await create_chain(chain)

    # Create three jobs with explicit ordering
    import asyncio
    for i, jid in enumerate(["a", "b", "c"]):
        await create_job(_make_pending_job(f"b4-order-{jid}", "b4-order", level=i + 1))
        # Tiny sleep to ensure distinct created_at timestamps on fast hardware
        await asyncio.sleep(0.01)

    aggregate = await get_chain_aggregate("b4-order")
    assert aggregate.jobs == ["b4-order-a", "b4-order-b", "b4-order-c"]
    assert aggregate.progress.completed == 0
    assert aggregate.progress.total == 3
