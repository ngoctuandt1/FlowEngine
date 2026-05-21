"""Settings API for the IdeaStudio setup surface."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from flow.settings import (
    FlowAuthContext,
    FlowSettingsClient,
    FlowSettingsProxyError,
)

from server.db.settings_store import (
    create_veo_account,
    delete_veo_account,
    get_flow_view_settings,
    get_ai_settings,
    list_veo_accounts,
    update_ai_settings,
    update_flow_view_settings,
    update_veo_account,
)
from server.models.settings import (
    AISettings,
    AISettingsPublic,
    AISettingsUpdate,
    FlowViewSettings,
    VeoAccount,
    VeoAccountCreate,
    VeoAccountPublic,
    VeoAccountUpdate,
)


router = APIRouter(prefix="/api/settings", tags=["settings"])
_flow_settings_client = FlowSettingsClient()


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


def _merge_view_settings(
    stored: FlowViewSettings,
    flow_payload: dict | None,
) -> FlowViewSettings:
    merged = stored.model_dump()
    if flow_payload:
        merged.update(flow_payload)
    return FlowViewSettings(**merged)


def _flow_auth_context(request: Request) -> FlowAuthContext:
    return FlowAuthContext.from_headers(request.headers)


def _flow_proxy_exception(exc: FlowSettingsProxyError) -> HTTPException:
    status_code = 504 if exc.error_kind == "flow_timeout" else 502
    detail = {
        "error_kind": exc.error_kind,
        "message": exc.message,
    }
    if exc.status_code is not None:
        detail["flow_status_code"] = exc.status_code
    return HTTPException(status_code=status_code, detail=detail)


@router.get("", response_model=FlowViewSettings)
async def get_view_settings_endpoint(request: Request):
    """Return effective Flow view settings."""
    stored = await get_flow_view_settings()
    auth_context = _flow_auth_context(request)
    if not auth_context.is_available:
        return stored
    try:
        flow_payload = await _flow_settings_client.get_user_settings(auth_context)
    except FlowSettingsProxyError as exc:
        raise _flow_proxy_exception(exc) from exc
    return _merge_view_settings(stored, flow_payload)


@router.post("", response_model=FlowViewSettings)
async def update_view_settings_endpoint(body: FlowViewSettings, request: Request):
    """Persist and proxy Flow view settings."""
    auth_context = _flow_auth_context(request)
    try:
        await _flow_settings_client.update_user_settings(
            body.model_dump(),
            auth_context,
        )
    except FlowSettingsProxyError as exc:
        raise _flow_proxy_exception(exc) from exc
    return await update_flow_view_settings(body)


@router.get("/ai", response_model=AISettingsPublic)
async def get_ai_settings_endpoint():
    """Return AI settings with secrets redacted."""
    return _to_ai_public(await get_ai_settings())


async def _update_ai_settings_handler(body: AISettingsUpdate) -> Response:
    await update_ai_settings(body)
    return Response(status_code=204)


@router.put("/ai", status_code=204)
async def update_ai_settings_endpoint(body: AISettingsUpdate) -> Response:
    """Persist AI settings, preserving redacted placeholders (idiomatic REST)."""
    return await _update_ai_settings_handler(body)


@router.post("/ai", status_code=204)
async def update_ai_settings_endpoint_post(body: AISettingsUpdate) -> Response:
    """POST alias for PUT /api/settings/ai.

    The frontend (``frontend/js/pages/settings.js``) sends POST; older
    clients in the wild may do the same. We accept both verbs and keep
    them under distinct operation IDs so the OpenAPI schema stays clean.
    """
    return await _update_ai_settings_handler(body)


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
