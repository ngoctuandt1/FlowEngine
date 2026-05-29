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

* ``tune Settings`` button (bottom composer): a visible ``button`` containing an
  icon element whose textContent is exactly ``tune`` + text "Settings". This is
  NOT the left-toolbar "View Settings" button (icon ``settings_2``) which
  appears earlier in the DOM and opens an unrelated *View Settings* panel.
* "Confirm before generating": two ``button[role=radio][data-state=…]``
  (Always / Never), icon ``radio_button_checked|unchecked``.
* Two sections, in DOM order: **Image generation default** then
  **Video generation default**. Each has aspect ``button[role=tab]``,
  count tabs ("1x"/"x2"/"x3"/"x4"), and a model ``button[aria-haspopup=menu]``.
* ``Save`` button (text "Save").

React handlers ignore programmatic ``element.click()`` (synthetic events need a
real pointer sequence). So the panel-critical controls (tune button, Never
radio, Save) are clicked via a *mark-then-Playwright-click* pattern: a JS
``page.evaluate`` tags the precise element with a unique ``data-as-*`` attribute,
then Playwright's ``locator.click`` dispatches real pointer events on it.
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
    """Locate the composer ``tune`` button, click it for real, wait for panel.

    The tune button is matched *precisely* (icon ligature exactly ``tune``;
    never the left-toolbar ``settings_2`` / "View Settings" button) and clicked
    with a real Playwright pointer event — React ignores programmatic JS
    ``.click()``, which is why the previous JS-click attempts no-op'd. Emits a
    diagnostic line so the next live run is debuggable without a screenshot.
    """
    # 1. Poll for the tune button to mount (slow composer), then diagnose.
    found = await _wait_tune_button(page)
    diag = await _probe_composer(page)
    logger.info(
        "agent_settings: composer probe -> visibleButtons=%s tuneMatch=%s "
        "viewSettings=%s (poll-found=%s)",
        diag.get("visibleButtons"), diag.get("tuneMatch"),
        diag.get("viewSettings"), found,
    )

    # 2. Mark the precise tune button + click it with real pointer events.
    if await _click_marked(page, _MARK_TUNE_JS, "data-as-tune"):
        logger.info("agent_settings: tune click (playwright) -> dispatched")
        if await _wait_panel_visible(page):
            return True
        logger.warning("agent_settings: tune clicked but panel did not open")
    else:
        logger.warning("agent_settings: precise tune button not found")

    return False


# JS that tags the bottom-composer tune button with ``data-as-tune='1'`` and
# returns whether exactly that button was found. It EXCLUDES the left-toolbar
# "View Settings" button (icon ``settings_2`` / text "View Settings"): we only
# accept a button containing an icon element whose textContent is exactly
# ``tune``. Matching the bare word "Settings" is forbidden — it hits the wrong
# button (View Settings appears earlier in the DOM).
_MARK_TUNE_JS = """
() => {
    document.querySelectorAll('[data-as-tune]').forEach((e) => e.removeAttribute('data-as-tune'));
    const iconText = (b) => Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
        .map((i) => (i.textContent || '').trim().toLowerCase());
    const cand = Array.from(document.querySelectorAll('button, [role="button"]'))
        .filter(__visible)
        .find((b) => {
            const icons = iconText(b);
            if (!icons.includes('tune')) return false;            // must have `tune` icon
            if (icons.includes('settings_2')) return false;       // not View Settings
            if (/view settings/i.test(b.textContent || '')) return false;
            return true;
        });
    if (!cand) return false;
    const target = cand.closest('button') || cand;
    target.setAttribute('data-as-tune', '1');
    try { target.scrollIntoView({ block: 'center' }); } catch (e) {}
    return true;
}
"""


async def _probe_composer(page) -> dict:
    """Diagnostic snapshot: visible button count, tune match, View-Settings presence."""
    try:
        return await page.evaluate(
            _VISIBLE_FN
            + """
            () => {
                const iconText = (b) => Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
                    .map((i) => (i.textContent || '').trim().toLowerCase());
                const btns = Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(__visible);
                const tuneMatch = btns.some((b) => {
                    const ic = iconText(b);
                    return ic.includes('tune') && !ic.includes('settings_2');
                });
                const viewSettings = btns.some((b) =>
                    iconText(b).includes('settings_2') || /view settings/i.test(b.textContent || ''));
                return { visibleButtons: btns.length, tuneMatch, viewSettings };
            }
            """
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent_settings: composer probe failed: %s", exc)
        return {}


async def _click_marked(page, mark_js: str, attr: str, timeout_ms: int = 4000) -> bool:
    """Tag an element with ``attr`` via ``mark_js`` then Playwright-click it.

    Combines precise JS matching (which CSS selectors can't express, e.g. "icon
    textContent == 'tune'") with a real Playwright pointer click that React's
    synthetic-event handlers actually respond to. Returns ``False`` if the JS
    found no element or the real click failed.
    """
    try:
        marked = await page.evaluate(_VISIBLE_FN + mark_js)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("agent_settings: mark (%s) eval failed: %s", attr, exc)
        return False
    if not marked:
        return False
    try:
        await page.locator(f"[{attr}='1']").click(timeout=timeout_ms)
        return True
    except Exception as exc:
        logger.debug("agent_settings: playwright click [%s] failed: %s", attr, exc)
        return False


async def _wait_tune_button(page, timeout_ms: int = 10000) -> bool:
    """Poll until a visible composer ``tune`` button exists (excludes View Settings)."""
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            found = await page.evaluate(
                _VISIBLE_FN
                + """
                () => Array.from(document.querySelectorAll('button, [role="button"]'))
                    .filter(__visible)
                    .some((b) => {
                        const ic = Array.from(b.querySelectorAll('i, span, [class*="symbol" i]'))
                            .map((i) => (i.textContent || '').trim().toLowerCase());
                        return ic.includes('tune') && !ic.includes('settings_2');
                    })
                """
            )
            if found:
                return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("agent_settings: tune-button probe failed: %s", exc)
        await _sleep(0.3)
    return False


async def _wait_panel_visible(page, timeout_ms: int = 8000) -> bool:
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
    """Check the "Never" confirm radio unless already checked (real click)."""
    # Mark the Never radio + report whether it was already checked / missing.
    state = await page.evaluate(
        _VISIBLE_FN
        + """
        () => {
            document.querySelectorAll('[data-as-never]').forEach((e) => e.removeAttribute('data-as-never'));
            const radios = Array.from(document.querySelectorAll('button[role="radio"]'))
                .filter(__visible);
            for (const r of radios) {
                const t = (r.textContent || '').toLowerCase();
                if (t.includes('never') || t.includes('automatically')) {
                    if (r.getAttribute('data-state') === 'checked') return 'already';
                    r.setAttribute('data-as-never', '1');
                    return 'marked';
                }
            }
            return 'missing';
        }
        """
    )
    if state == "missing":
        logger.info("agent_settings: confirm=Never -> missing")
        raise RuntimeError("Never radio not found")
    if state == "already":
        logger.info("agent_settings: confirm=Never -> already")
        return
    try:
        await page.locator("[data-as-never='1']").click(timeout=4000)
        logger.info("agent_settings: confirm=Never -> clicked")
    except Exception as exc:
        logger.warning("agent_settings: confirm=Never click failed: %s", exc)
        raise RuntimeError("Never radio click failed") from exc


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


_MARK_SAVE_JS = """
() => {
    document.querySelectorAll('[data-as-save]').forEach((e) => e.removeAttribute('data-as-save'));
    const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
    const btn = Array.from(document.querySelectorAll('button, [role="button"]'))
        .filter(__visible)
        .find((b) => norm(b.textContent).toLowerCase() === 'save');
    if (!btn) return false;
    btn.setAttribute('data-as-save', '1');
    return true;
}
"""


async def _click_save(page) -> None:
    if await _click_marked(page, _MARK_SAVE_JS, "data-as-save"):
        logger.info("agent_settings: Save -> clicked")
        return
    logger.info("agent_settings: Save -> not found")
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
