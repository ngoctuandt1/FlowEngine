"""Profile management endpoints."""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from flow.credentials.sheet_loader import (
    MalformedSheetError,
    SheetLoaderError,
    sheet_mode_enabled,
    sync_profiles_from_sheet,
)
from server.config import API_KEY
from server.models.profile import Profile, ProfileStatus, ProfileUpdate
from server.db.profile_store import (
    create_profile, get_profile, list_profiles, update_profile,
)
from server.db.job_store import list_jobs

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileStatusReason(BaseModel):
    """Optional reason payload for status mutations."""

    reason: Optional[str] = None


def _configured_api_key() -> str:
    return os.environ.get("API_KEY", API_KEY)


def _is_default_key(key: str) -> bool:
    return key in {"", "dev-key", "changeme"}


def _require_reload_key(worker_api_key: str | None) -> None:
    expected = _configured_api_key()
    if _is_default_key(expected) and not worker_api_key:
        return
    if not worker_api_key or not secrets.compare_digest(worker_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid worker API key")


@router.get("")
async def get_all_profiles():
    """List all registered profiles."""
    return await list_profiles()


@router.post("/reload")
async def reload_profiles_from_sheet(
    x_worker_api_key: str | None = Header(default=None, alias="X-Worker-API-Key"),
):
    """Reload profiles_ultra.txt from Google Sheet credentials."""

    _require_reload_key(x_worker_api_key)
    if not sheet_mode_enabled():
        raise HTTPException(status_code=409, detail="Sheet mode not enabled")
    try:
        result = sync_profiles_from_sheet()
    except MalformedSheetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SheetLoaderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"loaded": result.loaded, "profiles": result.profiles}


@router.post("", status_code=201)
async def register_profile(profile: Profile):
    """Create / register a new profile."""
    existing = await get_profile(profile.name)
    if existing is not None:
        raise HTTPException(409, f"Profile '{profile.name}' already exists")
    await create_profile(profile)
    return profile


@router.put("/{name}")
async def update_existing_profile(name: str, body: ProfileUpdate):
    """Update a profile's mutable fields."""
    existing = await get_profile(name)
    if existing is None:
        raise HTTPException(404, f"Profile '{name}' not found")
    updated = await update_profile(name, body)
    return updated


@router.get("/{name}")
async def get_single_profile(name: str):
    """Get a single profile by name."""
    profile = await get_profile(name)
    if profile is None:
        raise HTTPException(404, f"Profile '{name}' not found")
    return profile


@router.get("/{name}/jobs")
async def get_profile_jobs(name: str):
    """List all jobs bound to a given profile."""
    profile = await get_profile(name)
    if profile is None:
        raise HTTPException(404, f"Profile '{name}' not found")
    return await list_jobs(profile=name)


@router.post("/{name}/quarantine")
async def quarantine_profile(name: str, body: Optional[ProfileStatusReason] = None):
    """Mark a profile as quarantined and return the updated row."""
    existing = await get_profile(name)
    if existing is None:
        raise HTTPException(404, f"Profile '{name}' not found")
    updated = await update_profile(
        name,
        ProfileUpdate(status=ProfileStatus.QUARANTINED),
    )
    return updated


@router.post("/{name}/activate")
async def activate_profile(name: str):
    """Mark a profile as available and return the updated row."""
    existing = await get_profile(name)
    if existing is None:
        raise HTTPException(404, f"Profile '{name}' not found")
    updated = await update_profile(
        name,
        ProfileUpdate(status=ProfileStatus.AVAILABLE),
    )
    return updated
