"""JSON prompt builder endpoint."""

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


router = APIRouter(prefix="/api/prompt-builder", tags=["prompt"])

FIELD_LIMIT = 200
PROMPT_LIMIT = 1500
FIELD_ORDER = (
    "subject",
    "action",
    "environment",
    "mood",
    "camera",
    "lighting",
    "lens",
    "style",
    "motion",
    "audio",
    "negative",
)


class PromptSpec(BaseModel):
    subject: str
    action: Optional[str] = None
    environment: Optional[str] = None
    mood: Optional[str] = None
    camera: Optional[str] = None
    lighting: Optional[str] = None
    lens: Optional[str] = None
    style: Optional[str] = None
    motion: Optional[str] = None
    audio: Optional[str] = None
    negative: Optional[str] = None


def _normalize(value: Optional[str]) -> Optional[str]:
    """Trim whitespace and collapse empty strings to None."""
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _validate_lengths(spec: PromptSpec) -> dict[str, Optional[str]]:
    """Normalize values and enforce per-field length limits."""
    normalized: dict[str, Optional[str]] = {}
    for field_name in FIELD_ORDER:
        value = _normalize(getattr(spec, field_name))
        if value is not None and len(value) > FIELD_LIMIT:
            raise HTTPException(400, f"Field '{field_name}' exceeds {FIELD_LIMIT} characters")
        normalized[field_name] = value
    return normalized


def _assemble_prompt(parts: dict[str, Optional[str]]) -> str:
    """Build a deterministic Veo-ready prompt from normalized parts."""
    prompt_parts: list[str] = []

    for field_name in ("subject", "action", "environment", "mood"):
        value = parts[field_name]
        if value:
            prompt_parts.append(value)

    camera = parts["camera"]
    lens = parts["lens"]
    if camera and lens:
        prompt_parts.append(f"{camera}, {lens}")
    elif camera:
        prompt_parts.append(camera)
    elif lens:
        prompt_parts.append(lens)

    for field_name in ("lighting", "motion", "audio", "style"):
        value = parts[field_name]
        if value:
            prompt_parts.append(value)

    prompt = ", ".join(prompt_parts)
    negative = parts["negative"]
    if negative:
        prompt = f"{prompt} Avoid: {negative}."
    return prompt


@router.post("/assemble")
async def assemble_prompt(spec: PromptSpec):
    """Assemble a deterministic prompt from a JSON spec."""
    normalized = _validate_lengths(spec)
    prompt = _assemble_prompt(normalized)
    if len(prompt) > PROMPT_LIMIT:
        raise HTTPException(400, f"Prompt exceeds {PROMPT_LIMIT} characters")
    return {"prompt": prompt, "length": len(prompt)}
