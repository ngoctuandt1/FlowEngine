"""Multi-prompt L1 batch via N sequential composer submits.

Historical context: this module was previously a ``page.route()`` body
rewriter that turned 1 UI submit into ``requests: [N]`` to coax Flow
into returning N gen_ids in a single call. MCP probe of Flow's native
``x3`` chip (2026-05-05) showed the UI itself fires N **separate**
POSTs to ``v1/video:batchAsyncGenerateVideoText`` — same ``batchId``,
different ``seed``, ``requests: [1]`` each, ~160 ms apart. The endpoint
treats ``requests`` as effectively a 1-entry slot; ``requests: [N>1]``
is undefined behaviour (server sometimes returns N ops, sometimes 1).
That non-determinism plus a separate gen-time fail mode produced the
~50 % flake user observed on chains v6/v9/v10.

Strategy now matches Flow's native pattern: drive N composer cycles
sequentially. First submit creates the project; subsequent submits
reuse the open composer. Wall-time penalty is ~10 s per extra prompt
(composer cycle), bought back in determinism.

The function name ``submit_l1_batch_via_inflate`` is preserved so
callers (live_verify_batch_l1_inflate, live_verify_chain_l1_l2_l3,
live_verify_mass_gen) need no changes.
"""

from __future__ import annotations

import logging
from typing import Any

from flow.operations._l1_batch import (
    install_batch_response_capture,
    submit_generate_l1,
)
from flow.recaptcha import RecaptchaError

logger = logging.getLogger(__name__)


_DEFAULT_VIDEO_MODEL_KEY = "veo_3_1_t2v_lite_low_priority"


async def submit_l1_batch_via_inflate(
    client,
    *,
    prompts: list[str],
    aspect_ratio: str = "16:9",
    video_model_key: str | None = None,
    intercept_timeout_sec: float = 30.0,  # kept for signature compat; unused
) -> list[dict]:
    """Submit N L1 text-to-video prompts via N sequential composer cycles.

    Returns one dict per prompt in input order::

        {prompt, gen_id, submit_ts, calls_before, batch_resp_before,
         project_id, project_url}

    Empty list on total failure. Single-prompt input is one composer
    cycle. ``intercept_timeout_sec`` is accepted but unused (legacy
    signature).
    """
    del intercept_timeout_sec  # legacy arg; route-rewrite path retired
    del video_model_key  # composer chip selects model; arg kept for compat
    if not prompts:
        return []

    install_batch_response_capture(client)

    out: list[dict] = []
    project_url: str = ""
    for idx, prompt in enumerate(prompts):
        try:
            sub = await submit_generate_l1(
                client,
                _job_for(prompt, aspect_ratio, idx),
                project_already_open=(idx > 0),
            )
        except RecaptchaError:
            # Profile-burn signal — must propagate so the outer
            # ``with_recaptcha_recovery`` wrapper can swap profiles
            # and retry. Swallowing it here masked a 403 burst on
            # 2026-05-05 chain-deep verify (recovery never fired).
            raise
        except Exception as exc:
            logger.exception(
                "submit %d/%d (%r) failed: %s",
                idx + 1, len(prompts), prompt[:60], exc,
            )
            # Per-prompt non-recovery failure — continue with the
            # next prompt; partial result is better than total loss.
            # Caller checks len(out) vs len(prompts).
            continue

        if idx == 0:
            project_url = sub.get("project_url", "") or project_url
            logger.info(
                "sequential submit: project opened at %s",
                project_url[:80],
            )

        out.append(_record(prompt, sub))
        logger.info(
            "sequential submit %d/%d: gen=%s",
            idx + 1, len(prompts), str(sub.get("gen_id", ""))[-12:],
        )

    if len(out) < len(prompts):
        logger.warning(
            "sequential submit: %d/%d succeeded",
            len(out), len(prompts),
        )
    return out


def _job_for(prompt: str, aspect_ratio: str, idx: int) -> dict:
    return {
        "id": f"_seq_l1_{idx}",
        "type": "text-to-video",
        "prompt": prompt,
        "profile": "",
        "job_level": 1,
        "aspect_ratio": aspect_ratio,
    }


def _record(prompt: str, sub: dict) -> dict:
    return {
        "prompt": prompt,
        "gen_id": str(sub.get("gen_id", "")),
        "submit_ts": sub.get("submit_ts", 0.0),
        "calls_before": sub.get("calls_before", 0),
        "batch_resp_before": sub.get("batch_resp_before", 0),
        "project_id": sub.get("project_id", ""),
        "project_url": sub.get("project_url", ""),
    }
