"""Template models for parameterized workflow blueprints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field

from server.models.job import BBox, JobType


class TemplateStep(BaseModel):
    """Typed template step with placeholder-friendly string fields."""

    model_config = ConfigDict(extra="allow")

    type: JobType
    prompt: str | None = None
    model: str | None = None
    aspect_ratio: str | None = None
    parent_job_id: str | None = None
    bbox: BBox | None = None
    direction: str | None = None
    start_image_path: str | None = None
    end_image_path: str | None = None
    ref_image_path: str | None = None
    ingredient_image_paths: list[str] | None = None
    safety_filter: Literal["block_most", "block_some", "block_few"] | None = None


class TemplateCreate(BaseModel):
    """Request body for creating/updating a workflow template."""

    name: str
    description: str | None = None
    steps: list[TemplateStep] = Field(min_length=1)


class Template(BaseModel):
    """Stored workflow template."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None
    steps: list[TemplateStep] = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TemplateInstantiate(BaseModel):
    """Instantiate a template into a concrete job chain."""

    template_id: str
    vars: dict[str, str] = Field(default_factory=dict)
