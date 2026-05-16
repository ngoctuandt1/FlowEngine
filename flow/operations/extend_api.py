"""Reverse-API helpers for Flow video extend generation."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import re
import uuid
from typing import Any

try:  # pragma: no cover - exercised when image_api exists on sibling branches.
    from flow.operations.image_api import _mint_recaptcha_token
except ModuleNotFoundError:  # pragma: no cover - local fallback for master-only branch.
    _RECAPTCHA_JS = """async () => {
        const script = Array.from(document.scripts)
            .find(s => (s.src||'').includes('recaptcha') && (s.src||'').includes('render='));
        const fromScript = script ? new URL(script.src).searchParams.get('render') : null;
        const cfg = window.___grecaptcha_cfg || {};
        function findSiteKey(o, acc) {
            acc = acc || [];
            if (!o || typeof o !== 'object') return acc;
            for (const [k,v] of Object.entries(o)) {
                if ((k==='sitekey'||k==='siteKey') && typeof v==='string') acc.push(v);
                if (v && typeof v === 'object') findSiteKey(v, acc);
            }
            return acc;
        }
        const siteKey = fromScript || findSiteKey(cfg.clients||{})[0];
        const gr = window.grecaptcha && (window.grecaptcha.enterprise || window.grecaptcha);
        if (!siteKey || !gr || !gr.execute) return '';
        try { return await gr.execute(siteKey, {action: 'IMAGE_GENERATION'}); } catch(e) { return ''; }
    }"""

    async def _mint_recaptcha_token(page, *, caller: str = "batch_generate_images") -> str:
        try:
            minted = await page.evaluate(_RECAPTCHA_JS)
        except Exception as exc:
            logger.warning("%s: reCAPTCHA mint failed: %s", caller, exc)
            return ""
        return str(minted) if minted else ""


logger = logging.getLogger(__name__)

_EXTEND_GEN_URL_HINTS = (
    "batchasyncgeneratevideoextendvideo",
    "v1/video:batchasyncgenerate",
)
_EXTEND_HEADER_ALLOWLIST = frozenset(
    {"authorization", "content-type", "x-goog-api-key", "x-recaptcha-token"}
)
_PARENT_FIELD_CANDIDATES: tuple[tuple[str, ...], ...] = (
    ("videoExtendInput", "sourceMedia", "name"),
    ("videoExtendInput", "sourceMediaName"),
    ("videoExtendInput", "sourceMediaId"),
    ("videoExtendInput", "parentMediaId"),
    ("metadata", "sourceMediaId"),
    ("metadata", "parentMediaId"),
    ("sourceMedia", "name"),
    ("sourceMediaName",),
    ("sourceMediaId",),
    ("parentMediaId",),
)
_PROMPT_FIELD_CANDIDATES: tuple[tuple[str | int, ...], ...] = (
    ("textInput", "structuredPrompt", "parts", 0, "text"),
    ("structuredPrompt", "parts", 0, "text"),
    ("textInput", "text"),
    ("prompt",),
)
_MEDIA_ID_RE = re.compile(r"(?:^|/)media/([^/?#\s\"]+)", re.IGNORECASE)
_BARE_MEDIA_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def install_extend_request_capture(client) -> None:
    """Capture latest Flow batchAsyncGenerateVideoExtendVideo POST template."""
    page = getattr(client, "page", None)
    if page is None:
        client._extend_request_template = None
        return

    previous = getattr(client, "_extend_request_capture_listener", None)
    if previous is not None:
        _remove_page_listener(page, "request", previous)

    def _on_request(request) -> None:
        try:
            url = request.url or ""
            method = request.method or ""
        except Exception:
            return
        if method.upper() != "POST" or not _is_extend_generate_url(url):
            return
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        client._extend_request_template = {
            "url": url,
            "headers": headers,
            "post_data": post_data,
        }
        logger.info("Captured Flow extend reverseAPI template: %s", url)

    page.on("request", _on_request)
    client._extend_request_capture_listener = _on_request


def get_extend_request_template(client) -> dict | None:
    template = getattr(client, "_extend_request_template", None)
    return template if isinstance(template, dict) else None


async def replay_extend_via_api(client, parent_media_id: str, prompt: str) -> str:
    """Replay latest captured extend POST with new parent media + prompt."""
    template = get_extend_request_template(client)
    if template is None:
        raise RuntimeError("replay_extend_via_api: no captured template")

    page = _require_page(client)
    body = _parse_template_post_data(template)
    requests = body.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise RuntimeError("replay_extend_via_api: captured template has no requests[0]")
    body["requests"] = [deepcopy(requests[0])]
    request = body["requests"][0]

    parent_path = _set_parent_media_id(request, parent_media_id)
    prompt_path = _set_prompt_text(request, prompt)
    logger.info("replay_extend_via_api: rewrote parent media at %s", parent_path)
    logger.info("replay_extend_via_api: rewrote prompt at %s", prompt_path)

    media_context = body.get("mediaGenerationContext")
    if not isinstance(media_context, dict):
        media_context = {}
        body["mediaGenerationContext"] = media_context
    media_context["batchId"] = str(uuid.uuid4())

    recaptcha_token = await _mint_recaptcha_token(page, caller="replay_extend_via_api")
    if recaptcha_token:
        _set_replay_recaptcha_token(body, recaptcha_token)
    else:
        logger.warning(
            "replay_extend_via_api: reCAPTCHA mint returned empty token; "
            "using captured token if present"
        )

    headers = _replay_headers(template.get("headers") or {})
    if recaptcha_token:
        headers["x-recaptcha-token"] = recaptcha_token

    response = await page.context.request.post(
        template["url"],
        data=json.dumps(body),
        headers=headers,
        timeout=30000,
    )
    status = _response_status(response)
    if status < 200 or status >= 300:
        text = await _response_text(response)
        raise RuntimeError(
            f"replay_extend_via_api failed with HTTP {status}: {text[:500]}"
        )

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("replay_extend_via_api: response was not JSON") from exc

    media_ids = _extract_extend_media_names(data)
    if len(media_ids) != 1:
        raise RuntimeError(
            f"replay_extend_via_api: requested 1 media but got {len(media_ids)}"
        )
    return media_ids[0]


def clear_extend_capture(client) -> None:
    client._extend_request_template = None


def _is_extend_generate_url(url: str) -> bool:
    url_l = (url or "").lower()
    return all(hint in url_l for hint in _EXTEND_GEN_URL_HINTS)


def _parse_template_post_data(template: dict[str, Any]) -> dict[str, Any]:
    raw = template.get("post_data")
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("replay_extend_via_api: captured template has no JSON post_data")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("replay_extend_via_api: captured template JSON was not an object")
    return parsed


def _set_parent_media_id(request: dict[str, Any], parent_media_id: str) -> str:
    for path in _PARENT_FIELD_CANDIDATES:
        if _set_existing_path(request, path, parent_media_id):
            return _format_request_path(path)
    found = _set_first_parent_field(request, parent_media_id)
    if found:
        return found
    raise RuntimeError("replay_extend_via_api: parent media field not found in captured template")


def _set_prompt_text(request: dict[str, Any], prompt: str) -> str:
    for path in _PROMPT_FIELD_CANDIDATES:
        if _set_existing_path(request, path, prompt):
            return _format_request_path(path)

    text_input = request.get("textInput")
    if isinstance(text_input, dict):
        _ensure_structured_prompt_text(text_input, prompt)
        return "requests[0].textInput.structuredPrompt.parts[0].text"

    structured_prompt = request.get("structuredPrompt")
    if isinstance(structured_prompt, dict):
        _ensure_structured_prompt_text(request, prompt)
        return "requests[0].structuredPrompt.parts[0].text"

    raise RuntimeError("replay_extend_via_api: prompt field not found in captured template")


def _set_existing_path(target: dict[str, Any], path: tuple[str | int, ...], value: str) -> bool:
    current: Any = target
    for key in path[:-1]:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return False
            current = current[key]
        else:
            if not isinstance(current, dict) or key not in current:
                return False
            current = current[key]
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            return False
        current[last] = value
        return True
    if not isinstance(current, dict) or last not in current:
        return False
    current[last] = value
    return True


def _set_first_parent_field(target: Any, parent_media_id: str, path: str = "requests[0]") -> str | None:
    if isinstance(target, dict):
        source_media = target.get("sourceMedia")
        if isinstance(source_media, dict) and "name" in source_media:
            source_media["name"] = parent_media_id
            return f"{path}.sourceMedia.name"
        for key in ("sourceMediaId", "parentMediaId", "sourceMediaName"):
            if key in target:
                target[key] = parent_media_id
                return f"{path}.{key}"
        for key, value in target.items():
            found = _set_first_parent_field(value, parent_media_id, f"{path}.{key}")
            if found:
                return found
    elif isinstance(target, list):
        for index, item in enumerate(target):
            found = _set_first_parent_field(item, parent_media_id, f"{path}[{index}]")
            if found:
                return found
    return None


def _ensure_structured_prompt_text(target: dict[str, Any], prompt: str) -> None:
    structured_prompt = target.get("structuredPrompt")
    if not isinstance(structured_prompt, dict):
        structured_prompt = {}
        target["structuredPrompt"] = structured_prompt
    parts = structured_prompt.get("parts")
    if not isinstance(parts, list) or not parts:
        parts = [{}]
        structured_prompt["parts"] = parts
    if not isinstance(parts[0], dict):
        parts[0] = {}
    parts[0]["text"] = prompt


def _format_request_path(path: tuple[str | int, ...]) -> str:
    rendered = "requests[0]"
    for key in path:
        if isinstance(key, int):
            rendered += f"[{key}]"
        else:
            rendered += f".{key}"
    return rendered


def _replay_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {
        str(name).lower(): str(value)
        for name, value in headers.items()
        if str(name).lower() in _EXTEND_HEADER_ALLOWLIST
    }


def _set_replay_recaptcha_token(body: dict[str, Any], token: str) -> None:
    client_context = body.get("clientContext")
    if not isinstance(client_context, dict):
        client_context = {}
        body["clientContext"] = client_context
    _set_context_recaptcha_token(client_context, token)

    requests = body.get("requests")
    if not isinstance(requests, list):
        return
    for request in requests:
        if not isinstance(request, dict):
            continue
        request_recaptcha = request.get("recaptchaContext")
        if isinstance(request_recaptcha, dict):
            request_recaptcha["token"] = token
        request_context = request.get("clientContext")
        if isinstance(request_context, dict):
            _set_context_recaptcha_token(request_context, token)


def _set_context_recaptcha_token(context: dict[str, Any], token: str) -> None:
    recaptcha_context = context.get("recaptchaContext")
    if not isinstance(recaptcha_context, dict):
        recaptcha_context = {}
        context["recaptchaContext"] = recaptcha_context
    recaptcha_context["token"] = token


def _response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(response, "status_code", 0)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


async def _response_text(response: Any) -> str:
    try:
        return await response.text()
    except Exception:
        return ""


def _extract_extend_media_names(data: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(media_id: str | None) -> None:
        if media_id and media_id not in seen:
            seen.add(media_id)
            found.append(media_id)

    def walk(value: Any, key: str | None = None) -> None:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                walk(child_value, str(child_key))
            return
        if isinstance(value, list):
            for item in value:
                walk(item, key)
            return
        if not isinstance(value, str):
            return
        if key in {"mediaId", "media_id"}:
            add(value)
            return
        if key in {"name", "mediaName", "media_name"}:
            add(_media_id_from_name_or_bare(value))

    walk(data)
    return found


def _media_id_from_name(value: str) -> str | None:
    match = _MEDIA_ID_RE.search(value)
    return match.group(1) if match else None


def _looks_like_bare_media_id(value: str) -> bool:
    return bool(_BARE_MEDIA_ID_RE.match(value.strip()))


def _media_id_from_name_or_bare(value: str) -> str | None:
    media_id = _media_id_from_name(value)
    if media_id is not None:
        return media_id
    return value.strip() if _looks_like_bare_media_id(value) else None


def _remove_page_listener(page: Any, event_name: str, callback: Any) -> None:
    for method_name in ("remove_listener", "off"):
        method = getattr(page, method_name, None)
        if callable(method):
            try:
                method(event_name, callback)
                return
            except Exception:
                return
    listeners = getattr(page, "listeners", None)
    if isinstance(listeners, dict):
        callbacks = listeners.get(event_name)
        if isinstance(callbacks, list):
            listeners[event_name] = [item for item in callbacks if item is not callback]


def _require_page(client):
    page = getattr(client, "page", None)
    if page is None:
        raise RuntimeError("Flow client page is not initialized")
    return page
