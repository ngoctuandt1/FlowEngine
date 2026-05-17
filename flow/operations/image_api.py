"""Reverse-API helpers for Flow text-to-image generation."""

from __future__ import annotations

import base64
from copy import deepcopy
import json
import logging
import random
import re
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_IMAGE_MODEL_CODES: dict[str, str] = {
    "nano banana pro": "GEM_PIX_2",
    "banana pro": "GEM_PIX_2",
    "pro": "GEM_PIX_2",
    "nano banana 2": "NARWHAL",
    "nano banana": "NARWHAL",
    "nb2": "NARWHAL",
    "imagen 4": "IMAGEN_4",
    "imagen4": "IMAGEN_4",
    "imagen": "IMAGEN_4",
}

_ASPECT_RATIO_CODES: dict[str, str] = {
    "1:1": "IMAGE_ASPECT_RATIO_SQUARE",
    "16:9": "IMAGE_ASPECT_RATIO_LANDSCAPE_16_9",
    "9:16": "IMAGE_ASPECT_RATIO_PORTRAIT_9_16",
    "4:3": "IMAGE_ASPECT_RATIO_LANDSCAPE",
    "3:4": "IMAGE_ASPECT_RATIO_PORTRAIT",
}

_AUTH_SESSION_URL = "https://labs.google/fx/api/auth/session"
_CREATE_PROJECT_URL = "https://labs.google/fx/api/trpc/project.createProject"
_UPLOAD_IMAGE_URL = "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage"
_TOKEN_TTL_SECONDS = 30 * 60
_DEFAULT_PROJECT_TOOL = "PINHOLE"

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


async def ensure_access_token(client) -> str | None:
    """Return Bearer token from /fx/api/auth/session, cached on the client."""
    now = time.time()
    cached = getattr(client, "_access_token", None)
    cached_ts = float(getattr(client, "_access_token_ts", 0.0) or 0.0)
    if cached and now - cached_ts <= _TOKEN_TTL_SECONDS:
        return str(cached)

    page = getattr(client, "page", None)
    if page is None:
        return None

    data = await _fetch_session_with_request(page)
    token = _extract_access_token(data)
    if token is None:
        data = await _fetch_session_with_browser(page)
        token = _extract_access_token(data)
    if token is None:
        return None

    client._access_token = token
    client._access_token_ts = time.time()
    return token


async def create_project_via_api(client, title: str) -> str:
    """POST project.createProject trpc endpoint -> project_id string."""
    token = await ensure_access_token(client)
    if not token:
        raise RuntimeError("create_project_via_api: access token unavailable")

    page = _require_page(client)
    payload = {"json": {"projectTitle": title, "toolName": _DEFAULT_PROJECT_TOOL}}
    response = await page.context.request.post(
        _CREATE_PROJECT_URL,
        data=json.dumps(payload),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        },
        timeout=30000,
    )
    status = _response_status(response)
    if status < 200 or status >= 300:
        body = await _response_text(response)
        raise RuntimeError(
            f"create_project_via_api: HTTP {status} from project.createProject: {body[:300]}"
        )

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("create_project_via_api: response was not JSON") from exc

    project_id = _extract_project_id(data)
    if not project_id:
        raise RuntimeError("create_project_via_api: response missing project id")
    return project_id


async def upload_reference_image(client, project_id: str, image_path: str) -> str:
    """POST uploadImage endpoint -> media name."""
    token = await ensure_access_token(client)
    if not token:
        raise RuntimeError("upload_reference_image: access token unavailable")

    page = _require_page(client)
    path = Path(image_path)
    if not path.is_file():
        raise RuntimeError(f"upload_reference_image: file not found: {image_path}")

    payload = {
        "clientContext": {"projectId": project_id, "tool": _DEFAULT_PROJECT_TOOL},
        "imageBytes": base64.b64encode(path.read_bytes()).decode("ascii"),
    }
    response = await page.context.request.post(
        _UPLOAD_IMAGE_URL,
        data=json.dumps(payload),
        headers={
            "content-type": "text/plain;charset=UTF-8",
            "authorization": f"Bearer {token}",
        },
        timeout=60000,
    )
    status = _response_status(response)
    if status < 200 or status >= 300:
        body = await _response_text(response)
        raise RuntimeError(
            f"upload_reference_image: HTTP {status} from uploadImage: {body[:300]}"
        )

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("upload_reference_image: response was not JSON") from exc

    media_name = ((data.get("media") or {}).get("name") if isinstance(data, dict) else None)
    if not media_name:
        raise RuntimeError("upload_reference_image: response missing media.name")
    return str(media_name)


async def batch_generate_images(
    client,
    project_id: str,
    prompt: str,
    model_code: str = "GEM_PIX_2",
    aspect_ratio_code: str = "IMAGE_ASPECT_RATIO_SQUARE",
    count: int = 1,
    ref_image_name: str | None = None,
    recaptcha_token: str = "",
) -> list[str]:
    """POST batchGenerateImages -> list of media names."""
    try:
        count = int(count)
    except (TypeError, ValueError) as exc:
        raise ValueError("batch_generate_images: count must be an integer") from exc
    if count < 1:
        raise ValueError("batch_generate_images: count must be >= 1")

    token = await ensure_access_token(client)
    if not token:
        raise RuntimeError("batch_generate_images: access token unavailable")

    page = _require_page(client)
    recaptcha = recaptcha_token or await _mint_recaptcha_token(page)
    payload = _build_batch_generate_payload(
        project_id=project_id,
        prompt=prompt,
        model_code=model_code,
        aspect_ratio_code=aspect_ratio_code,
        count=count,
        ref_image_name=ref_image_name,
        recaptcha_token=recaptcha,
    )
    url = (
        f"https://aisandbox-pa.googleapis.com/v1/projects/{project_id}"
        "/flowMedia:batchGenerateImages"
    )
    response = await page.context.request.post(
        url,
        data=json.dumps(payload),
        headers={
            "content-type": "text/plain;charset=UTF-8",
            "authorization": f"Bearer {token}",
        },
        timeout=120000,
    )
    status = _response_status(response)
    if status < 200 or status >= 300:
        body = await _response_text(response)
        raise RuntimeError(
            f"batch_generate_images: HTTP {status} from batchGenerateImages: {body[:300]}"
        )

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("batch_generate_images: response was not JSON") from exc

    names = _extract_media_names(data)
    if len(names) != count:
        raise RuntimeError(
            f"batch_generate_images: expected {count} media names, got {len(names)}"
        )
    if not hasattr(client, "_image_names"):
        client._image_names = []
    client._image_names.extend(names)
    return names


def resolve_model_code(model_alias: str) -> str:
    """Map image.py model alias to API model code."""
    raw = str(model_alias or "").strip()
    if not raw:
        return _IMAGE_MODEL_CODES["nano banana pro"]

    normalized = raw.lower().replace("_", "-")
    lookup_keys = [
        normalized.replace("-", " "),
        re.sub(r"[^a-z0-9]+", "", normalized),
    ]
    direct = _IMAGE_MODEL_CODES.get(normalized.replace("-", " "))
    if direct:
        return direct

    aliases = {
        "nano-banana-pro": "nano banana pro",
        "nanobananapro": "nano banana pro",
        "banana-pro": "banana pro",
        "bananapro": "banana pro",
        "nano-banana-2": "nano banana 2",
        "nanobanana2": "nano banana 2",
        "nano-banana": "nano banana",
        "nanobanana": "nano banana",
        "imagen-4": "imagen 4",
        "imagen4": "imagen 4",
    }
    for key in lookup_keys:
        mapped = aliases.get(key)
        if mapped and mapped in _IMAGE_MODEL_CODES:
            return _IMAGE_MODEL_CODES[mapped]
        if key in _IMAGE_MODEL_CODES:
            return _IMAGE_MODEL_CODES[key]

    logger.warning("Unknown image model %r; falling back to nano banana pro", raw)
    return _IMAGE_MODEL_CODES["nano banana pro"]


def resolve_aspect_code(ratio: str) -> str:
    """Map ratio string to Flow image aspect enum string."""
    raw = str(ratio or "").strip()
    code = _ASPECT_RATIO_CODES.get(raw)
    if code:
        return code
    logger.warning("Unknown image aspect ratio %r; falling back to 1:1", raw)
    return _ASPECT_RATIO_CODES["1:1"]


async def _fetch_session_with_request(page) -> dict[str, Any] | None:
    try:
        response = await page.context.request.get(_AUTH_SESSION_URL)
        if _response_status(response) != 200:
            return None
        data = await response.json()
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("ensure_access_token request fetch failed: %s", exc)
        return None


async def _fetch_session_with_browser(page) -> dict[str, Any] | None:
    try:
        data = await page.evaluate(
            """async () => {
                const r = await fetch('https://labs.google/fx/api/auth/session', {credentials:'include'});
                return await r.json();
            }"""
        )
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("ensure_access_token browser fetch failed: %s", exc)
        return None


def _extract_access_token(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    token = data.get("access_token")
    if isinstance(token, str) and token:
        return token
    return None


def _extract_project_id(data: dict[str, Any]) -> str | None:
    try:
        json_data = data["result"]["data"]["json"]
    except Exception:
        json_data = None
    if isinstance(json_data, dict):
        for key in ("id", "projectId"):
            value = json_data.get(key)
            if value:
                return str(value)
        result = json_data.get("result")
        if isinstance(result, dict):
            for key in ("projectId", "id"):
                value = result.get(key)
                if value:
                    return str(value)
    return None


def _build_batch_generate_payload(
    *,
    project_id: str,
    prompt: str,
    model_code: str,
    aspect_ratio_code: str,
    count: int,
    ref_image_name: str | None,
    recaptcha_token: str,
) -> dict[str, Any]:
    client_context = {
        "recaptchaContext": {
            "token": recaptcha_token,
            "applicationType": "RECAPTCHA_APPLICATION_TYPE_WEB",
        },
        "projectId": project_id,
        "tool": _DEFAULT_PROJECT_TOOL,
        "sessionId": str(uuid.uuid4()),
    }
    requests: list[dict[str, Any]] = []
    for _ in range(count):
        item_context = dict(client_context)
        item_context["sessionId"] = str(uuid.uuid4())
        request_item: dict[str, Any] = {
            "clientContext": item_context,
            "imageAspectRatio": aspect_ratio_code,
            "structuredPrompt": {"parts": [{"text": prompt}]},
            "seed": random.randint(1, 999999),
        }
        if model_code:
            request_item["imageModelName"] = model_code
        if ref_image_name:
            request_item["imageInputs"] = [
                {
                    "imageInputType": "IMAGE_INPUT_TYPE_REFERENCE",
                    "name": _image_input_name(ref_image_name),
                }
            ]
        requests.append(request_item)

    return {
        "clientContext": client_context,
        "mediaGenerationContext": {"batchId": str(uuid.uuid4())},
        "useNewMedia": True,
        "requests": requests,
    }


def _image_input_name(media_name: str) -> str:
    match = re.search(
        r"([a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12})",
        str(media_name),
        re.IGNORECASE,
    )
    return match.group(1) if match else str(media_name)


async def _mint_recaptcha_token(page, *, caller: str = "batch_generate_images") -> str:
    try:
        minted = await page.evaluate(_RECAPTCHA_JS)
    except Exception as exc:
        logger.warning("%s: reCAPTCHA mint failed: %s", caller, exc)
        return ""
    return str(minted) if minted else ""


def _require_page(client):
    page = getattr(client, "page", None)
    if page is None:
        raise RuntimeError("Flow client page is not initialized")
    return page


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


def _extract_media_names(data: Any) -> list[str]:
    if not isinstance(data, dict):
        return []
    names: list[str] = []
    media = data.get("media") or []
    if isinstance(media, list):
        for item in media:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if name:
                names.append(str(name))
    return names


# Legacy replay helpers kept for older image.py callers on in-flight branches.
_IMAGE_GEN_URL_HINTS = ("batchgenerateimages", "aisandbox-pa.googleapis.com/v1")
_REPLAY_HEADER_ALLOWLIST = frozenset(
    {"authorization", "content-type", "x-goog-api-key", "x-recaptcha-token"}
)


def install_image_request_capture(client) -> None:
    """Capture Flow batchGenerateImages requests/responses for replay callers."""
    if getattr(client, "_image_capture_installed", False):
        return
    client._image_requests = []
    client._image_responses = []
    page = getattr(client, "page", None)
    if page is None:
        return

    def _on_request(request) -> None:
        try:
            url = request.url or ""
            method = request.method or ""
        except Exception:
            return
        if method.upper() != "POST" or not _is_image_generate_url(url):
            return
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        client._image_requests.append(
            {
                "url": url,
                "method": method,
                "headers": headers,
                "post_data": post_data,
                "ts": time.time(),
            }
        )

    async def _on_response(response) -> None:
        try:
            url = response.url or ""
        except Exception:
            return
        if not _is_image_generate_url(url):
            return
        body: Any = None
        body_err: str | None = None
        try:
            body = await response.json()
        except Exception as json_exc:
            body_err = f"json: {json_exc!r}"
            try:
                body = await response.text()
            except Exception as text_exc:
                body_err += f" / text: {text_exc!r}"
        client._image_responses.append(
            {
                "url": url,
                "status": _response_status(response),
                "body": body,
                "body_err": body_err,
                "ts": time.time(),
            }
        )

    page.on("request", _on_request)
    page.on("response", _on_response)
    client._image_capture_installed = True


async def replay_image_generate(client, prompt: str, count: int = 1) -> list[str]:
    """Replay latest captured batchGenerateImages POST with new prompt/count."""
    if count < 1:
        raise ValueError("count must be >= 1")
    page = _require_page(client)
    req = get_image_request_template(client)
    if req is None:
        raise RuntimeError("No captured image generation request")
    payload = _parse_template_post_data(req)
    body = _build_replay_payload(payload, prompt=prompt, count=count)
    recaptcha_token = await _mint_recaptcha_token(page, caller="replay_image_generate")
    if recaptcha_token:
        _set_replay_recaptcha_token(body, recaptcha_token)
    else:
        logger.warning(
            "replay_image_generate: reCAPTCHA mint returned empty token; "
            "using captured token if present"
        )
    headers = _replay_headers(req.get("headers") or {})
    if recaptcha_token:
        _refresh_recaptcha_header(headers, recaptcha_token)
    response = await page.context.request.post(
        req["url"],
        data=json.dumps(body),
        headers=headers,
        timeout=30000,
    )
    status = _response_status(response)
    if status < 200 or status >= 300:
        text = await _response_text(response)
        raise RuntimeError(f"replay_image_generate failed with HTTP {status}: {text[:300]}")
    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("replay_image_generate response was not JSON") from exc
    names = _extract_media_names(data)
    if len(names) != count:
        raise RuntimeError(
            f"replay_image_generate: requested {count} images but got {len(names)}"
        )
    if not hasattr(client, "_image_names"):
        client._image_names = []
    client._image_names.extend(names)
    return names


def get_image_request_template(client) -> dict | None:
    requests = getattr(client, "_image_requests", None) or []
    for entry in reversed(requests):
        if _is_image_generate_url(entry.get("url", "")):
            return entry
    return None


def _is_image_generate_url(url: str) -> bool:
    url_l = (url or "").lower()
    return all(hint in url_l for hint in _IMAGE_GEN_URL_HINTS)


def _parse_template_post_data(req: dict[str, Any]) -> dict[str, Any]:
    raw = req.get("post_data")
    if isinstance(raw, dict):
        return deepcopy(raw)
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("Captured image request has no JSON post_data")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("Captured image request post_data JSON was not an object")
    return parsed


def _build_replay_payload(payload: dict[str, Any], *, prompt: str, count: int) -> dict[str, Any]:
    body = deepcopy(payload)
    requests = body.get("requests")
    if not isinstance(requests, list) or not requests or not isinstance(requests[0], dict):
        raise RuntimeError("Captured image request payload has no requests[0] template")
    template = requests[0]
    body["requests"] = [_build_replay_request(template, prompt=prompt) for _ in range(count)]
    context = body.get("mediaGenerationContext")
    if not isinstance(context, dict):
        context = {}
    body["mediaGenerationContext"] = {**context, "batchId": str(uuid.uuid4())}
    _set_replay_session_ids(body)
    return body


def _build_replay_request(template: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    item = deepcopy(template)
    structured_prompt = item.get("structuredPrompt")
    if not isinstance(structured_prompt, dict):
        structured_prompt = {"parts": [{}]}
        item["structuredPrompt"] = structured_prompt
    parts = structured_prompt.get("parts")
    if not isinstance(parts, list) or not parts:
        parts = [{}]
        structured_prompt["parts"] = parts
    if not isinstance(parts[0], dict):
        parts[0] = {}
    parts[0]["text"] = prompt
    item["seed"] = random.randint(1, 2_000_000_000)
    return item


def _replay_headers(headers: dict[str, Any]) -> dict[str, str]:
    return {
        str(name): str(value)
        for name, value in headers.items()
        if str(name).lower() in _REPLAY_HEADER_ALLOWLIST
    }


def _set_replay_session_ids(body: dict[str, Any]) -> None:
    client_context = body.get("clientContext")
    if not isinstance(client_context, dict):
        client_context = {}
        body["clientContext"] = client_context
    client_context["sessionId"] = str(uuid.uuid4())

    requests = body.get("requests")
    if not isinstance(requests, list):
        return
    for request in requests:
        if not isinstance(request, dict):
            continue
        request_context = request.get("clientContext")
        if not isinstance(request_context, dict):
            request_context = deepcopy(client_context)
            request["clientContext"] = request_context
        request_context["sessionId"] = str(uuid.uuid4())


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
        request_context = request.get("clientContext")
        if not isinstance(request_context, dict):
            request_context = deepcopy(client_context)
            request["clientContext"] = request_context
        _set_context_recaptcha_token(request_context, token)


def _set_context_recaptcha_token(context: dict[str, Any], token: str) -> None:
    recaptcha_context = context.get("recaptchaContext")
    if not isinstance(recaptcha_context, dict):
        recaptcha_context = {}
        context["recaptchaContext"] = recaptcha_context
    recaptcha_context["token"] = token


def _refresh_recaptcha_header(headers: dict[str, str], token: str) -> None:
    for name in list(headers):
        if name.lower() == "x-recaptcha-token":
            headers[name] = token
