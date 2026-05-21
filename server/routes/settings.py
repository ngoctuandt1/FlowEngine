"""Settings API for the IdeaStudio setup surface."""

from __future__ import annotations

from urllib.parse import urlparse

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
    update_flow_view_settings_fields,
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


def _origin_for_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}".lower()


def _assert_same_origin_mutation(request: Request) -> None:
    from server import dashboard_auth

    if not dashboard_auth.DASHBOARD_AUTH_ENABLED:
        return

    request_origin = _origin_for_url(str(request.url))
    header_origin = request.headers.get("origin")
    if header_origin:
        if _origin_for_url(header_origin) != request_origin:
            raise HTTPException(status_code=403, detail="Cross-origin settings mutation rejected")
        return

    referer = request.headers.get("referer")
    if referer and _origin_for_url(referer) == request_origin:
        return

    raise HTTPException(status_code=403, detail="Settings mutation requires same-origin header")


async def _flow_auth_context() -> FlowAuthContext:
    accounts = await list_veo_accounts()
    account = next((item for item in accounts if item.enabled), None)
    if account is None:
        return FlowAuthContext(headers={})
    return FlowAuthContext.from_credentials(token=account.token, cookie=account.cookie)


def _flow_proxy_exception(exc: FlowSettingsProxyError) -> HTTPException:
    status_code = 504 if exc.error_kind == "flow_timeout" else 502
    detail = {
        "error_kind": exc.error_kind,
        "message": exc.message,
    }
    if exc.status_code is not None:
        detail["flow_status_code"] = exc.status_code
    return HTTPException(status_code=status_code, detail=detail)


def _flow_auth_unavailable() -> FlowSettingsProxyError:
    return FlowSettingsProxyError(
        "flow_auth_unavailable",
        "Flow auth context is unavailable",
    )


def _view_settings_update_fields(body: FlowViewSettings) -> dict:
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        fields = body.model_dump()
    return fields


@router.get("", response_model=FlowViewSettings)
async def get_view_settings_endpoint():
    """Return effective Flow view settings."""
    stored = await get_flow_view_settings()
    auth_context = await _flow_auth_context()
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
    _assert_same_origin_mutation(request)
    auth_context = await _flow_auth_context()
    if not auth_context.is_available:
        raise _flow_proxy_exception(_flow_auth_unavailable())
    fields = _view_settings_update_fields(body)
    try:
        await _flow_settings_client.update_user_settings(
            fields,
            auth_context,
        )
    except FlowSettingsProxyError as exc:
        raise _flow_proxy_exception(exc) from exc
    return await update_flow_view_settings_fields(fields)


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
