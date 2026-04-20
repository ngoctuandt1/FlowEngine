"""Job data models for FlowEngine."""

from datetime import UTC, datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


class JobType(str, Enum):
    TEXT_TO_VIDEO = "text-to-video"
    EXTEND_VIDEO = "extend-video"
    INSERT_OBJECT = "insert-object"
    REMOVE_OBJECT = "remove-object"
    CAMERA_MOVE = "camera-move"


class JobStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BBox(BaseModel):
    """Normalized bounding box (0-1 range)."""
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class JobCreate(BaseModel):
    """Request body for creating a single job."""
    type: JobType
    prompt: Optional[str] = None
    model: str = "veo-3.1-fast-lp"
    aspect_ratio: str = "16:9"

    # Pin an L1 job to a specific Chrome profile (Google account). For
    # L2+ the profile is inherited from the completed parent and this
    # field is ignored.
    profile: Optional[str] = None

    # Multi-level chain
    parent_job_id: Optional[str] = None
    chain_id: Optional[str] = None

    # Target (for Level-2 ops)
    project_url: Optional[str] = None
    media_id: Optional[str] = None

    # Operation-specific
    bbox: Optional[BBox] = None
    direction: Optional[str] = None  # Camera preset name


class ChainCreate(BaseModel):
    """Request body for creating a job chain."""
    jobs: list[JobCreate]
    profile: Optional[str] = None  # Pin to specific profile


class Job(BaseModel):
    """Complete job record."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: JobType
    status: JobStatus = JobStatus.PENDING

    # Chain fields
    job_level: int = 1
    parent_job_id: Optional[str] = None
    chain_id: Optional[str] = None

    # Account binding (CRITICAL)
    profile: Optional[str] = None       # Chrome profile = Google account
    project_url: Optional[str] = None   # Flow project URL
    media_id: Optional[str] = None      # Flow media UUID
    edit_url: Optional[str] = None      # project_url + /edit/ + media_id

    # Operation params
    prompt: Optional[str] = None
    model: str = "veo-3.1-fast-lp"
    aspect_ratio: str = "16:9"
    bbox: Optional[BBox] = None
    direction: Optional[str] = None

    # Output
    output_files: list[str] = Field(default_factory=list)
    generation_id: Optional[str] = None

    # Worker tracking
    worker_id: Optional[str] = None
    claimed_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error: Optional[str] = None

    # Timestamps
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def computed_edit_url(self) -> Optional[str]:
        """Build edit URL from project_url + media_id."""
        if self.project_url and self.media_id:
            base = self.project_url.rstrip("/")
            return f"{base}/edit/{self.media_id}"
        return self.edit_url


class JobUpdate(BaseModel):
    """Fields that worker can update after operation completes."""
    status: Optional[JobStatus] = None
    project_url: Optional[str] = None
    media_id: Optional[str] = None
    edit_url: Optional[str] = None
    profile: Optional[str] = None
    output_files: Optional[list[str]] = None
    generation_id: Optional[str] = None
    error: Optional[str] = None
    completed_at: Optional[datetime] = None
