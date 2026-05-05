"""Reverse-API L1 batch submit — POST N text-to-video ops in 1 HTTP call.

Discovered live 2026-05-04 by probing Flow's submit endpoint:
``v1/video:batchAsyncGenerateVideoText`` accepts a ``requests`` LIST with
N entries. One HTTP call submits N generations natively, skipping the
composer UI for jobs 2..N entirely (still need the composer for job 1
to seed the project + auth/recaptcha context).

Why this exists alongside the UI batch path:

* **Test speed** — 5 batched submits via UI takes ~30s of composer
  navigation per job. Reverse-API does the whole submit in one HTTP
  POST (~1-2s).
* **Mass-gen** — users wanting to fan out 10+ L1 jobs benefit from
  Flow's native multi-request batching (no Chrome UI bottleneck on
  the submit side; downloads still per-tile).

We REUSE the page's auth token + recaptcha context — they expire
quickly so we always derive them from a recent UI submit captured by
:func:`flow.operations._l1_batch.install_batch_response_capture`.
"""

from __future__ import annotations

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
_DEFAULT_ASPECT_RATIO = "VIDEO_ASPECT_RATIO_LANDSCAPE"


def _aspect_ratio_enum(ratio: str) -> str:
    if ratio == "9:16":
        return "VIDEO_ASPECT_RATIO_PORTRAIT"
    return _DEFAULT_ASPECT_RATIO


async def submit_l1_batch_via_api(
    client,
    *,
    prompts: list[str],
    aspect_ratio: str = "16:9",
    video_model_key: str | None = None,
    prime_with_ui_submit: bool = True,
) -> list[dict]:
    """Submit N L1 t2v in ONE HTTP POST.

    Returns ``[{prompt, gen_id, submit_ts, calls_before, batch_resp_before}, ...]``
    in input order. Each entry maps to one generation that the caller
    can feed into :func:`wait_for_all_l1_gens` and the tile-pinned
    download path.

    Strategy:
      1. (optional) ``prime_with_ui_submit`` — if no recent submit
         response is captured yet, drive ONE UI submit through
         :func:`submit_generate_l1` to (a) create the project, (b)
         populate ``client._batch_requests`` with auth headers +
         recaptchaContext token, (c) populate
         ``client._batch_responses`` with a project_id we can extract.
      2. Reuse the captured request as a template; replace ``requests``
         with N synthesized entries (one per prompt) and fresh
         ``batchId``.
      3. POST via ``page.context.request.post(...)`` — Playwright
         carries the page's cookies + headers, so the call is
         indistinguishable from the UI's own submit.
      4. Parse the response body's ``operations`` array → N gen_ids,
         in submission order.

    Returns an empty list on POST failure or schema mismatch.
    """
    if not prompts:
        return []
    page = client.page
    install_batch_response_capture(client)

    # Step 1: prime so we have a captured request template.
    if prime_with_ui_submit and not getattr(client, "_batch_requests", None):
        primer_job = {
            "id": "_api_primer",
            "type": "text-to-video",
            "prompt": prompts[0],
            "profile": getattr(client, "profile_name", ""),
            "job_level": 1,
            "aspect_ratio": aspect_ratio,
        }
        primer = await submit_generate_l1(
            client, primer_job, project_already_open=False,
        )
        # Primer's submit IS the first generation — record it as result[0].
        primer_results = [{
            "prompt": prompts[0],
            "gen_id": primer["gen_id"],
            "submit_ts": primer["submit_ts"],
            "calls_before": primer["calls_before"],
            "batch_resp_before": primer.get("batch_resp_before", 0),
            "project_id": primer.get("project_id", ""),
            "project_url": primer.get("project_url", ""),
        }]
        if len(prompts) == 1:
            return primer_results
        remaining = prompts[1:]
    else:
        primer_results = []
        remaining = list(prompts)

    template = _latest_submit_request(client)
    if template is None:
        logger.error(
            "submit_l1_batch_via_api: no captured submit request to use as template"
        )
        return primer_results

    parsed = _parse_post_data(template)
    if not parsed:
        logger.error(
            "submit_l1_batch_via_api: captured request has no usable JSON post_data"
        )
        return primer_results

    # reCAPTCHA tokens are single-use. Live verify 2026-05-04 confirmed
    # Flow returns 403 "reCAPTCHA evaluation failed" when the captured
    # token is reused on a second POST. Call grecaptcha.enterprise.execute
    # in the page's JS context to mint a fresh action-bound token before
    # we send the batched request.
    fresh_token = await _fresh_recaptcha_token(page)
    if fresh_token:
        ctx = parsed.get("clientContext") or {}
        rc = dict(ctx.get("recaptchaContext") or {})
        rc["token"] = fresh_token
        rc.setdefault("applicationType", "RECAPTCHA_APPLICATION_TYPE_WEB")
        ctx = dict(ctx)
        ctx["recaptchaContext"] = rc
        parsed["clientContext"] = ctx
    else:
        logger.warning(
            "submit_l1_batch_via_api: failed to mint fresh recaptcha token; "
            "retrying with captured (likely single-use) token"
        )

    project_id = (
        parsed.get("clientContext", {}).get("projectId")
        or _project_id_from_url(page.url)
    )
    if not project_id:
        logger.error("submit_l1_batch_via_api: cannot resolve projectId")
        return primer_results

    aspect_enum = _aspect_ratio_enum(aspect_ratio)
    model_key = video_model_key or _video_model_key_from_template(parsed)
    new_requests = [
        {
            "aspectRatio": aspect_enum,
            "seed": random.randint(1, 2_000_000_000),
            "textInput": {"structuredPrompt": {"parts": [{"text": p}]}},
            "videoModelKey": model_key,
            "metadata": {},
        }
        for p in remaining
    ]
    body = dict(parsed)
    body.setdefault("mediaGenerationContext", {})
    body["mediaGenerationContext"] = {
        **body["mediaGenerationContext"],
        "batchId": str(uuid.uuid4()),
    }
    body["requests"] = new_requests

    headers = _filter_request_headers(template.get("headers") or {})
    url = template["url"]

    calls_before = len(getattr(client, "_calls", []))
    batch_resp_before = len(getattr(client, "_batch_responses", []) or [])
    submit_ts = time.time()

    logger.info(
        "submit_l1_batch_via_api: POST %d ops to %s (project=%s)",
        len(new_requests), url, project_id[:12],
    )
    try:
        resp = await page.context.request.post(
            url,
            data=json.dumps(body),
            headers=headers,
            timeout=30000,
        )
    except Exception as exc:
        logger.exception("submit_l1_batch_via_api: POST failed: %s", exc)
        return primer_results

    if resp.status != 200:
        logger.error(
            "submit_l1_batch_via_api: HTTP %d from Flow: %s",
            resp.status, (await resp.text())[:200],
        )
        return primer_results

    try:
        data = await resp.json()
    except Exception:
        logger.exception("submit_l1_batch_via_api: response not JSON")
        return primer_results

    ops = data.get("operations") or []
    if len(ops) != len(remaining):
        logger.warning(
            "submit_l1_batch_via_api: expected %d operations, got %d",
            len(remaining), len(ops),
        )

    extra_results = []
    for prompt, op in zip(remaining, ops):
        op_inner = op.get("operation") or {}
        gen_id = op_inner.get("name") or op_inner.get("operationName") or ""
        if not gen_id:
            logger.warning("submit_l1_batch_via_api: missing gen name in op: %s",
                           json.dumps(op)[:200])
            continue
        extra_results.append({
            "prompt": prompt,
            "gen_id": str(gen_id),
            "submit_ts": submit_ts,
            "calls_before": calls_before,
            "batch_resp_before": batch_resp_before,
            "project_id": project_id,
            "project_url": (primer_results[0].get("project_url")
                            if primer_results else page.url),
        })

    return primer_results + extra_results


def _latest_submit_request(client) -> dict | None:
    """Return the most recent captured POST request matching submit URL hints."""
    requests = getattr(client, "_batch_requests", None) or []
    for entry in reversed(requests):
        url_l = (entry.get("url", "") or "").lower()
        if any(hint in url_l for hint in _BATCH_GEN_URL_HINTS):
            if entry.get("method") == "POST" and entry.get("post_data"):
                return entry
    return None


def _parse_post_data(template: dict) -> dict | None:
    raw = template.get("post_data")
    if isinstance(raw, dict):
        return dict(raw)
    if not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _video_model_key_from_template(parsed: dict) -> str:
    reqs = parsed.get("requests") or []
    if reqs and isinstance(reqs[0], dict):
        key = reqs[0].get("videoModelKey")
        if isinstance(key, str) and key:
            return key
    return _DEFAULT_VIDEO_MODEL_KEY


def _project_id_from_url(url: str) -> str:
    # ``/project/<uuid>`` or ``/project/<uuid>/edit/<other>``
    if not url:
        return ""
    parts = url.split("/project/", 1)
    if len(parts) != 2:
        return ""
    rest = parts[1]
    return rest.split("/", 1)[0].split("?", 1)[0]


# Headers we copy from the captured UI submit. Anything else (sec-ch-*,
# accept-*, etc.) is added by Playwright's request context automatically.
_PASSTHROUGH_HEADER_NAMES = frozenset({
    "authorization",
    "content-type",
    "referer",
    "user-agent",
    "x-goog-api-key",
})


def _filter_request_headers(headers: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        if k.lower() in _PASSTHROUGH_HEADER_NAMES:
            out[str(k)] = str(v)
    return out


# Flow's reCAPTCHA Enterprise site key, captured live 2026-05-04 from
# `https://www.google.com/recaptcha/enterprise/reload?k=...`. Stable across
# sessions; if Google rotates it, just re-grab from devtools network tab.
_FLOW_RECAPTCHA_SITE_KEY = "6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV"

# Candidate action names. Flow's bundle binds the recaptcha invocation to
# a specific action string; the token is rejected by the backend if the
# action doesn't match. The first one that produces a token Flow accepts
# wins. ``submit`` is the most generic and is what production Flow uses
# for the t2v composer's submit (verified by tracing
# ``grecaptcha.enterprise.execute`` calls).
_RECAPTCHA_ACTIONS = ["submit", "GENERATE_VIDEO", "videoGenerate", "generate"]


async def _fresh_recaptcha_token(page) -> str | None:
    """Mint a fresh action-bound reCAPTCHA Enterprise token from the page.

    Flow's submit endpoint rejects reused tokens with 403 "reCAPTCHA
    evaluation failed", so each batched API submit must come with its
    own freshly-executed token.
    """
    js = """
    async ({siteKey, actions}) => {
        if (!window.grecaptcha || !grecaptcha.enterprise) {
            return {error: 'grecaptcha.enterprise not loaded'};
        }
        for (const action of actions) {
            try {
                const tok = await grecaptcha.enterprise.execute(
                    siteKey, {action}
                );
                if (tok) return {token: tok, action};
            } catch (e) {
                // try next action
            }
        }
        return {error: 'all actions failed'};
    }
    """
    try:
        result = await page.evaluate(
            js,
            {"siteKey": _FLOW_RECAPTCHA_SITE_KEY,
             "actions": _RECAPTCHA_ACTIONS},
        )
    except Exception as exc:
        logger.warning("recaptcha mint failed: %s", exc)
        return None
    if isinstance(result, dict):
        if result.get("token"):
            logger.info("recaptcha token minted (action=%s, len=%d)",
                        result.get("action"), len(result["token"]))
            return str(result["token"])
        logger.warning("recaptcha mint: %s", result.get("error"))
    return None
