"""Settings models for the IdeaStudio setup surface."""

from __future__ import annotations

from datetime import UTC, datetime
import uuid

from pydantic import BaseModel, Field, model_validator


DEFAULT_GEMINI_MODEL = "gemini-2-flash-preview"


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


class AISettings(BaseModel):
    """Stored AI settings payload."""

    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    nano_api_key: str = ""


class AISettingsUpdate(BaseModel):
    """Partial AI settings update payload."""

    gemini_api_key: str | None = None
    gemini_model: str | None = None
    nano_api_key: str | None = None

    @model_validator(mode="after")
    def _normalize(self) -> "AISettingsUpdate":
        self.gemini_api_key = _normalize_optional_text(self.gemini_api_key)
        self.gemini_model = _normalize_optional_text(self.gemini_model)
        self.nano_api_key = _normalize_optional_text(self.nano_api_key)
        return self


class AISettingsPublic(BaseModel):
    """Read model with redacted secret fields."""

    gemini_api_key: str = ""
    gemini_model: str = DEFAULT_GEMINI_MODEL
    nano_api_key: str = ""


class VeoAccount(BaseModel):
    """Stored Veo account record."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    token: str
    cookie: str
    enabled: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VeoAccountCreate(BaseModel):
    """Create payload for one Veo account."""

    name: str = Field(min_length=1)
    token: str = Field(min_length=1)
    cookie: str = Field(min_length=1)
    enabled: bool = True

    @model_validator(mode="after")
    def _normalize(self) -> "VeoAccountCreate":
        self.name = self.name.strip()
        self.token = self.token.strip()
        self.cookie = self.cookie.strip()
        if not self.name:
            raise ValueError("name must not be empty")
        if not self.token:
            raise ValueError("token must not be empty")
        if not self.cookie:
            raise ValueError("cookie must not be empty")
        return self


class VeoAccountUpdate(BaseModel):
    """Partial update payload for one Veo account."""

    name: str | None = None
    token: str | None = None
    cookie: str | None = None
    enabled: bool | None = None

    @model_validator(mode="after")
    def _normalize(self) -> "VeoAccountUpdate":
        self.name = _normalize_optional_text(self.name)
        self.token = _normalize_optional_text(self.token)
        self.cookie = _normalize_optional_text(self.cookie)
        return self


class VeoAccountPublic(BaseModel):
    """Read model with redacted secret fields."""

    id: str
    name: str
    token: str
    cookie: str
    enabled: bool
