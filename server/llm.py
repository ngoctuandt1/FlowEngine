"""Thin Anthropic async client wrapper."""

from __future__ import annotations

import os
from typing import Any


DEFAULT_MODEL = "claude-3-5-sonnet-20241022"
_cached_client: Any | None = None
_cached_api_key: str | None = None


def _get_client() -> Any:
    """Return a cached AsyncAnthropic client for the current API key."""
    global _cached_client, _cached_api_key

    api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    if _cached_client is not None and _cached_api_key == api_key:
        return _cached_client

    try:
        from anthropic import AsyncAnthropic
    except ImportError as exc:
        raise RuntimeError("anthropic package is not installed") from exc

    _cached_client = AsyncAnthropic(api_key=api_key)
    _cached_api_key = api_key
    return _cached_client


async def call_claude(system: str, user: str, max_tokens: int = 1024) -> str:
    """Send a single-turn prompt to Claude and return the text response."""
    client = _get_client()
    response = await client.messages.create(
        model=os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )

    parts: list[str] = []
    for block in response.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)

    result = "".join(parts).strip()
    if not result:
        raise RuntimeError("Claude returned no text content")
    return result
