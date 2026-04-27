"""Template CRUD + instantiation helpers."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any

from fastapi import HTTPException

from server.db.database import get_db
from server.models.job import ChainCreate, JobCreate
from server.models.template import Template


VAR_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
PLACEHOLDER_RE = re.compile(r"\{\{([^{}]+)\}\}")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _row_to_template(row) -> Template:
    return Template(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        steps=json.loads(row["steps_json"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _scan_value(value: Any, found: set[str]) -> None:
    if isinstance(value, str):
        for match in PLACEHOLDER_RE.finditer(value):
            var_name = match.group(1)
            if not VAR_NAME_RE.fullmatch(var_name):
                raise HTTPException(422, f"Invalid template variable name: {var_name}")
            found.add(var_name)
        return

    if isinstance(value, list):
        for item in value:
            _scan_value(item, found)
        return

    if isinstance(value, dict):
        for item in value.values():
            _scan_value(item, found)


def extract_placeholders(steps: list[dict[str, Any]]) -> set[str]:
    """Collect all placeholder variable names used by a template."""
    found: set[str] = set()
    _scan_value(steps, found)
    return found


def validate_template_steps(steps: list[dict[str, Any]]) -> set[str]:
    """Validate placeholder syntax used in template steps."""
    return extract_placeholders(steps)


def validate_vars(vars: dict[str, str]) -> None:
    """Validate provided variable names."""
    for name in vars:
        if not VAR_NAME_RE.fullmatch(name):
            raise HTTPException(422, f"Invalid variable name: {name}")


def _substitute_value(value: Any, vars: dict[str, str], missing: set[str]) -> Any:
    if isinstance(value, str):
        def repl(match: re.Match[str]) -> str:
            var_name = match.group(1)
            if not VAR_NAME_RE.fullmatch(var_name):
                raise HTTPException(422, f"Invalid template variable name: {var_name}")
            if var_name not in vars:
                missing.add(var_name)
                return match.group(0)
            return vars[var_name]

        return PLACEHOLDER_RE.sub(repl, value)

    if isinstance(value, list):
        return [_substitute_value(item, vars, missing) for item in value]

    if isinstance(value, dict):
        return {
            key: _substitute_value(item, vars, missing)
            for key, item in value.items()
        }

    return value


async def create_template(template: Template) -> Template:
    """Insert a new template row and return it."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO templates (id, name, description, steps_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                template.id,
                template.name,
                template.description,
                json.dumps(template.steps),
                template.created_at.isoformat(),
                template.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return template


async def list_templates() -> list[Template]:
    """Return all templates ordered newest first."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM templates ORDER BY created_at DESC"
        )
        rows = await cursor.fetchall()
    return [_row_to_template(row) for row in rows]


async def get_template(template_id: str) -> Template | None:
    """Fetch one template by id."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM templates WHERE id = ?",
            (template_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _row_to_template(row)


async def update_template(template_id: str, template: Template) -> Template | None:
    """Replace an existing template."""
    async with get_db() as db:
        cursor = await db.execute(
            """
            UPDATE templates
            SET name = ?, description = ?, steps_json = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                template.name,
                template.description,
                json.dumps(template.steps),
                template.updated_at.isoformat(),
                template_id,
            ),
        )
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_template(template_id)


async def delete_template(template_id: str) -> bool:
    """Delete a template by id."""
    async with get_db() as db:
        cursor = await db.execute(
            "DELETE FROM templates WHERE id = ?",
            (template_id,),
        )
        await db.commit()
    return cursor.rowcount > 0


async def instantiate(template_id: str, vars: dict[str, str]) -> Any:
    """Instantiate a template and forward to the existing chain creation path."""
    validate_vars(vars)
    template = await get_template(template_id)
    if template is None:
        raise HTTPException(404, f"Template {template_id} not found")

    missing: set[str] = set()
    resolved_steps = _substitute_value(template.steps, vars, missing)
    if missing:
        missing_names = ", ".join(sorted(missing))
        raise HTTPException(422, f"Missing template variables: {missing_names}")

    chain_request = ChainCreate(
        jobs=[JobCreate.model_validate(step) for step in resolved_steps],
    )

    from server.routes.jobs import create_chain_endpoint

    return await create_chain_endpoint(chain_request)
