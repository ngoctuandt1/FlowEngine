"""Job CRUD operations (async, aiosqlite)."""

import json
import logging
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Optional

from server.db.database import get_db
from server.db.profile_store import get_available_profiles
from server.models.chain import Chain
from server.models.job import BBox, Job, JobStatus, JobUpdate

# Job states that release the profile and stamp completed_at (B5 + B6).
# Hoisted to module level so claim/update paths share a single source of truth.
TERMINAL_STATES = frozenset({"completed", "failed", "cancelled"})
TERMINAL_FAILURE_STATES = frozenset({"failed", "cancelled"})
CASCADE_CANCEL_STATES = frozenset({"pending", "running"})
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


async def _broadcast_cascaded_jobs(jobs: list[Job]) -> None:
    if not jobs:
        return
    try:
        from server.routes.ws import broadcast_job_update

        for job in jobs:
            await broadcast_job_update(job)
    except Exception:
        logger.exception("failed to broadcast cascaded job updates")


async def _cascade_cancel_descendants(
    db,
    parent_job_id: str,
    parent_status: str,
    now: str,
) -> list[Job]:
    cursor = await db.execute(
        """
        WITH RECURSIVE descendants(id, depth) AS (
            SELECT id, 1
            FROM jobs
            WHERE parent_job_id = ?
            UNION ALL
            SELECT child.id, descendants.depth + 1
            FROM jobs AS child
            JOIN descendants ON child.parent_job_id = descendants.id
            WHERE descendants.depth < ?
        )
        SELECT jobs.id
        FROM jobs
        JOIN descendants ON descendants.id = jobs.id
        WHERE jobs.status IN (?, ?)
        ORDER BY jobs.created_at ASC
        """,
        (
            parent_job_id,
            RELATED_CHAIN_DEPTH_CAP,
            *CASCADE_CANCEL_STATES,
        ),
    )
    target_ids = [row["id"] for row in await cursor.fetchall()]
    if not target_ids:
        return []

    placeholders = ", ".join("?" for _ in target_ids)
    error = f"parent_failed: {parent_job_id} ({parent_status})"
    await db.execute(
        f"""
        UPDATE jobs
        SET status = ?,
            error = ?,
            completed_at = ?,
            updated_at = ?
        WHERE id IN ({placeholders})
          AND status IN (?, ?)
        """,
        (
            JobStatus.CANCELLED.value,
            error,
            now,
            now,
            *target_ids,
            *CASCADE_CANCEL_STATES,
        ),
    )
    await db.execute(
        f"""
        UPDATE profiles
        SET current_job_id = NULL,
            worker_id = NULL
        WHERE current_job_id IN ({placeholders})
        """,
        target_ids,
    )
    cursor = await db.execute(
        f"SELECT * FROM jobs WHERE id IN ({placeholders}) ORDER BY created_at ASC",
        target_ids,
    )
    rows = await cursor.fetchall()
    return [_row_to_job(r) for r in rows]


async def _job_columns(db) -> set[str]:
    cursor = await db.execute("PRAGMA table_info(jobs)")
    rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def _ensure_chain_row(db, job: Job) -> None:
    if not job.chain_id:
        return
    created_at = job.created_at.isoformat()
    updated_at = job.updated_at.isoformat()
    await db.execute(
        """
        INSERT OR IGNORE INTO chains (
            id, profile, project_url, media_id, status, created_at, updated_at
        ) VALUES (?, ?, NULL, NULL, 'active', ?, ?)
        """,
        (job.chain_id, job.profile, created_at, updated_at),
    )


async def _validate_project_id(db, job: Job, columns: set[str]) -> None:
    if "project_id" not in columns or not job.project_id:
        return
    cursor = await db.execute(
        "SELECT 1 FROM projects WHERE id = ?",
        (job.project_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        raise sqlite3.IntegrityError("FOREIGN KEY constraint failed")


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

    columns = await _job_columns(db)
    await _ensure_chain_row(db, job)
    await _validate_project_id(db, job, columns)
    include_project_id = "project_id" in columns
    project_id_column = "project_id, " if include_project_id else ""
    project_id_placeholder = "?, " if include_project_id else ""
    project_id_value = (job.project_id,) if include_project_id else ()
    await db.execute(
        f"""
        INSERT INTO jobs (
            id, type, status, job_level, parent_job_id, chain_id,
            profile, {project_id_column}project_url, media_id, edit_url,
            prompt, model, aspect_ratio, bbox_json, direction,
            start_image_path, end_image_path, ingredient_image_paths_json, ref_image_path,
            output_files_json, generation_id,
            worker_id, claimed_at, completed_at, error,
            created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?,
            ?, {project_id_placeholder}?, ?, ?,
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
            *project_id_value,
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
    limit: Optional[int] = None,
    offset: int = 0,
) -> list[Job]:
    """List jobs with optional filters. limit=None returns all rows (internal use only)."""
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
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
        if offset:
            query += " OFFSET ?"
            params.append(offset)

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


async def list_pending_l1_siblings(
    *,
    project_url: Optional[str] = None,
    profile: Optional[str] = None,
    limit: int = 5,
) -> list[Job]:
    """List pending L1 jobs that can be batched into one Chrome.

    PRD §3.2 Phase 1. Used by the worker batch claim path: after claiming
    one L1 t2v, peek the next N-1 candidates that share the same project
    target (or the same profile when no project_url is bound yet).

    Filters:
      * status = 'pending'
      * job_level = 1
      * type = 'text-to-video' (Phase 1 only — image / frames defer)
      * project_url = ? (or NULL when None)
      * profile = ? (or any when None)

    Order: created_at ASC (FIFO so first-submitted siblings drain first).
    """
    if limit < 1:
        return []
    if limit > 20:
        limit = 20

    clauses = [
        "status = 'pending'",
        "job_level = 1",
        "type = 'text-to-video'",
    ]
    params: list = []
    if project_url is None:
        clauses.append("(project_url IS NULL OR project_url = '')")
    else:
        clauses.append("project_url = ?")
        params.append(project_url)
    if profile is not None:
        clauses.append("profile = ?")
        params.append(profile)

    query = (
        f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at ASC LIMIT ?"
    )
    params.append(limit)

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


L2_OP_TYPES = ("extend-video", "camera-move", "insert-object", "remove-object")


async def list_pending_l2_siblings(
    *,
    parent_job_id: str,
    profile: Optional[str] = None,
    limit: int = 5,
) -> list[Job]:
    """List pending L2 jobs sharing one L1 parent — eligible for batch.

    PRD §4. Used by the worker after claiming an L2 op to discover up to
    N-1 sibling ops that share the same `parent_job_id` (same L1 source
    clip) and the same profile.

    Filters:
      * status = 'pending'
      * job_level = 2
      * type IN (extend-video / camera-move / insert-object / remove-object)
      * parent_job_id = ?
      * profile = ? (or any when None)

    Order: created_at ASC (FIFO).
    """
    if not parent_job_id:
        return []
    if limit < 1:
        return []
    if limit > 20:
        limit = 20

    placeholders = ",".join("?" for _ in L2_OP_TYPES)
    clauses = [
        "status = 'pending'",
        "job_level = 2",
        f"type IN ({placeholders})",
        "parent_job_id = ?",
    ]
    params: list = list(L2_OP_TYPES) + [parent_job_id]
    if profile is not None:
        clauses.append("profile = ?")
        params.append(profile)

    query = (
        f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at ASC LIMIT ?"
    )
    params.append(limit)

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_job(r) for r in rows]


async def list_pending_l3_siblings(
    *,
    parent_job_id: str,
    profile: Optional[str] = None,
    limit: int = 5,
) -> list[Job]:
    """List pending L3+ jobs sharing one direct parent — eligible for batch.

    PRD §5. Worker uses this after claiming an L3+ op to discover up to
    N-1 sibling ops that share the same direct ``parent_job_id`` (the L2
    or L3 source clip) and the same profile.

    Filters:
      * status = 'pending'
      * job_level >= 3
      * type IN (extend-video / camera-move / insert-object / remove-object)
      * parent_job_id = ?
      * profile = ? (or any when None)

    Order: created_at ASC (FIFO).
    """
    if not parent_job_id:
        return []
    if limit < 1:
        return []
    if limit > 20:
        limit = 20

    placeholders = ",".join("?" for _ in L2_OP_TYPES)
    clauses = [
        "status = 'pending'",
        "job_level >= 3",
        f"type IN ({placeholders})",
        "parent_job_id = ?",
    ]
    params: list = list(L2_OP_TYPES) + [parent_job_id]
    if profile is not None:
        clauses.append("profile = ?")
        params.append(profile)

    query = (
        f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} "
        "ORDER BY created_at ASC LIMIT ?"
    )
    params.append(limit)

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

    cascaded_jobs: list[Job] = []
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(sql, params)
            if cursor.rowcount == 0:
                await db.execute("ROLLBACK")
                return None

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
            elif status_value == "pending" and (
                fields.get("worker_id") is None and "worker_id" in fields
            ):
                # Requeue path: job reset to pending with cleared claim metadata —
                # also clear the profiles table so the profile is not stuck as
                # "claimed" while waiting for the re-warmed worker to pick it up.
                await db.execute(
                    """
                    UPDATE profiles
                    SET current_job_id = NULL,
                        worker_id = NULL
                    WHERE current_job_id = ?
                    """,
                    (job_id,),
                )

            if status_value in TERMINAL_FAILURE_STATES:
                cascaded_jobs = await _cascade_cancel_descendants(
                    db,
                    parent_job_id=job_id,
                    parent_status=status_value,
                    now=_now_iso(),
                )

            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise

    await _broadcast_cascaded_jobs(cascaded_jobs)
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

    # Project-inflight cap: how many claimed/running L2+ jobs may share a
    # single Flow project_url at once. Default 1 = legacy mutex; raise via
    # FLOW_PROJECT_INFLIGHT to allow concurrent fan-out (e.g. 1 L1 → multi
    # L2 branches running in parallel on the same Flow project page).
    try:
        project_inflight_cap = max(
            1, int(os.environ.get("FLOW_PROJECT_INFLIGHT", "1").strip() or 1)
        )
    except ValueError:
        project_inflight_cap = 1

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
                  AND (
                      SELECT COUNT(*) FROM jobs active
                      WHERE active.project_url = parent.project_url
                        AND active.project_url IS NOT NULL
                        AND active.status IN ('claimed', 'running')
                  ) < ?
                ORDER BY j.created_at ASC
                LIMIT 1
                """,
                effective_profiles + [project_inflight_cap],
            )
            row = await cursor.fetchone()

            if row is not None:
                job_dict = dict(row)
                columns = await _job_columns(db)
                parent_select = "SELECT profile, project_url, media_id, edit_url"
                if "project_id" in columns:
                    parent_select += ", project_id"
                parent_select += " FROM jobs WHERE id = ?"
                parent_cur = await db.execute(
                    parent_select,
                    (job_dict["parent_job_id"],),
                )
                parent_row = await parent_cur.fetchone()
                if parent_row is not None:
                    bound_profile = parent_row["profile"]
                    bound_project_url = parent_row["project_url"]
                    bound_media_id = parent_row["media_id"]
                    bound_edit_url: Optional[str] = parent_row["edit_url"]
                    bound_project_id = parent_row["project_id"] if "project_id" in columns else None
                else:
                    bound_profile = None
                    bound_project_url = None
                    bound_media_id = None
                    bound_edit_url = None
                    bound_project_id = None

                project_id_set = ", project_id = ?" if "project_id" in columns else ""
                project_id_param = (bound_project_id,) if "project_id" in columns else ()
                await db.execute(
                    f"""
                    UPDATE jobs
                    SET status = 'claimed',
                        worker_id = ?,
                        claimed_at = ?,
                        profile = ?,
                        project_url = ?,
                        media_id = ?,
                        edit_url = ?{project_id_set},
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
                        *project_id_param,
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


async def claim_next_batch(
    worker_id: str,
    available_profiles: list[str],
    batch_size: int,
) -> list[Job]:
    """Atomically claim up to ``batch_size`` jobs in one transaction.

    PRD: docs/PRD_CLAIM_BATCH_DISPATCH.md.

    Mirrors :func:`claim_next_job` selection logic but loops within one
    ``BEGIN IMMEDIATE`` transaction. Constraints:

    * **Profile-coherent.** First claim locks ``profile``; subsequent rows
      on a different profile are skipped (left pending), not failed.
    * **Project-inflight cap honoured in-transaction.** ``FLOW_PROJECT_INFLIGHT``
      counts both already-running jobs *and* rows claimed earlier in this
      same batch.
    * **L2+ priority.** Step 1 fills as many L2+ ready-parent rows as it
      can; step 2 fills the remainder with L1 fresh rows. L1 and L2+ may
      coexist in a batch only if step 1 came up short of N — which the
      caller routes via the singleton path.
    """
    if batch_size <= 0:
        return []

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
        return []

    try:
        project_inflight_cap = max(
            1, int(os.environ.get("FLOW_PROJECT_INFLIGHT", "1").strip() or 1)
        )
    except ValueError:
        project_inflight_cap = 1

    claimed_ids: list[str] = []
    locked_profile: Optional[str] = None
    # Per-project_url counter for L2+ rows already claimed in *this* tx —
    # added on top of the SQL `(active count)` so a 3-job batch on a single
    # project_url never overshoots FLOW_PROJECT_INFLIGHT.
    project_inflight_local: dict[str, int] = {}

    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            columns = await _job_columns(db)
            include_project_id = "project_id" in columns
            project_id_set = ", project_id = ?" if include_project_id else ""

            # ---- Step 1: L2+ ready-parent rows -----------------------
            while len(claimed_ids) < batch_size:
                profile_filter = effective_profiles
                if locked_profile is not None:
                    if locked_profile not in profile_filter:
                        break
                    profile_filter = [locked_profile]
                ph = ", ".join("?" for _ in profile_filter)

                # SQLite quirk: `x NOT IN (NULL)` evaluates to NULL → row
                # is filtered. Only emit the NOT IN clause when we have
                # ids to exclude.
                excluded_clause = ""
                if claimed_ids:
                    excluded_ph = ", ".join("?" for _ in claimed_ids)
                    excluded_clause = f"AND j.id NOT IN ({excluded_ph})"
                cursor = await db.execute(
                    f"""
                    SELECT j.*
                    FROM jobs j
                    JOIN jobs parent ON j.parent_job_id = parent.id
                    WHERE j.status = 'pending'
                      AND j.job_level >= 2
                      {excluded_clause}
                      AND parent.status = 'completed'
                      AND parent.profile IN ({ph})
                      AND parent.project_url IS NOT NULL
                      AND parent.media_id IS NOT NULL
                      AND (
                          SELECT COUNT(*) FROM jobs active
                          WHERE active.project_url = parent.project_url
                            AND active.project_url IS NOT NULL
                            AND active.status IN ('claimed', 'running')
                      ) < ?
                    ORDER BY j.created_at ASC
                    LIMIT ?
                    """,
                    [
                        *claimed_ids,
                        *profile_filter,
                        project_inflight_cap,
                        # Over-fetch a few rows so we can skip ones that
                        # would breach the in-tx project_url counter.
                        max(batch_size, 8),
                    ],
                )
                rows = await cursor.fetchall()
                if not rows:
                    break

                progressed = False
                for row in rows:
                    if len(claimed_ids) >= batch_size:
                        break
                    job_dict = dict(row)
                    parent_select = "SELECT profile, project_url, media_id, edit_url"
                    if include_project_id:
                        parent_select += ", project_id"
                    parent_select += " FROM jobs WHERE id = ?"
                    parent_cur = await db.execute(
                        parent_select, (job_dict["parent_job_id"],),
                    )
                    parent_row = await parent_cur.fetchone()
                    if parent_row is None:
                        continue
                    bound_profile = parent_row["profile"]
                    bound_project_url = parent_row["project_url"]
                    bound_media_id = parent_row["media_id"]
                    bound_edit_url = parent_row["edit_url"]
                    bound_project_id = (
                        parent_row["project_id"] if include_project_id else None
                    )

                    if locked_profile is None:
                        locked_profile = bound_profile
                    elif bound_profile != locked_profile:
                        continue

                    inflight_local = project_inflight_local.get(
                        bound_project_url or "", 0,
                    )
                    # SQL `(active count)` gave the floor; add what we've
                    # claimed in this tx already.
                    if bound_project_url and (
                        inflight_local + 1 > project_inflight_cap
                    ):
                        # Defensive: already at cap inside this batch.
                        # Skip; the SQL also filters but it doesn't see
                        # in-tx claims.
                        continue

                    now = _now_iso()
                    project_id_param = (
                        (bound_project_id,) if include_project_id else ()
                    )
                    update_cur = await db.execute(
                        f"""
                        UPDATE jobs
                        SET status = 'claimed',
                            worker_id = ?,
                            claimed_at = ?,
                            profile = ?,
                            project_url = ?,
                            media_id = ?,
                            edit_url = ?{project_id_set},
                            updated_at = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (
                            worker_id,
                            now,
                            bound_profile,
                            bound_project_url,
                            bound_media_id,
                            bound_edit_url,
                            *project_id_param,
                            now,
                            job_dict["id"],
                        ),
                    )
                    # `BEGIN IMMEDIATE` already serializes writers, so
                    # rowcount=0 here is unreachable. Belt-and-braces:
                    # if a future refactor weakens isolation, this skip
                    # prevents a stale profile pointer from sticking.
                    if update_cur.rowcount == 0:
                        continue
                    if bound_profile:
                        await db.execute(
                            """
                            UPDATE profiles
                            SET current_job_id = ?, worker_id = ?, last_used_at = ?
                            WHERE name = ?
                            """,
                            (job_dict["id"], worker_id, now, bound_profile),
                        )
                    claimed_ids.append(job_dict["id"])
                    if bound_project_url:
                        project_inflight_local[bound_project_url] = (
                            inflight_local + 1
                        )
                    progressed = True
                if not progressed:
                    break

            # ---- Step 2: L1 fresh rows -------------------------------
            # Only run if we still have slots AND step 1 produced no L2+
            # rows. L1 + L2+ never share a batch (PRD §2): mixing would
            # require multi-tab routing semantics that don't exist for L1.
            if not claimed_ids:
                while len(claimed_ids) < batch_size:
                    profile_filter = effective_profiles
                    if locked_profile is not None:
                        if locked_profile not in profile_filter:
                            break
                        profile_filter = [locked_profile]
                    ph = ", ".join("?" for _ in profile_filter)

                    excluded_clause = ""
                    if claimed_ids:
                        excluded_ph = ", ".join("?" for _ in claimed_ids)
                        excluded_clause = f"AND id NOT IN ({excluded_ph})"
                    cursor = await db.execute(
                        f"""
                        SELECT *
                        FROM jobs
                        WHERE status = 'pending'
                          AND job_level = 1
                          {excluded_clause}
                          AND (profile IS NULL OR profile IN ({ph}))
                        ORDER BY created_at ASC
                        LIMIT 1
                        """,
                        [*claimed_ids, *profile_filter],
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        break

                    job_dict = dict(row)
                    assigned_profile = (
                        job_dict["profile"]
                        or locked_profile
                        or effective_profiles[0]
                    )
                    if locked_profile is None:
                        locked_profile = assigned_profile
                    elif assigned_profile != locked_profile:
                        # Skipping should not happen given the WHERE clause
                        # restricts to locked_profile once set; defensive.
                        break

                    now = _now_iso()
                    update_cur = await db.execute(
                        """
                        UPDATE jobs
                        SET status = 'claimed',
                            worker_id = ?,
                            claimed_at = ?,
                            profile = ?,
                            updated_at = ?
                        WHERE id = ? AND status = 'pending'
                        """,
                        (
                            worker_id, now, assigned_profile, now, job_dict["id"],
                        ),
                    )
                    if update_cur.rowcount == 0:
                        # See step 1 — defensive against a future weakening
                        # of `BEGIN IMMEDIATE` isolation.
                        continue
                    await db.execute(
                        """
                        UPDATE profiles
                        SET current_job_id = ?, worker_id = ?, last_used_at = ?
                        WHERE name = ?
                        """,
                        (job_dict["id"], worker_id, now, assigned_profile),
                    )
                    claimed_ids.append(job_dict["id"])

            await db.commit()
        except Exception:
            await db.execute("ROLLBACK")
            raise

    out: list[Job] = []
    for jid in claimed_ids:
        job = await get_job(jid)
        if job is not None:
            out.append(job)
    return out


async def claim_specific_pending_job(
    worker_id: str,
    job_id: str,
    *,
    profile: Optional[str] = None,
) -> Optional[Job]:
    """Atomically transition a specific pending job to 'claimed'.

    PRD §3.2 Phase 1. Used by the worker batch path: after claiming the
    first L1 t2v normally, sibling jobs are claimed by id (race-safe via
    BEGIN IMMEDIATE + status='pending' WHERE-clause).

    Returns the updated Job on success, or None if the job is no longer
    pending (someone else claimed it, or it was already cancelled).
    """
    now = _now_iso()
    async with get_db() as db:
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE id = ? AND status = 'pending'",
                (job_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.execute("COMMIT")
                return None

            assigned_profile = profile or row["profile"]

            await db.execute(
                """
                UPDATE jobs
                SET status = 'claimed',
                    worker_id = ?,
                    claimed_at = ?,
                    profile = ?,
                    updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (worker_id, now, assigned_profile, now, job_id),
            )
            if assigned_profile:
                await db.execute(
                    """
                    UPDATE profiles
                    SET current_job_id = ?, worker_id = ?, last_used_at = ?
                    WHERE name = ?
                    """,
                    (job_id, worker_id, now, assigned_profile),
                )
            await db.commit()
            return await get_job(job_id)
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
