"""Asset CRUD operations."""

from datetime import UTC, datetime
import json
import sqlite3
from typing import Any

from server.db.database import get_db
from server.models.asset import Asset, AssetType, AssetUpdate


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_datetime(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _row_to_asset(row) -> Asset:
    data = dict(row)
    return Asset(
        id=data["id"],
        type=AssetType(data["type"]),
        name=data["name"],
        description=data.get("description"),
        sample_url=data.get("sample_url"),
        source=data.get("source") or "user",
        created_at=_parse_datetime(data.get("created_at")),
    )


async def ensure_asset_schema() -> None:
    """Create asset table/indexes for existing databases."""

    async with get_db() as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS assets (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL,
                name        TEXT NOT NULL,
                description TEXT,
                sample_url  TEXT,
                source      TEXT NOT NULL DEFAULT 'user',
                created_at  TEXT NOT NULL
            )
            """
        )
        await db.execute("CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_assets_source ON assets(source)")
        await db.commit()


async def create_asset(asset: Asset) -> Asset:
    await ensure_asset_schema()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO assets (id, type, name, description, sample_url, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset.id,
                asset.type.value,
                asset.name,
                asset.description,
                asset.sample_url,
                asset.source,
                asset.created_at.isoformat(),
            ),
        )
        await db.commit()
    return asset


async def upsert_assets(assets: list[Asset]) -> list[Asset]:
    await ensure_asset_schema()
    if not assets:
        return []
    async with get_db() as db:
        await db.executemany(
            """
            INSERT INTO assets (id, type, name, description, sample_url, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                type = excluded.type,
                name = excluded.name,
                description = excluded.description,
                sample_url = excluded.sample_url,
                source = excluded.source
            """,
            [
                (
                    asset.id,
                    asset.type.value,
                    asset.name,
                    asset.description,
                    asset.sample_url,
                    asset.source,
                    asset.created_at.isoformat(),
                )
                for asset in assets
            ],
        )
        await db.commit()
    return assets


async def get_asset(asset_id: str) -> Asset | None:
    await ensure_asset_schema()
    async with get_db() as db:
        cursor = await db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
        row = await cursor.fetchone()
    return _row_to_asset(row) if row else None


async def list_assets(asset_type: AssetType | None = None) -> list[Asset]:
    await ensure_asset_schema()
    if asset_type is None:
        query = "SELECT * FROM assets ORDER BY type ASC, name COLLATE NOCASE ASC"
        params: tuple[str, ...] = ()
    else:
        query = "SELECT * FROM assets WHERE type = ? ORDER BY name COLLATE NOCASE ASC"
        params = (asset_type.value,)
    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
    return [_row_to_asset(row) for row in rows]


async def update_asset(asset_id: str, update: AssetUpdate) -> Asset | None:
    await ensure_asset_schema()
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_asset(asset_id)
    sets = [f"{field} = ?" for field in fields]
    params = [*fields.values(), asset_id]
    async with get_db() as db:
        cursor = await db.execute(
            f"UPDATE assets SET {', '.join(sets)} WHERE id = ? AND source != 'flow_preset'",
            params,
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_asset(asset_id)


async def delete_asset(asset_id: str) -> bool:
    await ensure_asset_schema()
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM assets WHERE id = ? AND source != 'flow_preset'",
            (asset_id,),
        )
        await db.commit()
        return cursor.rowcount > 0


def assets_from_project_initial_data(payload: dict[str, Any]) -> list[Asset]:
    """Map Flow projectInitialData external AUDIO records to voice assets."""

    contents = _unwrap_project_initial_data(payload).get("projectContents") or {}
    records = contents.get("externalReferenceMedia") or []
    assets: list[Asset] = []
    for record in records:
        if not isinstance(record, dict) or record.get("mediaType") != "AUDIO":
            continue
        media_id = str(record.get("mediaId") or "").strip()
        if not media_id:
            continue
        generated = (
            (record.get("media") or {})
            .get("audio", {})
            .get("generatedAudio", {})
        )
        name = (
            str(generated.get("name") or "").strip()
            or str(record.get("workflowDisplayName") or "").strip()
            or media_id
        )
        assets.append(
            Asset(
                id=media_id,
                type=AssetType.VOICE,
                name=name,
                description=str(generated.get("description") or "").strip() or None,
                sample_url=str(generated.get("audioSamplePath") or "").strip() or None,
                source="flow_preset",
            )
        )
    return assets


def _unwrap_project_initial_data(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload:
        return {}
    if "response_preview" in payload:
        try:
            payload = json.loads(payload["response_preview"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise sqlite3.DataError("response_preview is not valid JSON") from exc
    return (
        payload.get("result", {})
        .get("data", {})
        .get("json", payload)
    )
