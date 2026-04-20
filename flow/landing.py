"""Recovery helpers for the Flow marketing landing page."""

from __future__ import annotations

import asyncio
import time

_CREATE_WITH_FLOW_SELECTORS = (
    "button:has-text('Create with Flow')",
    "[role='button']:has-text('Create with Flow')",
    "a:has-text('Create with Flow')",
)


def is_flow_landing_url(url: str) -> bool:
    current = (url or "").lower()
    return (
        "labs.google/fx" in current
        and "/project/" not in current
        and "/edit/" not in current
        and "/tools/flow" in current
    )


async def _find_landing_cta(page):
    for selector in _CREATE_WITH_FLOW_SELECTORS:
        try:
            candidate = page.locator(selector).first
            if await candidate.is_visible(timeout=1500):
                return candidate
        except Exception:
            continue
    return None


async def recover_from_flow_landing(
    page,
    logger,
    target_url: str,
    timeout_sec: float = 12.0,
) -> bool:
    """Click the landing CTA and wait for Flow to resume project/editor routing.

    This recovery must trigger even when the address bar still shows a
    `/project/...` or `/edit/...` URL. In the broken state, Flow sometimes
    renders the marketing landing DOM on top of the intended route, and the
    visible "Create with Flow" CTA is the reliable signal.
    """
    cta = await _find_landing_cta(page)
    if cta is None:
        return False

    logger.info(
        "Flow landing detected — clicking CTA to resume target: %s",
        target_url[:100],
    )

    try:
        await cta.click(timeout=5000)
    except Exception as exc:
        logger.warning("Flow landing CTA click failed: %s", exc)
        return False

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        current = page.url
        if "/project/" in current or "/edit/" in current:
            logger.info("Landing recovery complete: %s", current[:100])
            await asyncio.sleep(1)
            return True
        await asyncio.sleep(0.2)

    logger.warning(
        "Flow landing CTA did not resume project/editor view within %.0fs. URL=%s target=%s",
        timeout_sec,
        page.url[:100],
        target_url[:100],
    )
    return False
