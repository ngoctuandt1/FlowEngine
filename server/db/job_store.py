"""Job CRUD operations (async, aiosqlite)."""

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Optional

from server.db.database import get_db
from server.db.profile_store import get_available_profiles
from server.models.chain import Chain
from server.models.job import BBox, Job, JobStatus, JobUpdate

# Job states that release the profile and stamp completed_at (B5 + B6).
# Hoisted to module level so claim/update paths share a single source of truth.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
RELATED_CHAIN_DEPTH_CAP = 256
logger = logging.getLogger(__name__)


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

    if d.get("ingredient_image_paths_json"):
        d["ingredient_image_paths"] = json.loads(d["ingredient_image_paths_json"])
    else:
        d["ingredient_image_paths"] = []
    del d["ingredient_image_paths_json"]

    return Job(**d)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_job(
    job: Job,
    *,
    db=None,
    commit: bool = True,
) -> Job:
    """Insert a new job row and return it."""
    if db is None:
        async with get_db() as db:
            return await create_job(job, db=db, commit=True)

    await db.execute(
        """
        INSERT INTO jobs (
            id, type, status, job_level, parent_job_id, chain_id,
            profile, project_url, media_id, edit_url,
            prompt, model, aspect_ratio, bbox_json, direction,
            start_image_path, end_image_path, ingredient_image_paths_json, ref_image_path,
            output_files_json, generation_id,
            worker_id, claimed_at, completed_at, error,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?, ?, ?, ?,
            ?, ?, ?, ?,
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
            json.dumps(job.ingredient_image_paths) if job.ingredient_image_paths else None,
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
    if commit:
        await db.commit()
    return job


async def create_chain_with_jobs(chain: Chain, jobs: list[Job]) -> list[Job]:
    """Create a chain row plus all child jobs in one transaction."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            await db.execute(
                """
                INSERT INTO chains (id, profile, project_url, media_id, status,
                                    created_at, updated_at)
                VALUES (?, ?, NULL, NULL, 'active', ?, ?)
                """,
                (
                    chain.id,
                    chain.profile,
                    chain.created_at.isoformat(),
                    chain.updated_at.isoformat(),
                ),
            )
            for job in jobs:
                await create_job(job, db=db, commit=False)
            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise
    return jobs


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

    new_status = fields.get("status")
    status_value: Optional[str] = None
    if new_status is not None:
        status_value = new_status.value if hasattr(new_status, "value") else new_status

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

    sets.append("updated_at = ?")
    params.append(_now_iso())

    params.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?"

    async with get_db() as db:
        cursor = await db.execute(sql, params)

        if status_value in TERMINAL_STATES:
            await db.execute(
                """
                UPDATE profiles
                SET current_job_id = NULL,
                    worker_id = NULL
                WHERE current_job_id = ?
                """,
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


async def get_jobs_by_chain(chain_id: str) -> list[Job]:
    """Return every job on a chain, oldest first, in one query."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM jobs WHERE chain_id = ? ORDER BY created_at ASC",
            (chain_id,),
        )
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


async def get_related_jobs(
    job_id: str,
    *,
    depth_cap: int = RELATED_CHAIN_DEPTH_CAP,
) -> Optional[dict]:
    """Fetch one job's lineage, adjacent relatives, and root-scoped stats."""
    async with get_db() as db:
        lineage_cursor = await db.execute(
            """
            WITH RECURSIVE ancestors(
                depth,
                id, type, status, job_level, parent_job_id, chain_id,
                profile, project_url, media_id, edit_url,
                prompt, model, aspect_ratio, bbox_json, direction,
                start_image_path, end_image_path, ingredient_image_paths_json, ref_image_path,
                output_files_json, generation_id,
                worker_id, claimed_at, completed_at, error,
                created_at, updated_at
            ) AS (
                SELECT
                    0,
                    jobs.id, jobs.type, jobs.status, jobs.job_level, jobs.parent_job_id, jobs.chain_id,
                    jobs.profile, jobs.project_url, jobs.media_id, jobs.edit_url,
                    jobs.prompt, jobs.model, jobs.aspect_ratio, jobs.bbox_json, jobs.direction,
                    jobs.start_image_path, jobs.end_image_path, jobs.ingredient_image_paths_json,
                    jobs.ref_image_path, jobs.output_files_json, jobs.generation_id,
                    jobs.worker_id, jobs.claimed_at, jobs.completed_at, jobs.error,
                    jobs.created_at, jobs.updated_at
                FROM jobs
                WHERE id = ?

                UNION ALL

                SELECT
                    ancestors.depth + 1,
                    parent.id, parent.type, parent.status, parent.job_level, parent.parent_job_id,
                    parent.chain_id, parent.profile, parent.project_url, parent.media_id,
                    parent.edit_url, parent.prompt, parent.model,
                    parent.aspect_ratio, parent.bbox_json, parent.direction,
                    parent.start_image_path, parent.end_image_path,
                    parent.ingredient_image_paths_json, parent.ref_image_path,
                    parent.output_files_json, parent.generation_id,
                    parent.worker_id, parent.claimed_at, parent.completed_at, parent.error,
                    parent.created_at, parent.updated_at
                FROM jobs AS parent
                JOIN ancestors ON ancestors.parent_job_id = parent.id
                WHERE ancestors.parent_job_id IS NOT NULL
                  AND ancestors.depth < ?
            ),
            root_job AS (
                SELECT id
                FROM ancestors
                ORDER BY depth DESC
                LIMIT 1
            ),
            chain_tree(depth, id, status) AS (
                SELECT 0, jobs.id, jobs.status
                FROM jobs
                JOIN root_job ON jobs.id = root_job.id

                UNION ALL

                SELECT chain_tree.depth + 1, child.id, child.status
                FROM jobs AS child
                JOIN chain_tree ON child.parent_job_id = chain_tree.id
                WHERE chain_tree.depth < ?
            )
            SELECT
                ancestors.*,
                (SELECT COUNT(*) FROM chain_tree) AS stat_total,
                (SELECT COUNT(*) FROM chain_tree WHERE status = 'completed') AS stat_completed,
                (SELECT COUNT(*) FROM chain_tree WHERE status = 'failed') AS stat_failed
            FROM ancestors
            ORDER BY depth ASC
            """,
            (job_id, depth_cap, depth_cap),
        )
        lineage_rows = await lineage_cursor.fetchall()
        if not lineage_rows:
            return None

        related_rows_cursor = await db.execute(
            """
            SELECT
                CASE
                    WHEN parent_job_id = ? THEN 'child'
                    ELSE 'sibling'
                END AS relation,
                jobs.*
            FROM jobs
            WHERE parent_job_id = ?
               OR (? IS NOT NULL AND parent_job_id = ? AND id <> ?)
            ORDER BY created_at ASC
            """,
            (
                job_id,
                job_id,
                lineage_rows[0]["parent_job_id"],
                lineage_rows[0]["parent_job_id"],
                job_id,
            ),
        )
        related_rows = await related_rows_cursor.fetchall()

    lineage_jobs = [_row_to_job(row) for row in lineage_rows]
    self_job = lineage_jobs[0]
    parent_job = lineage_jobs[1] if len(lineage_jobs) > 1 else None
    chain_root_job = lineage_jobs[-1]
    siblings: list[Job] = []
    children: list[Job] = []

    for row in related_rows:
        if row["relation"] == "child":
            children.append(_row_to_job(row))
        else:
            siblings.append(_row_to_job(row))

    total = int(lineage_rows[0]["stat_total"] or 0)
    completed = int(lineage_rows[0]["stat_completed"] or 0)
    failed = int(lineage_rows[0]["stat_failed"] or 0)

    return {
        "self": self_job,
        "parent": parent_job,
        "ancestors": list(reversed(lineage_jobs[1:])),
        "siblings": siblings,
        "children": children,
        "chain_id": next((job.chain_id for job in lineage_jobs if job.chain_id), None),
        "chain_root_id": chain_root_job.id,
        "stats": {
            "total": total,
            "completed": completed,
            "failed": failed,
            "pending": max(total - completed - failed, 0),
        },
    }


async def claim_next_job(
    worker_id: str, available_profiles: list[str]
) -> Optional[Job]:
    """Atomically claim the highest-priority pending job."""
    allowed_profile_set = set(await get_available_profiles(worker_id))
    effective_profiles = [p for p in available_profiles if p in allowed_profile_set]
    blocked_profiles = [p for p in available_profiles if p not in allowed_profile_set]
    if blocked_profiles:
        logger.warning(
            "Worker %s advertised unavailable or quarantined profiles: %s",
            worker_id,
            blocked_profiles,
        )
    if not effective_profiles:
        return None

    placeholders = ", ".join("?" for _ in effective_profiles)
    now = _now_iso()

    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                f"""
                SELECT j.*
                FROM jobs j
                JOIN jobs parent ON j.parent_job_id = parent.id
                WHERE j.status = 'pending'
                  AND j.job_level >= 2
                  AND parent.status = 'completed'
                  AND parent.profile IN ({placeholders})
                  AND parent.project_url IS NOT NULL
                  AND parent.media_id IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM jobs active
                      WHERE active.project_url = parent.project_url
                        AND active.project_url IS NOT NULL
                        AND active.status IN ('claimed', 'running')
                  )
                ORDER BY j.created_at ASC
                LIMIT 1
                """,
                effective_profiles,
            )
            row = await cursor.fetchone()

            if row is not None:
                job_dict = dict(row)
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
                effective_profiles,
            )
            row = await cursor.fetchone()

            if row is not None:
                job_dict = dict(row)
                assigned_profile = job_dict["profile"] or effective_profiles[0]

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
    """Find and reset jobs stuck in 'claimed' or 'running' for too long."""
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
            await db.execute(
                """
                UPDATE profiles
                SET current_job_id = NULL,
                    worker_id = NULL
                WHERE current_job_id = ?
                """,
                (job.id,),
            )
            job.status = JobStatus.PENDING
            recovered.append(job)

        await db.commit()
        return recovered


async def delete_job(job_id: str) -> Optional[Job]:
    """Delete only leaf pending jobs; otherwise preserve the row and cancel it."""
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            row = await cursor.fetchone()
            if row is None:
                await db.execute("ROLLBACK")
                return None

            job = _row_to_job(row)
            child_cursor = await db.execute(
                "SELECT 1 FROM jobs WHERE parent_job_id = ? LIMIT 1",
                (job_id,),
            )
            has_descendants = await child_cursor.fetchone() is not None

            if job.status == JobStatus.PENDING and not has_descendants:
                await db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
                await db.commit()
                job.status = JobStatus.CANCELLED
                return job

            if job.status != JobStatus.CANCELLED:
                now = _now_iso()
                await db.execute(
                    """
                    UPDATE jobs
                    SET status = 'cancelled',
                        completed_at = COALESCE(completed_at, ?),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, job_id),
                )
                await db.execute(
                    """
                    UPDATE profiles
                    SET current_job_id = NULL,
                        worker_id = NULL
                    WHERE current_job_id = ?
                    """,
                    (job_id,),
                )
                await db.commit()
                return await get_job(job_id)

            await db.commit()
            return job
        except Exception:
            await db.execute("ROLLBACK")
            raise
