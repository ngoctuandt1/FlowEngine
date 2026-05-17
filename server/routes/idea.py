"""Gemini-backed idea generation endpoint for the IdeaStudio panel."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.db.settings_store import get_ai_settings
from server.models.idea import IdeaGenerateRequest, IdeaGenerateResponse
from server.services import gemini_client


router = APIRouter(prefix="/api/idea", tags=["idea"])

DEFAULT_GEMINI_MODEL = "gemini-2-flash-preview"
MISSING_API_KEY_ERROR = "Gemini API key not configured"
SYSTEM_PROMPT = """You are an expert short-form video workflow planner for IdeaStudio.

Return JSON only. Do not wrap the result in markdown fences or extra prose.

The JSON must match this schema exactly:
{
  "script": "## Kịch bản đề xuất\\n\\n**Phân cảnh 1 (5 giây):** ...\\n\\n## Pipeline\\n\\n1. ...",
  "nodes": [
    {"type": "text-to-image", "prompt": "young woman in business suit, modern office, 9:16", "ratio": "9:16", "parent_index": null},
    {"type": "frames-to-video", "prompt": "woman walks confidently towards camera", "ratio": "9:16", "parent_index": 0}
  ]
}

Rules:
- `script` must be markdown.
- `nodes` must be ordered in the recommended canvas execution order.
- `parent_index` is zero-based and points at an earlier node, or null for a root node.
- Use only these node types when relevant: text-to-image, frames-to-video, ingredients-to-video, text-to-video, extend-video, insert-object, remove-object, camera-move.
- Prefer `9:16` unless the brief clearly asks for a different ratio.
- Base the plan on the attached reference images when they are provided.
"""


def _normalize_optional_str(value: Any) -> str | None:
    if value is None:
        return None
    nested_value = getattr(value, "value", None)
    if nested_value is not None and nested_value is not value:
        return _normalize_optional_str(nested_value)
    text = str(value).strip()
    return text or None


async def _load_gemini_config() -> tuple[str | None, str]:
    """Resolve the Gemini API key + model.

    Priority is the persisted settings row (managed via the Settings UI →
    ``server.db.settings_store.get_ai_settings``); env vars are the
    fallback so a clean install still works without DB writes.
    """
    api_key: str | None = None
    model: str | None = None
    try:
        stored = await get_ai_settings()
    except Exception:  # noqa: BLE001 - DB read failures must not 500 the route
        stored = None
    if stored is not None:
        api_key = _normalize_optional_str(stored.gemini_api_key)
        model = _normalize_optional_str(stored.gemini_model)

    if api_key is None:
        api_key = _normalize_optional_str(os.getenv("GEMINI_API_KEY"))
    if model is None:
        model = _normalize_optional_str(os.getenv("GEMINI_MODEL")) or DEFAULT_GEMINI_MODEL

    return api_key, model


def _build_user_prompt(body: IdeaGenerateRequest) -> str:
    sections = [f"User brief:\n{body.prompt}"]
    if body.chain_id:
        sections.append(f"Existing chain_id: {body.chain_id}")
    if body.ref_image_urls:
        sections.append(
            f"Reference images are attached separately: {len(body.ref_image_urls)} item(s)."
        )
    return "\n\n".join(sections)


async def _download_reference_images(
    ref_image_urls: list[str] | None,
) -> list[gemini_client.GeminiImage]:
    if not ref_image_urls:
        return []

    images: list[gemini_client.GeminiImage] = []
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        for url in ref_image_urls:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            mime_type = content_type.split(";", 1)[0].strip().lower()
            if mime_type and not mime_type.startswith("image/"):
                raise RuntimeError(f"Reference image URL did not return an image: {url}")
            if not response.content:
                raise RuntimeError(f"Reference image URL returned empty content: {url}")
            images.append(gemini_client.GeminiImage(data=response.content))
    return images


def _extract_json_object(text: str) -> str:
    start = text.find("{")
    if start == -1:
        raise ValueError("Gemini returned invalid JSON payload")

    depth = 0
    in_string = False
    escaped = False

    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("Gemini returned invalid JSON payload")


def _parse_generation_response(raw_text: str) -> IdeaGenerateResponse:
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        payload = json.loads(_extract_json_object(raw_text))

    try:
        return IdeaGenerateResponse.model_validate(payload)
    except Exception as exc:  # pydantic ValidationError with no hard dependency here
        raise ValueError("Gemini returned an invalid idea payload") from exc


def _error_response(status_code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": message})


@router.post("/generate", response_model=IdeaGenerateResponse)
async def generate_idea(body: IdeaGenerateRequest) -> IdeaGenerateResponse | JSONResponse:
    api_key, model = await _load_gemini_config()
    if not api_key:
        return _error_response(503, MISSING_API_KEY_ERROR)

    try:
        images = await _download_reference_images(body.ref_image_urls)
        raw_text = await gemini_client.generate(
            api_key=api_key,
            model=model,
            system_instruction=SYSTEM_PROMPT,
            prompt=_build_user_prompt(body),
            images=images,
        )
        return _parse_generation_response(raw_text)
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        return _error_response(502, str(exc) or "Gemini request failed")
