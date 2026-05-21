"""Character CRUD operations (async, aiosqlite)."""

from datetime import UTC, datetime
from typing import Optional
import json

from server.db.database import get_db
from server.models.character import Character, CharacterUpdate


_CHARACTER_COLUMNS = {
    "id": "id TEXT PRIMARY KEY",
    "project_id": "project_id TEXT",
    "name": "name TEXT NOT NULL",
    "ref_image_url": "ref_image_url TEXT",
    "voice_id": "voice_id TEXT",
    "description": "description TEXT",
    "image_paths": "image_paths TEXT NOT NULL DEFAULT '[]'",
    "created_at": "created_at TEXT",
    "updated_at": "updated_at TEXT",
}


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _stable_iso(value: str | datetime | None) -> str:
    if value is None or value == "":
        return _now_iso()
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _json_list(value: str | list[str] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if str(item).strip()]


def _row_to_character(row) -> Character:
    """Convert an aiosqlite.Row to a Character model."""

    data = dict(row)
    image_paths = _json_list(data.get("image_paths"))
    ref_image_url = data.get("ref_image_url") or (image_paths[0] if image_paths else None)
    return Character(
        id=data["id"],
        project_id=data.get("project_id"),
        name=data["name"],
        ref_image_url=ref_image_url,
        voice_id=data.get("voice_id"),
        description=data.get("description"),
        image_paths=image_paths,
        created_at=_stable_iso(data.get("created_at")),
        updated_at=_stable_iso(data.get("updated_at") or data.get("created_at")),
    )


async def _table_columns(db) -> set[str]:
    cursor = await db.execute("PRAGMA table_info(characters)")
    rows = await cursor.fetchall()
    return {row[1] for row in rows}


async def ensure_character_schema() -> None:
    """Migrate legacy local character rows to Wave 5 character shape."""

    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS characters (
                id            TEXT PRIMARY KEY,
                project_id    TEXT,
                name          TEXT NOT NULL,
                ref_image_url TEXT,
                voice_id      TEXT,
                description   TEXT,
                image_paths   TEXT NOT NULL DEFAULT '[]',
                created_at    TEXT,
                updated_at    TEXT
            )
            """
        )
        columns = await _table_columns(db)
        for column, definition in _CHARACTER_COLUMNS.items():
            if column not in columns:
                await db.execute(f"ALTER TABLE characters ADD COLUMN {definition}")
                columns.add(column)

        now = _now_iso()
        await db.execute(
            "UPDATE characters SET image_paths = '[]' WHERE image_paths IS NULL OR image_paths = ''"
        )
        await db.execute(
            "UPDATE characters SET created_at = ? WHERE created_at IS NULL OR created_at = ''",
            (now,),
        )
        await db.execute(
            "UPDATE characters SET updated_at = COALESCE(NULLIF(updated_at, ''), created_at, ?) WHERE updated_at IS NULL OR updated_at = ''",
            (now,),
        )
        await db.execute(
            """
            UPDATE characters
            SET ref_image_url = json_extract(image_paths, '$[0]')
            WHERE (ref_image_url IS NULL OR ref_image_url = '')
              AND json_valid(image_paths)
              AND json_array_length(image_paths) > 0
            """
        )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_characters_name_project ON characters(COALESCE(project_id, ''), name COLLATE NOCASE)"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_characters_project_id ON characters(project_id)"
        )
        await db.commit()


async def project_exists(project_id: str) -> bool:
    """Return whether a project row exists."""

    async with get_db() as db:
        cursor = await db.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,))
        row = await cursor.fetchone()
        return row is not None


async def create_character(character: Character) -> Character:
    """Insert a new character row and return it."""

    await ensure_character_schema()
    now_created = _stable_iso(character.created_at)
    now_updated = _stable_iso(character.updated_at)
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO characters (
                id, project_id, name, ref_image_url, voice_id,
                description, image_paths, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                character.id,
                character.project_id,
                character.name,
                character.ref_image_url,
                character.voice_id,
                character.description,
                json.dumps(character.image_paths),
                now_created,
                now_updated,
            ),
        )
        await db.commit()
    return await get_character(character.id) or character


async def get_character(character_id: str) -> Optional[Character]:
    """Fetch a single character by id, or None."""

    await ensure_character_schema()
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM characters WHERE id = ?",
            (character_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_character(row)


async def list_characters(project_id: str | None = None) -> list[Character]:
    """List characters in name order, optionally scoped to one project."""

    await ensure_character_schema()
    async with get_db() as db:
        if project_id:
            cursor = await db.execute(
                """
                SELECT * FROM characters
                WHERE project_id = ?
                ORDER BY name COLLATE NOCASE ASC
                """,
                (project_id,),
            )
        else:
            cursor = await db.execute(
                "SELECT * FROM characters ORDER BY name COLLATE NOCASE ASC"
            )
        rows = await cursor.fetchall()
        return [_row_to_character(row) for row in rows]


async def update_character(
    character_id: str, update: CharacterUpdate
) -> Optional[Character]:
    """Apply partial update to a character. Returns updated Character or None."""

    await ensure_character_schema()
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_character(character_id)

    sets: list[str] = []
    params: list = []

    for key, value in fields.items():
        if key == "image_paths":
            sets.append("image_paths = ?")
            params.append(json.dumps(value or []))
            if "ref_image_url" not in fields and value:
                sets.append("ref_image_url = ?")
                params.append(value[0])
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

    await ensure_character_schema()
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM characters WHERE id = ?",
            (character_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
