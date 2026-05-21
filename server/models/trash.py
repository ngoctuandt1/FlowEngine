"""Trash request/response models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


def _normalize_ids(ids: list[str] | None) -> list[str]:
    if not ids:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw_id in ids:
        item_id = str(raw_id or "").strip()
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        normalized.append(item_id)
    return normalized


class TrashItem(BaseModel):
    """One deleted job or project shown in the explicit trash list."""

    type: Literal["job", "project"]
    job_id: str | None = None
    project_id: str | None = None
    name: str | None = None
    prompt: str | None = None
    deleted_at: datetime


class TrashListResponse(BaseModel):
    """Explicit trash list response."""

    items: list[TrashItem] = Field(default_factory=list)


class TrashMutationRequest(BaseModel):
    """Restore/permanent-delete selector payload."""

    job_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    all: bool = False

    @model_validator(mode="after")
    def _require_explicit_selection(self) -> "TrashMutationRequest":
        self.job_ids = _normalize_ids(self.job_ids)
        self.project_ids = _normalize_ids(self.project_ids)
        if not self.all and not self.job_ids and not self.project_ids:
            raise ValueError("Provide job_ids, project_ids, or all=true")
        return self


class TrashMutationCounts(BaseModel):
    """Mutation counts grouped by row type."""

    jobs: int = 0
    projects: int = 0


class TrashRestoreResponse(BaseModel):
    """Restore response with grouped and flat counts."""

    restored: TrashMutationCounts
    restored_jobs: int
    restored_projects: int


class TrashPermanentDeleteResponse(BaseModel):
    """Permanent delete response with grouped and flat counts."""

    deleted: TrashMutationCounts
    deleted_jobs: int
    deleted_projects: int
