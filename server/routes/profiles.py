"""Profile management endpoints."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from server.models.profile import Profile, ProfileStatus, ProfileUpdate
from server.db.profile_store import (
    create_profile, get_profile, list_profiles, update_profile,
)
from server.db.job_store import list_jobs

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileStatusReason(BaseModel):
    """Optional reason payload for status mutations."""

    reason: Optional[str] = None


@router.get("")
async def get_all_profiles():
    """List all registered profiles."""
    return await list_profiles()


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
