"""Reusable media asset models for FlowEngine."""

from datetime import UTC, datetime
from enum import Enum
import re
from urllib.parse import urlparse
import uuid

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetType(str, Enum):
    VOICE = "voice"


ASSET_SOURCE_USER = "user"
ASSET_SOURCE_FLOW_PRESET = "flow_preset"
ASSET_SOURCES = frozenset({ASSET_SOURCE_USER, ASSET_SOURCE_FLOW_PRESET})
ASSET_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


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


def _validate_asset_id(value: str | None) -> str | None:
    text = _normalize_optional_text(value)
    if text is None:
        return None
    if not ASSET_ID_RE.fullmatch(text):
        raise ValueError("id must be selector-safe ASCII, 1-128 characters")
    return text


def _validate_sample_url(value: str | None) -> str | None:
    text = _normalize_optional_text(value)
    if text is None:
        return None
    parsed = urlparse(text)
    if parsed.scheme == "https" and parsed.netloc:
        return text
    if parsed.scheme or parsed.netloc:
        raise ValueError("sample_url must be https:// or /uploads/...")
    normalized = text.replace("\\", "/")
    if normalized.startswith("/uploads/") or normalized.startswith("uploads/"):
        parts = [part for part in normalized.split("/") if part]
        if ".." not in parts:
            return normalized
    raise ValueError("sample_url must be https:// or /uploads/...")


def _validate_asset_source(value: str, *, allow_flow_preset: bool) -> str:
    source = _normalize_required_text(value, field="source")
    if source not in ASSET_SOURCES:
        raise ValueError(f"source must be one of: {', '.join(sorted(ASSET_SOURCES))}")
    if source == ASSET_SOURCE_FLOW_PRESET and not allow_flow_preset:
        raise ValueError("flow_preset assets can only be imported from Flow")
    return source


class AssetCreate(BaseModel):
    """Request body for creating an Engine asset."""

    id: str | None = None
    type: AssetType = AssetType.VOICE
    name: str
    description: str | None = None
    sample_url: str | None = None
    source: str = ASSET_SOURCE_USER

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str | None) -> str | None:
        return _validate_asset_id(value)

    @field_validator("description")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("sample_url")
    @classmethod
    def _validate_sample_url(cls, value: str | None) -> str | None:
        return _validate_sample_url(value)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_required_text(value, field="name")

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _validate_asset_source(value, allow_flow_preset=False)


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

    @field_validator("sample_url")
    @classmethod
    def _validate_sample_url(cls, value: str | None) -> str | None:
        return _validate_sample_url(value)

    @model_validator(mode="after")
    def _reject_blank_source_update(self) -> "AssetUpdate":
        if "source" in self.model_fields_set:
            raise ValueError("source is immutable")
        return self


class Asset(BaseModel):
    """Complete reusable media asset record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: AssetType = AssetType.VOICE
    name: str
    description: str | None = None
    sample_url: str | None = None
    source: str = ASSET_SOURCE_USER
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        return _normalize_required_text(value, field="name")

    @field_validator("description", "sample_url")
    @classmethod
    def _normalize_optional(cls, value: str | None) -> str | None:
        return _normalize_optional_text(value)

    @field_validator("id")
    @classmethod
    def _validate_id(cls, value: str) -> str:
        normalized = _validate_asset_id(value)
        if normalized is None:
            raise ValueError("id is required")
        return normalized

    @field_validator("sample_url")
    @classmethod
    def _validate_sample_url(cls, value: str | None) -> str | None:
        return _validate_sample_url(value)

    @field_validator("source")
    @classmethod
    def _validate_source(cls, value: str) -> str:
        return _validate_asset_source(value, allow_flow_preset=True)
