"""Project data models for FlowEngine."""

import uuid
from datetime import UTC, datetime
from typing import Optional

from pydantic import BaseModel, Field, model_validator


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


class ProjectCreate(BaseModel):
    """Request body for creating a project."""

    name: str
    description: Optional[str] = None

    @model_validator(mode="after")
    def _validate_payload(self) -> "ProjectCreate":
        self.name = (self.name or "").strip()
        if not self.name:
            raise ValueError("Project name is required")
        self.description = _normalize_optional_text(self.description)
        return self


class ProjectUpdate(BaseModel):
    """Fields that can be updated on a project."""

    name: Optional[str] = None
    description: Optional[str] = None
    cover_chain_id: Optional[str] = None
    cover_job_id: Optional[str] = None

    @model_validator(mode="after")
    def _normalize_payload(self) -> "ProjectUpdate":
        self.name = _normalize_optional_text(self.name)
        self.description = _normalize_optional_text(self.description)
        self.cover_chain_id = _normalize_optional_text(self.cover_chain_id)
        self.cover_job_id = _normalize_optional_text(self.cover_job_id)
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Project name cannot be empty")
        return self


class Project(BaseModel):
    """Persisted project row."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: Optional[str] = None
    cover_chain_id: Optional[str] = None
    cover_job_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProjectSummary(BaseModel):
    """List payload for dashboard project cards."""

    id: str
    name: str
    description: Optional[str] = None
    cover_thumb_url: Optional[str] = None
    updated_at: datetime
    created_at: datetime


class ProjectChainSummary(BaseModel):
    """Chain summary nested under a project detail response."""

    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    job_count: int
    completed_jobs: int
    cover_thumb_url: Optional[str] = None


class ProjectDetail(Project):
    """Detail payload for one project, including its chains."""

    cover_thumb_url: Optional[str] = None
    chains: list[ProjectChainSummary] = Field(default_factory=list)
