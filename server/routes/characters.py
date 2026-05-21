"""Character library endpoints."""

from pathlib import Path
import os
import sqlite3

from fastapi import APIRouter, HTTPException

from server.db.character_store import (
    create_character,
    delete_character,
    get_character,
    list_characters,
    project_exists,
    update_character,
)
from server.models.character import Character, CharacterCreate, CharacterUpdate

router = APIRouter(prefix="/api/characters", tags=["characters"])

UPLOAD_DIR = Path(os.environ.get("FLOW_UPLOAD_DIR", "./uploads")).resolve()


def _normalize_image_path(path_value: str) -> str:
    """Validate that an image path resolves under FLOW_UPLOAD_DIR and exists."""
    trimmed = (path_value or "").strip()
    if not trimmed:
        raise HTTPException(400, "Image path must not be empty")

    raw_path = Path(trimmed)
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        parts = raw_path.parts
        if parts and parts[0].lower() == "uploads":
            parts = parts[1:]
        resolved = UPLOAD_DIR.joinpath(*parts).resolve()

    if not resolved.is_relative_to(UPLOAD_DIR):
        raise HTTPException(
            400,
            f"Image path escapes FLOW_UPLOAD_DIR: {path_value}",
        )
    if not resolved.is_file():
        raise HTTPException(
            400,
            f"Image path does not exist under FLOW_UPLOAD_DIR: {path_value}",
        )

    return Path("uploads", *resolved.relative_to(UPLOAD_DIR).parts).as_posix()


def _normalize_ref_image_url(ref_image_url: str) -> str:
    """Restrict character reference images to local upload paths."""
    if ref_image_url.lower().startswith(("http://", "https://")):
        raise HTTPException(
            400,
            "ref_image_url must reference an uploaded image path under /uploads",
        )
    if ref_image_url.startswith("/"):
        ref_image_url = ref_image_url[1:]
    return _normalize_image_path(ref_image_url)


def _normalize_image_paths(image_paths: list[str]) -> list[str]:
    """Validate and normalize image path payloads."""
    return [_normalize_image_path(path) for path in image_paths]


async def _validate_project_binding(project_id: str | None) -> None:
    """Reject character rows bound to missing projects."""
    if project_id is None:
        return
    if not await project_exists(project_id):
        raise HTTPException(404, f"Project {project_id} not found")


def _normalized_character_payload(body: CharacterCreate | CharacterUpdate) -> dict:
    """Normalize legacy image_paths and Wave 5 ref_image_url fields."""

    payload = body.model_dump(exclude_unset=True)
    image_paths = payload.get("image_paths")
    if image_paths is not None:
        normalized_paths = _normalize_image_paths(image_paths)
        payload["image_paths"] = normalized_paths
        if payload.get("ref_image_url") is None and normalized_paths:
            payload["ref_image_url"] = normalized_paths[0]
    ref_image_url = payload.get("ref_image_url")
    if ref_image_url:
        payload["ref_image_url"] = _normalize_ref_image_url(ref_image_url)
        if "image_paths" not in payload:
            payload["image_paths"] = [payload["ref_image_url"]]
    return payload


@router.post("", response_model=Character, status_code=201)
async def create_character_endpoint(body: CharacterCreate):
    """Create a reusable character entry."""
    await _validate_project_binding(body.project_id)
    payload = _normalized_character_payload(body)
    character = Character(
        project_id=payload.get("project_id"),
        name=payload["name"],
        ref_image_url=payload.get("ref_image_url"),
        voice_id=payload.get("voice_id"),
        description=payload.get("description"),
        image_paths=payload.get("image_paths") or [],
    )
    try:
        character = await create_character(character)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Character '{body.name}' already exists") from None
    return character


@router.get("", response_model=list[Character])
async def list_characters_endpoint(project_id: str | None = None):
    """List all characters."""
    await _validate_project_binding(project_id)
    return await list_characters(project_id=project_id)


@router.get("/{character_id}", response_model=Character)
async def get_character_endpoint(character_id: str):
    """Fetch a single character by id."""
    character = await get_character(character_id)
    if character is None:
        raise HTTPException(404, f"Character {character_id} not found")
    return character


@router.put("/{character_id}", response_model=Character)
async def update_character_endpoint(character_id: str, body: CharacterUpdate):
    """Update a character."""
    existing = await get_character(character_id)
    if existing is None:
        raise HTTPException(404, f"Character {character_id} not found")

    await _validate_project_binding(body.project_id)
    payload = _normalized_character_payload(body)

    try:
        updated = await update_character(character_id, CharacterUpdate(**payload))
    except sqlite3.IntegrityError:
        conflict_name = payload.get("name", existing.name)
        raise HTTPException(409, f"Character '{conflict_name}' already exists") from None

    if updated is None:
        raise HTTPException(404, f"Character {character_id} not found")
    return updated


@router.delete("/{character_id}")
async def delete_character_endpoint(character_id: str):
    """Delete a character by id."""
    deleted = await delete_character(character_id)
    if not deleted:
        raise HTTPException(404, f"Character {character_id} not found")
    return {"deleted": character_id}
