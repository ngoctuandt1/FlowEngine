"""Recovery helpers for the Flow marketing landing page."""

from __future__ import annotations

import asyncio
import time

# Ordered by specificity. Earlier entries win so the hero CTA is preferred
# over nav / footer shortcuts that share the same "Create with Flow" text
# but resolve to in-page scroll anchors (`href='#capabilities'` etc.) —
# issue #48 evidence, 2026-04-24.
_CREATE_WITH_FLOW_SELECTORS: tuple[str, ...] = (
    # Hero section: scoped under <main>, exclude in-page anchors.
    "main button:has-text('Create with Flow')",
    "main [role='button']:has-text('Create with Flow')",
    "main a:has-text('Create with Flow'):not([href^='#'])",
    # Document-wide fallbacks for variants without a <main> wrapper.
    "button:has-text('Create with Flow')",
    "[role='button']:has-text('Create with Flow')",
    "a:has-text('Create with Flow'):not([href^='#'])",
)


# URL fragments observed on the marketing landing when the CTA click only
# scrolled to an in-page anchor instead of mounting the app (issue #48).
_ANCHOR_SCROLL_FRAGMENTS: tuple[str, ...] = (
    "#capabilities", "#partners", "#faq", "#models", "#explore",
)


_FLOW_CANVAS_TEXT_SIGNALS: tuple[str, ...] = (
    "no chain nodes yet",
    "this chain does not have any jobs to render yet",
    "run workflow",
    "batch run",
    "open gallery",
)


_FLOW_CANVAS_NAV_SELECTORS: tuple[str, ...] = (
    "a[href*='gallery' i]",
    "a:has-text('Gallery')",
    "button:has-text('Gallery')",
    "[role='link']:has-text('Gallery')",
    "[role='button']:has-text('Gallery')",
    "a:has-text('Open jobs')",
    "button:has-text('Open jobs')",
    "[role='link']:has-text('Open jobs')",
    "[role='button']:has-text('Open jobs')",
)


_NEW_PROJECT_TEXT_SELECTOR = "text=/New project|Dự án mới|Tạo dự án/"
_NEW_PROJECT_ROLE_NAMES: tuple[str, ...] = (
    "New project", "Dự án mới", "Tạo dự án",
)


def is_flow_landing_url(url: str) -> bool:
    current = (url or "").lower()
    return (
        "labs.google/fx" in current
        and "/project/" not in current
        and "/edit/" not in current
        and "/tools/flow" in current
    )


def is_marketing_anchor_url(url: str) -> bool:
    """Return True when URL is the marketing page scrolled to an anchor.

    After clicking a "Create with Flow" scroll-link the URL gains a
    fragment like `#capabilities` without leaving the marketing page —
    the app never mounts. Used by :func:`dismiss_flow_marketing_landing`
    as the "wrong CTA clicked, try next candidate" signal.
    """
    if not url or "/project/" in url or "/edit/" in url:
        return False
    lowered = url.lower()
    return any(frag in lowered for frag in _ANCHOR_SCROLL_FRAGMENTS)


def is_flow_canvas_page(url: str, page_text: str) -> bool:
    """Return True when Flow shows the Canvas/Workflow builder shell."""
    current = (url or "").lower()
    if "labs.google/fx" in current and "/tools/flow" in current and "/chain/" in current:
        return True

    normalized_text = " ".join((page_text or "").lower().split())
    return any(signal in normalized_text for signal in _FLOW_CANVAS_TEXT_SIGNALS)


def _is_flow_auth_url(url: str) -> bool:
    current = (url or "").lower()
    return "labs.google/fx/api/auth/signin" in current or "accounts.google." in current


async def _new_project_button_visible(page, timeout_ms: int = 1000) -> bool:
    try:
        await page.wait_for_selector(
            _NEW_PROJECT_TEXT_SELECTOR,
            state="visible",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        pass

    per_name = max(250, timeout_ms // len(_NEW_PROJECT_ROLE_NAMES))
    for name in _NEW_PROJECT_ROLE_NAMES:
        try:
            button = page.get_by_role("button", name=name).filter(visible=True).first
            if await button.is_visible(timeout=per_name):
                return True
        except Exception:
            continue
    return False


async def recover_from_flow_canvas_page(page, logger, homepage_url: str) -> bool:
    """Leave the Flow Canvas/Workflow builder and wait for the project list."""
    try:
        page_text = await page.evaluate("document.body?.innerText || ''")
    except Exception:
        page_text = ""

    if not is_flow_canvas_page(page.url, page_text):
        return False

    logger.warning("Flow Canvas page detected — recovering to project list")

    for selector in _FLOW_CANVAS_NAV_SELECTORS:
        try:
            target = page.locator(selector).first
            if not await target.is_visible(timeout=1000):
                continue
            await target.click(timeout=5000)
            await asyncio.sleep(3)
            if await _new_project_button_visible(page, timeout_ms=5000):
                logger.info("Recovered from Flow Canvas via selector: %s", selector)
                return True
        except Exception:
            continue

    try:
        await page.goto(homepage_url, wait_until="commit", timeout=30000)
    except Exception as exc:
        if "ERR_ABORTED" not in str(exc):
            logger.warning("Flow Canvas homepage recovery navigation failed: %s", exc)
            return False
        logger.warning("Flow Canvas homepage recovery navigation aborted; continuing: %s", exc)
    await asyncio.sleep(3)

    try:
        await page.reload(wait_until="domcontentloaded", timeout=15000)
    except Exception as exc:
        if "ERR_ABORTED" not in str(exc):
            logger.warning("Flow Canvas homepage recovery reload failed: %s", exc)
        else:
            logger.warning("Flow Canvas homepage recovery reload aborted; continuing: %s", exc)
    await asyncio.sleep(3)

    if getattr(page, "is_closed", lambda: False)() or _is_flow_auth_url(page.url):
        logger.warning("Flow Canvas recovery stopped at auth/login page: %s", page.url[:120])
        return False

    recovered = await _new_project_button_visible(page, timeout_ms=15000)
    if not recovered:
        if getattr(page, "is_closed", lambda: False)() or _is_flow_auth_url(page.url):
            logger.warning("Flow Canvas recovery stopped at auth/login page: %s", page.url[:120])
            return False

        async def _ready() -> bool:
            return await _new_project_button_visible(page, timeout_ms=2000)

        if await dismiss_flow_marketing_landing(page, logger, _ready):
            recovered = await _new_project_button_visible(page, timeout_ms=15000)

    if recovered:
        logger.info("Recovered from Flow Canvas via homepage reload")
    else:
        logger.warning("Flow Canvas recovery did not reveal New project button")
    return recovered


async def _find_landing_cta(page):
    for selector in _CREATE_WITH_FLOW_SELECTORS:
        try:
            candidate = page.locator(selector).first
            if await candidate.is_visible(timeout=1500):
                return candidate
        except Exception:
            continue
    return None


async def _dismiss_landing_once(
    page,
    logger,
    is_ready,
    per_click_timeout_sec: float,
) -> bool:
    """One pass over CTA candidates — see :func:`dismiss_flow_marketing_landing`."""
    for selector in _CREATE_WITH_FLOW_SELECTORS:
        try:
            cta = page.locator(selector).first
            if not await cta.is_visible(timeout=1500):
                continue
        except Exception:
            continue

        logger.info("Flow marketing landing detected — clicking '%s'", selector)
        # If Flow has already scroll-navigated to an in-page anchor, the
        # button moves with the layout and Playwright's "scroll into view"
        # step retriggers the scroll listener, racing the click. Reset
        # the URL before each attempt so the hero is back at its baseline.
        if is_marketing_anchor_url(page.url):
            try:
                await page.evaluate(
                    "() => history.replaceState(null, '', location.pathname)"
                )
            except Exception:
                pass

        # The marketing page has a sticky <header> and scroll-linked hash
        # listener. Playwright's actionability check fails with "header
        # intercepts pointer events" or scrolls the URL to #capabilities
        # before the click lands. `force=True` skips the actionability
        # check entirely and dispatches at the element's centre.
        clicked = False
        try:
            await cta.click(timeout=5000, force=True)
            clicked = True
        except Exception as exc:
            logger.warning("CTA click(force=True) failed for '%s': %s", selector, exc)
        if not clicked:
            continue

        # Memory `feedback_flow_marketing_landing_bypass.md`: "click + settle
        # + proceed". The CTA's React onClick can fire an async SPA route that
        # takes several seconds, during which the scroll listener mutates the
        # URL to `#partners` / `#capabilities`. Abandoning the candidate on
        # that URL change kills the very navigation we want. Just click, wait
        # the full timeout, poll is_ready — never bail on URL state.
        deadline = time.monotonic() + per_click_timeout_sec
        success = False
        while time.monotonic() < deadline:
            await asyncio.sleep(0.4)
            if "/project/" in page.url or "/edit/" in page.url:
                success = True
                break
            try:
                if await is_ready():
                    success = True
                    break
            except Exception:
                pass

        if success:
            return True
        logger.warning(
            "CTA '%s' did not mount app within %.0fs (url=%s) — trying next",
            selector, per_click_timeout_sec, page.url[:120],
        )

    return False


async def dismiss_flow_marketing_landing(
    page,
    logger,
    is_ready,
    *,
    per_click_timeout_sec: float = 8.0,
    max_reloads: int = 2,
    reload_settle_sec: float = 2.0,
) -> bool:
    """Click a "Create with Flow" CTA until the app mounts, reloading on failure.

    Unlike :func:`recover_from_flow_landing` — which keys on the URL
    flipping to ``/project/`` — the L1 homepage path leaves the URL at
    ``/tools/flow`` after a correct click. Callers supply an *is_ready*
    coroutine that returns True once the app DOM is usable (e.g. the
    "+ New project" button is attached). After each candidate click we
    poll *is_ready* and the URL; if neither indicates success and the
    URL degrades to an in-page anchor (`#capabilities` etc.), we abandon
    that candidate and try the next.

    When a full pass over all candidates fails without mounting,
    :func:`page.reload` is attempted up to *max_reloads* times as a
    cheap safety net. (Historically this guarded against a suspected
    per-request A/B on the CTA's onClick — issue #51 — but the actual
    root cause of the 2026-04-24 batch failures turned out to be CDP
    picking a ``chrome://omnibox-popup`` page; see `flow/client.py`
    and `feedback_cdp_skip_chrome_internal_pages.md`.)

    Parameters
    ----------
    page: Playwright Page (real, not a mock — locator chain is used).
    logger: caller's logger.
    is_ready: ``async () -> bool`` predicate. Returns True when the
        caller's "we are in the app" signal is satisfied. Must not
        raise; return False on timeout.
    per_click_timeout_sec: max seconds to wait for the app to mount
        after each individual CTA click before moving on.
    max_reloads: number of :func:`page.reload` attempts after the
        first pass fails. ``0`` disables reload-retry (preserves the
        pre-#51 behavior).
    reload_settle_sec: seconds to sleep after each reload before
        re-running the candidate loop.

    Returns
    -------
    bool: True if some candidate produced a ready state; False if all
    candidates were exhausted across all reload attempts.
    """
    for attempt in range(max_reloads + 1):
        if attempt > 0:
            logger.info(
                "Marketing landing persisted — reload retry %d/%d",
                attempt, max_reloads,
            )
            try:
                await page.reload(wait_until="domcontentloaded", timeout=15000)
            except Exception as exc:
                logger.warning("Reload failed on retry %d: %s", attempt, exc)
                continue
            await asyncio.sleep(reload_settle_sec)
            # Fast path — reload may have served the app variant directly.
            try:
                if await is_ready():
                    return True
            except Exception:
                pass

        if await _dismiss_landing_once(
            page, logger, is_ready, per_click_timeout_sec
        ):
            return True

    return False


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
