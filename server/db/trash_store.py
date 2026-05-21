"""Soft-delete trash store."""

from __future__ import annotations

from datetime import UTC, datetime

from server.db.database import get_db
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
        await db.execute("BEGIN IMMEDIATE")
        try:
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
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def permanently_delete_trash(
    *, job_ids: list[str] | None = None, project_ids: list[str] | None = None, all: bool = False
) -> dict[str, int]:
    """Permanently delete selected trash rows only; active rows are untouched."""

    async with get_db() as db:
        await ensure_soft_delete_columns(db)
        await db.execute("BEGIN IMMEDIATE")
        try:
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
        except Exception:
            await db.execute("ROLLBACK")
            raise
