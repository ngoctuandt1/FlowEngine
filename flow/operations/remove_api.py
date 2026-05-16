"""Reverse-API helpers for Flow remove-object generation."""

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
)

_REMOVE_TEMPLATE_ATTR = "_remove_request_template"
_REMOVE_LISTENER_ATTR = "_remove_request_capture_listener"
_REMOVE_URL_HINTS = (
    "batchasyncgeneratevideoobjectremoval",
    "batchasyncgeneratevideoremoveobject",
    "objectremoval",
    "removeobject",
)
_PARENT_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoObjectRemovalInput", "sourceMedia", "name"),
    ("videoObjectRemovalInput", "sourceMediaName"),
    ("videoObjectRemovalInput", "sourceMediaId"),
    ("videoObjectRemovalInput", "parentMediaId"),
    ("videoRemoveObjectInput", "sourceMedia", "name"),
    ("videoRemoveObjectInput", "sourceMediaName"),
    ("videoRemoveObjectInput", "sourceMediaId"),
    ("videoRemoveObjectInput", "parentMediaId"),
    ("removeObjectInput", "sourceMedia", "name"),
    ("removeObjectInput", "sourceMediaName"),
    ("removeObjectInput", "sourceMediaId"),
    ("removeObjectInput", "parentMediaId"),
    ("sourceMedia", "name"),
    ("sourceMediaName",),
    ("sourceMediaId",),
    ("parentMediaId",),
)
_BBOX_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoObjectRemovalInput", "bbox"),
    ("videoObjectRemovalInput", "boundingBox"),
    ("videoObjectRemovalInput", "mask", "bbox"),
    ("videoObjectRemovalInput", "mask", "boundingBox"),
    ("videoObjectRemovalInput", "selection", "bbox"),
    ("videoObjectRemovalInput", "selection", "boundingBox"),
    ("videoRemoveObjectInput", "bbox"),
    ("videoRemoveObjectInput", "boundingBox"),
    ("videoRemoveObjectInput", "mask", "bbox"),
    ("videoRemoveObjectInput", "selection", "bbox"),
    ("removeObjectInput", "bbox"),
    ("removeObjectInput", "boundingBox"),
    ("removeObjectInput", "mask", "bbox"),
    ("removeObjectInput", "selection", "bbox"),
    ("bbox",),
    ("boundingBox",),
    ("mask", "bbox"),
    ("selection", "bbox"),
)


def install_remove_request_capture(client) -> None:
    """Capture latest Flow remove-object POST template."""
    install_l2_request_capture(
        client,
        template_attr=_REMOVE_TEMPLATE_ATTR,
        listener_attr=_REMOVE_LISTENER_ATTR,
        url_hints=_REMOVE_URL_HINTS,
        operation_label="remove",
    )


def get_remove_request_template(client) -> dict | None:
    return get_l2_request_template(client, _REMOVE_TEMPLATE_ATTR)


async def replay_remove_via_api(
    client,
    parent_media_id: str,
    bbox: dict[str, Any],
) -> str:
    """Replay captured remove-object POST with parent media and bbox."""
    return await replay_l2_via_api(
        client,
        template=get_remove_request_template(client),
        parent_media_id=parent_media_id,
        parent_field_candidates=_PARENT_FIELD_CANDIDATES,
        apply_operation_fields=lambda request, body: _set_remove_fields(request, bbox),
        caller="replay_remove_via_api",
        mint_recaptcha_token=_mint_recaptcha_token,
    )


def clear_remove_capture(client) -> None:
    clear_l2_capture(client, _REMOVE_TEMPLATE_ATTR)


def _set_remove_fields(request: dict[str, Any], bbox: dict[str, Any]) -> dict[str, str]:
    bbox_path = set_bbox_field(
        request,
        bbox,
        field_candidates=_BBOX_FIELD_CANDIDATES,
        caller="replay_remove_via_api",
    )
    return {"bbox": bbox_path}
