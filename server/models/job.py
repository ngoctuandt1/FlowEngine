"""Job data models for FlowEngine."""

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# Canonical camera-move presets (duplicated from flow.operations.camera to keep
# server import surface free of browser/Playwright baggage). A drift-guard test
# in tests/test_camera_direction_validator.py asserts the runtime sync, and
# tests/test_camera_preset_consistency.py guards the frontend mirror.
CAMERA_PRESETS: frozenset[str] = frozenset({
    # Motion
    "Dolly in", "Dolly out", "Orbit left", "Orbit right",
    "Orbit up", "Orbit low", "Dolly in zoom out", "Dolly out zoom in",
    # Position
    "Center", "Left", "Right", "High", "Low", "Closer", "Further",
})

DEFAULT_MODEL = "veo-3.1-lite-lp"

# `server/routes/jobs.py` still uses the old fast-LP token as a text-to-image
# sentinel to map omitted video-model defaults onto the image-model default
# (`nano-banana-pro`). Keep the route contract stable here until that path is
# migrated separately.
TEXT_TO_IMAGE_ROUTE_SENTINEL = "veo-3.1-fast-lp"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _require_non_empty_text(value: str | None, *, job_type: str, field: str) -> None:
    if _normalize_optional_text(value) is None:
        if field == "direction":
            raise ValueError(f"{job_type} requires 'direction'")
        raise ValueError(f"{job_type} requires {field}")


class JobType(str, Enum):
    TEXT_TO_VIDEO = "text-to-video"
    FRAMES_TO_VIDEO = "frames-to-video"
    INGREDIENTS_TO_VIDEO = "ingredients-to-video"
    TEXT_TO_IMAGE = "text-to-image"
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
    model: str = DEFAULT_MODEL
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
    start_image_path: Optional[str] = None
    end_image_path: Optional[str] = None
    ingredient_image_paths: list[str] = Field(default_factory=list)
    ref_image_path: Optional[str] = None

    @model_validator(mode="after")
    def _validate_create_request(self) -> "JobCreate":
        """Fail fast on missing operation-specific inputs before queuing work."""
        self.prompt = _normalize_optional_text(self.prompt)
        self.profile = _normalize_optional_text(self.profile)
        self.parent_job_id = _normalize_optional_text(self.parent_job_id)
        self.chain_id = _normalize_optional_text(self.chain_id)
        self.project_url = _normalize_optional_text(self.project_url)
        self.media_id = _normalize_optional_text(self.media_id)
        self.direction = _normalize_optional_text(self.direction)
        self.start_image_path = _normalize_optional_text(self.start_image_path)
        self.end_image_path = _normalize_optional_text(self.end_image_path)
        self.ref_image_path = _normalize_optional_text(self.ref_image_path)
        self.ingredient_image_paths = [
            path.strip()
            for path in self.ingredient_image_paths
            if isinstance(path, str) and path.strip()
        ]

        # Preserve the existing text-to-image route default until that path is
        # migrated off its fast-LP sentinel.
        if (
            self.type == JobType.TEXT_TO_IMAGE
            and "model" not in self.model_fields_set
            and self.model == DEFAULT_MODEL
        ):
            self.model = TEXT_TO_IMAGE_ROUTE_SENTINEL

        if self.type == JobType.FRAMES_TO_VIDEO:
            _require_non_empty_text(
                self.start_image_path,
                job_type=self.type.value,
                field="start_image_path",
            )
            return self

        if self.type == JobType.INGREDIENTS_TO_VIDEO:
            if not self.ingredient_image_paths:
                raise ValueError("ingredients-to-video requires ingredient_image_paths")
            return self

        if self.type in {JobType.INSERT_OBJECT, JobType.REMOVE_OBJECT}:
            if self.bbox is None:
                raise ValueError(f"{self.type.value} requires bbox")
            return self

        if self.type == JobType.CAMERA_MOVE:
            _require_non_empty_text(
                self.direction,
                job_type=self.type.value,
                field="direction",
            )
            if self.direction not in CAMERA_PRESETS:
                raise ValueError(
                    f"Unknown camera preset {self.direction!r}. "
                    f"Valid presets: {sorted(CAMERA_PRESETS)}"
                )
        return self


class ChainCreate(BaseModel):
    """Request body for creating a job chain."""
    jobs: list[JobCreate] = Field(min_length=1)
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
    model: str = DEFAULT_MODEL
    aspect_ratio: str = "16:9"
    bbox: Optional[BBox] = None
    direction: Optional[str] = None
    start_image_path: Optional[str] = None
    end_image_path: Optional[str] = None
    ingredient_image_paths: list[str] = Field(default_factory=list)
    ref_image_path: Optional[str] = None

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


class JobWithThumb(Job):
    """Job payload enriched with a route-local thumbnail URL."""

    thumb_url: Optional[str] = None


class JobRelatedStats(BaseModel):
    """Root-scoped aggregate counts for a related-job response."""

    total: int = 0
    completed: int = 0
    failed: int = 0
    pending: int = 0


class JobRelatedResponse(BaseModel):
    """Consolidated lineage context for one job detail request."""

    self: JobWithThumb
    parent: Optional[JobWithThumb] = None
    ancestors: list[JobWithThumb] = Field(default_factory=list)
    siblings: list[JobWithThumb] = Field(default_factory=list)
    children: list[JobWithThumb] = Field(default_factory=list)
    chain_id: str
    chain_root_id: str
    stats: JobRelatedStats
