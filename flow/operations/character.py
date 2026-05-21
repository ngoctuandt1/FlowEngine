"""Character-create Flow operation."""

from __future__ import annotations

from typing import Any, Iterable

from flow.characters import (
    DEFAULT_CHARACTER_MODEL,
    create_character_via_ui,
    generate_character_prompt,
    resolve_character_tags,
    validate_character_tags,
)


async def run_character_create(
    client: Any,
    *,
    project_id: str,
    prompt: str,
    known_characters: Iterable[Any] | None = None,
    model: str = DEFAULT_CHARACTER_MODEL,
    locale: str = "",
    timeout_sec: float = 15.0,
) -> dict[str, Any]:
    """Create a Flow character through captured UI path only."""

    return await create_character_via_ui(
        client,
        project_id=project_id,
        prompt=prompt,
        known_characters=known_characters,
        model=model,
        locale=locale,
        timeout_sec=timeout_sec,
    )


async def character_create(
    client: Any,
    job: dict[str, Any],
    known_characters: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Dispatcher-friendly wrapper for character-create job payloads."""

    project_id = str(job.get("project_id") or "").strip()
    if not project_id:
        raise ValueError("character-create requires project_id")
    prompt = str(job.get("prompt") or job.get("description") or "").strip()
    if not prompt:
        raise ValueError("character-create requires prompt")
    return await run_character_create(
        client,
        project_id=project_id,
        prompt=prompt,
        known_characters=known_characters,
        model=str(job.get("model") or DEFAULT_CHARACTER_MODEL),
        locale=str(job.get("locale") or ""),
    )


__all__ = [
    "character_create",
    "generate_character_prompt",
    "resolve_character_tags",
    "run_character_create",
    "validate_character_tags",
]
