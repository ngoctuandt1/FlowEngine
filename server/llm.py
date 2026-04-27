"""Thin async wrapper around the configured OpenAI Responses-compatible LLM."""

from __future__ import annotations

import os
from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency in some environments
    httpx = None


DEFAULT_BASE_URL = "http://192.168.86.42:20128/v1"
DEFAULT_MODEL = "cx/gpt-5.4"
LLM_AVAILABLE = httpx is not None


def _get_settings() -> tuple[str, str, str]:
    """Return base URL, API key, and model for the current request."""
    base_url = (os.getenv("LLM_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("NINEROUTER_API_KEY")
        or "dummy"
    ).strip() or "dummy"
    model = (os.getenv("LLM_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    return base_url, api_key, model


def _extract_text(payload: dict[str, Any]) -> str:
    """Extract the first text segment from an OpenAI Responses payload."""
    output = payload.get("output")
    if not isinstance(output, list):
        raise RuntimeError("LLM returned no output list")

    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()

    raise RuntimeError("LLM returned no text content")


async def call_llm(system: str, user: str, max_tokens: int = 1024) -> str:
    """Send a single-turn prompt and return the first text response."""
    if not LLM_AVAILABLE:
        raise RuntimeError("httpx package is not installed")

    base_url, api_key, model = _get_settings()
    # 9router's responses endpoint rejects max_output_tokens (verified
    # 2026-04-28 — `[400]: Unsupported parameter: max_output_tokens`).
    # Omit it; rely on the upstream provider's default cap. If the
    # operator points LLM_BASE_URL at a real OpenAI Responses API, the
    # default still works. To re-enable, set LLM_SEND_MAX_TOKENS=1.
    body: dict[str, Any] = {
        "model": model,
        "input": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if os.getenv("LLM_SEND_MAX_TOKENS") == "1":
        body["max_output_tokens"] = max_tokens

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"{base_url}/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=body,
        )
        response.raise_for_status()
        return _extract_text(response.json())


async def call_claude(system: str, user: str, max_tokens: int = 1024) -> str:
    """Backward-compatible alias for routes still importing `call_claude`."""
    return await call_llm(system=system, user=user, max_tokens=max_tokens)
