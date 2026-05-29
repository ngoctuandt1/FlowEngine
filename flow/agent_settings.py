"""Drive Google Flow's "Agent settings" panel (2026-05 UI redesign).

Flow is now a single agent composer (no Video/Image/Frames mode tabs).
Output type and generation defaults are configured globally in an **Agent
settings** panel opened from the composer's ``tune Settings`` button.

The single public entry point, :func:`ensure_agent_settings`, opens that
panel, applies the requested defaults, and clicks ``Save``. It is idempotent
and best-effort: every sub-step is guarded so a missing control degrades to a
log line rather than aborting the whole call. It returns ``False`` only when
the panel never opens (the one hard failure we cannot recover from).

DOM facts (real probe on profile ``ngoctuandt20``, see
``plans/findings-260529-flow-agent-ui-redesign.md``):

* ``tune Settings`` button: ``<i>`` ligature ``tune`` + text "Settings".
* "Confirm before generating": two ``button[role=radio][data-state=…]``
  (Always / Never), icon ``radio_button_checked|unchecked``.
* Two sections, in DOM order: **Image generation default** then
  **Video generation default**. Each has aspect ``button[role=tab]``,
  count tabs ("1x"/"x2"/"x3"/"x4"), and a model ``button[aria-haspopup=menu]``.
* ``Save`` button (text "Save").
"""

from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

# Shared visible-only predicate injected into every page.evaluate helper.
_VISIBLE_FN = """
const __visible = (el) => {
    if (!el) return false;
    const s = getComputedStyle(el);
    const r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden'
        && parseFloat(s.opacity || '1') > 0 && r.width > 0 && r.height > 0;
};
"""

# Section headings used to scope aspect/count/model controls to image vs video.
_IMAGE_HEADING = "Image generation default"
_VIDEO_HEADING = "Video generation default"


async def ensure_agent_settings(
    page,
    *,
    confirm_never: bool = True,
    image_model: str | None = None,
    video_model: str | None = None,
    aspect: str | None = None,
    count: int = 1,
) -> bool:
    """Open the ``tune Settings`` panel, apply settings, and Save.

    Returns ``True`` on success (panel opened + Save clicked). Idempotent —
    safe to call once per project/session.

    Args:
        confirm_never: select the "Never" confirm radio (required for headless
            auto-generate; with "Always" the agent waits for a manual click).
        image_model / video_model: substring matched against the respective
            model dropdown menu items. ``None`` skips that dropdown.
        aspect: e.g. "16:9" / "9:16"; applied to both sections when present.
        count: 1..4 -> click the "1x"/"x2".. count tab in both sections.
    """
    if not await _open_panel(page):
        await _capture_failure(page, "agent_settings_panel_not_opened")
        return False

    if confirm_never:
        await _safe(_select_confirm_never(page), "confirm=Never")

    if aspect:
        await _safe(_apply_to_sections(page, _click_aspect_tab, aspect), f"aspect={aspect}")

    count = max(1, min(4, int(count)))
    await _safe(_apply_to_sections(page, _click_count_tab, count), f"count={count}")

    if image_model:
        await _safe(_select_model(page, _IMAGE_HEADING, image_model), f"image_model~={image_model!r}")
    if video_model:
        await _safe(_select_model(page, _VIDEO_HEADING, video_model), f"video_model~={video_model!r}")

    await _safe(_click_save(page), "Save")
    return True


# --------------------------------------------------------------------------
# Step helpers
# --------------------------------------------------------------------------

async def _open_panel(page) -> bool:
    """Wait for the composer ``tune Settings`` button, click it, wait for panel.

    Robust against slow composer mounts (poll for the button ~10s) and against
    JS ``.click()`` no-ops (Playwright ``locator.click`` fallback). Emits a
    diagnostic line (visible button count + tune-match + click result) so the
    next live run is debuggable without a screenshot.
    """
    # 1. Poll for the tune Settings button to exist + be visible (slow mounts).
    found = await _wait_tune_button(page)

    # 2. Diagnostic snapshot of the composer state.
    diag = await page.evaluate(
        _VISIBLE_FN
        + """
        () => {
            const btns = Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(__visible);
            const tuneMatch = btns.some((b) => {
                const hasTune = Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
                    .some((i) => (i.textContent || '').trim().toLowerCase() === 'tune');
                return hasTune || /settings/i.test((b.textContent || ''));
            });
            return { visibleButtons: btns.length, tuneMatch };
        }
        """
    )
    logger.info(
        "agent_settings: composer probe -> visibleButtons=%s tuneMatch=%s (poll-found=%s)",
        diag.get("visibleButtons"), diag.get("tuneMatch"), found,
    )

    # 3. Click via JS (targets the real <button> via closest()).
    clicked = await page.evaluate(
        _VISIBLE_FN
        + """
        () => {
            const cand = Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(__visible)
                .find((b) => {
                    const hasTune = Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
                        .some((i) => (i.textContent || '').trim().toLowerCase() === 'tune');
                    return hasTune || /settings/i.test((b.textContent || ''));
                });
            if (!cand) return false;
            const target = cand.closest('button') || cand;
            try { target.scrollIntoView({ block: 'center' }); } catch (e) {}
            target.click();
            return true;
        }
        """
    )
    logger.info("agent_settings: tune click (js) -> %s", clicked)

    if clicked and await _wait_panel_visible(page):
        return True

    # 4. Playwright locator fallback — handles cases where JS .click() is a no-op
    #    (e.g. React handler bound to a pointer event, or overlay capture).
    if await _click_tune_via_locator(page):
        logger.info("agent_settings: tune click (locator fallback) dispatched")
        if await _wait_panel_visible(page):
            return True

    logger.warning("agent_settings: tune Settings button not found / panel did not open")
    return False


async def _wait_tune_button(page, timeout_ms: int = 10000) -> bool:
    """Poll until a visible tune/Settings composer button exists."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            found = await page.evaluate(
                _VISIBLE_FN
                + """
                () => Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(__visible)
                    .some((b) => {
                        const hasTune = Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
                            .some((i) => (i.textContent || '').trim().toLowerCase() === 'tune');
                        return hasTune || /settings/i.test((b.textContent || ''));
                    })
                """
            )
            if found:
                return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("agent_settings: tune-button probe failed: %s", exc)
        await _sleep(0.3)
    return False


async def _click_tune_via_locator(page) -> bool:
    """Playwright-native click fallback (real pointer events, auto-scroll)."""
    try:
        # Button whose accessible text contains "Settings" (renders "tuneSettings").
        loc = page.locator("button", has_text="Settings")
        n = await loc.count()
        for i in range(n):
            cand = loc.nth(i)
            try:
                if await cand.is_visible():
                    await cand.scroll_into_view_if_needed(timeout=2000)
                    await cand.click(timeout=3000)
                    return True
            except Exception:
                continue
    except Exception as exc:
        logger.debug("agent_settings: locator fallback failed: %s", exc)
    return False


async def _wait_panel_visible(page, timeout_ms: int = 6000) -> bool:
    """Poll for the panel heading text (case-insensitive, allows animate-in)."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            visible = await page.evaluate(
                _VISIBLE_FN
                + """
                () => {
                    const wanted = ['agent settings', 'confirm before generating'];
                    const els = Array.from(document.querySelectorAll('h1,h2,h3,h4,div,span,p'));
                    return els.some((el) => __visible(el)
                        && wanted.some((w) => (el.textContent || '').toLowerCase().includes(w)));
                }
                """
            )
            if visible:
                logger.info("agent_settings: panel opened")
                return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("agent_settings: panel-visible probe failed: %s", exc)
        await _sleep(0.25)
    logger.warning("agent_settings: panel did not become visible")
    return False


async def _select_confirm_never(page) -> None:
    """Check the "Never" confirm radio unless already checked."""
    result = await page.evaluate(
        _VISIBLE_FN
        + """
        () => {
            const radios = Array.from(document.querySelectorAll('button[role="radio"]'))
                .filter(__visible);
            for (const r of radios) {
                const t = (r.textContent || '').toLowerCase();
                if (t.includes('never') || t.includes('automatically')) {
                    if (r.getAttribute('data-state') === 'checked') return 'already';
                    r.click();
                    return 'clicked';
                }
            }
            return 'missing';
        }
        """
    )
    logger.info("agent_settings: confirm=Never -> %s", result)
    if result == "missing":
        raise RuntimeError("Never radio not found")


async def _apply_to_sections(page, fn, value) -> None:
    """Run a per-section click helper for both Image and Video sections."""
    for heading in (_IMAGE_HEADING, _VIDEO_HEADING):
        try:
            await fn(page, heading, value)
        except Exception as exc:
            logger.debug("agent_settings: section %r apply skipped: %s", heading, exc)


async def _click_aspect_tab(page, heading: str, aspect: str) -> None:
    ok = await _click_in_section(page, heading, role="tab", text=aspect)
    logger.info("agent_settings: [%s] aspect=%s -> %s", heading, aspect, "ok" if ok else "miss")


async def _click_count_tab(page, heading: str, count: int) -> None:
    # Flow renders "1x" but "x2"/"x3"/"x4". For count==1 try "1x" then "x1".
    forms = [f"{count}x", f"x{count}"]
    for form in forms:
        if await _click_in_section(page, heading, role="tab", text=form, exact=True):
            logger.info("agent_settings: [%s] count=%s via %r", heading, count, form)
            return
    logger.info("agent_settings: [%s] count=%s -> miss (tried %s)", heading, count, forms)


async def _click_in_section(page, heading: str, *, role: str, text: str, exact: bool = False) -> bool:
    """Click a ``[role=role]`` whose text matches inside the named section.

    The section is the heading element's enclosing settings group. We pick the
    nearest ancestor that contains BOTH the heading and ``[role]`` elements, so
    Image controls aren't confused with Video controls.
    """
    return await page.evaluate(
        _VISIBLE_FN
        + """
        ([heading, role, text, exact]) => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const headEl = Array.from(document.querySelectorAll('*'))
                .find((el) => __visible(el) && norm(el.textContent) === heading
                    || (__visible(el) && el.children.length === 0 && norm(el.textContent) === heading));
            if (!headEl) return false;
            // Walk up to the smallest ancestor that also holds role controls.
            let scope = headEl;
            for (let i = 0; i < 8 && scope.parentElement; i++) {
                scope = scope.parentElement;
                if (scope.querySelector('[role="' + role + '"]')) break;
            }
            const matchTxt = (t) => {
                const n = norm(t).toLowerCase();
                const w = text.toLowerCase();
                return exact ? n === w : n.includes(w);
            };
            const els = Array.from(scope.querySelectorAll('[role="' + role + '"]'))
                .filter(__visible);
            for (const el of els) {
                if (matchTxt(el.textContent)) { el.click(); return true; }
            }
            return false;
        }
        """,
        [heading, role, text, exact],
    )


async def _select_model(page, heading: str, substring: str) -> None:
    """Open the section's model dropdown and click the matching menu item."""
    opened = await page.evaluate(
        _VISIBLE_FN
        + """
        (heading) => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const headEl = Array.from(document.querySelectorAll('*'))
                .find((el) => __visible(el) && el.children.length === 0
                    && norm(el.textContent) === heading);
            if (!headEl) return false;
            let scope = headEl;
            for (let i = 0; i < 8 && scope.parentElement; i++) {
                scope = scope.parentElement;
                if (scope.querySelector('button[aria-haspopup="menu"]')) break;
            }
            const dd = Array.from(scope.querySelectorAll('button[aria-haspopup="menu"]'))
                .filter(__visible)[0];
            if (!dd) return false;
            dd.click();
            return true;
        }
        """,
        heading,
    )
    if not opened:
        logger.info("agent_settings: [%s] model dropdown not found", heading)
        return
    await _sleep(0.4)
    picked = await page.evaluate(
        _VISIBLE_FN
        + """
        (substr) => {
            const want = substr.toLowerCase();
            const items = Array.from(document.querySelectorAll(
                '[role="menuitem"], [role="option"], [role="menuitemradio"]'
            )).filter(__visible);
            for (const it of items) {
                if ((it.textContent || '').toLowerCase().includes(want)) {
                    it.click();
                    return true;
                }
            }
            return false;
        }
        """,
        substring,
    )
    logger.info(
        "agent_settings: [%s] model~=%r -> %s",
        heading, substring, "ok" if picked else "not found",
    )
    if not picked:
        # Close the open menu so it doesn't shadow the Save button.
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass


async def _click_save(page) -> None:
    clicked = await page.evaluate(
        _VISIBLE_FN
        + """
        () => {
            const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
            const btns = Array.from(document.querySelectorAll('button, [role="button"]'))
                .filter(__visible);
            for (const b of btns) {
                if (norm(b.textContent).toLowerCase() === 'save') { b.click(); return true; }
            }
            return false;
        }
        """
    )
    logger.info("agent_settings: Save -> %s", "clicked" if clicked else "not found")
    if not clicked:
        raise RuntimeError("Save button not found")


# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------

async def _safe(coro, label: str) -> None:
    """Await a sub-step coroutine, logging (not raising) on failure."""
    try:
        await coro
    except Exception as exc:
        logger.warning("agent_settings: step %s failed (continuing): %s", label, exc)


async def _sleep(seconds: float) -> None:
    import asyncio
    await asyncio.sleep(seconds)


async def _capture_failure(page, kind: str) -> None:
    """Best-effort screenshot at the raise site, FLOW_ERROR_CAPTURE_DIR gated."""
    cap_dir = os.environ.get("FLOW_ERROR_CAPTURE_DIR", "")
    if not cap_dir or os.environ.get("FLOW_ERROR_CAPTURE", "1") == "0":
        return
    try:
        os.makedirs(cap_dir, exist_ok=True)
        ts = int(time.time())
        await page.screenshot(path=os.path.join(cap_dir, f"{ts}_{kind}.png"))
        html = await page.content()
        with open(os.path.join(cap_dir, f"{ts}_{kind}.full.html"), "w", encoding="utf-8") as fh:
            fh.write(html)
        logger.error("agent_settings forensics: %s_%s.{png,full.html}", ts, kind)
    except Exception as exc:
        logger.debug("agent_settings forensic capture failed: %s", exc)
