"""Shared reverse-API helpers for image-backed L1 video submits."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
import logging
from pathlib import Path
import re
import time
import uuid
from typing import Any, Callable

from flow.operations._l1_inflate_batch import submit_l1_batch_via_inflate

logger = logging.getLogger(__name__)

UPLOAD_IMAGE_URL = "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage"

_HEADER_ALLOWLIST = frozenset(
    {"authorization", "content-type", "referer", "user-agent", "x-goog-api-key", "x-recaptcha-token"}
)
_IMAGE_REF_KEY_RE = re.compile(r"image|media|frame|ingredient|reference|asset|name|uri|url|id", re.I)
_IMAGE_REF_VALUE_RE = re.compile(r"(?:^|/)(?:media|image|images|assets|uploads?)/|https?://", re.I)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)
_FORBIDDEN_REF_PATH_RE = re.compile(
    r"recaptcha|token|batchid|projectid|videomodelkey|aspectratio|seed|clientcontext|mediagenerationcontext",
    re.I,
)
_PROMPT_PATH_RE = re.compile(r"structuredprompt|textinput|prompt|parts\.\d+\.text", re.I)


UrlMatcher = Callable[[str, Any], bool]
AnchorExtractor = Callable[[dict[str, Any] | None], dict[str, Any]]


def install_l1_video_request_capture(
    client,
    *,
    template_attr: str,
    listener_attr: str,
    operation_label: str,
    url_matcher: UrlMatcher,
    anchor_extractor: AnchorExtractor,
) -> None:
    """Install one request listener and store latest matching POST template."""
    page = getattr(client, "page", None)
    if page is None:
        setattr(client, template_attr, None)
        return

    previous = getattr(client, listener_attr, None)
    if previous is not None:
        remove_page_listener(page, "request", previous)

    def _on_request(request) -> None:
        try:
            url = request.url or ""
            method = request.method or ""
        except Exception:
            return
        if method.upper() != "POST":
            return
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        if not url_matcher(url, post_data):
            return
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        parsed = parse_post_data_value(post_data)
        anchors = anchor_extractor(parsed)
        setattr(
            client,
            template_attr,
            {
                "url": url,
                "headers": headers,
                "post_data": post_data,
                "anchors": anchors,
                "ts": time.time(),
            },
        )
        logger.info(
            "Captured Flow %s reverseAPI template: %s (anchors=%s)",
            operation_label,
            url,
            {k: v for k, v in anchors.items() if k.endswith("_path") or k in {"start", "end", "ingredients"}},
        )

    page.on("request", _on_request)
    setattr(client, listener_attr, _on_request)


def get_request_template(client, template_attr: str) -> dict[str, Any] | None:
    template = getattr(client, template_attr, None)
    return template if isinstance(template, dict) else None


def clear_request_template(client, template_attr: str) -> None:
    setattr(client, template_attr, None)


def prepare_single_request_body(
    template: dict[str, Any], *, operation_label: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    body = parse_template_post_data(template, operation_label=operation_label)
    requests = body.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise RuntimeError(f"{operation_label}: captured template has no requests[0]")
    body["requests"] = [deepcopy(requests[0])]
    ensure_fresh_batch_id(body)
    return body, body["requests"][0]


def parse_template_post_data(template: dict[str, Any], *, operation_label: str) -> dict[str, Any]:
    parsed = parse_post_data_value(template.get("post_data"))
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{operation_label}: captured template has no usable JSON post_data")
    return deepcopy(parsed)


def parse_post_data_value(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
    except Exception:
        return None
    return parsed if isinstance(parsed, dict) else None


def ensure_fresh_batch_id(body: dict[str, Any]) -> None:
    media_context = body.get("mediaGenerationContext")
    if not isinstance(media_context, dict):
        media_context = {}
        body["mediaGenerationContext"] = media_context
    media_context["batchId"] = str(uuid.uuid4())


def set_prompt_text(request: dict[str, Any], prompt: str) -> str:
    candidates: tuple[tuple[str | int, ...], ...] = (
        ("textInput", "structuredPrompt", "parts", 0, "text"),
        ("structuredPrompt", "parts", 0, "text"),
        ("textInput", "text"),
        ("prompt",),
    )
    for path in candidates:
        if set_existing_path(request, path, prompt):
            return format_path(path)
    request["textInput"] = {"structuredPrompt": {"parts": [{"text": prompt}]}}
    return "textInput.structuredPrompt.parts[0].text"


def replay_headers(headers: dict[str, Any], *, include_content_type: bool = True) -> dict[str, str]:
    allowed = _HEADER_ALLOWLIST if include_content_type else _HEADER_ALLOWLIST - {"content-type"}
    return {
        str(name).lower(): str(value)
        for name, value in headers.items()
        if str(name).lower() in allowed
    }


async def submit_replay_body_via_inflate_piggyback(
    client,
    *,
    url: str,
    body: dict[str, Any],
    prompt: str,
    aspect_ratio: str = "16:9",
    operation_label: str,
) -> str:
    """Trigger one UI L1 submit, rewriting its POST to this replay body."""
    page = require_page(client)
    route_method = getattr(page, "route", None)
    unroute_method = getattr(page, "unroute", None)
    if not callable(route_method) or not callable(unroute_method):
        return ""

    route_pattern = "**/v1/video:batchAsyncGenerate**"
    intercepted = False

    async def _route_handler(route, request) -> None:
        nonlocal intercepted
        try:
            request_url = request.url or ""
            request_method = request.method or ""
        except Exception:
            await route.continue_()
            return
        if intercepted or request_method.upper() != "POST" or "batchasyncgenerate" not in request_url.lower():
            await route.continue_()
            return

        intercepted = True
        outgoing_body = None
        try:
            outgoing_body = parse_post_data_value(request.post_data)
        except Exception:
            outgoing_body = None

        replay_body = deepcopy(body)
        graft_piggyback_context(replay_body, outgoing_body)
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        logger.info(
            "%s: piggybacking UI submit %s -> %s",
            operation_label,
            request_url[:100],
            url[:100],
        )
        await route.continue_(
            url=url,
            method="POST",
            headers=headers,
            post_data=json.dumps(replay_body),
        )

    await route_method(route_pattern, _route_handler)
    try:
        results = await submit_l1_batch_via_inflate(
            client,
            prompts=[prompt],
            aspect_ratio=aspect_ratio,
        )
    finally:
        try:
            await unroute_method(route_pattern, _route_handler)
        except Exception:
            logger.debug("%s: failed to unroute piggyback handler", operation_label, exc_info=True)

    if not intercepted:
        raise RuntimeError(f"{operation_label}: inflate piggyback did not intercept a submit POST")
    if not results:
        raise RuntimeError(f"{operation_label}: inflate piggyback returned no submit result")
    gen_id = str(results[0].get("gen_id") or "")
    if not gen_id:
        raise RuntimeError(f"{operation_label}: inflate piggyback returned empty gen_id")
    return gen_id


def graft_piggyback_context(target: dict[str, Any], source: dict[str, Any] | None) -> None:
    """Copy fresh UI project/session/reCAPTCHA context into replay payload."""
    if not isinstance(source, dict):
        return
    source_context = source.get("clientContext")
    if isinstance(source_context, dict):
        target_context = target.get("clientContext")
        if not isinstance(target_context, dict):
            target_context = {}
            target["clientContext"] = target_context
        for key in ("sessionId", "recaptchaContext"):
            if key in source_context:
                target_context[key] = deepcopy(source_context[key])

    source_requests = source.get("requests")
    target_requests = target.get("requests")
    if not isinstance(source_requests, list) or not source_requests:
        return
    if not isinstance(target_requests, list) or not target_requests:
        return
    source_request = source_requests[0]
    target_request = target_requests[0]
    if not isinstance(source_request, dict) or not isinstance(target_request, dict):
        return
    for key in ("clientContext", "recaptchaContext"):
        if key in source_request:
            target_request[key] = deepcopy(source_request[key])


async def upload_image_via_api(
    client,
    image_path: str,
    *,
    headers: dict[str, Any] | None = None,
    project_id: str = "",
    caller: str,
    upload_url: str = UPLOAD_IMAGE_URL,
) -> str:
    """Upload one local image through Flow's aisandbox upload endpoint."""
    page = require_page(client)
    resolved = resolve_image_path(image_path, label="Image")
    request_headers = replay_headers(headers or {}, include_content_type=True)
    request_headers["content-type"] = "text/plain;charset=UTF-8"
    payload: dict[str, Any] = {
        "imageBytes": base64.b64encode(resolved.read_bytes()).decode("ascii"),
    }
    if project_id:
        payload["clientContext"] = {"projectId": project_id, "tool": "PINHOLE"}
    response = await page.context.request.post(
        upload_url,
        data=json.dumps(payload),
        headers=request_headers,
        timeout=60000,
    )
    status = response_status(response)
    if status < 200 or status >= 300:
        text = await response_text(response)
        raise RuntimeError(f"{caller}: uploadImage failed with HTTP {status}: {text[:500]}")
    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError(f"{caller}: uploadImage response was not JSON") from exc
    ref = extract_uploaded_image_ref(data)
    if not ref:
        raise RuntimeError(f"{caller}: uploadImage response had no image reference")
    return ref


def resolve_image_path(path_value: str, *, label: str) -> Path:
    if not path_value:
        raise RuntimeError(f"{label} image path is required")
    candidate = Path(path_value).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except FileNotFoundError:
        raise RuntimeError(f"{label} image not found: {path_value}") from None
    except OSError as exc:
        raise RuntimeError(f"Invalid {label.lower()} image path: {path_value} ({exc})") from exc
    if not resolved.is_file():
        raise RuntimeError(f"{label} image path is not a file: {path_value}")
    return resolved


def extract_uploaded_image_ref(data: Any) -> str:
    preferred_keys = {"name", "mediaName", "media_name", "imageName", "resourceName", "uri", "url", "mediaId", "id"}
    fallback: list[str] = []

    def walk(value: Any, key: str | None = None, path: tuple[str | int, ...] = ()) -> str:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                found = walk(child_value, str(child_key), (*path, str(child_key)))
                if found:
                    return found
            return ""
        if isinstance(value, list):
            for idx, item in enumerate(value):
                found = walk(item, key, (*path, idx))
                if found:
                    return found
            return ""
        if not isinstance(value, str):
            return ""
        stripped = value.strip()
        if not stripped or len(stripped) > 1000:
            return ""
        path_text = path_to_text(path)
        if key in preferred_keys and not _FORBIDDEN_REF_PATH_RE.search(path_text):
            return stripped
        if _IMAGE_REF_VALUE_RE.search(stripped) or _UUID_RE.match(stripped):
            fallback.append(stripped)
        return ""

    return walk(data) or (fallback[0] if fallback else "")


def extract_frame_anchors(body: dict[str, Any] | None) -> dict[str, Any]:
    candidates = collect_image_ref_candidates(body)
    start = first_candidate_value(candidates, r"start|begin|first|opening")
    end = first_candidate_value(candidates, r"end|last|final|closing")
    frame_values = unique_values(
        c["value"] for c in candidates if "frame" in path_to_text(c["path"]).lower()
    )
    if not start and frame_values:
        start = frame_values[0]
    if not end and len(frame_values) > 1:
        end = frame_values[1]
    return {
        "start": start,
        "end": end,
        "start_path": first_candidate_path(candidates, start),
        "end_path": first_candidate_path(candidates, end),
        "candidates": frame_values or unique_values(c["value"] for c in candidates),
    }


def extract_ingredient_anchors(body: dict[str, Any] | None) -> dict[str, Any]:
    candidates = [
        c
        for c in collect_image_ref_candidates(body)
        if re.search(r"ingredient|reference|image|media|asset", path_to_text(c["path"]), re.I)
    ]
    return {
        "ingredients": unique_values(c["value"] for c in candidates),
        "ingredient_paths": [c["path"] for c in candidates],
        "ingredient_path_labels": [format_path(c["path"]) for c in candidates],
    }


def apply_frame_refs(
    body: dict[str, Any],
    request: dict[str, Any],
    *,
    start_ref: str,
    end_ref: str,
    anchors: dict[str, Any] | None,
    operation_label: str,
) -> list[str]:
    if not start_ref:
        raise RuntimeError(f"{operation_label}: missing uploaded start frame reference")
    if not end_ref:
        raise RuntimeError(f"{operation_label}: missing uploaded end frame reference")
    anchors = anchors or extract_frame_anchors(body)
    replacements: list[tuple[str, str, str]] = []
    if anchors.get("start"):
        replacements.append((str(anchors["start"]), start_ref, "start"))
    else:
        set_frame_ref(request, "startFrame", start_ref)
    if anchors.get("end"):
        replacements.append((str(anchors["end"]), end_ref, "end"))
    else:
        set_frame_ref(request, "endFrame", end_ref)

    touched: list[str] = []
    for old, new, label in replacements:
        paths = replace_string_occurrences(body, {old: new})
        if not paths:
            raise RuntimeError(f"{operation_label}: captured {label} frame ref not found in template body")
        touched.extend(paths)
    return touched


def set_frame_ref(request: dict[str, Any], slot: str, ref: str) -> None:
    frames_input = request.get("videoFramesInput")
    if not isinstance(frames_input, dict):
        frames_input = {}
        request["videoFramesInput"] = frames_input
    slot_obj = frames_input.get(slot)
    if not isinstance(slot_obj, dict):
        slot_obj = {}
        frames_input[slot] = slot_obj
    media_obj = slot_obj.get("media")
    if not isinstance(media_obj, dict):
        media_obj = {}
        slot_obj["media"] = media_obj
    media_obj["name"] = ref


def apply_ingredient_refs(
    body: dict[str, Any],
    request: dict[str, Any],
    *,
    ingredient_refs: list[str],
    anchors: dict[str, Any] | None,
    operation_label: str,
) -> list[str]:
    if not ingredient_refs:
        raise RuntimeError(f"{operation_label}: at least one ingredient image is required")
    anchors = anchors or extract_ingredient_anchors(body)
    old_refs = [str(v) for v in anchors.get("ingredients") or [] if v]
    candidate_paths = [p for p in anchors.get("ingredient_paths") or [] if isinstance(p, tuple)]

    rewritten_list_path = rewrite_image_ref_list(body, candidate_paths, ingredient_refs)
    if rewritten_list_path:
        return [rewritten_list_path]

    if old_refs and len(old_refs) == len(ingredient_refs):
        touched: list[str] = []
        for old, new in zip(old_refs, ingredient_refs):
            paths = replace_string_occurrences(body, {old: new})
            if not paths:
                raise RuntimeError(f"{operation_label}: captured ingredient ref not found in template body")
            touched.extend(paths)
        return touched

    request["videoIngredientsInput"] = {
        "ingredients": [{"imageResource": {"name": ref}} for ref in ingredient_refs]
    }
    return ["requests[0].videoIngredientsInput.ingredients"]


def rewrite_image_ref_list(
    body: dict[str, Any], candidate_paths: list[tuple[str | int, ...]], refs: list[str]
) -> str:
    for candidate_path in candidate_paths:
        list_path = deepest_ref_list_path(candidate_path)
        if not list_path:
            continue
        source_list = get_by_path(body, list_path)
        if not isinstance(source_list, list) or not source_list:
            continue
        proto = source_list[0]
        old_values = unique_values(c["value"] for c in collect_image_ref_candidates(proto))
        new_items = []
        for ref in refs:
            item = deepcopy(proto)
            if old_values:
                replace_string_occurrences(item, {old: ref for old in old_values})
            else:
                item = {"imageResource": {"name": ref}}
            new_items.append(item)
        set_by_path(body, list_path, new_items)
        return format_path(list_path)
    return ""


def deepest_ref_list_path(path: tuple[str | int, ...]) -> tuple[str | int, ...] | None:
    candidates: list[tuple[str | int, ...]] = []
    for idx, part in enumerate(path):
        if not isinstance(part, int):
            continue
        list_path = path[:idx]
        path_text = path_to_text(list_path)
        if "requests" == path_text.lower():
            continue
        if re.search(r"ingredient|reference|image|media|asset", path_text, re.I):
            candidates.append(list_path)
    return candidates[-1] if candidates else None


def collect_image_ref_candidates(body: Any) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    if body is None:
        return candidates
    for path, value in iter_string_paths(body):
        stripped = value.strip()
        if not stripped or len(stripped) > 1000:
            continue
        path_text = path_to_text(path)
        if _FORBIDDEN_REF_PATH_RE.search(path_text):
            continue
        if _PROMPT_PATH_RE.search(path_text) and not _IMAGE_REF_VALUE_RE.search(stripped):
            continue
        if _IMAGE_REF_KEY_RE.search(path_text) or _IMAGE_REF_VALUE_RE.search(stripped) or _UUID_RE.match(stripped):
            candidates.append({"path": path, "value": stripped})
    return candidates


def iter_string_paths(value: Any, path: tuple[str | int, ...] = ()):
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            yield from iter_string_paths(child_value, (*path, str(child_key)))
        return
    if isinstance(value, list):
        for idx, item in enumerate(value):
            yield from iter_string_paths(item, (*path, idx))
        return
    if isinstance(value, str):
        yield path, value


def replace_string_occurrences(target: Any, replacements: dict[str, str]) -> list[str]:
    touched: list[str] = []

    def replace_value(value: Any, path: tuple[str | int, ...]) -> Any:
        if isinstance(value, dict):
            for key, child in list(value.items()):
                value[key] = replace_value(child, (*path, str(key)))
            return value
        if isinstance(value, list):
            for idx, child in enumerate(list(value)):
                value[idx] = replace_value(child, (*path, idx))
            return value
        if not isinstance(value, str):
            return value
        new_value = value
        for old, new in replacements.items():
            if old:
                new_value = new_value.replace(old, new)
        if new_value != value:
            touched.append(format_path(path))
        return new_value

    replace_value(target, ())
    return touched


def extract_generated_media_ids(data: Any) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(value: str | None) -> None:
        if value and value not in seen:
            seen.add(value)
            found.append(value)

    def media_id_from_name(value: str) -> str | None:
        match = re.search(r"(?:^|/)media/([^/?#\s\"]+)", value, re.I)
        if match:
            return match.group(1)
        if _UUID_RE.match(value):
            return value
        return None

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
            add(value if _UUID_RE.match(value) else media_id_from_name(value))
            return
        if key in {"name", "mediaName", "media_name", "operationName"}:
            add(media_id_from_name(value) or (value if _UUID_RE.match(value) else None))

    walk(data)
    return found


async def mint_recaptcha_token(page, *, caller: str) -> str:
    js = """async () => {
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
        for (const action of ['submit', 'IMAGE_GENERATION', 'GENERATE_VIDEO', 'videoGenerate', 'generate']) {
            try {
                const tok = await gr.execute(siteKey, {action});
                if (tok) return tok;
            } catch(e) {}
        }
        return '';
    }"""
    try:
        minted = await page.evaluate(js)
    except Exception as exc:
        logger.warning("%s: reCAPTCHA mint failed: %s", caller, exc)
        return ""
    return str(minted) if minted else ""


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


def body_mentions(body: dict[str, Any] | None, needles: tuple[str, ...]) -> bool:
    if body is None:
        return False
    text = json.dumps(body, sort_keys=True).lower()
    return any(needle.lower() in text for needle in needles)


def first_candidate_value(candidates: list[dict[str, Any]], path_pattern: str) -> str:
    pattern = re.compile(path_pattern, re.I)
    for candidate in candidates:
        if pattern.search(path_to_text(candidate["path"])):
            return str(candidate["value"])
    return ""


def first_candidate_path(candidates: list[dict[str, Any]], value: str) -> str:
    if not value:
        return ""
    for candidate in candidates:
        if candidate["value"] == value:
            return format_path(candidate["path"])
    return ""


def unique_values(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(str(value))
            out.append(str(value))
    return out


def set_existing_path(target: dict[str, Any], path: tuple[str | int, ...], value: Any) -> bool:
    current: Any = target
    for key in path[:-1]:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return False
            current = current[key]
            continue
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


def get_by_path(target: Any, path: tuple[str | int, ...]) -> Any:
    current = target
    for key in path:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return None
            current = current[key]
            continue
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def set_by_path(target: Any, path: tuple[str | int, ...], value: Any) -> bool:
    current = target
    for key in path[:-1]:
        if isinstance(key, int):
            if not isinstance(current, list) or key >= len(current):
                return False
            current = current[key]
            continue
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
    last = path[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            return False
        current[last] = value
        return True
    if not isinstance(current, dict):
        return False
    current[last] = value
    return True


def path_to_text(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)


def format_path(path: tuple[str | int, ...]) -> str:
    parts: list[str] = []
    for part in path:
        if isinstance(part, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(part)
    return ".".join(parts)


def remove_page_listener(page: Any, event_name: str, callback: Any) -> None:
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
