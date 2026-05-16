"""Reverse-API helpers for Flow frames-to-video generation."""

from __future__ import annotations

import json
import logging
from typing import Any

from flow.navigation import extract_project_id
from flow.operations._video_l1_api_common import (
    apply_frame_refs,
    body_mentions,
    clear_request_template,
    extract_frame_anchors,
    extract_generated_media_ids,
    get_request_template,
    install_l1_video_request_capture,
    mint_recaptcha_token,
    parse_post_data_value,
    prepare_single_request_body,
    replay_headers,
    require_page,
    response_status,
    response_text,
    set_prompt_text,
    set_replay_recaptcha_token,
    submit_replay_body_via_inflate_piggyback,
    upload_image_via_api,
)

logger = logging.getLogger(__name__)

_F2V_TEMPLATE_ATTR = "_f2v_request_template"
_F2V_LISTENER_ATTR = "_f2v_request_capture_listener"
_F2V_STRONG_URL_HINTS = (
    "batchasyncgeneratevideofromframes",
    "videofromframes",
    "fromframes",
)
_F2V_WEAK_URL_HINTS = (
    "frames",
)
_F2V_BODY_HINTS = ("startframe", "endframe", "videoframesinput", "frame")


def install_f2v_request_capture(client) -> None:
    """Capture latest Flow frames-to-video submit POST template."""
    install_l1_video_request_capture(
        client,
        template_attr=_F2V_TEMPLATE_ATTR,
        listener_attr=_F2V_LISTENER_ATTR,
        operation_label="f2v",
        url_matcher=_is_f2v_generate_url,
        anchor_extractor=extract_frame_anchors,
    )


def get_f2v_request_template(client) -> dict[str, Any] | None:
    return get_request_template(client, _F2V_TEMPLATE_ATTR)


def clear_f2v_capture(client) -> None:
    clear_request_template(client, _F2V_TEMPLATE_ATTR)


async def replay_f2v_via_inflate(
    client,
    prompt: str,
    start_frame_path: str,
    end_frame_path: str,
) -> str:
    """Replay captured frames-to-video template with freshly uploaded frames."""
    template = get_f2v_request_template(client)
    if template is None:
        raise RuntimeError("replay_f2v_via_inflate: no captured template")

    page = require_page(client)
    body, request = prepare_single_request_body(template, operation_label="replay_f2v_via_inflate")
    project_id = _project_id_from_body_or_url(body, getattr(page, "url", ""))

    start_ref = await upload_image_via_api(
        client,
        start_frame_path,
        headers=template.get("headers") or {},
        project_id=project_id,
        caller="replay_f2v_via_inflate start frame",
    )
    end_ref = await upload_image_via_api(
        client,
        end_frame_path,
        headers=template.get("headers") or {},
        project_id=project_id,
        caller="replay_f2v_via_inflate end frame",
    )

    touched_frame_paths = apply_frame_refs(
        body,
        request,
        start_ref=start_ref,
        end_ref=end_ref,
        anchors=template.get("anchors") if isinstance(template.get("anchors"), dict) else None,
        operation_label="replay_f2v_via_inflate",
    )
    prompt_path = set_prompt_text(request, prompt)
    logger.info("replay_f2v_via_inflate: rewrote frames at %s", touched_frame_paths)
    logger.info("replay_f2v_via_inflate: rewrote prompt at %s", prompt_path)

    piggyback_gen_id = await submit_replay_body_via_inflate_piggyback(
        client,
        url=template["url"],
        body=body,
        prompt=prompt,
        operation_label="replay_f2v_via_inflate",
    )
    if piggyback_gen_id:
        return piggyback_gen_id

    recaptcha_token = await mint_recaptcha_token(page, caller="replay_f2v_via_inflate")
    if recaptcha_token:
        set_replay_recaptcha_token(body, recaptcha_token)
    else:
        logger.warning(
            "replay_f2v_via_inflate: reCAPTCHA mint returned empty token; using captured token if present"
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
        raise RuntimeError(f"replay_f2v_via_inflate failed with HTTP {status}: {text[:500]}")

    try:
        data = await response.json()
    except Exception as exc:
        raise RuntimeError("replay_f2v_via_inflate: response was not JSON") from exc

    media_ids = extract_generated_media_ids(data)
    if len(media_ids) != 1:
        raise RuntimeError(f"replay_f2v_via_inflate: requested 1 media but got {len(media_ids)}")
    return media_ids[0]


def _is_f2v_generate_url(url: str, post_data: Any = None) -> bool:
    url_l = (url or "").lower()
    if any(hint in url_l for hint in _F2V_STRONG_URL_HINTS):
        return True
    is_generate_url = "v1/video:batchasyncgenerate" in url_l or ":generatevideo" in url_l
    if is_generate_url and any(hint in url_l for hint in _F2V_WEAK_URL_HINTS):
        logger.warning("f2v capture matched weak URL hint: %s", url)
        return True
    if not is_generate_url:
        return False
    if body_mentions(parse_post_data_value(post_data), _F2V_BODY_HINTS):
        logger.warning("f2v capture matched generic batchAsyncGenerate URL via frame body hints: %s", url)
        return True
    return False


def _project_id_from_body_or_url(body: dict[str, Any], url: str) -> str:
    client_context = body.get("clientContext")
    if isinstance(client_context, dict):
        project_id = client_context.get("projectId")
        if project_id:
            return str(project_id)
    return extract_project_id(url) or ""
