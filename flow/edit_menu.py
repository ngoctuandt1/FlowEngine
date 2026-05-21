"""Reusable edit-view overflow menu helpers."""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_AI_LOCATOR_TRUE_VALUES = {"1", "true", "yes", "on"}
_EDIT_VIEW_KEBAB_AI_CACHE_KEY = "flow.edit_menu.edit_view_kebab"

EDIT_VIEW_KEBAB_SELECTORS: tuple[str, ...] = (
    "button[aria-haspopup='menu']:has(i:text-is('more_vert'))",
    "button[aria-haspopup='menu']:has(i:text-is('more_horiz'))",
    "button[aria-label='More options']",
    "button[aria-label='More actions']",
    "button[title='More options']",
    "button[title='More actions']",
    "[role='button'][aria-haspopup='menu']:has(i:text-is('more_vert'))",
    "[role='button'][aria-haspopup='menu']:has(i:text-is('more_horiz'))",
)


def _ai_locator_enabled() -> bool:
    return os.getenv("FLOW_AI_LOCATOR_ENABLED", "false").lower() in _AI_LOCATOR_TRUE_VALUES


async def _click_ai_locator_result(page, result, *, timeout_ms: int) -> bool:
    if result.selector:
        await page.locator(result.selector).first.click(timeout=timeout_ms)
        return True
    if result.coordinates:
        await page.mouse.click(*result.coordinates)
        return True
    return False


async def open_edit_view_kebab(page, *, timeout_ms: int = 3000) -> bool:
    """Open the edit-view kebab menu for future share/trash operations."""
    for selector in EDIT_VIEW_KEBAB_SELECTORS:
        try:
            button = page.locator(selector).first
            if await button.is_visible(timeout=1500):
                await button.click(timeout=timeout_ms)
                logger.info("Opened edit-view kebab via: %s", selector)
                return True
        except Exception:
            continue

    if not _ai_locator_enabled():
        return False

    from flow.ai_locator import ai_locate

    result = await ai_locate(
        page,
        (
            "Find the visible overflow/kebab menu button in the Google Flow edit view. "
            "Target only the button that opens share/trash/more actions for the current "
            "video; do not choose composer settings chips, mode buttons, model controls, "
            "download buttons, or submit controls."
        ),
        candidates=(),
        cache_key=_EDIT_VIEW_KEBAB_AI_CACHE_KEY,
    )
    try:
        if await _click_ai_locator_result(page, result, timeout_ms=timeout_ms):
            logger.info("Opened edit-view kebab via AI locator")
            return True
    except Exception as exc:
        logger.debug("AI locator edit-view kebab click failed: %s", exc)
    return False
