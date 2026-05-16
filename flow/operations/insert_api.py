"""Reverse-API helpers for Flow insert-object generation."""

from __future__ import annotations

from typing import Any

from flow.operations._l2_api_common import (
    PathCandidate,
    _mint_recaptcha_token,
    clear_l2_capture,
    get_l2_request_template,
    install_l2_request_capture,
    replay_l2_via_api,
    set_bbox_field,
    set_string_field,
)

_INSERT_TEMPLATE_ATTR = "_insert_request_template"
_INSERT_LISTENER_ATTR = "_insert_request_capture_listener"
_INSERT_URL_HINTS = (
    "batchasyncgeneratevideoobjectinsertion",
    "batchasyncgeneratevideoinsertobject",
    "objectinsertion",
    "insertobject",
)
_PARENT_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoObjectInsertionInput", "sourceMedia", "name"),
    ("videoObjectInsertionInput", "sourceMediaName"),
    ("videoObjectInsertionInput", "sourceMediaId"),
    ("videoObjectInsertionInput", "parentMediaId"),
    ("videoInsertObjectInput", "sourceMedia", "name"),
    ("videoInsertObjectInput", "sourceMediaName"),
    ("videoInsertObjectInput", "sourceMediaId"),
    ("videoInsertObjectInput", "parentMediaId"),
    ("insertObjectInput", "sourceMedia", "name"),
    ("insertObjectInput", "sourceMediaName"),
    ("insertObjectInput", "sourceMediaId"),
    ("insertObjectInput", "parentMediaId"),
    ("sourceMedia", "name"),
    ("sourceMediaName",),
    ("sourceMediaId",),
    ("parentMediaId",),
)
_PROMPT_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoObjectInsertionInput", "textInput", "structuredPrompt", "parts", 0, "text"),
    ("videoObjectInsertionInput", "structuredPrompt", "parts", 0, "text"),
    ("videoObjectInsertionInput", "textInput", "text"),
    ("videoObjectInsertionInput", "prompt"),
    ("videoInsertObjectInput", "textInput", "structuredPrompt", "parts", 0, "text"),
    ("videoInsertObjectInput", "structuredPrompt", "parts", 0, "text"),
    ("videoInsertObjectInput", "textInput", "text"),
    ("videoInsertObjectInput", "prompt"),
    ("insertObjectInput", "textInput", "structuredPrompt", "parts", 0, "text"),
    ("insertObjectInput", "textInput", "text"),
    ("insertObjectInput", "prompt"),
    ("textInput", "structuredPrompt", "parts", 0, "text"),
    ("structuredPrompt", "parts", 0, "text"),
    ("textInput", "text"),
    ("prompt",),
)
_BBOX_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoObjectInsertionInput", "bbox"),
    ("videoObjectInsertionInput", "boundingBox"),
    ("videoObjectInsertionInput", "mask", "bbox"),
    ("videoObjectInsertionInput", "mask", "boundingBox"),
    ("videoObjectInsertionInput", "selection", "bbox"),
    ("videoObjectInsertionInput", "selection", "boundingBox"),
    ("videoInsertObjectInput", "bbox"),
    ("videoInsertObjectInput", "boundingBox"),
    ("videoInsertObjectInput", "mask", "bbox"),
    ("videoInsertObjectInput", "selection", "bbox"),
    ("insertObjectInput", "bbox"),
    ("insertObjectInput", "boundingBox"),
    ("insertObjectInput", "mask", "bbox"),
    ("insertObjectInput", "selection", "bbox"),
    ("bbox",),
    ("boundingBox",),
    ("mask", "bbox"),
    ("selection", "bbox"),
)
_PROMPT_FALLBACK_KEYS = ("prompt", "text", "description")


def install_insert_request_capture(client) -> None:
    """Capture latest Flow insert-object POST template."""
    install_l2_request_capture(
        client,
        template_attr=_INSERT_TEMPLATE_ATTR,
        listener_attr=_INSERT_LISTENER_ATTR,
        url_hints=_INSERT_URL_HINTS,
        operation_label="insert",
    )


def get_insert_request_template(client) -> dict | None:
    return get_l2_request_template(client, _INSERT_TEMPLATE_ATTR)


async def replay_insert_via_api(
    client,
    parent_media_id: str,
    prompt: str,
    bbox: dict[str, Any],
) -> str:
    """Replay captured insert-object POST with parent media, prompt, and bbox."""
    return await replay_l2_via_api(
        client,
        template=get_insert_request_template(client),
        parent_media_id=parent_media_id,
        parent_field_candidates=_PARENT_FIELD_CANDIDATES,
        apply_operation_fields=lambda request, body: _set_insert_fields(
            request, prompt, bbox
        ),
        caller="replay_insert_via_api",
        mint_recaptcha_token=_mint_recaptcha_token,
    )


def clear_insert_capture(client) -> None:
    clear_l2_capture(client, _INSERT_TEMPLATE_ATTR)


def _set_insert_fields(
    request: dict[str, Any], prompt: str, bbox: dict[str, Any]
) -> dict[str, str]:
    prompt_path = set_string_field(
        request,
        prompt,
        field_candidates=_PROMPT_FIELD_CANDIDATES,
        fallback_keys=_PROMPT_FALLBACK_KEYS,
        caller="replay_insert_via_api",
        field_label="prompt",
    )
    bbox_path = set_bbox_field(
        request,
        bbox,
        field_candidates=_BBOX_FIELD_CANDIDATES,
        caller="replay_insert_via_api",
    )
    return {"prompt": prompt_path, "bbox": bbox_path}
