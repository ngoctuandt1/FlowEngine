"""Job share-link models."""

from datetime import datetime

from pydantic import BaseModel, Field

from server.models.job import BBox, JobStatus, JobType


class JobShare(BaseModel):
    """Stored share metadata for one job."""

    job_id: str
    share_token: str | None = None
    share_url: str | None = None
    shared_at: datetime | None = None
    revoked_at: datetime | None = None


class JobShareResponse(JobShare):
    """API response for share mint/revoke operations."""


class PublicSharedJob(BaseModel):
    """Redacted job fields safe for unauthenticated share reads."""

    id: str
    type: JobType
    status: JobStatus
    job_level: int = 1
    parent_job_id: str | None = None
    chain_id: str | None = None
    prompt: str | None = None
    model: str
    aspect_ratio: str
    bbox: BBox | None = None
    direction: str | None = None
    output_files: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class PublicJobShareResponse(BaseModel):
    """Public job detail exposed through a live share token."""

    job: PublicSharedJob
    share_url: str = Field(description="Copyable public URL used for this read.")
    shared_at: datetime
