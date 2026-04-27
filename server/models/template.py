"""Template models for parameterized workflow blueprints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
import uuid

from pydantic import BaseModel, Field


class TemplateCreate(BaseModel):
    """Request body for creating/updating a workflow template."""

    name: str
    description: str | None = None
    steps: list[dict[str, Any]]


class Template(BaseModel):
    """Stored workflow template."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None
    steps: list[dict[str, Any]]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TemplateInstantiate(BaseModel):
    """Instantiate a template into a concrete job chain."""

    template_id: str
    vars: dict[str, str] = Field(default_factory=dict)
