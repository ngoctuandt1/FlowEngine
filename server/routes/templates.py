"""Workflow template endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

from server.db.template_store import (
    create_template,
    delete_template,
    get_template,
    instantiate,
    list_templates,
    update_template,
    validate_template_steps,
    validate_vars,
)
from server.models.template import Template, TemplateCreate, TemplateInstantiate


router = APIRouter(prefix="/api/templates", tags=["templates"])


@router.post("", status_code=201)
async def create_template_endpoint(req: TemplateCreate):
    validate_template_steps(req.steps)
    template = Template(
        name=req.name,
        description=req.description,
        steps=req.steps,
    )
    await create_template(template)
    return template


@router.get("")
async def list_templates_endpoint():
    return await list_templates()


@router.get("/{template_id}")
async def get_template_endpoint(template_id: str):
    template = await get_template(template_id)
    if template is None:
        raise HTTPException(404, f"Template {template_id} not found")
    return template


@router.put("/{template_id}")
async def update_template_endpoint(template_id: str, req: TemplateCreate):
    validate_template_steps(req.steps)
    existing = await get_template(template_id)
    if existing is None:
        raise HTTPException(404, f"Template {template_id} not found")

    updated = Template(
        id=existing.id,
        name=req.name,
        description=req.description,
        steps=req.steps,
        created_at=existing.created_at,
        updated_at=datetime.now(UTC),
    )
    return await update_template(template_id, updated)


@router.delete("/{template_id}")
async def delete_template_endpoint(template_id: str):
    deleted = await delete_template(template_id)
    if not deleted:
        raise HTTPException(404, f"Template {template_id} not found")
    return {"deleted": template_id}


@router.post("/{template_id}/instantiate")
async def instantiate_template_endpoint(template_id: str, req: TemplateInstantiate):
    if req.template_id != template_id:
        raise HTTPException(422, "template_id in path and body must match")
    validate_vars(req.vars)
    return await instantiate(template_id, req.vars)
