"""Direct status polling + media URL fetch — bypass DOM tile dependency.

When inflate-batch submits N gens via 1 user click, Flow's frontend only
renders ONE tile (for the user-typed prompt). The other N-1 generations
are valid backend-side but never surface as DOM tiles, so the
DOM/media-event-driven wait path can't observe their completion.

Endpoint discovered live 2026-05-04 in the project's network capture:
    POST https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus

Schema (best-guess until probed): standard Google long-running ops::

    request:  {"operations": [{"name": "<uuid>"}, ...]}
              or {"operationNames": ["<uuid>", ...]}
    response: {"operations": [{
                  "operation": {"name": "<uuid>"},
                  "status": "MEDIA_GENERATION_STATUS_OK|PENDING|FAILED",
                  "media": [{...media url info...}],
                  ...
              }, ...]}

The poll primitive auto-tries multiple request shapes and parses
multiple response shapes so the first probe live-verifies the contract.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

_STATUS_URL = (
    "https://aisandbox-pa.googleapis.com/v1/video:batchCheckAsyncVideoGenerationStatus"
)


async def poll_status_via_api(
    client,
    *,
    gen_ids: list[str],
    poll_interval_sec: float = 6.0,
    hard_timeout_sec: float = 900.0,
) -> dict[str, dict[str, Any]]:
    """Poll Flow's backend status endpoint until all gen_ids resolve.

    Returns a dict keyed by gen_id::

        {
            "<gen_id>": {
                "status": "completed" | "failed" | "timeout",
                "media_id": "<uuid>" | None,
                "media_url": "<https://..>" | None,
                "raw": <last-seen response entry>,
            },
            ...
        }

    Auto-detects working request shape on the first poll and reuses it.
    """
    if not gen_ids:
        return {}

    page = client.page
    out: dict[str, dict[str, Any]] = {
        g: {"status": "pending", "media_id": None, "media_url": None}
        for g in gen_ids
    }

    headers = _build_headers(client)
    deadline = time.monotonic() + hard_timeout_sec
    request_shape = None  # discovered after first non-error poll

    while time.monotonic() < deadline:
        pending = [g for g, v in out.items() if v["status"] == "pending"]
        if not pending:
            break

        body, request_shape = _build_status_body(pending, request_shape)
        try:
            resp = await page.context.request.post(
                _STATUS_URL,
                data=json.dumps(body),
                headers=headers,
                timeout=30000,
            )
        except Exception as exc:
            logger.warning("status poll: POST failed: %s", exc)
            await asyncio.sleep(poll_interval_sec)
            continue

        status_code = resp.status
        if status_code != 200:
            txt = await resp.text()
            logger.warning("status poll: HTTP %d body=%s", status_code, txt[:200])
            # If body shape is wrong, try the next shape next iteration.
            if status_code == 400 and request_shape is not None:
                logger.info("status poll: rotating request shape")
                request_shape = _next_shape(request_shape)
            await asyncio.sleep(poll_interval_sec)
            continue

        try:
            data = await resp.json()
        except Exception:
            logger.warning("status poll: response not JSON")
            await asyncio.sleep(poll_interval_sec)
            continue

        _ingest_response(data, out)
        n_done = sum(
            1 for v in out.values()
            if v["status"] in ("completed", "failed")
        )
        logger.info("status poll: %d/%d resolved", n_done, len(out))

        if n_done == len(out):
            return out
        await asyncio.sleep(poll_interval_sec)

    # Hard timeout: mark unresolved as timeout.
    for g, v in out.items():
        if v["status"] == "pending":
            v["status"] = "timeout"
    return out


def _build_headers(client) -> dict[str, str]:
    """Reuse auth + content-type from the most recent batch submit request."""
    requests = getattr(client, "_batch_requests", None) or []
    headers: dict[str, str] = {
        "content-type": "text/plain;charset=UTF-8",
    }
    for entry in reversed(requests):
        if entry.get("method") != "POST":
            continue
        for k, v in (entry.get("headers") or {}).items():
            kl = k.lower()
            if kl == "authorization":
                headers["authorization"] = str(v)
                break
        if "authorization" in headers:
            break
    return headers


_REQUEST_SHAPES = ("operationNames", "operations_str", "operations_obj")


def _build_status_body(
    pending: list[str],
    shape: str | None,
) -> tuple[dict, str]:
    use = shape or _REQUEST_SHAPES[0]
    if use == "operationNames":
        return {"operationNames": pending}, use
    if use == "operations_str":
        return {"operations": pending}, use
    if use == "operations_obj":
        return {"operations": [{"name": g} for g in pending]}, use
    return {"operationNames": pending}, "operationNames"


def _next_shape(current: str) -> str:
    try:
        idx = _REQUEST_SHAPES.index(current)
    except ValueError:
        return _REQUEST_SHAPES[0]
    return _REQUEST_SHAPES[(idx + 1) % len(_REQUEST_SHAPES)]


_TERMINAL_STATUSES_OK = {
    "MEDIA_GENERATION_STATUS_OK", "STATUS_OK", "DONE", "SUCCEEDED",
    "MEDIA_GENERATION_STATUS_DONE",
}
_TERMINAL_STATUSES_FAILED = {
    "MEDIA_GENERATION_STATUS_FAILED", "STATUS_FAILED", "FAILED",
    "MEDIA_GENERATION_STATUS_ALL_FAILED",
}


def _ingest_response(data: dict, out: dict[str, dict[str, Any]]) -> None:
    """Walk the response and update `out` with new statuses."""
    operations = data.get("operations") or data.get("statuses") or []
    if not isinstance(operations, list):
        return
    for entry in operations:
        if not isinstance(entry, dict):
            continue
        gen_id = _extract_gen_id(entry)
        if not gen_id or gen_id not in out:
            # try matching on suffix (some responses return short ids)
            short = (gen_id or "")[-12:]
            for k in out.keys():
                if k.endswith(short) and short:
                    gen_id = k
                    break
        if not gen_id or gen_id not in out:
            continue
        slot = out[gen_id]
        slot["raw"] = entry
        status = (entry.get("status") or "").upper()
        if status in _TERMINAL_STATUSES_OK or entry.get("done") is True:
            slot["status"] = "completed"
            slot["media_id"] = _extract_media_id(entry)
            slot["media_url"] = _extract_media_url(entry)
        elif status in _TERMINAL_STATUSES_FAILED:
            slot["status"] = "failed"
            slot["error"] = entry.get("error") or status


def _extract_gen_id(entry: dict) -> str:
    inner = entry.get("operation") or {}
    if isinstance(inner, dict):
        n = inner.get("name") or inner.get("operationName")
        if n:
            return str(n)
    return str(entry.get("name") or entry.get("operationName") or "")


def _extract_media_id(entry: dict) -> str | None:
    media = entry.get("media") or entry.get("result") or {}
    if isinstance(media, list) and media:
        media = media[0]
    if isinstance(media, dict):
        for key in ("mediaId", "media_id", "id", "name"):
            v = media.get(key)
            if v:
                return str(v)
    return None


def _extract_media_url(entry: dict) -> str | None:
    media = entry.get("media") or entry.get("result") or {}
    if isinstance(media, list) and media:
        media = media[0]
    if isinstance(media, dict):
        for key in ("downloadUrl", "url", "videoUrl", "fifeUrl"):
            v = media.get(key)
            if v:
                return str(v)
        # nested under a 'video' key
        v = media.get("video") or {}
        if isinstance(v, dict):
            for key in ("url", "downloadUrl"):
                u = v.get(key)
                if u:
                    return str(u)
    return None


async def download_via_url(
    client,
    *,
    url: str,
    out_path: str,
) -> str | None:
    """Direct GET on a media URL using the page's request context."""
    if not url:
        return None
    try:
        resp = await client.page.context.request.get(url, timeout=120000)
        if resp.status != 200:
            logger.error("download_via_url: HTTP %d for %s", resp.status, url[:80])
            return None
        body = await resp.body()
    except Exception as exc:
        logger.exception("download_via_url failed: %s", exc)
        return None
    try:
        with open(out_path, "wb") as f:
            f.write(body)
    except Exception as exc:
        logger.exception("download_via_url write failed: %s", exc)
        return None
    return out_path
