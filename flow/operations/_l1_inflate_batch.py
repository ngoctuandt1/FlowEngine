"""Inflate-batch: 1 UI click → N generations via request-route rewrite.

The reverse-API direct POST hits Flow's reCAPTCHA-v3 wall (token from
``grecaptcha.execute()`` scores too low for the backend threshold).
But Flow's submit endpoint already accepts a ``requests`` list — and the
``batchId`` in ``mediaGenerationContext`` strongly implies that one
recaptcha token validates the whole batch on the backend.

Strategy:

  1. User-driven UI submit produces a high-score token bound to a real
     pointer click on the composer's submit button.
  2. Right before that POST flies, a ``page.route()`` interceptor
     replaces the body's ``requests: [single]`` with
     ``requests: [N entries]``. The recaptcha token, auth, project, and
     ``batchId`` stay intact.
  3. Flow processes all N as one batch and returns N operations.
  4. Caller receives N gen_ids in submission order, ready for the
     existing :func:`wait_for_all_l1_gens` + tile-pinned download path.

Wall-time win: skip N-1 composer cycles. For N=5 jobs that is roughly
5 × 10 s = 50 s saved on submit, plus collective wait runs in parallel
naturally.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from typing import Any

from flow.operations._l1_batch import (
    _BATCH_GEN_URL_HINTS,
    install_batch_response_capture,
    submit_generate_l1,
)

logger = logging.getLogger(__name__)


_DEFAULT_VIDEO_MODEL_KEY = "veo_3_1_t2v_lite_low_priority"
_DEFAULT_ASPECT_RATIO_ENUM = "VIDEO_ASPECT_RATIO_LANDSCAPE"
_BATCH_SUBMIT_URL_GLOB = (
    "**/aisandbox-pa.googleapis.com/v1/video:batchAsync*"
)


def _aspect_enum(ratio: str) -> str:
    return "VIDEO_ASPECT_RATIO_PORTRAIT" if ratio == "9:16" else _DEFAULT_ASPECT_RATIO_ENUM


def _build_request_entry(
    prompt: str,
    *,
    aspect_ratio: str,
    video_model_key: str,
) -> dict[str, Any]:
    return {
        "aspectRatio": _aspect_enum(aspect_ratio),
        "seed": random.randint(1, 2_000_000_000),
        "textInput": {"structuredPrompt": {"parts": [{"text": prompt}]}},
        "videoModelKey": video_model_key,
        "metadata": {},
    }


async def submit_l1_batch_via_inflate(
    client,
    *,
    prompts: list[str],
    aspect_ratio: str = "16:9",
    video_model_key: str | None = None,
    intercept_timeout_sec: float = 30.0,
) -> list[dict]:
    """Drive 1 UI submit with N prompts via route-rewrite.

    Returns one dict per prompt in input order::

        {prompt, gen_id, submit_ts, calls_before, batch_resp_before,
         project_id, project_url}

    Empty list on failure. Single-prompt input degrades to the regular
    UI submit path (no inflate needed).
    """
    if not prompts:
        return []

    install_batch_response_capture(client)
    page = client.page

    if len(prompts) == 1:
        primer = await submit_generate_l1(
            client, _job_for(prompts[0], aspect_ratio),
            project_already_open=False,
        )
        return [_record(prompts[0], primer)]

    extra_prompts = prompts[1:]
    target_count = len(prompts)
    inflate_state: dict[str, Any] = {
        "fired": False,
        "ok": False,
        "error": None,
        "request_url": None,
    }

    async def _on_route(route, request):
        url_l = (request.url or "").lower()
        if not any(hint in url_l for hint in _BATCH_GEN_URL_HINTS):
            await route.continue_()
            return
        if inflate_state["fired"]:
            await route.continue_()
            return
        try:
            raw = request.post_data or ""
            body = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(body, dict) or "requests" not in body:
                logger.warning("inflate: body has no 'requests' field; passing through")
                await route.continue_()
                return
            existing = body.get("requests") or []
            if not isinstance(existing, list) or not existing:
                await route.continue_()
                return

            base_entry = existing[0] if isinstance(existing[0], dict) else {}
            mk = (
                video_model_key
                or base_entry.get("videoModelKey")
                or _DEFAULT_VIDEO_MODEL_KEY
            )
            new_entries = [
                _build_request_entry(
                    p, aspect_ratio=aspect_ratio, video_model_key=mk,
                )
                for p in extra_prompts
            ]
            body["requests"] = list(existing) + new_entries
            ctx = body.setdefault("mediaGenerationContext", {})
            ctx["batchId"] = str(uuid.uuid4())
            new_post_data = json.dumps(body)
            inflate_state["fired"] = True
            logger.info(
                "inflate: intercepted POST %s — rewriting requests %d → %d",
                request.url[:100], len(existing), len(body["requests"]),
            )

            # Use route.fetch() so we can SEE the response and decide
            # whether the rewrite was accepted. If Flow signs/verifies
            # the body and rejects, fall back to the original request so
            # the primer submit still goes through.
            try:
                api_resp = await route.fetch(post_data=new_post_data)
            except Exception as exc:
                logger.error("inflate: route.fetch failed: %s — falling back", exc)
                inflate_state["error"] = f"fetch: {exc}"
                await route.continue_()
                return

            try:
                status = api_resp.status
                resp_body_text = await api_resp.text()
            except Exception as exc:
                logger.error("inflate: read response failed: %s", exc)
                inflate_state["error"] = f"read: {exc}"
                await route.continue_()
                return

            if status != 200:
                logger.error(
                    "inflate: rewritten POST returned HTTP %d — falling back. "
                    "Body[:200]=%s",
                    status, resp_body_text[:200],
                )
                inflate_state["error"] = f"status {status}: {resp_body_text[:200]}"
                # Fallback: re-issue the ORIGINAL request so the primer
                # submit still produces a usable single gen.
                try:
                    fb = await route.fetch(post_data=raw)
                    fb_body = await fb.body()
                    await route.fulfill(
                        status=fb.status,
                        headers=dict(fb.headers),
                        body=fb_body,
                    )
                except Exception as exc:
                    logger.exception("inflate: fallback fulfill failed: %s", exc)
                    try:
                        await route.continue_()
                    except Exception:
                        pass
                return

            # Success — fulfill with the rewritten response. The body
            # carries N operations.
            inflate_state["accepted"] = True
            try:
                parsed = json.loads(resp_body_text)
                ops_n = len(parsed.get("operations") or [])
                logger.info(
                    "inflate: ACCEPTED — Flow returned %d operations for %d requested",
                    ops_n, len(body["requests"]),
                )
                inflate_state["response_body"] = parsed
            except Exception:
                logger.warning("inflate: response not JSON; passing raw body")

            await route.fulfill(
                status=status,
                headers=dict(api_resp.headers),
                body=resp_body_text,
            )
        except Exception as exc:
            inflate_state["error"] = repr(exc)
            logger.exception("inflate: route handler crashed: %s", exc)
            try:
                await route.continue_()
            except Exception:
                pass

    await page.route(_BATCH_SUBMIT_URL_GLOB, _on_route)
    try:
        # Drive the UI submit with prompts[0]; the route handler swaps in
        # the additional prompts before the POST leaves the browser.
        primer = await submit_generate_l1(
            client, _job_for(prompts[0], aspect_ratio),
            project_already_open=False,
        )
    finally:
        try:
            await page.unroute(_BATCH_SUBMIT_URL_GLOB, _on_route)
        except Exception:
            pass

    if not inflate_state["fired"]:
        logger.error(
            "inflate: route never matched the submit POST (err=%s) — "
            "returning UI-only result",
            inflate_state["error"],
        )
        return [_record(prompts[0], primer)]

    # If the route handler captured the response body in-place that's the
    # authoritative source. Otherwise fall back to the side-channel
    # listener (which fires on the fulfilled response).
    response_body = inflate_state.get("response_body")
    if response_body is None:
        deadline = asyncio.get_event_loop().time() + intercept_timeout_sec
        while asyncio.get_event_loop().time() < deadline:
            body = _latest_inflated_response(client, expected=target_count)
            if body is not None:
                response_body = body
                break
            await asyncio.sleep(0.3)

    if response_body is None:
        logger.error(
            "inflate: no response with %d operations within %.0fs (err=%s)",
            target_count, intercept_timeout_sec, inflate_state.get("error"),
        )
        return [_record(prompts[0], primer)]

    ops = response_body.get("operations") or []
    if len(ops) < target_count:
        logger.warning(
            "inflate: response has %d operations, expected %d",
            len(ops), target_count,
        )

    submit_ts = primer["submit_ts"]
    calls_before = primer["calls_before"]
    batch_resp_before = primer["calls_before"]  # unused for downstream but kept

    out: list[dict] = []
    for prompt, op in zip(prompts, ops):
        inner = op.get("operation") or {}
        gen_id = inner.get("name") or inner.get("operationName") or ""
        if not gen_id:
            logger.warning("inflate: op missing name: %s", json.dumps(op)[:200])
            continue
        out.append({
            "prompt": prompt,
            "gen_id": str(gen_id),
            "submit_ts": submit_ts,
            "calls_before": calls_before,
            "batch_resp_before": batch_resp_before,
            "project_id": primer.get("project_id", ""),
            "project_url": primer.get("project_url", ""),
        })
    return out


def _job_for(prompt: str, aspect_ratio: str) -> dict:
    return {
        "id": "_inflate_primer",
        "type": "text-to-video",
        "prompt": prompt,
        "profile": "",
        "job_level": 1,
        "aspect_ratio": aspect_ratio,
    }


def _record(prompt: str, primer: dict) -> dict:
    return {
        "prompt": prompt,
        "gen_id": primer["gen_id"],
        "submit_ts": primer["submit_ts"],
        "calls_before": primer["calls_before"],
        "batch_resp_before": primer.get("batch_resp_before", 0),
        "project_id": primer.get("project_id", ""),
        "project_url": primer.get("project_url", ""),
    }


def _latest_inflated_response(client, *, expected: int) -> dict | None:
    """Return the most recent batch-submit response whose operations list
    has at least ``expected`` entries — that's our inflated response."""
    for entry in reversed(getattr(client, "_batch_responses", []) or []):
        url_l = (entry.get("url", "") or "").lower()
        if not any(hint in url_l for hint in _BATCH_GEN_URL_HINTS):
            continue
        body = entry.get("body")
        if not isinstance(body, dict):
            continue
        ops = body.get("operations") or []
        if len(ops) >= expected:
            return body
    return None
