"""Job CRUD operations (async, aiosqlite)."""

import json
from datetime import UTC, datetime, timedelta
from typing import Optional

from server.db.database import get_db
from server.models.job import BBox, Job, JobStatus, JobUpdate

# Job states that release the profile and stamp completed_at (B5 + B6).
# Hoisted to module level so claim/update paths share a single source of truth.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_job(row) -> Job:
    """Convert an aiosqlite.Row to a Job model."""
    d = dict(row)

    # Deserialise JSON columns
    if d.get("bbox_json"):
        d["bbox"] = BBox(**json.loads(d["bbox_json"]))
    else:
        d["bbox"] = None
    del d["bbox_json"]

    if d.get("output_files_json"):
        d["output_files"] = json.loads(d["output_files_json"])
    else:
        d["output_files"] = []
    del d["output_files_json"]

    return Job(**d)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_job(job: Job) -> Job:
    """Insert a new job row and return it."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO jobs (
                id, type, status, job_level, parent_job_id, chain_id,
                profile, project_url, media_id, edit_url,
                prompt, model, aspect_ratio, bbox_json, direction,
                start_image_path, end_image_path, ref_image_path,
                output_files_json, generation_id,
                worker_id, claimed_at, completed_at, error,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?
            )
            """,
            (
                job.id,
                job.type.value,
                job.status.value,
                job.job_level,
                job.parent_job_id,
                job.chain_id,
                job.profile,
                job.project_url,
                job.media_id,
                job.edit_url,
                job.prompt,
                job.model,
                job.aspect_ratio,
                json.dumps(job.bbox.model_dump()) if job.bbox else None,
                job.direction,
                job.start_image_path,
                job.end_image_path,
                job.ref_image_path,
                json.dumps(job.output_files) if job.output_files else None,
                job.generation_id,
                job.worker_id,
                job.claimed_at.isoformat() if job.claimed_at else None,
                job.completed_at.isoformat() if job.completed_at else None,
                job.error,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return job


async def get_job(job_id: str) -> Optional[Job]:
    """Fetch a single job by id, or None."""
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_job(row)


async def list_jobs(
    *,
    status: Optional[JobStatus] = None,
    type: Optional[str] = None,
    profile: Optional[str] = None,
    chain_id: Optional[str] = None,
) -> list[Job]:
    """List jobs with optional filters."""
    clauses: list[str] = []
    params: list = []

    if status is not None:
        clauses.append("status = ?")
        params.append(status.value if isinstance(status, JobStatus) else status)
    if type is not None:
        clauses.append("type = ?")
        params.append(type)
    if profile is not None:
        clauses.append("profile = ?")
        params.append(profile)
    if chain_id is not None:
        clauses.append("chain_id = ?")
        params.append(chain_id)

    where = " AND ".join(clauses) if clauses else "1"
    query = f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC"

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


async def update_job(job_id: str, update: JobUpdate) -> Optional[Job]:
    """Apply partial update to a job. Returns updated Job or None if not found."""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_job(job_id)

    # Normalise incoming status (enum or str) once — reused by B5 stamp
    # and B6 profile clear below.
    new_status = fields.get("status")
    status_value: Optional[str] = None
    if new_status is not None:
        status_value = new_status.value if hasattr(new_status, "value") else new_status

    # B5: stamp completed_at when the caller moves the job into a terminal
    # state but didn't set the timestamp themselves. Explicit caller wins.
    if status_value in TERMINAL_STATES and "completed_at" not in fields:
        fields["completed_at"] = _now_iso()

    sets: list[str] = []
    params: list = []

    for key, value in fields.items():
        if key == "output_files":
            sets.append("output_files_json = ?")
            params.append(json.dumps(value) if value is not None else None)
        elif key == "status":
            sets.append("status = ?")
            params.append(value.value if isinstance(value, JobStatus) else value)
        elif key == "completed_at":
            sets.append("completed_at = ?")
            params.append(value.isoformat() if isinstance(value, datetime) else value)
        else:
            sets.append(f"{key} = ?")
            params.append(value)

    # Always bump updated_at
    sets.append("updated_at = ?")
    params.append(_now_iso())

    params.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?"

    async with get_db() as db:
        cursor = await db.execute(sql, params)

        # B6: release the profile pointer when the job reaches a terminal
        # state. Same connection as the jobs UPDATE so the two rows commit
        # together — UI never sees a profile pinned to a completed job.
        # No-op when no profile row references this job (e.g. worker using
        # a profile that was never registered in the DB).
        if status_value in TERMINAL_STATES:
            await db.execute(
                "UPDATE profiles SET current_job_id = NULL WHERE current_job_id = ?",
                (job_id,),
            )

        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_job(job_id)


async def get_children(parent_job_id: str) -> list[Job]:
    """Return all direct children of a given parent job."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM jobs WHERE parent_job_id = ? ORDER BY created_at",
            (parent_job_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


async def claim_next_job(
    worker_id: str, available_profiles: list[str]
) -> Optional[Job]:
    """Atomically claim the highest-priority pending job.

    Priority order (inside a single IMMEDIATE transaction):
      1. Level-2+ jobs whose parent is completed, parent profile is in
         *available_profiles*, and there is NO other active (claimed/running)
         job on the same project_url.
      2. Level-1 jobs assignable to any of the *available_profiles*.

    Returns the claimed Job or None if nothing qualifies.
    """
    if not available_profiles:
        return None

    placeholders = ", ".join("?" for _ in available_profiles)
    now = _now_iso()

    async with get_db() as db:
        # BEGIN IMMEDIATE gives us a write-lock immediately, preventing
        # concurrent workers from both claiming the same row.
        await db.execute("BEGIN IMMEDIATE")
        try:
            # ----- Priority 1: Level-2+ child jobs -----
            cursor = await db.execute(
                f"""
                SELECT j.*
                FROM jobs j
                JOIN jobs parent ON j.parent_job_id = parent.id
                WHERE j.status = 'pending'
                  AND j.job_level >= 2
                  AND parent.status = 'completed'
                  AND parent.profile IN ({placeholders})
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs active
                      WHERE active.project_url = parent.project_url
                        AND active.project_url IS NOT NULL
                        AND active.status IN ('claimed', 'running')
                  )
                ORDER BY j.created_at ASC
                LIMIT 1
                """,
                available_profiles,
            )
            row = await cursor.fetchone()

            if row is not None:
                job_dict = dict(row)
                # B4 profile + B22 target-field inheritance. The direct parent
                # is the single source of truth for child routing. Keep
                # project_url, media_id, and edit_url aligned to the same
                # direct-parent clip; splitting edit_url from an ancestor
                # media_id created an unrecoverable mismatch in Run 20 follow-up
                # jobs after landing recovery.
                parent_cur = await db.execute(
                    "SELECT profile, project_url, media_id, edit_url "
                    "FROM jobs WHERE id = ?",
                    (job_dict["parent_job_id"],),
                )
                parent_row = await parent_cur.fetchone()
                if parent_row is not None:
                    bound_profile = parent_row["profile"]
                    bound_project_url = parent_row["project_url"]
                    bound_media_id = parent_row["media_id"]
                    bound_edit_url: Optional[str] = parent_row["edit_url"]
                else:
                    bound_profile = None
                    bound_project_url = None
                    bound_media_id = None
                    bound_edit_url = None

                await db.execute(
                    """
                    UPDATE jobs
                    SET status = 'claimed',
                        worker_id = ?,
                        claimed_at = ?,
                        profile = ?,
                        project_url = ?,
                        media_id = ?,
                        edit_url = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        worker_id,
                        now,
                        bound_profile,
                        bound_project_url,
                        bound_media_id,
                        bound_edit_url,
                        now,
                        job_dict["id"],
                    ),
                )
                # B6: mirror the claim onto the profile row in the same
                # transaction so the dashboard sees a consistent view.
                await db.execute(
                    """
                    UPDATE profiles
                    SET current_job_id = ?, worker_id = ?, last_used_at = ?
                    WHERE name = ?
                    """,
                    (job_dict["id"], worker_id, now, bound_profile),
                )
                await db.commit()
                return await get_job(job_dict["id"])

            # ----- Priority 2: Level-1 jobs (any available profile) -----
            cursor = await db.execute(
                f"""
                SELECT *
                FROM jobs
                WHERE status = 'pending'
                  AND job_level = 1
                  AND (profile IS NULL OR profile IN ({placeholders}))
                ORDER BY created_at ASC
                LIMIT 1
                """,
                available_profiles,
            )
            row = await cursor.fetchone()

            if row is not None:
                job_dict = dict(row)
                # Assign first available profile if not already pinned
                assigned_profile = job_dict["profile"] or available_profiles[0]

                await db.execute(
                    """
                    UPDATE jobs
                    SET status = 'claimed',
                        worker_id = ?,
                        claimed_at = ?,
                        profile = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (worker_id, now, assigned_profile, now, job_dict["id"]),
                )
                # B6: mirror the claim onto the profile row so the dashboard
                # can show which profile is running which job.
                await db.execute(
                    """
                    UPDATE profiles
                    SET current_job_id = ?, worker_id = ?, last_used_at = ?
                    WHERE name = ?
                    """,
                    (job_dict["id"], worker_id, now, assigned_profile),
                )
                await db.commit()
                return await get_job(job_dict["id"])

            # Nothing to claim
            await db.execute("COMMIT")
            return None

        except Exception:
            await db.execute("ROLLBACK")
            raise


async def get_job_counts() -> dict[str, int]:
    """Return job counts grouped by status."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
        )
        rows = await cursor.fetchall()
        counts = {s.value: 0 for s in JobStatus}
        for row in rows:
            counts[row["status"]] = row["cnt"]
        return counts


async def recover_stale_jobs(stale_minutes: int = 30) -> list[Job]:
    """Find and reset jobs stuck in 'claimed' or 'running' for too long.

    These are jobs where the worker died or lost connection without
    reporting back. Reset them to 'pending' so they can be re-claimed.

    Returns list of recovered jobs.
    """
    cutoff = (datetime.now(UTC) - timedelta(minutes=stale_minutes)).isoformat()

    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT * FROM jobs
            WHERE status IN ('claimed', 'running')
              AND updated_at < ?
            ORDER BY created_at ASC
            """,
            (cutoff,),
        )
        rows = await cursor.fetchall()

        if not rows:
            return []

        recovered = []
        now = _now_iso()
        for row in rows:
            job = _row_to_job(row)
            await db.execute(
                """
                UPDATE jobs
                SET status = 'pending',
                    worker_id = NULL,
                    claimed_at = NULL,
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (f"Recovered from stale {job.status} (was stuck since {job.updated_at})", now, job.id),
            )
            job.status = JobStatus.PENDING
            recovered.append(job)

        await db.commit()
        return recovered


async def delete_job(job_id: str) -> bool:
    """Delete a job by id. Returns True if a row was removed."""
    async with get_db() as db:
        cursor = await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        await db.commit()
        return cursor.rowcount > 0
