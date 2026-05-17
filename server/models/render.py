"""Render compose models."""

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator


# Resource bounds — applied at the Pydantic layer so an unbounded timeline is
# rejected with 422 BEFORE we hand the payload to the background ffmpeg job.
MAX_TRACKS_PER_TIMELINE = 10
MAX_CLIPS_PER_TRACK = 100
MAX_TOTAL_DURATION_SEC = 600.0  # 10 minutes


class RenderStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TimelineRatio(str, Enum):
    PORTRAIT = "9:16"
    LANDSCAPE = "16:9"
    SQUARE = "1:1"


class TimelineTrackKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"


class TimelineClip(BaseModel):
    asset_id: str = Field(min_length=1)
    start_sec: float = Field(ge=0)
    duration_sec: float = Field(gt=0)
    trim_in: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _normalize(self) -> "TimelineClip":
        self.asset_id = self.asset_id.strip()
        if not self.asset_id:
            raise ValueError("asset_id is required")
        return self


class TimelineTrack(BaseModel):
    kind: TimelineTrackKind
    clips: list[TimelineClip] = Field(default_factory=list)


class TimelinePayload(BaseModel):
    ratio: TimelineRatio
    tracks: list[TimelineTrack] = Field(min_length=1, max_length=MAX_TRACKS_PER_TIMELINE)
    total_duration_sec: float = Field(gt=0, le=MAX_TOTAL_DURATION_SEC)

    @model_validator(mode="after")
    def _validate_track_bounds(self) -> "TimelinePayload":
        for track in self.tracks:
            if len(track.clips) > MAX_CLIPS_PER_TRACK:
                raise ValueError(
                    f"track exceeds {MAX_CLIPS_PER_TRACK} clip limit"
                )
            for clip in track.clips:
                clip_end = clip.start_sec + clip.duration_sec
                if clip_end > (self.total_duration_sec + 1e-6):
                    raise ValueError(
                        f"clip {clip.asset_id!r} exceeds total_duration_sec"
                    )
        return self


class RenderJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: RenderStatus = RenderStatus.QUEUED
    progress: int = Field(default=0, ge=0, le=100)
    ratio: TimelineRatio
    payload: TimelinePayload
    output_path: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


class RenderCreateResponse(BaseModel):
    render_id: str
    status: RenderStatus


class RenderStatusResponse(BaseModel):
    render_id: str
    status: RenderStatus
    progress: int
    output_url: str | None = None
    error: str | None = None
