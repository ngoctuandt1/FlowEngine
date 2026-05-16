"""Shared reverse-API helpers for Flow L2+ operations."""

from __future__ import annotations

from copy import deepcopy
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
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

PathCandidate = tuple[str | int, ...]
ReplayMutator = Callable[[dict[str, Any], dict[str, Any]], dict[str, str]]
RecaptchaMint = Callable[..., Awaitable[str]]

_HEADER_ALLOWLIST = frozenset(
    {"authorization", "content-type", "x-goog-api-key", "x-recaptcha-token"}
)
_MEDIA_ID_RE = re.compile(r"(?:^|/)media/([^/?#\s\"]+)", re.IGNORECASE)
_BARE_MEDIA_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_EMBEDDED_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)
_PARENT_KEY_PATTERN = re.compile(r"parent|source|media|name|reference", re.IGNORECASE)
_BBOX_KEY_SETS = (
    ("x", "y", "w", "h"),
    ("x", "y", "width", "height"),
    ("left", "top", "right", "bottom"),
    ("xmin", "ymin", "xmax", "ymax"),
    ("xMin", "yMin", "xMax", "yMax"),
    ("normalizedX", "normalizedY", "normalizedWidth", "normalizedHeight"),
)


def install_l2_request_capture(
    client,
    *,
    template_attr: str,
    listener_attr: str,
    url_hints: tuple[str, ...],
    operation_label: str,
) -> None:
    """Capture latest matching Flow L2 batchAsyncGenerate POST template."""
    page = getattr(client, "page", None)
    if page is None:
        setattr(client, template_attr, None)
        return

    previous = getattr(client, listener_attr, None)
    if previous is not None:
        _remove_page_listener(page, "request", previous)

    def _on_request(request) -> None:
        try:
            url = request.url or ""
            method = request.method or ""
        except Exception:
            return
        if method.upper() != "POST" or not is_l2_generate_url(url, url_hints):
            return
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        anchored_parent = anchored_parent_from_post_data(post_data)
        setattr(
            client,
            template_attr,
            {
                "url": url,
                "headers": headers,
                "post_data": post_data,
                "anchored_parent": anchored_parent,
            },
        )
        logger.info(
            "Captured Flow %s reverseAPI template: %s (anchored_parent=%s)",
            operation_label,
            url,
            anchored_parent or "<none>",
        )

    page.on("request", _on_request)
    setattr(client, listener_attr, _on_request)


def get_l2_request_template(client, template_attr: str) -> dict | None:
    template = getattr(client, template_attr, None)
    return template if isinstance(template, dict) else None


def clear_l2_capture(client, template_attr: str) -> None:
    setattr(client, template_attr, None)


def is_l2_generate_url(url: str, url_hints: tuple[str, ...]) -> bool:
    url_l = (url or "").lower()
    if "batchasyncgenerate" not in url_l or "batchcheckasync" in url_l:
        return False
    return any(hint.lower() in url_l for hint in url_hints)


async def replay_l2_via_api(
    client,
    *,
    template: dict | None,
    parent_media_id: str,
    parent_field_candidates: tuple[PathCandidate, ...],
    apply_operation_fields: ReplayMutator,
    caller: str,
    mint_recaptcha_token: RecaptchaMint = _mint_recaptcha_token,
) -> str:
    """Replay latest captured L2 POST with new parent and op-specific fields."""
    if template is None:
        raise RuntimeError(f"{caller}: no captured template")

    page = require_page(client)
    body = parse_template_post_data(template, caller=caller)
    request = first_request_only(body, caller=caller)

    parent_path = rewrite_parent_media_id(
        request=request,
        body=body,
        anchored_parent=template.get("anchored_parent"),
        new_parent_media_id=parent_media_id,
        field_candidates=parent_field_candidates,
        caller=caller,
    )
    changed_paths = apply_operation_fields(request, body)
    logger.info("%s: rewrote parent media at %s", caller, parent_path)
    for label, path in changed_paths.items():
        logger.info("%s: rewrote %s at %s", caller, label, path)

    media_context = body.get("mediaGenerationContext")
    if not isinstance(media_context, dict):
        media_context = {}
        body["mediaGenerationContext"] = media_context
    media_context["batchId"] = str(uuid.uuid4())

    recaptcha_token = await mint_recaptcha_token(page, caller=caller)
    if recaptcha_token:
        set_replay_recaptcha_token(body, recaptcha_token)
    else:
        logger.warning(
            "%s: reCAPTCHA mint returned empty token; using captured token if present",
            caller,
        )

    headers = replay_headers(template.get("headers") or {})
    if recaptcha_token:
        headers["x-recaptcha-token"] = recaptcha_token

    response = await page.context.request.post(
        template["url"],
        data=json.dumps(body),
        headers=headers,
        timeout=30000,
    )
    status = response_status(response)
    if status < 200 or status >= 300:
        text = await response_text(response)
        raise RuntimeError(f"{caller} failed with HTTP {status}: {text[:500]}")

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError(f"{caller}: response was not JSON") from exc

    media_ids = extract_media_names(data)
    if len(media_ids) != 1:
        raise RuntimeError(f"{caller}: requested 1 media but got {len(media_ids)}")
    return media_ids[0]


def parse_template_post_data(template: dict[str, Any], *, caller: str) -> dict[str, Any]:
    raw = template.get("post_data")
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError(f"{caller}: captured template has no JSON post_data")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{caller}: captured template JSON was not an object")
    return parsed


def first_request_only(body: dict[str, Any], *, caller: str) -> dict[str, Any]:
    requests = body.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise RuntimeError(f"{caller}: captured template has no requests[0]")
    body["requests"] = [deepcopy(requests[0])]
    return body["requests"][0]


def rewrite_parent_media_id(
    *,
    request: dict[str, Any],
    body: dict[str, Any],
    anchored_parent: str | None,
    new_parent_media_id: str,
    field_candidates: tuple[PathCandidate, ...],
    caller: str,
) -> str:
    try:
        return set_parent_media_id(request, new_parent_media_id, field_candidates)
    except RuntimeError as exc:
        path_error = exc

    if not anchored_parent:
        raise RuntimeError(
            f"{caller}: parent media field not found in captured template and "
            "no anchored parent UUID was captured "
            f"(path-candidate error: {path_error})"
        )

    old_uuid_bare = strip_media_prefix(anchored_parent)
    if not old_uuid_bare:
        raise RuntimeError(f"{caller}: anchored parent UUID was empty")

    new_uuid_bare = strip_media_prefix(new_parent_media_id)
    replaced_paths = replace_uuid_in_body(body, old_uuid_bare, new_uuid_bare)
    if not replaced_paths:
        raise RuntimeError(
            f"{caller}: anchored parent UUID {old_uuid_bare!r} not found in captured template body"
        )

    logger.info(
        "%s: walk-by-value replaced parent UUID at %d location(s): %s",
        caller,
        len(replaced_paths),
        replaced_paths,
    )
    if len(replaced_paths) == 1:
        return replaced_paths[0]
    return f"[{len(replaced_paths)} locations]: {', '.join(replaced_paths)}"


def set_parent_media_id(
    request: dict[str, Any],
    parent_media_id: str,
    field_candidates: tuple[PathCandidate, ...],
) -> str:
    for path in field_candidates:
        if set_existing_media_path(request, path, parent_media_id):
            return format_request_path(path)
    found = set_first_parent_field(request, parent_media_id)
    if found:
        return found
    raise RuntimeError("parent media field not found in captured template")


def set_string_field(
    target: dict[str, Any],
    value: str,
    *,
    field_candidates: tuple[PathCandidate, ...],
    fallback_keys: tuple[str, ...],
    caller: str,
    field_label: str,
) -> str:
    for path in field_candidates:
        if set_existing_path(target, path, value):
            return format_request_path(path)

    found = set_first_string_field(target, value, fallback_keys)
    if found:
        return found
    raise RuntimeError(f"{caller}: {field_label} field not found in captured template")


def set_bbox_field(
    target: dict[str, Any],
    bbox: dict[str, Any],
    *,
    field_candidates: tuple[PathCandidate, ...],
    caller: str,
) -> str:
    normalized = normalize_bbox(bbox, caller=caller)
    for path in field_candidates:
        current = get_existing_path(target, path)
        if isinstance(current, dict):
            write_bbox_dict(current, normalized)
            return format_request_path(path)
        if isinstance(current, list) and len(current) >= 4:
            current[:4] = [normalized["x"], normalized["y"], normalized["w"], normalized["h"]]
            return format_request_path(path)

    found = set_first_bbox_dict(target, normalized)
    if found:
        return found
    raise RuntimeError(f"{caller}: bbox field not found in captured template")


def set_existing_path(target: dict[str, Any], path: PathCandidate, value: Any) -> bool:
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


def set_existing_media_path(
    target: dict[str, Any], path: PathCandidate, parent_media_id: str
) -> bool:
    current = get_existing_path(target, path)
    if current is _MISSING:
        return False
    value = replace_media_leaf_value(current, parent_media_id)
    return set_existing_path(target, path, value)


def get_existing_path(target: dict[str, Any], path: PathCandidate) -> Any:
    current: Any = target
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return _MISSING
            current = current[key]
        else:
            if not isinstance(current, dict) or key not in current:
                return _MISSING
            current = current[key]
    return current


class _Missing:
    pass


_MISSING = _Missing()


def replace_media_leaf_value(current: Any, parent_media_id: str) -> Any:
    if not isinstance(current, str):
        return parent_media_id
    match = _EMBEDDED_UUID_RE.search(current)
    if not match:
        return parent_media_id
    return current.replace(match.group(0), strip_media_prefix(parent_media_id))


def set_first_parent_field(target: Any, parent_media_id: str, path: str = "requests[0]") -> str | None:
    if isinstance(target, dict):
        source_media = target.get("sourceMedia")
        if isinstance(source_media, dict) and "name" in source_media:
            source_media["name"] = replace_media_leaf_value(
                source_media["name"], parent_media_id
            )
            return f"{path}.sourceMedia.name"
        for key in ("sourceMediaId", "parentMediaId", "sourceMediaName"):
            if key in target:
                target[key] = replace_media_leaf_value(target[key], parent_media_id)
                return f"{path}.{key}"
        for key, value in target.items():
            found = set_first_parent_field(value, parent_media_id, f"{path}.{key}")
            if found:
                return found
    elif isinstance(target, list):
        for index, item in enumerate(target):
            found = set_first_parent_field(item, parent_media_id, f"{path}[{index}]")
            if found:
                return found
    return None


def set_first_string_field(
    target: Any, value: str, fallback_keys: tuple[str, ...], path: str = "requests[0]"
) -> str | None:
    if isinstance(target, dict):
        for key in fallback_keys:
            if key in target and isinstance(target[key], str):
                target[key] = value
                return f"{path}.{key}"
        for key, child in target.items():
            found = set_first_string_field(child, value, fallback_keys, f"{path}.{key}")
            if found:
                return found
    elif isinstance(target, list):
        for index, item in enumerate(target):
            found = set_first_string_field(item, value, fallback_keys, f"{path}[{index}]")
            if found:
                return found
    return None


def format_request_path(path: PathCandidate) -> str:
    rendered = "requests[0]"
    for key in path:
        if isinstance(key, int):
            rendered += f"[{key}]"
        else:
            rendered += f".{key}"
    return rendered


def normalize_bbox(bbox: dict[str, Any], *, caller: str) -> dict[str, float]:
    x = bbox.get("x", bbox.get("left"))
    y = bbox.get("y", bbox.get("top"))
    w = bbox.get("w", bbox.get("width"))
    h = bbox.get("h", bbox.get("height"))
    if x is None or y is None or w is None or h is None:
        raise RuntimeError(f"{caller}: bbox must contain x, y, w, h")
    try:
        return {"x": float(x), "y": float(y), "w": float(w), "h": float(h)}
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"{caller}: bbox coordinates must be numeric") from exc


def write_bbox_dict(target: dict[str, Any], bbox: dict[str, float]) -> None:
    if all(key in target for key in ("x", "y", "w", "h")) or not is_bbox_dict(target):
        target.update({"x": bbox["x"], "y": bbox["y"], "w": bbox["w"], "h": bbox["h"]})
        return
    if all(key in target for key in ("x", "y", "width", "height")):
        target.update(
            {"x": bbox["x"], "y": bbox["y"], "width": bbox["w"], "height": bbox["h"]}
        )
        return
    if all(key in target for key in ("left", "top", "right", "bottom")):
        target.update(
            {
                "left": bbox["x"],
                "top": bbox["y"],
                "right": bbox["x"] + bbox["w"],
                "bottom": bbox["y"] + bbox["h"],
            }
        )
        return
    if all(key in target for key in ("xmin", "ymin", "xmax", "ymax")):
        target.update(
            {
                "xmin": bbox["x"],
                "ymin": bbox["y"],
                "xmax": bbox["x"] + bbox["w"],
                "ymax": bbox["y"] + bbox["h"],
            }
        )
        return
    if all(key in target for key in ("xMin", "yMin", "xMax", "yMax")):
        target.update(
            {
                "xMin": bbox["x"],
                "yMin": bbox["y"],
                "xMax": bbox["x"] + bbox["w"],
                "yMax": bbox["y"] + bbox["h"],
            }
        )
        return
    if all(
        key in target
        for key in ("normalizedX", "normalizedY", "normalizedWidth", "normalizedHeight")
    ):
        target.update(
            {
                "normalizedX": bbox["x"],
                "normalizedY": bbox["y"],
                "normalizedWidth": bbox["w"],
                "normalizedHeight": bbox["h"],
            }
        )


def set_first_bbox_dict(
    target: Any, bbox: dict[str, float], path: str = "requests[0]"
) -> str | None:
    if isinstance(target, dict):
        if is_bbox_dict(target):
            write_bbox_dict(target, bbox)
            return path
        for key, value in target.items():
            found = set_first_bbox_dict(value, bbox, f"{path}.{key}")
            if found:
                return found
    elif isinstance(target, list):
        for index, item in enumerate(target):
            found = set_first_bbox_dict(item, bbox, f"{path}[{index}]")
            if found:
                return found
    return None


def is_bbox_dict(value: dict[str, Any]) -> bool:
    return any(all(key in value for key in keys) for keys in _BBOX_KEY_SETS)


def replay_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {
        str(name).lower(): str(value)
        for name, value in headers.items()
        if str(name).lower() in _HEADER_ALLOWLIST
    }


def set_replay_recaptcha_token(body: dict[str, Any], token: str) -> None:
    client_context = body.get("clientContext")
    if not isinstance(client_context, dict):
        client_context = {}
        body["clientContext"] = client_context
    set_context_recaptcha_token(client_context, token)

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
            set_context_recaptcha_token(request_context, token)


def set_context_recaptcha_token(context: dict[str, Any], token: str) -> None:
    recaptcha_context = context.get("recaptchaContext")
    if not isinstance(recaptcha_context, dict):
        recaptcha_context = {}
        context["recaptchaContext"] = recaptcha_context
    recaptcha_context["token"] = token


def response_status(response: Any) -> int:
    status = getattr(response, "status", None)
    if status is None:
        status = getattr(response, "status_code", 0)
    try:
        return int(status)
    except (TypeError, ValueError):
        return 0


async def response_text(response: Any) -> str:
    try:
        return await response.text()
    except Exception:
        return ""


def extract_media_names(data: Any) -> list[str]:
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
            add(media_id_from_name_or_bare(value))

    walk(data)
    return found


def media_id_from_name(value: str) -> str | None:
    match = _MEDIA_ID_RE.search(value)
    return match.group(1) if match else None


def looks_like_bare_media_id(value: str) -> bool:
    return bool(_BARE_MEDIA_ID_RE.match(value.strip()))


def media_id_from_name_or_bare(value: str) -> str | None:
    media_id = media_id_from_name(value)
    if media_id is not None:
        return media_id
    return value.strip() if looks_like_bare_media_id(value) else None


def strip_media_prefix(value: str) -> str:
    if not value:
        return ""
    match = _EMBEDDED_UUID_RE.search(value)
    if match:
        return match.group(0)
    return value.strip()


def anchored_parent_from_post_data(post_data: Any) -> str | None:
    if post_data is None:
        return None
    raw: Any = post_data
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except Exception:
            return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, (dict, list)):
        return None
    return find_anchored_parent_uuid(raw)


def find_anchored_parent_uuid(body: Any) -> str | None:
    def walk(value: Any, key_context: bool) -> str | None:
        if isinstance(value, dict):
            ordered = sorted(
                value.items(),
                key=lambda item: 0 if _PARENT_KEY_PATTERN.search(str(item[0])) else 1,
            )
            for child_key, child_value in ordered:
                child_in_context = key_context or bool(
                    _PARENT_KEY_PATTERN.search(str(child_key))
                )
                found = walk(child_value, child_in_context)
                if found:
                    return found
            return None
        if isinstance(value, list):
            for item in value:
                found = walk(item, key_context)
                if found:
                    return found
            return None
        if not key_context or not isinstance(value, str):
            return None
        match = _EMBEDDED_UUID_RE.search(value)
        return match.group(0) if match else None

    return walk(body, key_context=False)


def replace_uuid_in_body(body: Any, old_uuid: str, new_uuid: str) -> list[str]:
    if not old_uuid:
        return []
    replaced: list[str] = []

    def walk(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            for key, child in value.items():
                next_path = f"{path}.{key}" if path else str(key)
                value[key] = walk(child, next_path)
            return value
        if isinstance(value, list):
            for index, item in enumerate(value):
                value[index] = walk(item, f"{path}[{index}]")
            return value
        if isinstance(value, str) and old_uuid in value:
            replaced.append(path)
            return value.replace(old_uuid, new_uuid)
        return value

    walk(body, "")
    return replaced


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


def require_page(client):
    page = getattr(client, "page", None)
    if page is None:
        raise RuntimeError("Flow client page is not initialized")
    return page
