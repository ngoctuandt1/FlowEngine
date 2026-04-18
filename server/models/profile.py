"""Profile data models for FlowEngine."""

from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class ProfileStatus(str, Enum):
    AVAILABLE = "available"
    BUSY = "busy"
    QUARANTINED = "quarantined"


class Profile(BaseModel):
    """Chrome profile = Google account identity."""
    name: str                              # Chrome profile directory name
    google_account: Optional[str] = None   # Google email (for display)
    locale: str = "en"                     # "en" | "vi"
    tier: str = "ultra"                    # "ultra" | "free"
    status: ProfileStatus = ProfileStatus.AVAILABLE
    current_job_id: Optional[str] = None   # Job currently running
    worker_id: Optional[str] = None        # Worker that owns this profile
    last_used_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ProfileUpdate(BaseModel):
    """Fields that can be updated on a profile."""
    status: Optional[ProfileStatus] = None
    current_job_id: Optional[str] = None
    worker_id: Optional[str] = None
    google_account: Optional[str] = None
    locale: Optional[str] = None
    tier: Optional[str] = None
