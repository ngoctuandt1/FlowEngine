"""Character library data models for FlowEngine."""

from datetime import UTC, datetime
from typing import Optional
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_name(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Character name is required")
    if len(text) > 64:
        raise ValueError("Character name must be 64 characters or fewer")
    return text


def _validate_image_paths(value: list[str] | None) -> list[str] | None:
    if value is None:
        return None
    if not value:
        raise ValueError("image_paths must include at least one image when provided")
    if len(value) > 10:
        raise ValueError("image_paths must include 10 images or fewer")
    return value


class CharacterCreate(BaseModel):
    """Request body for creating a reusable character reference."""

    project_id: Optional[str] = None
    name: str
    ref_image_url: Optional[str] = None
    voice_id: Optional[str] = None

    description: Optional[str] = None
    image_paths: Optional[list[str]] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_name(value)

    @field_validator("project_id", "ref_image_url", "voice_id", "description")
    @classmethod
    def _normalize_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("image_paths")
    @classmethod
    def _validate_paths(cls, value: list[str] | None) -> list[str] | None:
        return _validate_image_paths(value)


class CharacterUpdate(BaseModel):
    """Fields that can be updated on a character."""

    project_id: Optional[str] = None
    name: Optional[str] = None
    ref_image_url: Optional[str] = None
    voice_id: Optional[str] = None

    description: Optional[str] = None
    image_paths: Optional[list[str]] = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_name(value)

    @field_validator("project_id", "ref_image_url", "voice_id", "description")
    @classmethod
    def _normalize_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("image_paths")
    @classmethod
    def _validate_paths(cls, value: list[str] | None) -> list[str] | None:
        return _validate_image_paths(value)

    @model_validator(mode="after")
    def _reject_blank_name_update(self) -> "CharacterUpdate":
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Character name cannot be empty")
        return self


class Character(BaseModel):
    """Complete character record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    project_id: Optional[str] = None
    name: str
    ref_image_url: Optional[str] = None
    voice_id: Optional[str] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    description: Optional[str] = None
    image_paths: list[str] = Field(default_factory=list, max_length=10)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_name(value)

    @field_validator("project_id", "ref_image_url", "voice_id", "description")
    @classmethod
    def _normalize_text(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def _derive_ref_image(self) -> "Character":
        if self.ref_image_url is None and self.image_paths:
            self.ref_image_url = self.image_paths[0]
        return self
