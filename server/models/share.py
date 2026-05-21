"""Job share-link models."""

from datetime import datetime

from pydantic import BaseModel, Field

from server.models.job import Job


class JobShare(BaseModel):
    """Stored share metadata for one job."""

    job_id: str
    share_token: str | None = None
    share_url: str | None = None
    shared_at: datetime | None = None
    revoked_at: datetime | None = None


class JobShareResponse(JobShare):
    """API response for share mint/revoke operations."""


class PublicJobShareResponse(BaseModel):
    """Public job detail exposed through a live share token."""

    job: Job
    share_url: str = Field(description="Copyable public URL used for this read.")
    shared_at: datetime

