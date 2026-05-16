"""Reverse-API helpers for Flow camera-move generation."""

from __future__ import annotations

from typing import Any

from flow.operations._l2_api_common import (
    PathCandidate,
    _mint_recaptcha_token,
    clear_l2_capture,
    get_l2_request_template,
    install_l2_request_capture,
    replay_l2_via_api,
    set_string_field,
)

_CAMERA_TEMPLATE_ATTR = "_camera_request_template"
_CAMERA_LISTENER_ATTR = "_camera_request_capture_listener"
_CAMERA_URL_HINTS = (
    "batchasyncgeneratevideoreshootvideo",
    "batchasyncgeneratevideocameramove",
    "reshootvideo",
    "cameramove",
)
_PARENT_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("videoReshootInput", "sourceMedia", "name"),
    ("videoReshootInput", "sourceMediaName"),
    ("videoReshootInput", "sourceMediaId"),
    ("videoReshootInput", "parentMediaId"),
    ("videoCameraMoveInput", "sourceMedia", "name"),
    ("videoCameraMoveInput", "sourceMediaName"),
    ("videoCameraMoveInput", "sourceMediaId"),
    ("videoCameraMoveInput", "parentMediaId"),
    ("cameraInput", "sourceMedia", "name"),
    ("cameraInput", "sourceMediaName"),
    ("cameraInput", "sourceMediaId"),
    ("cameraInput", "parentMediaId"),
    ("sourceMedia", "name"),
    ("sourceMediaName",),
    ("sourceMediaId",),
    ("parentMediaId",),
)
_DIRECTION_FIELD_CANDIDATES: tuple[PathCandidate, ...] = (
    ("cameraInput", "direction"),
    ("cameraInput", "preset"),
    ("cameraInput", "cameraPreset"),
    ("videoReshootInput", "cameraInput", "direction"),
    ("videoReshootInput", "cameraInput", "preset"),
    ("videoReshootInput", "cameraInput", "cameraPreset"),
    ("videoReshootInput", "direction"),
    ("videoReshootInput", "preset"),
    ("videoReshootInput", "cameraPreset"),
    ("videoCameraMoveInput", "cameraInput", "direction"),
    ("videoCameraMoveInput", "cameraInput", "preset"),
    ("videoCameraMoveInput", "direction"),
    ("videoCameraMoveInput", "preset"),
    ("direction",),
    ("preset",),
    ("cameraPreset",),
)
_DIRECTION_FALLBACK_KEYS = (
    "direction",
    "preset",
    "cameraPreset",
    "cameraMovePreset",
)


def install_camera_request_capture(client) -> None:
    """Capture latest Flow camera-move POST template."""
    install_l2_request_capture(
        client,
        template_attr=_CAMERA_TEMPLATE_ATTR,
        listener_attr=_CAMERA_LISTENER_ATTR,
        url_hints=_CAMERA_URL_HINTS,
        operation_label="camera",
    )


def get_camera_request_template(client) -> dict | None:
    return get_l2_request_template(client, _CAMERA_TEMPLATE_ATTR)


async def replay_camera_via_api(client, parent_media_id: str, direction: str) -> str:
    """Replay captured camera-move POST with new parent media + preset."""
    return await replay_l2_via_api(
        client,
        template=get_camera_request_template(client),
        parent_media_id=parent_media_id,
        parent_field_candidates=_PARENT_FIELD_CANDIDATES,
        apply_operation_fields=lambda request, body: _set_camera_fields(request, direction),
        caller="replay_camera_via_api",
        mint_recaptcha_token=_mint_recaptcha_token,
    )


def clear_camera_capture(client) -> None:
    clear_l2_capture(client, _CAMERA_TEMPLATE_ATTR)


def _set_camera_fields(request: dict[str, Any], direction: str) -> dict[str, str]:
    direction_path = set_string_field(
        request,
        direction,
        field_candidates=_DIRECTION_FIELD_CANDIDATES,
        fallback_keys=_DIRECTION_FALLBACK_KEYS,
        caller="replay_camera_via_api",
        field_label="direction",
    )
    return {"direction": direction_path}
