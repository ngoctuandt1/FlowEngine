"""Character CRUD operations (async, aiosqlite)."""

import json
from datetime import UTC, datetime
from typing import Optional

from server.db.database import get_db
from server.models.character import Character, CharacterUpdate


def _row_to_character(row) -> Character:
    """Convert an aiosqlite.Row to a Character model."""
    data = dict(row)
    data["image_paths"] = json.loads(data["image_paths"])
    return Character(**data)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


async def create_character(character: Character) -> Character:
    """Insert a new character row and return it."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO characters (
                id, name, description, image_paths, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                character.id,
                character.name,
                character.description,
                json.dumps(character.image_paths),
                character.created_at.isoformat(),
                character.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return character


async def get_character(character_id: str) -> Optional[Character]:
    """Fetch a single character by id, or None."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM characters WHERE id = ?",
            (character_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_character(row)


async def list_characters() -> list[Character]:
    """List characters in name order."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM characters ORDER BY name COLLATE NOCASE ASC"
        )
        rows = await cursor.fetchall()
        return [_row_to_character(row) for row in rows]


async def update_character(
    character_id: str, update: CharacterUpdate
) -> Optional[Character]:
    """Apply partial update to a character. Returns updated Character or None."""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_character(character_id)

    sets: list[str] = []
    params: list = []

    for key, value in fields.items():
        if key == "image_paths":
            sets.append("image_paths = ?")
            params.append(json.dumps(value))
        else:
            sets.append(f"{key} = ?")
            params.append(value)

    sets.append("updated_at = ?")
    params.append(_now_iso())
    params.append(character_id)

    sql = f"UPDATE characters SET {', '.join(sets)} WHERE id = ?"
    async with get_db() as db:
        cursor = await db.execute(sql, params)
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_character(character_id)


async def delete_character(character_id: str) -> bool:
    """Delete a character by id. Returns True if a row was removed."""
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
