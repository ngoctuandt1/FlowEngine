"""Reverse-API helpers for Flow text-to-video generation."""

from __future__ import annotations

import logging
from typing import Any

from flow.operations._l1_inflate_batch import submit_l1_batch_via_inflate
from flow.operations._l1_status_poll import download_via_url, poll_status_via_api

logger = logging.getLogger(__name__)

_T2V_GEN_URL_HINTS = (
    "batchasyncgeneratevideotext",
    "v1/video:batchasyncgenerate",
)


def install_t2v_request_capture(client) -> None:
    """Capture latest Flow batchAsyncGenerateVideoText POST template."""
    page = getattr(client, "page", None)
    if page is None:
        client._t2v_request_template = None
        return

    previous = getattr(client, "_t2v_request_capture_listener", None)
    if previous is not None:
        _remove_page_listener(page, "request", previous)

    def _on_request(request) -> None:
        try:
            url = request.url or ""
            method = request.method or ""
        except Exception:
            return
        if method.upper() != "POST" or not _is_t2v_generate_url(url):
            return
        try:
            headers = dict(request.headers)
        except Exception:
            headers = {}
        try:
            post_data = request.post_data
        except Exception:
            post_data = None
        client._t2v_request_template = {
            "url": url,
            "headers": headers,
            "post_data": post_data,
            "anchored_parent": None,
        }
        logger.info(
            "Captured Flow t2v reverseAPI template: %s (anchored_parent=<none>)",
            url,
        )

    page.on("request", _on_request)
    client._t2v_request_capture_listener = _on_request


def get_t2v_request_template(client) -> dict | None:
    template = getattr(client, "_t2v_request_template", None)
    return template if isinstance(template, dict) else None


async def replay_t2v_via_inflate(client, prompts: list[str]) -> list[str]:
    """Submit text-to-video prompts through the L1 inflate-batch path.

    Direct ``batchAsyncGenerateVideoText`` replay is blocked by Flow's
    reCAPTCHA v3 evaluation. Live callers must trigger a real UI submit first;
    the inflate path piggybacks on that browser/user-validated submit instead
    of minting a programmatic low-score token. This wrapper only returns the
    gen_ids from Flow's accepted submits; callers should pass them to
    ``poll_status_via_api(client, gen_ids=[...])`` and then download completed
    media via ``download_via_url``.
    """
    prompt_list = _validate_prompts(prompts)
    if not prompt_list:
        return []

    records = await submit_l1_batch_via_inflate(client, prompts=prompt_list)
    if not isinstance(records, list):
        raise RuntimeError("replay_t2v_via_inflate: inflate helper returned non-list")
    if len(records) != len(prompt_list):
        raise RuntimeError(
            f"replay_t2v_via_inflate: requested {len(prompt_list)} prompts "
            f"but got {len(records)} submissions"
        )

    gen_ids: list[str] = []
    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            raise RuntimeError(
                f"replay_t2v_via_inflate: submission {idx} was not a dict"
            )
        gen_id = str(record.get("gen_id") or "")
        if not gen_id:
            raise RuntimeError(
                f"replay_t2v_via_inflate: submission {idx} missing gen_id"
            )
        gen_ids.append(gen_id)
    return gen_ids


async def poll_t2v_status_via_api(
    client,
    *,
    gen_ids: list[str],
    project_id: str | None = None,
    poll_interval_sec: float = 6.0,
    hard_timeout_sec: float = 900.0,
) -> dict[str, dict[str, Any]]:
    """Poll replayed T2V gen_ids using the existing L1 status API helper."""
    if not gen_ids:
        return {}
    return await poll_status_via_api(
        client,
        gen_ids=gen_ids,
        project_id=project_id,
        poll_interval_sec=poll_interval_sec,
        hard_timeout_sec=hard_timeout_sec,
    )


def clear_t2v_capture(client) -> None:
    client._t2v_request_template = None


def _is_t2v_generate_url(url: str) -> bool:
    url_l = (url or "").lower()
    return all(hint in url_l for hint in _T2V_GEN_URL_HINTS)


def _validate_prompts(prompts: list[str]) -> list[str]:
    if not isinstance(prompts, list):
        raise TypeError("replay_t2v_via_inflate: prompts must be a list[str]")
    for idx, prompt in enumerate(prompts):
        if not isinstance(prompt, str):
            raise TypeError(
                f"replay_t2v_via_inflate: prompts[{idx}] must be str"
            )
    return list(prompts)


def _remove_page_listener(page: Any, event_name: str, callback: Any) -> None:
    for method_name in ("remove_listener", "off"):
        method = getattr(page, method_name, None)
        if callable(method):
            try:
                method(event_name, callback)
                return
            except Exception:
                continue
    listeners = getattr(page, "listeners", None)
    if isinstance(listeners, dict):
        callbacks = listeners.get(event_name)
        if isinstance(callbacks, list):
            listeners[event_name] = [item for item in callbacks if item is not callback]


__all__ = [
    "clear_t2v_capture",
    "download_via_url",
    "get_t2v_request_template",
    "install_t2v_request_capture",
    "poll_status_via_api",
    "poll_t2v_status_via_api",
    "replay_t2v_via_inflate",
]
