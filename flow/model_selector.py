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
    # The model chip is a button near the composer showing current model.
    # EN: "Video 🖥️ x1" or "Videox1"
    # VI: "🍌 Nano Banana Pro 📱 x1" or similar model name
    # It may also show "Veo" or "Imagen" depending on selection.
    chip_selectors = [
        # Direct model name matches
        "button:has-text('Veo')",
        "button:has-text('Video')",
        "button:has-text('Videox1')",
        "button:has-text('Imagen')",
        "button:has-text('Nano')",
        "button:has-text('Banana')",
        "[role='button']:has-text('Veo')",
        "[role='button']:has-text('Video')",
        "[role='button']:has-text('Imagen')",
        "[role='button']:has-text('Nano')",
        "[role='listbox']",
        # Chip contains "x1" or "x4" (generation count)
        "button:has-text('x1')",
        "button:has-text('x4')",
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

    # Step 2.7: Open the MODEL DROPDOWN within the panel
    # After Video tab switch, the panel shows the current model as:
    #   "Veo 3.1 - Fast arrow_drop_down" (button with dropdown arrow)
    # Must click this button to reveal the LP model options.
    dropdown_opened = await _open_model_dropdown(page)

    # Step 3: Find and click the target model
    # Use broad selectors: menuitem, button, [role] — and retry up to 3 times.
    MODEL_ITEM_SELECTORS = (
        "menuitem, [role='menuitem'], [role='option'], "
        "button, [role='button'], [role='listbox'] button"
    )

    is_lp = "Lower Priority" in target_text
    base_name = target_text.split(" [")[0].strip()  # "Veo 3.1 - Fast"

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
    """Check if a button contains a dropdown arrow icon."""
    try:
        arrow = btn.locator("i:has-text('arrow_drop_down'), span:has-text('arrow_drop_down')")
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

    Must click "Video" tab before selecting a Veo model.
    Uses the same approach as the old engine (flow_model_steps_v2.py):
      panel.locator("button,[role='tab']").filter(has_text=r"videocam|video")
    """
    # Priority: [role='tab'] first (most specific), then button with icon text
    TAB_SELECTORS = [
        "[role='tab']:has-text('Video')",
        "[role='tab']:has-text('videocam')",
        # Buttons that look like tabs (Material UI sometimes uses button for tabs)
        "button:has-text('videocam')",
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

    # JS fallback: find tab-like element with "Video" or "videocam" text
    # Must NOT match the model chip itself (which has "Video x1" or "Videox1")
    try:
        clicked = await page.evaluate("""() => {
            const candidates = document.querySelectorAll(
                '[role="tab"], button, [role="button"]'
            );
            for (const el of candidates) {
                const text = (el.innerText || '').trim();
                const lower = text.toLowerCase();
                const rect = el.getBoundingClientRect();
                // Match "Video" or "videocam" tab button
                // Exclude the chip: chip text contains "x1"/"x4" or is wider
                if ((lower === 'video' || lower === 'videocam'
                     || lower.includes('videocam'))
                    && !lower.match(/x[1-4]/i)
                    && rect.width > 20 && rect.height > 20
                    && rect.width < 200) {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
        if clicked:
            logger.info("Switched to Video tab via JS fallback")
            # Wait for model list to re-render
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
