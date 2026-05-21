"""Project CRUD and derived project views."""

import json
from datetime import UTC, datetime
from typing import Optional
from urllib.parse import quote

from server.db.chain_store import compute_aggregated_status
from server.db.database import get_db
from server.models.project import (
    Project,
    ProjectChainSummary,
    ProjectDetail,
    ProjectSummary,
    ProjectUpdate,
)


def _row_to_project(row) -> Project:
    """Convert an aiosqlite.Row to a Project model."""

    return Project(**dict(row))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def _ensure_column(db, *, table: str, name: str, ddl: str) -> None:
    cursor = await db.execute(f"PRAGMA table_info({table})")
    rows = await cursor.fetchall()
    if any(row[1] == name for row in rows):
        return
    await db.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


async def _ensure_soft_delete_columns(db) -> None:
    await _ensure_column(db, table="projects", name="deleted_at", ddl="deleted_at TEXT")
    await _ensure_column(db, table="jobs", name="deleted_at", ddl="deleted_at TEXT")


def _output_media_url(path: str) -> str:
    normalized = str(path or "").replace("\\", "/").strip()
    if not normalized:
        return ""
    if normalized.lower().startswith(("http://", "https://")):
        return normalized
    if normalized.lower().startswith("/downloads/"):
        relative = normalized[len("/downloads/"):]
    elif normalized.lower().startswith("downloads/"):
        relative = normalized[len("downloads/"):]
    else:
        marker = normalized.lower().rfind("/downloads/")
        relative = normalized[marker + len("/downloads/"):] if marker != -1 else normalized
    return f"/downloads/{quote(relative, safe='/')}"


def _first_output_file_url(output_files_json: Optional[str]) -> Optional[str]:
    if not output_files_json:
        return None
    try:
        output_files = json.loads(output_files_json)
    except json.JSONDecodeError:
        return None
    if not output_files:
        return None
    url = _output_media_url(output_files[0])
    return url or None


def _build_project_summary(
    project: Project, *, cover_thumb_url: Optional[str]
) -> ProjectSummary:
    return ProjectSummary(
        id=project.id,
        name=project.name,
        description=project.description,
        cover_thumb_url=cover_thumb_url,
        deleted_at=project.deleted_at,
        updated_at=project.updated_at,
        created_at=project.created_at,
    )


async def _resolve_cover_thumb_url(db, project: Project) -> Optional[str]:
    if project.cover_job_id:
        cursor = await db.execute(
            "SELECT output_files_json FROM jobs WHERE id = ? AND deleted_at IS NULL",
            (project.cover_job_id,),
        )
        row = await cursor.fetchone()
        return _first_output_file_url(row["output_files_json"]) if row is not None else None

    cursor = await db.execute(
        """
        SELECT output_files_json
        FROM jobs
        WHERE project_id = ?
          AND status = 'completed'
          AND deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        (project.id,),
    )
    row = await cursor.fetchone()
    return _first_output_file_url(row["output_files_json"]) if row is not None else None


async def _build_chain_summary(
    db, *, project_id: str, chain_id: str
) -> Optional[ProjectChainSummary]:
    cursor = await db.execute(
        """
        SELECT status, created_at, updated_at
        FROM jobs
        WHERE project_id = ?
          AND chain_id = ?
          AND deleted_at IS NULL
        ORDER BY created_at ASC
        """,
        (project_id, chain_id),
    )
    rows = await cursor.fetchall()
    if not rows:
        return None

    cover_cursor = await db.execute(
        """
        SELECT output_files_json
        FROM jobs
        WHERE project_id = ?
          AND chain_id = ?
          AND status = 'completed'
          AND deleted_at IS NULL
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        (project_id, chain_id),
    )
    cover_row = await cover_cursor.fetchone()

    statuses = [row["status"] for row in rows]
    created_at = datetime.fromisoformat(rows[0]["created_at"])
    updated_at = max(datetime.fromisoformat(row["updated_at"]) for row in rows)

    return ProjectChainSummary(
        id=chain_id,
        status=compute_aggregated_status(statuses),
        created_at=created_at,
        updated_at=updated_at,
        job_count=len(rows),
        completed_jobs=sum(1 for status in statuses if status == "completed"),
        cover_thumb_url=(
            _first_output_file_url(cover_row["output_files_json"])
            if cover_row is not None
            else None
        ),
    )


async def create_project(project: Project) -> Project:
    """Insert a new project row and return it."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        await db.execute(
            """
            INSERT INTO projects (
                id, name, description, cover_chain_id, cover_job_id,
                deleted_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project.id,
                project.name,
                project.description,
                project.cover_chain_id,
                project.cover_job_id,
                project.deleted_at.isoformat() if project.deleted_at else None,
                project.created_at.isoformat(),
                project.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return project


async def get_project(project_id: str) -> Optional[Project]:
    """Fetch one project row, or None."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        cursor = await db.execute(
            "SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_project(row)


async def get_project_summary(project_id: str) -> Optional[ProjectSummary]:
    """Fetch one project plus its derived cover thumbnail."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        cursor = await db.execute(
            "SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        project = _row_to_project(row)
        cover_thumb_url = await _resolve_cover_thumb_url(db, project)
        return _build_project_summary(project, cover_thumb_url=cover_thumb_url)


async def list_projects() -> list[ProjectSummary]:
    """List projects ordered by most recent update."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        cursor = await db.execute(
            """
            SELECT * FROM projects
            WHERE deleted_at IS NULL
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        rows = await cursor.fetchall()

        projects: list[ProjectSummary] = []
        for row in rows:
            project = _row_to_project(row)
            cover_thumb_url = await _resolve_cover_thumb_url(db, project)
            projects.append(
                _build_project_summary(project, cover_thumb_url=cover_thumb_url)
            )
        return projects


async def get_project_detail(project_id: str) -> Optional[ProjectDetail]:
    """Fetch one project with a derived chain list."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        cursor = await db.execute(
            "SELECT * FROM projects WHERE id = ? AND deleted_at IS NULL",
            (project_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        project = _row_to_project(row)
        cover_thumb_url = await _resolve_cover_thumb_url(db, project)

        chain_cursor = await db.execute(
            """
            SELECT chain_id
            FROM jobs
            WHERE project_id = ?
              AND chain_id IS NOT NULL
              AND deleted_at IS NULL
            GROUP BY chain_id
            ORDER BY MAX(updated_at) DESC, MAX(created_at) DESC
            """,
            (project_id,),
        )
        chain_rows = await chain_cursor.fetchall()

        chains: list[ProjectChainSummary] = []
        for chain_row in chain_rows:
            chain = await _build_chain_summary(
                db,
                project_id=project_id,
                chain_id=chain_row["chain_id"],
            )
            if chain is not None:
                chains.append(chain)

        return ProjectDetail(
            **project.model_dump(mode="python"),
            cover_thumb_url=cover_thumb_url,
            chains=chains,
        )


async def update_project(project_id: str, update: ProjectUpdate) -> Optional[Project]:
    """Apply a partial update to a project row."""

    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_project(project_id)

    sets: list[str] = []
    params: list[Optional[str]] = []

    for key, value in fields.items():
        sets.append(f"{key} = ?")
        params.append(value)

    sets.append("updated_at = ?")
    params.append(_now_iso())
    params.append(project_id)

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        cursor = await db.execute(
            f"UPDATE projects SET {', '.join(sets)} WHERE id = ? AND deleted_at IS NULL",
            params,
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_project(project_id)


async def delete_project(project_id: str) -> bool:
    """Soft-delete a project row without unlinking jobs."""

    async with get_db() as db:
        await _ensure_soft_delete_columns(db)
        now = _now_iso()
        cursor = await db.execute(
            """
            UPDATE projects
            SET deleted_at = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, project_id),
        )
        await db.commit()
        return cursor.rowcount > 0
