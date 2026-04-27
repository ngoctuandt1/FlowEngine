"""Character library data models for FlowEngine."""

from datetime import UTC, datetime
from typing import Optional
import uuid

from pydantic import BaseModel, Field


class CharacterCreate(BaseModel):
    """Request body for creating a reusable character reference."""

    name: str = Field(min_length=1, max_length=64)
    description: Optional[str] = None
    image_paths: list[str] = Field(min_length=1, max_length=10)


class CharacterUpdate(BaseModel):
    """Fields that can be updated on a character."""

    name: Optional[str] = Field(default=None, min_length=1, max_length=64)
    description: Optional[str] = None
    image_paths: Optional[list[str]] = Field(default=None, min_length=1, max_length=10)


class Character(BaseModel):
    """Complete character record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(min_length=1, max_length=64)
    description: Optional[str] = None
    image_paths: list[str] = Field(min_length=1, max_length=10)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
