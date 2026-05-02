"""Render job CRUD operations."""

import json
from datetime import UTC, datetime

from server.db.database import get_db
from server.models.render import RenderJob, RenderStatus, TimelinePayload

_UNSET = object()
_TERMINAL_RENDER_STATES = frozenset(
    {RenderStatus.COMPLETED.value, RenderStatus.FAILED.value}
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_render_job(row) -> RenderJob:
    data = dict(row)
    data["payload"] = TimelinePayload.model_validate(json.loads(data["payload"]))
    return RenderJob(**data)


async def create_render_job(job: RenderJob) -> RenderJob:
    """Insert a new render job row and return it."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO render_jobs (
                id,
                status,
                progress,
                ratio,
                payload,
                output_path,
                error,
                created_at,
                updated_at,
                completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.status.value,
                job.progress,
                job.ratio.value,
                job.payload.model_dump_json(),
                job.output_path,
                job.error,
                job.created_at.isoformat(),
                job.updated_at.isoformat(),
                job.completed_at.isoformat() if job.completed_at else None,
            ),
        )
        await db.commit()
    return job


async def get_render_job(render_id: str) -> RenderJob | None:
    """Fetch a single render job by id, or None."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM render_jobs WHERE id = ?",
            (render_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_render_job(row)


async def update_render_job(
    render_id: str,
    *,
    status: RenderStatus | str | object = _UNSET,
    progress: int | object = _UNSET,
    output_path: str | None | object = _UNSET,
    error: str | None | object = _UNSET,
    completed_at: datetime | str | None | object = _UNSET,
) -> RenderJob | None:
    """Apply a partial update to a render job."""
    fields: list[tuple[str, object]] = []

    status_value: str | None = None
    if status is not _UNSET:
        status_value = status.value if isinstance(status, RenderStatus) else str(status)
        fields.append(("status", status_value))

    if progress is not _UNSET:
        fields.append(("progress", progress))

    if output_path is not _UNSET:
        fields.append(("output_path", output_path))

    if error is not _UNSET:
        fields.append(("error", error))

    if completed_at is _UNSET and status_value in _TERMINAL_RENDER_STATES:
        completed_at = _now_iso()

    if completed_at is not _UNSET:
        serialized_completed_at = (
            completed_at.isoformat()
            if isinstance(completed_at, datetime)
            else completed_at
        )
        fields.append(("completed_at", serialized_completed_at))

    if not fields:
        return await get_render_job(render_id)

    fields.append(("updated_at", _now_iso()))

    sets = [f"{column} = ?" for column, _ in fields]
    values = [value for _, value in fields]
    values.append(render_id)

    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE render_jobs SET {', '.join(sets)} WHERE id = ?",
            values,
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_render_job(render_id)
