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


def _adapt_flow_template(template: dict, pending: list[str]) -> dict:
    """Replace the gen_ids inside a scraped UI status template.

    Live verify 2026-05-04 confirmed Flow's status request shape::

        {"media": [{"name": "<gen_id>", "projectId": "<uuid>"}, ...]}

    The list field is ``media`` (not ``operations``). We replicate the
    template's first entry as a proto and swap ``name`` for each
    pending gen_id.
    """
    body = dict(template)
    list_key = None
    for k in ("media", "operations"):
        if isinstance(template.get(k), list) and template[k]:
            list_key = k
            break
    if list_key is None:
        # Last-ditch fallback to the legacy guess shape.
        body["media"] = [{"name": g} for g in pending]
        return body
    proto = template[list_key][0]
    if not isinstance(proto, dict):
        body[list_key] = [{"name": g} for g in pending]
        return body
    new_entries = []
    for g in pending:
        entry = json.loads(json.dumps(proto))
        _set_gen_id_in_entry(entry, g)
        new_entries.append(entry)
    body[list_key] = new_entries
    return body


def _set_gen_id_in_entry(entry: dict, gen_id: str) -> None:
    """In-place swap of any name/id field in an AsyncOperation entry."""
    inner = entry.get("operation")
    if isinstance(inner, dict):
        for k in ("name", "operationName", "id"):
            if k in inner:
                inner[k] = gen_id
                return
        inner["name"] = gen_id
        return
    for k in ("name", "operationName", "id", "operationId"):
        if k in entry:
            entry[k] = gen_id
            return
    entry["name"] = gen_id


def _harvest_flow_status_request_template(client) -> dict | None:
    """Try to scrape a real Flow-UI status request body from the captures.

    When Flow's own React state polls `batchCheckAsyncVideoGenerationStatus`,
    the side-channel listener (`install_batch_response_capture`) records
    the request body. Reusing that as the template removes the need to
    guess the AsyncOperation proto shape.
    """
    requests = getattr(client, "_batch_requests", None) or []
    for entry in reversed(requests):
        url_l = (entry.get("url") or "").lower()
        if "batchcheckasync" not in url_l:
            continue
        if entry.get("method") != "POST":
            continue
        raw = entry.get("post_data")
        if not isinstance(raw, str) or not raw:
            continue
        try:
            return json.loads(raw)
        except Exception:
            continue
    return None


async def poll_status_via_api(
    client,
    *,
    gen_ids: list[str],
    project_id: str | None = None,
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
    out_dump_done: dict[str, bool] = {}

    headers = _build_headers(client)
    deadline = time.monotonic() + hard_timeout_sec
    request_shape = None  # discovered after first non-error poll
    flow_template = _harvest_flow_status_request_template(client)
    if flow_template is not None:
        logger.info(
            "status poll: scraped Flow's own request template (keys=%s)",
            list(flow_template.keys())[:8],
        )

    while time.monotonic() < deadline:
        pending = [g for g, v in out.items() if v["status"] == "pending"]
        if not pending:
            break

        # Re-harvest each iteration — Flow UI starts polling a few
        # seconds after submit, so the template may not exist on our
        # first try but appear on a later one.
        if flow_template is None:
            flow_template = _harvest_flow_status_request_template(client)
            if flow_template is not None:
                logger.info(
                    "status poll: harvested Flow template mid-run keys=%s",
                    list(flow_template.keys())[:8],
                )
        if flow_template is not None:
            body = _adapt_flow_template(flow_template, pending)
        elif project_id:
            # Direct shape verified live 2026-05-04:
            #   {"media": [{"name": "<gen_id>", "projectId": "<uuid>"}, ...]}
            body = {
                "media": [
                    {"name": g, "projectId": project_id}
                    for g in pending
                ]
            }
        else:
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
        # When nothing resolved, log the response keys + first item shape so
        # we can iterate on the parser without burning credits on full
        # generations. Cap to first few status polls only.
        if n_done == 0 and out_dump_done.get("count", 0) < 2:
            try:
                ops_first = (data.get("operations") or [None])[0]
                med_first = (data.get("media") or [None])[0]
                logger.info(
                    "status poll: response keys=%s ops[0]=%s media[0]=%s",
                    list(data.keys())[:8],
                    json.dumps(ops_first)[:500] if ops_first else "null",
                    json.dumps(med_first)[:500] if med_first else "null",
                )
            except Exception:
                pass
            out_dump_done["count"] = out_dump_done.get("count", 0) + 1

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


# Shape candidates rotated until Flow's status endpoint stops returning
# 400. Live verify 2026-05-04 narrowed it down: the request expects a
# field named ``operations`` whose entries are
# ``type.googleapis.com/google.internal.labs.aisandbox.v1.AsyncOperation``
# protos (so plain strings and ``{name:...}`` both 400). Try each known-
# plausible shape.
_REQUEST_SHAPES = (
    "operations_obj_operation",   # [{"operation": {"name": gid}}]   — mirrors submit response
    "operations_obj_id",          # [{"id": gid}]
    "operations_obj_op_name",     # [{"operationName": gid}]
    "operations_obj_with_status", # [{"operation": {"name": gid}, "status": "PENDING"}]
    "operations_str",             # [gid, ...]
    "operations_obj_name",        # [{"name": gid}]
    "operationNames",             # {"operationNames": [gid, ...]}
)


def _build_status_body(
    pending: list[str],
    shape: str | None,
) -> tuple[dict, str]:
    use = shape or _REQUEST_SHAPES[0]
    if use == "operationNames":
        return {"operationNames": pending}, use
    if use == "operations_str":
        return {"operations": pending}, use
    if use == "operations_obj_name":
        return {"operations": [{"name": g} for g in pending]}, use
    if use == "operations_obj_id":
        return {"operations": [{"id": g} for g in pending]}, use
    if use == "operations_obj_op_name":
        return {"operations": [{"operationName": g} for g in pending]}, use
    if use == "operations_obj_operation":
        return {"operations": [{"operation": {"name": g}} for g in pending]}, use
    if use == "operations_obj_with_status":
        return {"operations": [
            {"operation": {"name": g}, "status": "MEDIA_GENERATION_STATUS_PENDING"}
            for g in pending
        ]}, use
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
    """Walk the response and update `out` with new statuses.

    Flow's response has either:
      * ``{"operations": [...]}`` — older shape, each entry is an
        ``AsyncOperation``-ish dict with ``operation.name`` + ``status``.
      * ``{"media": [...]}`` — current shape (live verify 2026-05-04),
        each entry carries the gen_id alongside the resolved media data.

    We try both. Each entry is matched to one of our pending gen_ids
    via deep-walk on any string field that equals (or ends with) a
    pending id; the matching slot is updated with status + media_id +
    media_url.
    """
    candidates = []
    if isinstance(data.get("operations"), list):
        candidates += data["operations"]
    if isinstance(data.get("media"), list):
        candidates += data["media"]
    if isinstance(data.get("statuses"), list):
        candidates += data["statuses"]
    for entry in candidates:
        if not isinstance(entry, dict):
            continue
        gen_id = _match_pending_gen_id(entry, out.keys())
        if not gen_id:
            continue
        slot = out[gen_id]
        slot["raw"] = entry
        status = _extract_status(entry)
        # Done indicator: in addition to status enum, presence of a
        # media URL field (videoOutputs / fifeUrl / downloadUrl) implies
        # completion. Keep enum check for early failures.
        media_url = _extract_media_url(entry)
        media_id = _extract_media_id(entry)
        is_done = (
            status in _TERMINAL_STATUSES_OK
            or entry.get("done") is True
            or bool(media_url)
        )
        if is_done:
            slot["status"] = "completed"
            slot["media_id"] = media_id
            slot["media_url"] = media_url
            logger.info(
                "status: gen=%s -> completed (mid=%s, url=%s)",
                gen_id[-12:], (media_id or "")[:12],
                "yes" if media_url else "no",
            )
        elif status in _TERMINAL_STATUSES_FAILED:
            slot["status"] = "failed"
            slot["error"] = entry.get("error") or status
            logger.info("status: gen=%s -> failed (%s)", gen_id[-12:], status)
        else:
            # Still pending — log entry keys + status periodically so we
            # can iterate on the parser if Flow surfaces completion
            # under an unfamiliar field.
            logger.debug(
                "status: gen=%s pending status=%r keys=%s",
                gen_id[-12:], status, list(entry.keys())[:10],
            )


def _match_pending_gen_id(entry: dict, pending_ids) -> str | None:
    """Find the first pending gen_id referenced anywhere in `entry`."""
    direct = _extract_gen_id(entry)
    if direct:
        for k in pending_ids:
            if direct == k or direct.endswith(k[-12:]) or k.endswith(direct[-12:]):
                return k
    # Deep-walk every string value for a suffix match — Flow's status
    # response may bury the gen id under nested keys we haven't named.
    suffix_to_id = {k[-12:]: k for k in pending_ids}
    return _walk_for_suffix(entry, suffix_to_id)


def _walk_for_suffix(node: Any, suffix_to_id: dict[str, str]) -> str | None:
    if isinstance(node, str):
        for suf, full in suffix_to_id.items():
            if suf and suf in node:
                return full
        return None
    if isinstance(node, dict):
        for v in node.values():
            hit = _walk_for_suffix(v, suffix_to_id)
            if hit:
                return hit
        return None
    if isinstance(node, list):
        for v in node:
            hit = _walk_for_suffix(v, suffix_to_id)
            if hit:
                return hit
    return None


def _extract_status(entry: dict) -> str:
    """Pull a status string out of any common location in the entry."""
    direct = entry.get("status")
    if isinstance(direct, str):
        return direct.upper()
    nested = entry.get("operation") or {}
    if isinstance(nested, dict):
        s = nested.get("status")
        if isinstance(s, str):
            return s.upper()
    return ""


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
