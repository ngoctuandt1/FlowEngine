"""Trash management endpoints."""

from fastapi import APIRouter

from server.db.trash_store import (
    list_trash_items,
    permanently_delete_trash,
    restore_trash,
)
from server.models.trash import (
    TrashListResponse,
    TrashMutationCounts,
    TrashMutationRequest,
    TrashPermanentDeleteResponse,
    TrashRestoreResponse,
)


router = APIRouter(prefix="/api/trash", tags=["trash"])


@router.get("", response_model=TrashListResponse)
async def list_trash_endpoint() -> TrashListResponse:
    """List deleted rows only."""

    return TrashListResponse(items=await list_trash_items())


@router.post("/restore", response_model=TrashRestoreResponse)
async def restore_trash_endpoint(body: TrashMutationRequest) -> TrashRestoreResponse:
    """Restore selected deleted rows; active or missing ids count as zero."""

    counts = await restore_trash(
        job_ids=body.job_ids,
        project_ids=body.project_ids,
        all=body.all,
    )
    restored = TrashMutationCounts(jobs=counts["jobs"], projects=counts["projects"])
    return TrashRestoreResponse(
        restored=restored,
        restored_jobs=restored.jobs,
        restored_projects=restored.projects,
    )


@router.delete("/permanent", response_model=TrashPermanentDeleteResponse)
async def permanent_delete_trash_endpoint(
    body: TrashMutationRequest,
) -> TrashPermanentDeleteResponse:
    """Permanently delete selected trash rows only."""

    counts = await permanently_delete_trash(
        job_ids=body.job_ids,
        project_ids=body.project_ids,
        all=body.all,
    )
    deleted = TrashMutationCounts(jobs=counts["jobs"], projects=counts["projects"])
    return TrashPermanentDeleteResponse(
        deleted=deleted,
        deleted_jobs=deleted.jobs,
        deleted_projects=deleted.projects,
    )
