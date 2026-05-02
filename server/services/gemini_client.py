"""Thin async wrapper around the legacy google-generativeai SDK."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from io import BytesIO
from typing import Iterable

from PIL import Image, UnidentifiedImageError

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover - dependency may be absent in some test envs
    genai = None


@dataclass(slots=True)
class GeminiImage:
    data: bytes


def _decode_image(data: bytes) -> Image.Image:
    try:
        with Image.open(BytesIO(data)) as image:
            image.load()
            return image.copy()
    except UnidentifiedImageError as exc:
        raise RuntimeError("Reference image payload is not a supported image") from exc


def _extract_text(response: object) -> str:
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()

    for candidate in getattr(response, "candidates", []) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", []) or []:
            part_text = getattr(part, "text", None)
            if isinstance(part_text, str) and part_text.strip():
                return part_text.strip()

    raise RuntimeError("Gemini returned no text content")


def _generate_sync(
    *,
    api_key: str,
    model: str,
    system_instruction: str,
    prompt: str,
    images: Iterable[GeminiImage],
) -> str:
    if genai is None:
        raise RuntimeError("google-generativeai dependency is not installed")

    genai.configure(api_key=api_key)
    generation_config = genai.GenerationConfig(response_mime_type="application/json")
    client = genai.GenerativeModel(
        model_name=model,
        system_instruction=system_instruction,
        generation_config=generation_config,
    )

    contents: list[object] = [prompt]
    contents.extend(_decode_image(image.data) for image in images)
    response = client.generate_content(contents)
    return _extract_text(response)


async def generate(
    *,
    api_key: str,
    model: str,
    system_instruction: str,
    prompt: str,
    images: list[GeminiImage] | None = None,
) -> str:
    """Run a single Gemini generation request and return the text payload."""
    return await asyncio.to_thread(
        _generate_sync,
        api_key=api_key,
        model=model,
        system_instruction=system_instruction,
        prompt=prompt,
        images=images or [],
    )
