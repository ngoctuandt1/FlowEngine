"""Gemini-backed idea generation endpoint for the IdeaStudio panel."""

from __future__ import annotations

import inspect
import ipaddress
import json
import os
import socket
from importlib import import_module
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from server.models.idea import IdeaGenerateRequest, IdeaGenerateResponse
from server.services import gemini_client


router = APIRouter(prefix="/api/idea", tags=["idea"])

DEFAULT_GEMINI_MODEL = "gemini-2-flash-preview"
MISSING_API_KEY_ERROR = "Gemini API key not configured"

# SSRF guards for fetching attacker-supplied reference image URLs.
REF_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB cap per image
REF_IMAGE_TIMEOUT_SECONDS = 30.0
_FORBIDDEN_HOSTNAMES = {"localhost", "internal"}
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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def _load_setting_from_store(key: str) -> str | None:
    try:
        settings_store = import_module("server.db.settings_store")
    except ImportError:
        return None

    for getter_name in ("get_app_setting", "get_setting"):
        getter = getattr(settings_store, getter_name, None)
        if getter is None:
            continue
        return _normalize_optional_str(await _maybe_await(getter(key)))

    getter = getattr(settings_store, "get_settings", None)
    if getter is None:
        return None

    settings = await _maybe_await(getter())
    if isinstance(settings, dict):
        return _normalize_optional_str(settings.get(key))
    return _normalize_optional_str(getattr(settings, key, None))


async def _load_gemini_config() -> tuple[str | None, str]:
    api_key = await _load_setting_from_store("gemini_api_key")
    model = await _load_setting_from_store("gemini_model")

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


def _ip_is_forbidden(ip_text: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        # Unknown address family → reject defensively.
        return True
    # `is_global` is the tightest allow-list: it rejects every special-use
    # range (private RFC1918, loopback, link-local, CGNAT 100.64/10,
    # documentation 192.0.2/24, benchmarking 198.18/15, multicast,
    # unspecified, reserved). The bare `is_*` predicates miss CGNAT.
    if not ip.is_global:
        return True
    if ip.is_multicast:
        return True
    return False


def _extract_peer_ip(response: object) -> str | None:
    """Best-effort extraction of the live socket peer IP from an httpx Response.

    Returns None when the attribute path is unavailable (e.g. mocked tests) so
    callers can degrade to the pre-flight DNS validation alone.
    """
    network_stream = None
    try:
        network_stream = response.extensions.get("network_stream")  # type: ignore[attr-defined]
    except Exception:
        network_stream = None
    if network_stream is None:
        return None
    try:
        addr = network_stream.get_extra_info("server_addr")
    except Exception:
        return None
    if not addr:
        return None
    candidate = addr[0] if isinstance(addr, (tuple, list)) else addr
    if not isinstance(candidate, str):
        return None
    return candidate.split("%", 1)[0]


def _validate_public_url(url: str) -> None:
    """Reject URLs that would let an attacker reach internal services (SSRF).

    Resolves the hostname and confirms EVERY A/AAAA record is public. Raises
    ``RuntimeError`` for any disallowed scheme, host, or resolved address.
    """

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RuntimeError(f"Reference image URL must use http(s): {url}")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise RuntimeError(f"Reference image URL is missing a host: {url}")
    if host in _FORBIDDEN_HOSTNAMES or host.endswith(".internal") or host.endswith(".local"):
        raise RuntimeError(f"Reference image URL host is not allowed: {url}")

    try:
        parsed_ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            addrinfos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise RuntimeError(f"Reference image URL host could not be resolved: {url}") from exc
        for family, *_rest, sockaddr in addrinfos:
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            candidate_ip = sockaddr[0] if sockaddr else None
            if not isinstance(candidate_ip, str):
                continue
            # Strip IPv6 zone if present (fe80::1%eth0).
            candidate_ip = candidate_ip.split("%", 1)[0]
            if _ip_is_forbidden(candidate_ip):
                raise RuntimeError(f"Reference image URL host is not allowed: {url}")
    else:
        if _ip_is_forbidden(str(parsed_ip)):
            raise RuntimeError(f"Reference image URL host is not allowed: {url}")


async def _fetch_capped(client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
    """Stream a response with a hard size cap to defeat memory-DoS payloads."""

    async with client.stream("GET", url) as response:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        # Reject upfront when Content-Length declares an over-cap payload —
        # cheaper than streaming the bytes just to discard them.
        declared = response.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > REF_IMAGE_MAX_BYTES:
            raise RuntimeError(
                f"Reference image exceeds {REF_IMAGE_MAX_BYTES} byte limit: {url}"
            )
        # Validate the peer's resolved IP a second time — a DNS-rebind that
        # flipped public → private between getaddrinfo and connect would land
        # here. httpx exposes the live socket via the underlying network
        # stream; we read it defensively because the attribute path differs
        # across httpx versions.
        peer_ip = _extract_peer_ip(response)
        if peer_ip and _ip_is_forbidden(peer_ip):
            raise RuntimeError(f"Reference image URL host is not allowed: {url}")
        buffer = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(buffer) + len(chunk) > REF_IMAGE_MAX_BYTES:
                raise RuntimeError(
                    f"Reference image exceeds {REF_IMAGE_MAX_BYTES} byte limit: {url}"
                )
            buffer.extend(chunk)
        return bytes(buffer), content_type


async def _download_reference_images(
    ref_image_urls: list[str] | None,
) -> list[gemini_client.GeminiImage]:
    if not ref_image_urls:
        return []

    for url in ref_image_urls:
        _validate_public_url(url)

    images: list[gemini_client.GeminiImage] = []
    # follow_redirects=False so a 30x to http://169.254.169.254/ cannot bypass
    # the pre-flight host validation above.
    async with httpx.AsyncClient(
        timeout=REF_IMAGE_TIMEOUT_SECONDS, follow_redirects=False
    ) as client:
        for url in ref_image_urls:
            content, content_type = await _fetch_capped(client, url)
            mime_type = content_type.split(";", 1)[0].strip().lower()
            if mime_type and not mime_type.startswith("image/"):
                raise RuntimeError(f"Reference image URL did not return an image: {url}")
            if not content:
                raise RuntimeError(f"Reference image URL returned empty content: {url}")
            images.append(gemini_client.GeminiImage(data=content))
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
