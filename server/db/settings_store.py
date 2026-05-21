"""Settings persistence helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from server.db.database import get_db
from server.models.settings import (
    AISettings,
    AISettingsUpdate,
    FlowViewSettings,
    VeoAccount,
    VeoAccountCreate,
    VeoAccountUpdate,
)


AI_SETTINGS_KEY = "ai"
FLOW_VIEW_SETTINGS_KEY = "flow_view"
REDACTED_PREFIX = "***"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _is_redacted_placeholder(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(REDACTED_PREFIX)


def _resolve_secret_update(existing: str, incoming: str | None) -> str:
    if incoming is None or _is_redacted_placeholder(incoming):
        return existing
    return incoming


def _row_to_veo_account(row) -> VeoAccount:
    data = dict(row)
    data["enabled"] = bool(data["enabled"])
    return VeoAccount(**data)


async def _get_json_setting(key: str) -> dict[str, Any] | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?",
            (key,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        value = row["value"]
        if not value:
            return None
        return json.loads(value)


async def _upsert_json_setting(key: str, value: dict[str, Any]) -> None:
    now = _now_iso()
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), now),
        )
        await db.commit()


async def get_ai_settings() -> AISettings:
    payload = await _get_json_setting(AI_SETTINGS_KEY)
    if payload is None:
        return AISettings()
    return AISettings(**payload)


async def update_ai_settings(update: AISettingsUpdate) -> AISettings:
    current = await get_ai_settings()
    fields = update.model_dump(exclude_unset=True)

    merged = AISettings(
        gemini_api_key=_resolve_secret_update(
            current.gemini_api_key,
            fields.get("gemini_api_key"),
        ),
        gemini_model=(
            current.gemini_model
            if fields.get("gemini_model") is None
            else fields["gemini_model"]
        ),
        nano_api_key=_resolve_secret_update(
            current.nano_api_key,
            fields.get("nano_api_key"),
        ),
    )
    await _upsert_json_setting(AI_SETTINGS_KEY, merged.model_dump())
    return merged


async def get_flow_view_settings() -> FlowViewSettings:
    payload = await _get_json_setting(FLOW_VIEW_SETTINGS_KEY)
    if payload is None:
        return FlowViewSettings()
    return FlowViewSettings(**payload)


async def update_flow_view_settings(update: FlowViewSettings) -> FlowViewSettings:
    current = await get_flow_view_settings()
    merged = current.model_dump()
    merged.update(update.model_dump())
    settings = FlowViewSettings(**merged)
    await _upsert_json_setting(FLOW_VIEW_SETTINGS_KEY, settings.model_dump())
    return settings


async def list_veo_accounts() -> list[VeoAccount]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM veo_accounts ORDER BY created_at ASC"
        )
        rows = await cursor.fetchall()
        return [_row_to_veo_account(row) for row in rows]


async def get_veo_account(account_id: str) -> VeoAccount | None:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM veo_accounts WHERE id = ?",
            (account_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_veo_account(row)


async def create_veo_account(payload: VeoAccountCreate) -> VeoAccount:
    account = VeoAccount(
        name=payload.name,
        token=payload.token,
        cookie=payload.cookie,
        enabled=payload.enabled,
    )
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO veo_accounts (
                id, name, token, cookie, enabled, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account.id,
                account.name,
                account.token,
                account.cookie,
                1 if account.enabled else 0,
                account.created_at.isoformat(),
                account.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return account


async def update_veo_account(
    account_id: str,
    update: VeoAccountUpdate,
) -> VeoAccount | None:
    existing = await get_veo_account(account_id)
    if existing is None:
        return None

    fields = update.model_dump(exclude_unset=True)
    updated = VeoAccount(
        id=existing.id,
        name=existing.name if fields.get("name") is None else fields["name"],
        token=_resolve_secret_update(existing.token, fields.get("token")),
        cookie=_resolve_secret_update(existing.cookie, fields.get("cookie")),
        enabled=existing.enabled if fields.get("enabled") is None else fields["enabled"],
        created_at=existing.created_at,
        updated_at=datetime.now(UTC),
    )

    async with get_db() as db:
        await db.execute(
            """
            UPDATE veo_accounts
            SET name = ?, token = ?, cookie = ?, enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                updated.name,
                updated.token,
                updated.cookie,
                1 if updated.enabled else 0,
                updated.updated_at.isoformat(),
                account_id,
            ),
        )
        await db.commit()
    return updated


async def delete_veo_account(account_id: str) -> bool:
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM veo_accounts WHERE id = ?",
            (account_id,),
        )
        await db.commit()
        return cursor.rowcount > 0
