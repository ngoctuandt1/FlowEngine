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
from flow.model_selector import canonicalize_video_model_key

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
_MODEL_FIELD_KEYS = ("videoModelKey", "video_model_key", "modelKey", "modelName", "model")


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
    *,
    model: str | None = None,
    free_mode: bool = True,
) -> str:
    """Replay captured insert-object POST with parent media, prompt, bbox, and model."""
    model_key = (
        canonicalize_video_model_key(model, free_mode=free_mode)
        if model is not None
        else None
    )
    return await replay_l2_via_api(
        client,
        template=get_insert_request_template(client),
        parent_media_id=parent_media_id,
        parent_field_candidates=_PARENT_FIELD_CANDIDATES,
        apply_operation_fields=lambda request, body: _set_insert_fields(
            request, prompt, bbox, model_key=model_key
        ),
        caller="replay_insert_via_api",
        mint_recaptcha_token=_mint_recaptcha_token,
    )


def clear_insert_capture(client) -> None:
    clear_l2_capture(client, _INSERT_TEMPLATE_ATTR)


def _set_insert_fields(
    request: dict[str, Any], prompt: str, bbox: dict[str, Any], *, model_key: str | None = None
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
    changed_paths = {"prompt": prompt_path, "bbox": bbox_path}
    if model_key is not None:
        changed_paths["model"] = _set_insert_model_key(request, model_key)
    return changed_paths


def _set_insert_model_key(request: dict[str, Any], model_key: str) -> str:
    paths = _set_model_key_fields(request, model_key)
    if not paths:
        raise RuntimeError(
            "replay_insert_via_api: video model key field not found in captured template"
        )
    if len(paths) == 1:
        return paths[0]
    return f"[{len(paths)} locations]: {', '.join(paths)}"


def _set_model_key_fields(
    target: Any, model_key: str, path: str = "requests[0]"
) -> list[str]:
    paths: list[str] = []
    if isinstance(target, dict):
        for key in _MODEL_FIELD_KEYS:
            value = target.get(key)
            if isinstance(value, str):
                target[key] = model_key
                paths.append(f"{path}.{key}")
        for key, child in target.items():
            paths.extend(_set_model_key_fields(child, model_key, f"{path}.{key}"))
    elif isinstance(target, list):
        for index, item in enumerate(target):
            paths.extend(_set_model_key_fields(item, model_key, f"{path}[{index}]"))
    return paths
