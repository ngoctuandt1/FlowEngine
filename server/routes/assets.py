"""Asset catalog endpoints."""

from datetime import UTC, datetime
import sqlite3

from fastapi import APIRouter, HTTPException, Query

from server.db.asset_store import (
    assets_from_project_initial_data,
    create_asset,
    delete_asset,
    get_asset,
    list_assets,
    update_asset,
    upsert_assets,
)
from server.models.asset import Asset, AssetCreate, AssetType, AssetUpdate


router = APIRouter(prefix="/api/assets", tags=["assets"])


@router.post("", response_model=Asset, status_code=201)
async def create_asset_endpoint(req: AssetCreate) -> Asset:
    values = {
        "type": req.type,
        "name": req.name,
        "description": req.description,
        "sample_url": req.sample_url,
        "source": req.source,
    }
    if req.id:
        values["id"] = req.id
    asset = Asset(**values)
    return await create_asset(asset)


@router.get("", response_model=list[Asset])
async def list_assets_endpoint(
    type: AssetType | None = Query(default=None),
) -> list[Asset]:
    return await list_assets(type)


@router.get("/{asset_id}", response_model=Asset)
async def get_asset_endpoint(asset_id: str) -> Asset:
    asset = await get_asset(asset_id)
    if asset is None:
        raise HTTPException(404, f"Asset {asset_id} not found")
    return asset


@router.put("/{asset_id}", response_model=Asset)
async def update_asset_endpoint(asset_id: str, req: AssetUpdate) -> Asset:
    existing = await get_asset(asset_id)
    if existing is None:
        raise HTTPException(404, f"Asset {asset_id} not found")
    if existing.source == "flow_preset":
        raise HTTPException(409, "Flow preset assets are read-only")
    updated = await update_asset(asset_id, req)
    if updated is None:
        raise HTTPException(404, f"Asset {asset_id} not found")
    return updated


@router.delete("/{asset_id}")
async def delete_asset_endpoint(asset_id: str) -> dict[str, str]:
    existing = await get_asset(asset_id)
    if existing is None:
        raise HTTPException(404, f"Asset {asset_id} not found")
    if existing.source == "flow_preset":
        raise HTTPException(409, "Flow preset assets are read-only")
    try:
        deleted = await delete_asset(asset_id)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "Asset is referenced by jobs") from exc
    if not deleted:
        raise HTTPException(404, f"Asset {asset_id} not found")
    return {"deleted": asset_id}


@router.post("/voices/import", response_model=list[Asset])
async def import_voice_assets_endpoint(payload: dict) -> list[Asset]:
    assets = assets_from_project_initial_data(payload)
    if not assets:
        return []
    timestamp = datetime.now(UTC)
    assets = [asset.model_copy(update={"created_at": timestamp}) for asset in assets]
    return await upsert_assets(assets)
