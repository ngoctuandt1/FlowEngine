"""Soft-delete trash store and job-store compatibility patching."""

from __future__ import annotations

import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

from server.db.database import get_db
from server.models.job import Job, JobStatus
from server.models.trash import TrashItem


STATIC_TRASH_ENDPOINT_HINTS: tuple[str, ...] = (
    "https://aisandbox-pa.googleapis.com/v1/flow:batchDeleteAssets",
    "https://aisandbox-pa.googleapis.com/v1/flowMedia/{media_id}",
    "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/{workflow_id}",
)

STATIC_PROJECT_ENDPOINT_HINTS: tuple[str, ...] = (
    "https://aisandbox-pa.googleapis.com/v1/flow/projects/{project_id}",
    "https://aisandbox-pa.googleapis.com/v1/projects/{project_id}",
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _ensure_column(db, *, table: str, name: str, ddl: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    if any(row[1] == name for row in rows):
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def ensure_soft_delete_columns(db) -> None:
    """Install additive soft-delete columns on legacy databases."""

    await _ensure_column(db, table="projects", name="deleted_at", ddl="deleted_at TEXT")
    await _ensure_column(db, table="jobs", name="deleted_at", ddl="deleted_at TEXT")


def _placeholders(values: list[str]) -> str:
    return ",".join("?" for _ in values)


def _is_missing_deleted_at(exc: sqlite3.OperationalError) -> bool:
    return "no such column" in str(exc).lower() and "deleted_at" in str(exc).lower()


def _with_limit_offset(query: str, params: list[Any], limit: int | None, offset: int) -> tuple[str, list[Any]]:
    query_params = list(params)
    if limit is not None:
        query += " LIMIT ?"
        query_params.append(limit)
        if offset:
            query += " OFFSET ?"
            query_params.append(offset)
    return query, query_params


def _row_to_trash_job(row) -> TrashItem:
    return TrashItem(
        type="job",
        job_id=row["id"],
        project_id=row["project_id"],
        name=None,
        prompt=row["prompt"] or row["type"],
        deleted_at=row["deleted_at"],
    )


def _row_to_trash_project(row) -> TrashItem:
    return TrashItem(
        type="project",
        job_id=None,
        project_id=row["id"],
        name=row["name"],
        prompt=None,
        deleted_at=row["deleted_at"],
    )


async def list_trash_items() -> list[TrashItem]:
    """Return deleted jobs and projects only."""

    async with get_db() as db:
        await ensure_soft_delete_columns(db)
        job_cursor = await db.execute(
            """
            SELECT id, type, prompt, project_id, deleted_at
            FROM jobs
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, updated_at DESC
            """
        )
        project_cursor = await db.execute(
            """
            SELECT id, name, deleted_at
            FROM projects
            WHERE deleted_at IS NOT NULL
            ORDER BY deleted_at DESC, updated_at DESC
            """
        )
        items = [_row_to_trash_job(row) for row in await job_cursor.fetchall()]
        items.extend(_row_to_trash_project(row) for row in await project_cursor.fetchall())
        return sorted(items, key=lambda item: item.deleted_at, reverse=True)


async def restore_trash(
    *, job_ids: list[str] | None = None, project_ids: list[str] | None = None, all: bool = False
) -> dict[str, int]:
    """Restore selected deleted rows. Idempotent for already-active/missing rows."""

    async with get_db() as db:
        await ensure_soft_delete_columns(db)
        now = _now_iso()
        jobs = 0
        projects = 0
        if all:
            cursor = await db.execute(
                """
                UPDATE jobs
                SET deleted_at = NULL, updated_at = ?
                WHERE deleted_at IS NOT NULL
                """,
                (now,),
            )
            jobs = cursor.rowcount
            cursor = await db.execute(
                """
                UPDATE projects
                SET deleted_at = NULL, updated_at = ?
                WHERE deleted_at IS NOT NULL
                """,
                (now,),
            )
            projects = cursor.rowcount
        else:
            normalized_job_ids = list(job_ids or [])
            normalized_project_ids = list(project_ids or [])
            if normalized_job_ids:
                cursor = await db.execute(
                    f"""
                    UPDATE jobs
                    SET deleted_at = NULL, updated_at = ?
                    WHERE id IN ({_placeholders(normalized_job_ids)})
                      AND deleted_at IS NOT NULL
                    """,
                    (now, *normalized_job_ids),
                )
                jobs = cursor.rowcount
            if normalized_project_ids:
                cursor = await db.execute(
                    f"""
                    UPDATE projects
                    SET deleted_at = NULL, updated_at = ?
                    WHERE id IN ({_placeholders(normalized_project_ids)})
                      AND deleted_at IS NOT NULL
                    """,
                    (now, *normalized_project_ids),
                )
                projects = cursor.rowcount
        await db.commit()
        return {"jobs": jobs, "projects": projects}


async def permanently_delete_trash(
    *, job_ids: list[str] | None = None, project_ids: list[str] | None = None, all: bool = False
) -> dict[str, int]:
    """Permanently delete selected trash rows only; active rows are untouched."""

    async with get_db() as db:
        await ensure_soft_delete_columns(db)
        jobs = 0
        projects = 0
        if all:
            job_cursor = await db.execute(
                """
                SELECT id
                FROM jobs
                WHERE deleted_at IS NOT NULL
                ORDER BY job_level DESC, created_at DESC
                """
            )
            normalized_job_ids = [row["id"] for row in await job_cursor.fetchall()]
            project_cursor = await db.execute(
                "SELECT id FROM projects WHERE deleted_at IS NOT NULL"
            )
            normalized_project_ids = [row["id"] for row in await project_cursor.fetchall()]
        else:
            normalized_job_ids = list(job_ids or [])
            normalized_project_ids = list(project_ids or [])

        if normalized_job_ids:
            delete_cursor = await db.execute(
                f"""
                SELECT id
                FROM jobs
                WHERE id IN ({_placeholders(normalized_job_ids)})
                  AND deleted_at IS NOT NULL
                ORDER BY job_level DESC, created_at DESC
                """,
                normalized_job_ids,
            )
            for row in await delete_cursor.fetchall():
                cursor = await db.execute(
                    """
                    DELETE FROM jobs
                    WHERE id = ?
                      AND deleted_at IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1
                          FROM jobs AS child
                          WHERE child.parent_job_id = jobs.id
                      )
                    """,
                    (row["id"],),
                )
                jobs += cursor.rowcount
        if normalized_project_ids:
            cursor = await db.execute(
                f"""
                DELETE FROM projects
                WHERE id IN ({_placeholders(normalized_project_ids)})
                  AND deleted_at IS NOT NULL
                """,
                normalized_project_ids,
            )
            projects = cursor.rowcount
        await db.commit()
        return {"jobs": jobs, "projects": projects}


async def _list_active_jobs(
    *,
    status: JobStatus | str | None = None,
    type: str | None = None,
    profile: str | None = None,
    chain_id: str | None = None,
    q: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[Job]:
    from server.db import job_store

    clauses: list[str] = ["deleted_at IS NULL"]
    params: list[Any] = []
    uses_fts = False
    query_text = ""

    def like_pattern(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"%{escaped}%"

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
    if q is not None:
        query_text = q.strip().lower()
        if query_text:
            if job_store.FTS_SAFE_QUERY_RE.fullmatch(query_text) and len(query_text) >= 3:
                clauses.append(
                    "jobs.rowid IN (SELECT rowid FROM jobs_fts WHERE jobs_fts MATCH ?)"
                )
                params.append(f"{query_text}*")
                uses_fts = True
            else:
                clauses.append(
                    "(LOWER(COALESCE(id, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(project_url, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(type, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(profile, '')) LIKE ? ESCAPE '\\')"
                )
                params.extend([like_pattern(query_text)] * 4)

    where = " AND ".join(clauses)
    query, query_params = _with_limit_offset(
        f"SELECT * FROM jobs WHERE {where} ORDER BY created_at DESC",
        params,
        limit,
        offset,
    )

    async with get_db() as db:
        try:
            cursor = await db.execute(query, query_params)
        except sqlite3.OperationalError as exc:
            if _is_missing_deleted_at(exc):
                fallback_clauses = [
                    clause for clause in clauses if clause != "deleted_at IS NULL"
                ]
                fallback_where = " AND ".join(fallback_clauses) if fallback_clauses else "1"
                fallback_query, fallback_params = _with_limit_offset(
                    f"SELECT * FROM jobs WHERE {fallback_where} ORDER BY created_at DESC",
                    params,
                    limit,
                    offset,
                )
                cursor = await db.execute(fallback_query, fallback_params)
            elif not uses_fts:
                raise
            else:
                clauses = [clause for clause in clauses if "jobs_fts MATCH" not in clause]
                clauses.append(
                    "(LOWER(COALESCE(id, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(project_url, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(type, '')) LIKE ? ESCAPE '\\' "
                    "OR LOWER(COALESCE(profile, '')) LIKE ? ESCAPE '\\')"
                )
                fallback_params = params[:-1] + [like_pattern(query_text)] * 4
                query, fallback_params = _with_limit_offset(
                    f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} ORDER BY created_at DESC",
                    fallback_params,
                    limit,
                    offset,
                )
                cursor = await db.execute(query, fallback_params)
        rows = await cursor.fetchall()
        return [job_store._row_to_job(row) for row in rows]


async def _get_active_job(job_id: str) -> Job | None:
    from server.db import job_store

    async with get_db() as db:
        try:
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL",
                (job_id,),
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_deleted_at(exc):
                raise
            cursor = await db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = await cursor.fetchone()
        return job_store._row_to_job(row) if row is not None else None


async def _soft_delete_job(job_id: str) -> Job | None:
    from server.db import job_store

    async with get_db() as db:
        await ensure_soft_delete_columns(db)
        await db.execute("BEGIN IMMEDIATE")
        try:
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE id = ? AND deleted_at IS NULL",
                (job_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await db.execute("ROLLBACK")
                return None

            job = job_store._row_to_job(row)
            now = _now_iso()
            await db.execute(
                """
                UPDATE jobs
                SET status = 'cancelled',
                    deleted_at = ?,
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (now, now, now, job_id),
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
            job.status = JobStatus.CANCELLED
            return job
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def _get_active_children(parent_job_id: str) -> list[Job]:
    from server.db import job_store

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """
                SELECT * FROM jobs
                WHERE parent_job_id = ? AND deleted_at IS NULL
                ORDER BY created_at
                """,
                (parent_job_id,),
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_deleted_at(exc):
                raise
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE parent_job_id = ? ORDER BY created_at",
                (parent_job_id,),
            )
        rows = await cursor.fetchall()
        return [job_store._row_to_job(row) for row in rows]


async def _get_active_jobs_by_chain(chain_id: str) -> list[Job]:
    from server.db import job_store

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """
                SELECT * FROM jobs
                WHERE chain_id = ? AND deleted_at IS NULL
                ORDER BY created_at ASC
                """,
                (chain_id,),
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_deleted_at(exc):
                raise
            cursor = await db.execute(
                "SELECT * FROM jobs WHERE chain_id = ? ORDER BY created_at ASC",
                (chain_id,),
            )
        rows = await cursor.fetchall()
        return [job_store._row_to_job(row) for row in rows]


async def _list_active_completed_jobs_by_project_url(
    project_url: str, *, limit: int = 2
) -> list[Job]:
    from server.db import job_store

    async with get_db() as db:
        try:
            cursor = await db.execute(
                """
                SELECT * FROM jobs
                WHERE project_url = ?
                  AND status = 'completed'
                  AND deleted_at IS NULL
                ORDER BY datetime(completed_at) DESC, datetime(updated_at) DESC
                LIMIT ?
                """,
                (project_url, limit),
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_deleted_at(exc):
                raise
            cursor = await db.execute(
                """
                SELECT * FROM jobs
                WHERE project_url = ?
                  AND status = 'completed'
                ORDER BY datetime(completed_at) DESC, datetime(updated_at) DESC
                LIMIT ?
                """,
                (project_url, limit),
            )
        rows = await cursor.fetchall()
        return [job_store._row_to_job(row) for row in rows]


async def _get_active_job_counts() -> dict[str, int]:
    async with get_db() as db:
        try:
            cursor = await db.execute(
                """
                SELECT status, COUNT(*) as cnt
                FROM jobs
                WHERE deleted_at IS NULL
                GROUP BY status
                """
            )
        except sqlite3.OperationalError as exc:
            if not _is_missing_deleted_at(exc):
                raise
            cursor = await db.execute(
                "SELECT status, COUNT(*) as cnt FROM jobs GROUP BY status"
            )
        rows = await cursor.fetchall()
        counts = {status.value: 0 for status in JobStatus}
        for row in rows:
            counts[row["status"]] = row["cnt"]
        return counts


def install_job_store_soft_delete_patch() -> None:
    """Patch job-store read/delete call sites before job routes import symbols."""

    from server.db import job_store

    if getattr(job_store, "_flowengine_soft_delete_patch", False):
        return

    replacements = {
        "list_jobs": _list_active_jobs,
        "get_job": _get_active_job,
        "delete_job": _soft_delete_job,
        "get_children": _get_active_children,
        "get_jobs_by_chain": _get_active_jobs_by_chain,
        "list_completed_jobs_by_project_url": _list_active_completed_jobs_by_project_url,
        "get_job_counts": _get_active_job_counts,
    }
    for name, replacement in replacements.items():
        setattr(job_store, name, replacement)
    job_store._flowengine_soft_delete_patch = True

    routes_jobs = sys.modules.get("server.routes.jobs")
    if routes_jobs is not None:
        for name, replacement in replacements.items():
            setattr(routes_jobs, name, replacement)
