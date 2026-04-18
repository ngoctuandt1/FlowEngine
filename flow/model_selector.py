"""Model selector -- pick LP (Lower Priority) models for 0-credit generation."""

import asyncio
import re
import logging

logger = logging.getLogger(__name__)

# Model name mapping (user-facing -> what to look for in DOM)
MODEL_MAP = {
    "veo-3.1-lite-lp": "Veo 3.1 - Lite [Lower Priority]",
    "veo-3.1-fast-lp": "Veo 3.1 - Fast [Lower Priority]",
    "veo-3.1-lite": "Veo 3.1 - Lite",
    "veo-3.1-fast": "Veo 3.1 - Fast",
    "veo-3.1-quality": "Veo 3.1 - Quality",
}

# Default to fast LP (free)
DEFAULT_MODEL = "veo-3.1-fast-lp"


async def select_model(
    page,
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
) -> bool:
    """Select the specified model in the Flow UI.

    Steps:
    1. Find and click the model selector chip/button (shows current model name)
    2. Wait for the dropdown menu to appear
    3. Find the target model menuitem
    4. Click it
    5. Verify selection via credit footer text (0 credits for LP)

    Args:
        page: Playwright page
        model: Model key from MODEL_MAP
        free_mode: If True, force LP model regardless of model param

    Returns True if model was selected successfully.
    """
    # Resolve target model text
    if free_mode and "lp" not in model.lower() and "lower" not in model.lower():
        # Force LP version
        model = model.rstrip("-lp") + "-lp" if not model.endswith("-lp") else model
        if model not in MODEL_MAP:
            model = DEFAULT_MODEL

    target_text = MODEL_MAP.get(model, MODEL_MAP[DEFAULT_MODEL])
    logger.info("Selecting model: %s", target_text)

    # Step 1: Open model dropdown
    #
    # The model/settings chip is a button with:
    #   * ``aria-haspopup="menu"``
    #   * child ``<i>`` whose text is exactly the crop ligature
    #     (``crop_16_9`` default landscape, ``crop_9_16`` portrait)
    # Verified live 2026-04-19 on both /project/ and /edit/ views. The
    # surrounding button innerText is locale-dependent ("Videox1" vs
    # localized model name) and was a source of mis-matches — we now pin
    # on the icon text and the haspopup attribute instead.
    chip_selectors = [
        # /project/ composer: chip shows the aspect crop icon (16:9 / 9:16 / 1:1).
        "button[aria-haspopup='menu']:has(i:text-is('crop_16_9'))",
        "button[aria-haspopup='menu']:has(i:text-is('crop_9_16'))",
        "button[aria-haspopup='menu']:has(i:text-is('crop_1_1'))",
        # /edit/ composer: the model chip shows "Veo 3.1 - <variant>" + <i>arrow_drop_down</i>.
        # Verified unique live 2026-04-19 (sole aria-haspopup=menu button with that icon).
        "button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))",
    ]

    opened = False
    for sel in chip_selectors:
        try:
            chip = page.locator(sel).first
            if await chip.is_visible(timeout=2000):
                await chip.click(timeout=2000)
                logger.info("Opened model dropdown via: %s", sel)
                opened = True
                break
        except Exception:
            continue

    if not opened:
        # JS fallback: find the model chip by position (near composer, bottom half)
        try:
            opened = await page.evaluate("""() => {
                // Model chip is a button near the composer with 'x1' or 'x4' text
                const btns = document.querySelectorAll('button, [role="button"]');
                for (const btn of btns) {
                    const text = (btn.innerText || '').trim();
                    const rect = btn.getBoundingClientRect();
                    // Must be in bottom half, not too large, contains x1/x4 or model keywords
                    if (rect.top > window.innerHeight * 0.5 && rect.width < 300
                        && (text.match(/x[1-4]$/i) || /veo|video|imagen|nano/i.test(text))) {
                        btn.click();
                        return true;
                    }
                }
                return false;
            }""")
            if opened:
                logger.info("Opened model dropdown via JS fallback")
        except Exception:
            pass

    if not opened:
        logger.warning("Could not find model selector chip — will proceed with default model")
        return False

    # Step 2: Wait for panel to appear
    await asyncio.sleep(0.5)

    # Step 2.5: Switch to Video tab
    # The model panel has TWO tabs: "Hình ảnh"/"Image" and "Video".
    # Veo models only appear under the Video tab.
    if any(kw in target_text.lower() for kw in ("veo", "video")):
        await _switch_to_video_tab(page)

    # Step 2.7: Check if LP items already visible BEFORE opening dropdown.
    # In extend mode, the model panel may already show LP options directly
    # without needing to click the Veo dropdown. Clicking it would TOGGLE
    # the dropdown closed, hiding the LP items.
    is_lp = "Lower Priority" in target_text
    base_name = target_text.split(" [")[0].strip()  # "Veo 3.1 - Fast"

    MODEL_ITEM_SELECTORS = (
        "menuitem, [role='menuitem'], [role='option'], "
        "button, [role='button'], [role='listbox'] button"
    )

    # Pre-check: are LP items already visible?
    dropdown_opened = False
    if is_lp:
        try:
            lp_items = page.locator(MODEL_ITEM_SELECTORS).filter(
                has_text=re.compile(r"Lower Priority", re.IGNORECASE)
            )
            lp_count = await lp_items.count()
            if lp_count > 0:
                logger.info("LP items already visible (%d) — skipping dropdown open", lp_count)
            else:
                dropdown_opened = await _open_model_dropdown(page)
        except Exception:
            dropdown_opened = await _open_model_dropdown(page)
    else:
        dropdown_opened = await _open_model_dropdown(page)

    # Step 3: Find and click the target model
    for attempt in range(3):
        if attempt > 0:
            logger.info("Model select retry %d, waiting 1.5s...", attempt + 1)
            await asyncio.sleep(1.5)

        try:
            if is_lp:
                # Look for any element with "Lower Priority" text
                items = page.locator(MODEL_ITEM_SELECTORS).filter(
                    has_text=re.compile(r"Lower Priority", re.IGNORECASE)
                )
                count = await items.count()
                logger.info(
                    "LP model search (attempt %d): found %d items matching 'Lower Priority'",
                    attempt + 1, count,
                )

                if count == 0:
                    # Debug: log what model options are visible
                    await _debug_model_options(page)
                    continue

                # Pick the one matching our base model name
                for i in range(count):
                    item_text = await items.nth(i).inner_text()
                    if base_name.lower() in item_text.lower():
                        await items.nth(i).click(timeout=3000, force=True)
                        logger.info("Selected LP model: %s", item_text.strip()[:80])
                        await asyncio.sleep(0.5)
                        ok = await _verify_credits(page, expected=0)
                        await _close_model_panel(page, dropdown_opened)
                        return ok

                # Fallback: click first LP model found
                text = await items.first.inner_text()
                await items.first.click(timeout=3000, force=True)
                logger.info("Selected first LP model: %s", text.strip()[:80])
                await asyncio.sleep(0.5)
                ok = await _verify_credits(page, expected=0)
                await _close_model_panel(page, dropdown_opened)
                return ok

            else:
                # Non-LP: match base model name
                items = page.locator(MODEL_ITEM_SELECTORS).filter(
                    has_text=re.compile(re.escape(base_name), re.IGNORECASE)
                )
                if await items.first.is_visible(timeout=2000):
                    await items.first.click(timeout=3000, force=True)
                    logger.info("Selected model: %s", target_text)
                    await asyncio.sleep(0.5)
                    await _close_model_panel(page, dropdown_opened)
                    return True

        except Exception as e:
            logger.warning("Model select attempt %d failed: %s", attempt + 1, e)

    # All attempts exhausted — try JS fallback
    logger.warning("Playwright selectors failed — trying JS fallback for model selection")
    js_ok = await _select_model_js(page, base_name, is_lp)
    if js_ok:
        ok = await _verify_credits(page, expected=0) if is_lp else True
        await _close_model_panel(page, dropdown_opened)
        return ok

    # Close menu
    await _close_model_panel(page, dropdown_opened)

    logger.error("Failed to select model after all attempts")
    return False


async def _verify_credits(page, expected: int = 0) -> bool:
    """Verify credit cost matches expected value."""
    try:
        result = await page.evaluate(
            """(expected) => {
            const body = document.body.innerText || '';

            // Pattern 1: "will use X credits" / "tốn X tín dụng"
            const en = body.match(/will use (\\d+) credits?/i);
            if (en) return { cost: parseInt(en[1]), source: 'en_will_use' };
            const vi = body.match(/tốn (\\d+) tín dụng/i);
            if (vi) return { cost: parseInt(vi[1]), source: 'vi_ton' };

            // Pattern 2: "X credits" / "X tín dụng" near model text
            const credits = body.match(/(\\d+)\\s*credits?/i);
            if (credits) return { cost: parseInt(credits[1]), source: 'en_credits' };
            const tinDung = body.match(/(\\d+)\\s*tín dụng/i);
            if (tinDung) return { cost: parseInt(tinDung[1]), source: 'vi_tin_dung' };

            // Pattern 3: LP model selected = 0 credits (check for text indicator)
            if (expected === 0 && /lower priority/i.test(body)) {
                return { cost: 0, source: 'lp_text' };
            }

            return null;
        }""",
            expected,
        )

        if result is not None:
            cost = result["cost"]
            if cost == expected:
                logger.info("Credit verification OK: %d credits (via %s)", cost, result["source"])
                return True
            logger.warning("Credit mismatch: expected %d, got %d (via %s)", expected, cost, result["source"])
            return False

        logger.warning("Could not find credit text -- assuming OK")
        return True
    except Exception as e:
        logger.warning("Credit verify error: %s", e)
        return True


async def _open_model_dropdown(page) -> bool:
    """Click the model name button inside the panel to open model list.

    After switching to Video tab, the panel shows the current model:
      "Veo 3.1 - Fast arrow_drop_down" (264px wide button)
    OR if LP was previously selected:
      "Veo 3.1 - Fast [Lower Priority] arrow_drop_down"

    Clicking this opens the actual dropdown with LP model options.
    """
    # Playwright: find button with "Veo" text + "arrow_drop_down" (the model name button)
    # First pass: prefer non-LP button (standard model name).
    # Second pass: accept LP button too (account remembered LP selection).
    try:
        veo_btns = page.locator("button").filter(has_text="Veo")
        count = await veo_btns.count()

        # First pass: non-LP Veo button
        for i in range(count):
            btn = veo_btns.nth(i)
            try:
                txt = await btn.inner_text()
                if "Lower Priority" in txt:
                    continue
                if "arrow_drop_down" in txt or await _has_dropdown_arrow(btn):
                    await btn.click(timeout=3000)
                    logger.info("Opened model dropdown via Veo button: %s", txt.strip()[:60])
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                continue

        # Second pass: LP Veo button (account remembered LP from previous project)
        for i in range(count):
            btn = veo_btns.nth(i)
            try:
                txt = await btn.inner_text()
                if "Lower Priority" in txt and ("arrow_drop_down" in txt or await _has_dropdown_arrow(btn)):
                    await btn.click(timeout=3000)
                    logger.info("Opened model dropdown via LP Veo button: %s", txt.strip()[:60])
                    await asyncio.sleep(1.0)
                    return True
            except Exception:
                continue
    except Exception as e:
        logger.debug("Veo button search failed: %s", e)

    # JS fallback: find button with "Veo" + arrow_drop_down, accept LP too
    try:
        clicked = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button, [role="button"]');
            for (const btn of btns) {
                const text = (btn.innerText || '').trim();
                const lower = text.toLowerCase();
                if (lower.includes('veo') && lower.includes('arrow_drop_down')) {
                    const rect = btn.getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 20) {
                        btn.click();
                        return text.substring(0, 60);
                    }
                }
            }
            return null;
        }""")
        if clicked:
            logger.info("Opened model dropdown via JS: %s", clicked)
            await asyncio.sleep(1.0)
            return True
    except Exception:
        pass

    logger.warning("Could not find model dropdown button inside panel")
    return False


async def _has_dropdown_arrow(btn) -> bool:
    """Check if a button contains a dropdown arrow icon.

    Uses exact ``:text-is('arrow_drop_down')`` on the child ``<i>`` (Material
    Icon ligature). Fuzzy ``:has-text`` would also match "arrow_drop_down_circle"
    or similar distractors — exact-text avoids those.
    """
    try:
        arrow = btn.locator("i:text-is('arrow_drop_down')")
        return await arrow.count() > 0
    except Exception:
        return False


async def _close_model_panel(page, dropdown_was_opened: bool = True) -> None:
    """Dismiss model selector panel/dropdown.

    Uses click-outside (on the Slate editor / prompt textbox) instead of
    Escape, because Escape can accidentally close parent overlays
    (extend/insert/remove panels).
    """
    # Click on the prompt editor to dismiss model panel (click-outside)
    try:
        editors = page.locator("[data-slate-editor='true']")
        count = await editors.count()
        if count > 0:
            await editors.last.click(timeout=2000)
            await asyncio.sleep(0.3)
            logger.debug("Dismissed model panel by clicking editor")
            return
    except Exception:
        pass

    # Fallback: single Escape (safer than double)
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.3)
    except Exception:
        pass


async def _debug_model_options(page) -> None:
    """Log visible model-like elements for debugging."""
    try:
        info = await page.evaluate("""() => {
            const sels = [
                'menuitem', '[role="menuitem"]', '[role="option"]',
                'button', '[role="button"]'
            ];
            const seen = new Set();
            const results = [];
            for (const s of sels) {
                for (const el of document.querySelectorAll(s)) {
                    const text = (el.innerText || '').trim().substring(0, 80);
                    const rect = el.getBoundingClientRect();
                    const key = text + '|' + s;
                    if (seen.has(key) || !text || rect.width === 0) continue;
                    seen.add(key);
                    // Only log elements related to models (Veo, LP, credits, etc.)
                    const lower = text.toLowerCase();
                    if (lower.includes('veo') || lower.includes('lower')
                        || lower.includes('priority') || lower.includes('credit')
                        || lower.includes('lite') || lower.includes('fast')
                        || lower.includes('quality') || lower.includes('video')
                        || lower.includes('imagen') || lower.includes('nano')) {
                        results.push({sel: s, text: text, w: Math.round(rect.width), h: Math.round(rect.height)});
                    }
                }
            }
            return results;
        }""")
        logger.info("Model-related elements on page: %s", info)
    except Exception as e:
        logger.debug("Debug model options failed: %s", e)


async def _select_model_js(page, base_name: str, is_lp: bool) -> bool:
    """JS fallback: click model option by scanning visible text.

    Searches all clickable elements for 'Lower Priority' (LP mode)
    or the base model name, then clicks the matching one.
    """
    try:
        target_lp = "lower priority" if is_lp else ""
        clicked = await page.evaluate("""(args) => {
            const baseName = args.baseName.toLowerCase();
            const targetLP = args.targetLP;
            const clickable = document.querySelectorAll(
                'menuitem, [role="menuitem"], [role="option"], button, [role="button"]'
            );
            let bestMatch = null;
            for (const el of clickable) {
                const text = (el.innerText || '').toLowerCase();
                const rect = el.getBoundingClientRect();
                if (rect.width < 30 || rect.height < 20) continue;

                if (targetLP && text.includes(targetLP) && text.includes(baseName)) {
                    // Perfect match: LP + base name
                    el.click();
                    return text.trim().substring(0, 80);
                }
                if (targetLP && text.includes(targetLP)) {
                    bestMatch = el;  // LP match without base name
                }
                if (!targetLP && text.includes(baseName)) {
                    el.click();
                    return text.trim().substring(0, 80);
                }
            }
            if (bestMatch) {
                bestMatch.click();
                return (bestMatch.innerText || '').trim().substring(0, 80);
            }
            return null;
        }""", {"baseName": base_name, "targetLP": target_lp})

        if clicked:
            logger.info("Selected model via JS fallback: %s", clicked)
            await asyncio.sleep(0.5)
            return True
    except Exception as e:
        logger.warning("JS model select failed: %s", e)

    return False


async def _switch_to_video_tab(page) -> bool:
    """Click the 'Video' tab in the model selector panel.

    The model selector panel has two tabs:
    - "Hình ảnh" / "Image" — image models (Imagen, Nano Banana)
    - "Video" — video models (Veo 3.1 variants)

    B26 ROOT CAUSE (fixed 2026-04-19): earlier JS fallback used
    ``lower.includes('videocam')`` which also matched the /edit/ bottom-strip
    **Camera mode-switcher** button whose ``innerText`` is ``"videocam\\nCamera"``.
    Clicking it toggled Camera mode on and navigated away from extend mode,
    killing the submit flow with ``gen_id=None`` + ``new_api_calls=0``.

    Fix: match the Video tab via ``[role='tab']`` with ``:text-is('Video')``
    (Radix gives the tab role="tab", so this is authoritative). For the JS
    fallback we require either ``role='tab'`` with exact textContent ``'Video'``
    OR a child ``<i>`` whose textContent is EXACTLY ``videocam``, AND the
    element's ``title`` must NOT be one of the mode-switcher titles.
    """
    # Exact-text selectors only. No fuzzy :has-text — see B26 note.
    TAB_SELECTORS = [
        "[role='tab']:text-is('Video')",
        # Rare fallback: tab whose only child is the videocam icon ligature.
        "[role='tab']:has(i:text-is('videocam'))",
    ]

    for sel in TAB_SELECTORS:
        try:
            tab = page.locator(sel).first
            if await tab.is_visible(timeout=2000):
                await tab.click(timeout=2000)
                logger.info("Switched to Video tab via: %s", sel)
                # Wait for model list to re-render after tab switch
                await asyncio.sleep(1.5)
                return True
        except Exception:
            continue

    # JS fallback — exact-text ONLY. Mode-switcher titles are excluded so we
    # never re-trigger the B26 bug by clicking the Camera button on /edit/.
    try:
        clicked = await page.evaluate("""() => {
            const MODE_TITLES = new Set([
                'Camera',
                'Mở rộng', 'Extend',
                'Chèn', 'Insert',
                'Xoá', 'Xóa', 'Remove', 'Delete',
            ]);
            const candidates = document.querySelectorAll('[role="tab"], button, [role="button"]');
            for (const el of candidates) {
                // Skip the /edit/ bottom-strip mode-switcher buttons.
                const title = el.getAttribute('title') || '';
                if (MODE_TITLES.has(title)) continue;

                const rect = el.getBoundingClientRect();
                if (rect.width < 20 || rect.height < 20) continue;

                // Prefer role="tab" with exact textContent 'Video'.
                if (el.getAttribute('role') === 'tab') {
                    const text = (el.textContent || '').trim();
                    if (text === 'Video') { el.click(); return 'tab-text-video'; }
                }

                // Fallback: child <i> with EXACT textContent 'videocam'.
                for (const icon of el.querySelectorAll('i')) {
                    if ((icon.textContent || '').trim() === 'videocam') {
                        el.click();
                        return 'icon-videocam';
                    }
                }
            }
            return null;
        }""")
        if clicked:
            logger.info("Switched to Video tab via JS fallback (%s)", clicked)
            await asyncio.sleep(1.5)
            return True
    except Exception:
        pass

    logger.warning("Could not find Video tab — panel may already show video models")
    return False


async def get_current_model(page) -> str | None:
    """Read the currently selected model from the UI chip."""
    try:
        for sel in [
            "button:has-text('Veo')",
            "[role='button']:has-text('Veo')",
        ]:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=1000):
                    return (await el.inner_text()).strip()
            except Exception:
                continue
    except Exception:
        pass
    return None
