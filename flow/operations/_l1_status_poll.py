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
_RECAPTCHA_URL_TOKENS = (
    "google.com/recaptcha",
    "recaptcha.net/recaptcha",
    "gstatic.com/recaptcha",
    "/recaptcha/api2/",
    "/recaptcha/enterprise/",
)


def detect_recaptcha_from_status_response(response, response_text=None) -> bool:
    """Return True when a Flow status response indicates reCAPTCHA blocking."""
    if isinstance(response, dict):
        return _status_payload_has_recaptcha(response)

    response_url = _response_string(response, "url")
    location = _response_header(response, "location")
    if _contains_recaptcha_url_token(response_url) or _contains_recaptcha_url_token(location):
        return True

    if _response_status(response) not in (403, 429):
        return False

    return _contains_recaptcha_url_token(response_text or "")


def _response_status(response) -> int | None:
    for name in ("status", "status_code"):
        status = getattr(response, name, None)
        if callable(status):
            try:
                status = status()
            except TypeError:
                continue
        try:
            return int(status)
        except (TypeError, ValueError):
            continue
    return None


def _response_string(response, name: str) -> str:
    value = getattr(response, name, "")
    if callable(value):
        try:
            value = value()
        except TypeError:
            return ""
    return str(value or "")


def _response_header(response, name: str) -> str:
    headers = getattr(response, "headers", None)
    if callable(headers):
        try:
            headers = headers()
        except TypeError:
            return ""
    if not isinstance(headers, dict):
        return ""
    for key, value in headers.items():
        if str(key).lower() == name.lower():
            return str(value or "")
    return ""


def _contains_recaptcha_url_token(value: object) -> bool:
    text = str(value or "").lower().replace("\\/", "/")
    return any(token in text for token in _RECAPTCHA_URL_TOKENS)


_STATUS_RECAPTCHA_TEXT_TOKENS = (
    "recaptcha",
    "captcha",
    "unusual traffic",
    "verify you are human",
    "verify you're human",
)
_STATUS_RECAPTCHA_TEXT_KEYS = (
    "status",
    "error",
    "message",
    "reason",
    "description",
    "detail",
)


def _status_payload_has_recaptcha(node: Any, key_hint: str = "") -> bool:
    if isinstance(node, str):
        if _contains_recaptcha_url_token(node):
            return True
        if _is_status_recaptcha_text_key(key_hint):
            text = node.lower().replace("\\/", "/")
            return any(token in text for token in _STATUS_RECAPTCHA_TEXT_TOKENS)
        return False
    if isinstance(node, dict):
        return any(
            _status_payload_has_recaptcha(value, str(key))
            for key, value in node.items()
        )
    if isinstance(node, (list, tuple)):
        return any(_status_payload_has_recaptcha(value, key_hint) for value in node)
    return False


def _is_status_recaptcha_text_key(key: str) -> bool:
    key_l = key.lower()
    return any(token in key_l for token in _STATUS_RECAPTCHA_TEXT_KEYS)


async def _page_has_recaptcha_block(page) -> bool:
    if page is None:
        return False
    try:
        from flow import recaptcha as recaptcha_module

        probe = getattr(recaptcha_module, "is_recaptcha_blocked", None)
        if probe is None:
            probe = getattr(recaptcha_module, "detect_recaptcha", None)
        if probe is None:
            return False
        result = probe(page)
        if hasattr(result, "__await__"):
            result = await result
        return bool(result)
    except Exception as exc:
        logger.debug("status poll: reCAPTCHA page probe failed: %s", exc)
        return False


def _recaptcha_error(message: str, url: str | None = None) -> Exception:
    from flow import wait as flow_wait

    try:
        return flow_wait.RecaptchaError(
            kind="v3_invisible_or_block",
            message=message,
        )
    except TypeError:
        error = flow_wait.RecaptchaError(message)
        error.args = (message,)
        setattr(error, "kind", "v3_invisible_or_block")
        setattr(error, "url", url)
        return error


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


_PER_TILE_SCRAPE_JS = """() => {
    // Walk every project tile and pull out its progress + visible
    // state cues. Flow renders each generation as a `[data-tile-id]`
    // wrapper; the running cards have a `<progressbar>`-style
    // element with aria-valuenow OR an inner element whose textContent
    // includes "<NN>%". Done cards have a <video> element. Failed
    // cards show a Material Icon "warning" or text "Failed".
    const out = [];
    const seenIds = new Set();
    const tiles = document.querySelectorAll('[data-tile-id]');
    for (const t of tiles) {
        const id = t.getAttribute('data-tile-id') || '';
        if (!id || seenIds.has(id)) continue;
        seenIds.add(id);

        const r = t.getBoundingClientRect();
        if (r.width < 40 || r.height < 40) continue; // skip rail mirrors

        // Progress %: prefer aria-valuenow, then any text matching N%
        let progress = -1;
        const progressEl = t.querySelector('[aria-valuenow]');
        if (progressEl) {
            const v = parseInt(progressEl.getAttribute('aria-valuenow') || '');
            if (!isNaN(v) && v >= 0 && v <= 100) progress = v;
        }
        if (progress < 0) {
            const txt = (t.innerText || '').match(/(\\d{1,3})\\s*%/);
            if (txt) {
                const v = parseInt(txt[1]);
                if (!isNaN(v) && v >= 0 && v <= 100) progress = v;
            }
        }

        // Done = has visible <video>
        const hasVideo = t.querySelector('video') !== null;

        // Failed = Material warning icon or text "Failed"/"Lỗi"
        const failed = /failed|error|lỗi|loi/i.test(t.innerText || '')
            || t.querySelector('i.google-symbols')?.textContent?.includes('warning');

        let state = 'pending';
        if (failed) state = 'failed';
        else if (hasVideo) state = 'done';
        else if (progress >= 0) state = 'running';

        out.push({tile_id: id, progress: progress, state: state});
    }
    return out;
}"""


async def scrape_dom_progress(page) -> list[dict]:
    """Return per-tile progress + state read from the project DOM.

    Used to render real-time % progress for the user. Combined with the
    API's authoritative state enum in :func:`poll_status_via_api`.

    Bounded by a 3s timeout: ``page.evaluate`` has no default deadline,
    and a wedged page (observed live 2026-05-04 chain v6 poll #4) silently
    stalls the entire poll loop. Returning [] on timeout lets the API-
    side polling continue without DOM enrichment.
    """
    try:
        return await asyncio.wait_for(
            page.evaluate(_PER_TILE_SCRAPE_JS),
            timeout=3.0,
        )
    except asyncio.TimeoutError:
        logger.warning("scrape_dom_progress: page.evaluate timed out (>3s)")
        return []
    except Exception as exc:
        logger.debug("scrape_dom_progress failed: %s", exc)
        return []


_PROGRESS_KEY_HINTS = (
    "progress", "percent", "complete", "elapsed", "remaining",
    "eta", "duration", "fraction",
)


def _walk_for_progress(node, prefix: str = "") -> list[str]:
    """Find any leaf whose key name suggests progress %.

    Returns list of "path=value" strings for any matching leaf. Used to
    discover Flow's progress field at run-time without guessing the
    schema name.
    """
    out: list[str] = []
    if isinstance(node, dict):
        for k, v in node.items():
            p = f"{prefix}.{k}" if prefix else k
            kl = k.lower()
            if any(h in kl for h in _PROGRESS_KEY_HINTS) and not isinstance(v, (dict, list)):
                out.append(f"{p}={v!r}")
            elif isinstance(v, (dict, list)):
                out.extend(_walk_for_progress(v, p))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_walk_for_progress(v, f"{prefix}[{i}]"))
    return out[:8]  # cap


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
        g: {
            "status": "pending",
            "media_id": None,
            "media_url": None,
            "progress_pct": -1,    # filled by DOM scrape per poll
            "dom_state": "unknown",
        }
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

        if await _page_has_recaptcha_block(page):
            raise _recaptcha_error(
                "reCAPTCHA detected during status poll page probe",
                getattr(page, "url", None),
            )

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
            if detect_recaptcha_from_status_response(resp, txt):
                raise _recaptcha_error(
                    f"reCAPTCHA blocked Flow status poll (HTTP {status_code})",
                    _response_string(resp, "url") or _response_header(resp, "location"),
                )
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

        if _status_payload_has_recaptcha(data):
            raise _recaptcha_error(
                "reCAPTCHA blocked Flow status poll (HTTP 200 JSON payload)",
                _response_string(resp, "url") or _response_header(resp, "location"),
            )

        _ingest_response(data, out)
        n_done = sum(
            1 for v in out.values()
            if v["status"] in ("completed", "failed")
        )

        # Layer DOM-scraped per-tile progress on top of API state.
        # Per-tile order on /project/ is most-recent-first; submission
        # order is i=0..N-1 so tile_index = N-1-i for our gens.
        try:
            dom_tiles = await scrape_dom_progress(page)
        except Exception:
            dom_tiles = []
        if dom_tiles:
            n = len(out)
            ordered_keys = list(out.keys())  # submission order
            for submit_idx, gen_id in enumerate(ordered_keys):
                tile_idx = n - 1 - submit_idx
                if 0 <= tile_idx < len(dom_tiles):
                    tile = dom_tiles[tile_idx]
                    pct = tile.get("progress", -1)
                    if isinstance(pct, int) and pct >= 0:
                        out[gen_id]["progress_pct"] = pct
                    state = tile.get("state")
                    if isinstance(state, str):
                        out[gen_id]["dom_state"] = state

        logger.info(
            "status poll: %d/%d resolved | %s",
            n_done, len(out),
            " ".join(
                f"[{i}]{out[g]['status'][0].upper()}{out[g]['progress_pct']:>3}%"
                for i, g in enumerate(out.keys())
            ),
        )
        # When nothing resolved, log the response keys + first item shape so
        # we can iterate on the parser without burning credits on full
        # generations. Cap to first few status polls only.
        # Per-poll status summary + progress field probe (every poll
        # until at least one transitions; then every 4th).
        out_dump_done["count"] = out_dump_done.get("count", 0) + 1
        should_log = (
            n_done == 0
            or out_dump_done["count"] % 4 == 1
        )
        if should_log:
            try:
                media_list = data.get("media") or []
                summary = []
                for m in media_list:
                    if not isinstance(m, dict):
                        continue
                    mm = m.get("mediaMetadata") or {}
                    ms = (mm.get("mediaStatus") or {}).get(
                        "mediaGenerationStatus", "?"
                    )
                    summary.append(ms.replace("MEDIA_GENERATION_STATUS_", ""))
                # Hunt for any progress-bearing field anywhere in the
                # response. Walks string/number leaves whose key name
                # contains "progress" or "percent" or "complete".
                progress_hits = _walk_for_progress(data)
                logger.info(
                    "status poll #%d: per-gen=%s progress=%s",
                    out_dump_done["count"], summary,
                    progress_hits if progress_hits else "no-field",
                )
            except Exception:
                pass

        # On FIRST poll, dump full media[0] so we can see whether the
        # status endpoint exposes a numeric progress. Caps at one dump.
        if not out_dump_done.get("full_dumped"):
            try:
                m0 = (data.get("media") or [None])[0]
                if isinstance(m0, dict):
                    logger.info(
                        "status poll FULL media[0]:\n%s",
                        json.dumps(m0, indent=2)[:8000],
                    )
                    out_dump_done["full_dumped"] = True
            except Exception:
                pass

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
    # Verified live 2026-05-04 11:21: Flow uses
    # MEDIA_GENERATION_STATUS_SUCCESSFUL on completion.
    "MEDIA_GENERATION_STATUS_SUCCESSFUL",
    "MEDIA_GENERATION_STATUS_OK", "STATUS_OK", "DONE", "SUCCEEDED",
    "SUCCESSFUL", "MEDIA_GENERATION_STATUS_DONE",
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
    """Pull a status string out of any common location in the entry.

    Live verify 2026-05-04 confirmed Flow's status response buries the
    real status at::

        entry["mediaMetadata"]["mediaStatus"]["mediaGenerationStatus"]

    e.g. ``MEDIA_GENERATION_STATUS_PENDING`` while running,
    ``MEDIA_GENERATION_STATUS_OK`` (or similar terminal value) when
    done. We check that path first, then fall back to flatter shapes
    in case Flow rotates the schema.
    """
    media_meta = entry.get("mediaMetadata") or {}
    if isinstance(media_meta, dict):
        mstatus = media_meta.get("mediaStatus") or {}
        if isinstance(mstatus, dict):
            s = mstatus.get("mediaGenerationStatus")
            if isinstance(s, str):
                return s.upper()
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
    # The status-response media[i].name IS the canonical media uuid
    # (verified live 2026-05-04 — Flow surfaces the same uuid on the
    # download URL).
    name = entry.get("name")
    if isinstance(name, str) and name:
        return name
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
    """Walk the entry for any URL-bearing field that points to the
    finished media. Field name varies across Flow builds — known names
    include ``fifeUrl`` / ``downloadUrl`` / ``url`` / ``videoUrl``,
    sometimes nested under ``video`` / ``generatedVideo`` / ``media``.
    """
    seen: list[str] = []
    _walk_for_media_url(entry, seen)
    return seen[0] if seen else None


def _walk_for_media_url(node, out: list[str]) -> None:
    if not isinstance(node, (dict, list)) and not out:
        return
    if isinstance(node, dict):
        for k, v in node.items():
            kl = str(k).lower()
            if isinstance(v, str) and v.startswith("http") and any(
                t in kl for t in ("url", "fife", "uri", "download")
            ):
                out.append(v)
                return
            if isinstance(v, (dict, list)):
                _walk_for_media_url(v, out)
                if out:
                    return
    elif isinstance(node, list):
        for v in node:
            _walk_for_media_url(v, out)
            if out:
                return


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
