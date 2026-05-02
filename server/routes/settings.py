"""Settings API for the IdeaStudio setup surface."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response

from server.db.settings_store import (
    create_veo_account,
    delete_veo_account,
    get_ai_settings,
    list_veo_accounts,
    update_ai_settings,
    update_veo_account,
)
from server.models.settings import (
    AISettings,
    AISettingsPublic,
    AISettingsUpdate,
    VeoAccount,
    VeoAccountCreate,
    VeoAccountPublic,
    VeoAccountUpdate,
)


router = APIRouter(prefix="/api/settings", tags=["settings"])


def _redact_secret(value: str) -> str:
    if not value:
        return ""
    return f"***{value[-4:]}"


def _to_ai_public(settings: AISettings) -> AISettingsPublic:
    return AISettingsPublic(
        gemini_api_key=_redact_secret(settings.gemini_api_key),
        gemini_model=settings.gemini_model,
        nano_api_key=_redact_secret(settings.nano_api_key),
    )


def _to_veo_public(account: VeoAccount) -> VeoAccountPublic:
    return VeoAccountPublic(
        id=account.id,
        name=account.name,
        token=_redact_secret(account.token),
        cookie=_redact_secret(account.cookie),
        enabled=account.enabled,
    )


@router.get("/ai", response_model=AISettingsPublic)
async def get_ai_settings_endpoint():
    """Return AI settings with secrets redacted."""
    return _to_ai_public(await get_ai_settings())


@router.put("/ai", status_code=204)
async def update_ai_settings_endpoint(body: AISettingsUpdate):
    """Persist AI settings, preserving redacted placeholders."""
    await update_ai_settings(body)
    return Response(status_code=204)


@router.get("/veo-accounts", response_model=list[VeoAccountPublic])
async def list_veo_accounts_endpoint():
    """List Veo accounts with secrets redacted."""
    accounts = await list_veo_accounts()
    return [_to_veo_public(account) for account in accounts]


@router.post("/veo-accounts", response_model=VeoAccountPublic, status_code=201)
async def create_veo_account_endpoint(body: VeoAccountCreate):
    """Create a Veo account."""
    account = await create_veo_account(body)
    return _to_veo_public(account)


@router.put("/veo-accounts/{account_id}", status_code=204)
async def update_veo_account_endpoint(account_id: str, body: VeoAccountUpdate):
    """Update one Veo account, preserving redacted placeholders."""
    updated = await update_veo_account(account_id, body)
    if updated is None:
        raise HTTPException(404, f"Veo account {account_id} not found")
    return Response(status_code=204)


@router.delete("/veo-accounts/{account_id}", status_code=204)
async def delete_veo_account_endpoint(account_id: str):
    """Delete one Veo account."""
    deleted = await delete_veo_account(account_id)
    if not deleted:
        raise HTTPException(404, f"Veo account {account_id} not found")
    return Response(status_code=204)
