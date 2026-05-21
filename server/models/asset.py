"""Reusable media asset models for FlowEngine."""

from datetime import UTC, datetime
from enum import Enum
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetType(str, Enum):
    VOICE = "voice"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_required_text(value: str, *, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field} is required")
    return text


class AssetCreate(BaseModel):
    """Request body for creating an Engine asset."""

    id: str | None = None
    type: AssetType = AssetType.VOICE
    name: str
    description: str | None = None
    sample_url: str | None = None
    source: str = "user"

    @field_validator("id", "description", "sample_url")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_required_text(value, field="name")

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _normalize_required_text(value, field="source")


class AssetUpdate(BaseModel):
    """Fields that can be updated on a user-owned asset."""

    name: str | None = None
    description: str | None = None
    sample_url: str | None = None
    source: str | None = None

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _normalize_required_text(value, field="name")

    @field_validator("description", "sample_url", "source")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @model_validator(mode="after")
    def _reject_blank_source_update(self) -> "AssetUpdate":
        if "source" in self.model_fields_set and self.source is None:
            raise ValueError("source cannot be empty")
        return self


class Asset(BaseModel):
    """Complete reusable media asset record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: AssetType = AssetType.VOICE
    name: str
    description: str | None = None
    sample_url: str | None = None
    source: str = "user"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_required_text(value, field="name")

    @field_validator("description", "sample_url")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _normalize_required_text(value, field="source")
