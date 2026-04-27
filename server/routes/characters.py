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


def _normalize_image_paths(image_paths: list[str]) -> list[str]:
    """Validate and normalize image path payloads."""
    return [_normalize_image_path(path) for path in image_paths]


@router.post("", response_model=Character, status_code=201)
async def create_character_endpoint(body: CharacterCreate):
    """Create a reusable character entry."""
    character = Character(
        name=body.name,
        description=body.description,
        image_paths=_normalize_image_paths(body.image_paths),
    )
    try:
        await create_character(character)
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"Character '{body.name}' already exists") from None
    return character


@router.get("", response_model=list[Character])
async def list_characters_endpoint():
    """List all characters."""
    return await list_characters()


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

    payload = body.model_dump(exclude_unset=True)
    if "image_paths" in payload:
        payload["image_paths"] = _normalize_image_paths(payload["image_paths"])

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
