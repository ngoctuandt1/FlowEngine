"""Text-to-Video generation — Level 1 operation."""

import asyncio
import logging
import re

from flow.navigation import flow_url, extract_project_id, extract_media_id
from flow.login import is_login_page, handle_login_redirect
from flow.model_selector import select_model, DEFAULT_MODEL
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video

logger = logging.getLogger(__name__)


async def text_to_video(
    client,
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    free_mode: bool = True,
) -> dict:
    """Execute text-to-video generation.

    Steps:
    1. Navigate to Flow homepage
    2. Click "+ New project" to create fresh project
    3. Select model (LP for free)
    4. Set aspect ratio (if UI supports it)
    5. Type prompt in composer
    6. Submit and confirm
    7. Wait for generation to complete
    8. Download result video
    9. Extract and return all metadata

    Returns:
        {
            "project_url": str,
            "media_id": str | None,
            "edit_url": str | None,
            "output_files": list[str],
            "generation_id": str | None,
            "profile": str,
        }
    """
    page = client.page
    locale = ""  # Will detect from URL

    # === Step 1: Navigate to Flow homepage ===
    logger.info("Step 1: Navigate to Flow homepage")
    homepage = flow_url(locale)
    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)  # Let page settle

    # Handle Google login redirect if needed
    current = page.url
    if is_login_page(current):
        logger.warning("Redirected to Google login — attempting auto-resolve")
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=client.profile_name,
        )
        if not login_ok:
            raise RuntimeError("Google login required — profile session expired.")
        # Re-navigate after login resolution
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)
        current = page.url

    # Detect locale from final URL
    if "/vi/" in current:
        locale = "vi"
    logger.info(f"On Flow homepage: {current}, locale={locale or 'en'}")

    # === Step 2: Click "+ New project" ===
    logger.info("Step 2: Create new project")
    # Wait for homepage to fully load before looking for buttons
    await asyncio.sleep(3)

    # Dismiss any welcome/onboarding overlay first
    await _dismiss_overlays(page)

    new_project_clicked = False

    # Try multiple selectors for the new project button
    # IMPORTANT: "+ New project" / "+ Dự án mới" FIRST, "Create" last
    # (because "Create" can match wrong buttons on welcome overlays)
    NEW_PROJECT_SELECTORS = [
        "button:has-text('New project')",
        "button:has-text('Dự án mới')",
        "a:has-text('New project')",
        "a:has-text('Dự án mới')",
        "[role='button']:has-text('New project')",
        "[role='button']:has-text('Dự án mới')",
        # "+" icon button (the actual new project FAB)
        "button:has-text('add')",
        "[aria-label*='New project' i]",
        "[aria-label*='new' i][aria-label*='project' i]",
        "[aria-label*='Create' i]",
        # Last resort: generic create buttons
        "button:has-text('Create')",
        "button:has-text('Tạo')",
    ]

    for sel in NEW_PROJECT_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=5000):
                await btn.click(timeout=5000)
                new_project_clicked = True
                logger.info(f"Clicked new project via: {sel}")
                break
        except Exception:
            continue

    if not new_project_clicked:
        # Last resort: screenshot for debug
        try:
            title = await page.title()
            body_text = await page.evaluate("document.body?.innerText?.substring(0, 500) || ''")
            logger.error("Page title: %s", title)
            logger.error("Page text preview: %s", body_text[:300])
        except Exception:
            pass
        raise RuntimeError("Failed to find '+ New project' button on Flow homepage")

    # Wait for project editor to load — URL may contain /project/ or just change
    try:
        await page.wait_for_url("**/project/**", timeout=20000)
    except Exception:
        # Fallback: wait for URL to change from homepage
        await asyncio.sleep(5)
        logger.warning("URL pattern wait failed, current: %s", page.url[:100])

    await asyncio.sleep(3)

    # Check if "Create" click redirected to Google login (session expired)
    current = page.url
    if is_login_page(current):
        logger.warning("Login redirect after Create click — handling")
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name,
        )
        if not login_ok:
            raise RuntimeError("Google login required — profile session expired.")
        # Re-navigate to homepage and retry project creation
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        # Re-click Create
        for sel in NEW_PROJECT_SELECTORS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    await btn.click(timeout=5000)
                    logger.info("Re-clicked new project via: %s", sel)
                    break
            except Exception:
                continue

        try:
            await page.wait_for_url("**/project/**", timeout=20000)
        except Exception:
            await asyncio.sleep(5)

        await asyncio.sleep(3)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full)
    if not project_id:
        # Try to extract from any URL pattern
        logger.warning("No project_id from URL: %s", project_url_full[:100])
    logger.info(f"New project created: {project_url_full}")

    # Wait for project editor (Slate composer) to fully render
    # The Slate.js editor can take a few seconds to initialize after page load
    logger.info("Waiting for project editor to fully render...")
    await _wait_for_composer(page)

    # === Step 3: Select model ===
    logger.info(f"Step 3: Select model ({model})")
    await select_model(page, model=model, free_mode=free_mode)

    # === Step 4: Aspect ratio ===
    # The aspect ratio is typically set in the model options panel
    # For now, we set it during model selection or skip if not critical
    logger.info(f"Step 4: Aspect ratio = {aspect_ratio}")
    await _set_aspect_ratio(page, aspect_ratio)

    # === Step 5: Type prompt ===
    logger.info(f"Step 5: Type prompt ({len(prompt)} chars)")
    await _type_prompt(page, prompt)

    # === Step 6: Count baseline cards, clear captures, submit ===
    logger.info("Step 6: Submit generation")
    before_cards = await _count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        timeout_sec=15.0,
        prompt_text=prompt,
    )

    if not confirmed:
        raise RuntimeError("Submit not confirmed — generation may not have started")

    logger.info("Submit confirmed, waiting for generation...")

    # === Step 7: Wait for completion ===
    logger.info("Step 7: Wait for completion")
    result = await wait_for_completion(client, job_type="text-to-video")

    if not result.get("done"):
        error = result.get("error", "unknown")
        raise RuntimeError(f"Generation failed: {error}")

    logger.info("Generation complete!")

    # === Step 8: Extract metadata ===
    current_url = page.url
    media_id = extract_media_id(current_url)
    if not media_id and result.get("media_ids"):
        media_id = result["media_ids"][0]

    # Build edit_url
    edit_url_val = None
    if media_id and project_id:
        base = flow_url(locale)
        edit_url_val = f"{base}/project/{project_id}/edit/{media_id}"

    # === Step 9: Download ===
    logger.info("Step 8: Download video")
    output_files = await download_video(
        client,
        media_ids=result.get("media_ids", [media_id] if media_id else []),
        prefix="t2v",
    )

    # Build project_url (without /edit/ part)
    proj_url = None
    if project_id:
        proj_url = f"{flow_url(locale)}/project/{project_id}"

    return {
        "project_url": proj_url or project_url_full,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _dismiss_overlays(page):
    """Dismiss welcome/onboarding overlays that block the homepage UI.

    Flow may show a "Meet the new Flow" overlay or similar announcements
    after login or UI updates. These need to be dismissed before we can
    interact with the actual homepage buttons.
    """
    # Try pressing Escape to close any modal
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    except Exception:
        pass

    # Close button patterns
    CLOSE_SELECTORS = [
        "button[aria-label*='close' i]",
        "button[aria-label*='dismiss' i]",
        "button:has-text('Got it')",
        "button:has-text('Đã hiểu')",
        "button:has-text('OK')",
        "[role='button'][aria-label*='close' i]",
        "button:has(i:has-text('close'))",
    ]

    for sel in CLOSE_SELECTORS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=1000):
                await btn.click(timeout=2000)
                logger.info("Dismissed overlay via: %s", sel)
                await asyncio.sleep(1)
                break
        except Exception:
            continue

    # Also try clicking outside any overlay (click on a safe area)
    try:
        await page.evaluate("""() => {
            // Click backdrop/overlay if present
            const overlays = document.querySelectorAll(
                '[class*="overlay"], [class*="backdrop"], [class*="scrim"]'
            );
            for (const el of overlays) {
                const s = getComputedStyle(el);
                if (s.display !== 'none' && s.visibility !== 'hidden') {
                    el.click();
                    return true;
                }
            }
            return false;
        }""")
    except Exception:
        pass

    await asyncio.sleep(0.5)


async def _wait_for_composer(page, timeout_sec: float = 15.0):
    """Wait until the Slate.js composer editor is visible on page.

    Checks for data-slate-editor, contenteditable, or the placeholder text.
    This prevents premature prompt typing on a still-loading page.
    """
    COMPOSER_SELECTORS = [
        "[data-slate-editor='true']",
        "[role='textbox'][aria-multiline='true']",
        "[data-testid='composer_input']",
    ]

    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        for sel in COMPOSER_SELECTORS:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=500):
                    logger.info("Composer ready via: %s", sel)
                    return
            except Exception:
                continue

        # Also check for placeholder text (locale-independent check)
        try:
            found = await page.evaluate("""() => {
                const text = document.body?.innerText || '';
                return /what do you want to create|bạn muốn tạo gì/i.test(text);
            }""")
            if found:
                logger.info("Composer ready (placeholder text detected)")
                return
        except Exception:
            pass

        await asyncio.sleep(1)

    logger.warning("Composer not detected after %.0fs — proceeding anyway", timeout_sec)


async def _type_prompt(page, prompt: str):
    """Type prompt into the Flow composer (Slate.js rich-text editor).

    Flow uses a Slate.js editor with:
    - data-slate-editor="true" + contenteditable="true"
    - Wrapped in [role='textbox'][aria-multiline='true']
    - Placeholder: "What do you want to create?" (EN) / "Bạn muốn tạo gì?" (VI)

    Strategy: try Slate-specific selectors first (high confidence), then
    generic fallbacks. Retry up to 3 rounds with increasing waits to
    handle slow page loads after project creation.
    """
    # Priority order: Slate-specific → role-based → generic
    PROMPT_SELECTORS = [
        # Slate.js editor (Flow's actual composer)
        "[data-slate-editor='true'][contenteditable='true']",
        "[role='textbox'][aria-multiline='true'] [contenteditable='true']",
        "[role='textbox'][aria-multiline='true']",
        # Test IDs (if present)
        "[data-testid='composer_input']",
        "[data-testid*='prompt']",
        "[data-testid*='composer']",
        # Generic editable elements
        "[role='textbox']",
        "textarea:not([name*='recaptcha' i])",
        "[contenteditable='true']",
        # Placeholder/label based
        "[aria-label*='create' i]",
        "[aria-label*='prompt' i]",
        "[placeholder*='create' i]",
        "[placeholder*='want' i]",
        "[placeholder*='muốn' i]",
    ]

    MAX_ROUNDS = 3
    for round_idx in range(MAX_ROUNDS):
        # Increasing wait: 2s, 4s, 6s — composer may still be loading
        wait_sec = 2 + round_idx * 2
        if round_idx > 0:
            logger.info("Prompt editor retry round %d, waiting %ds...", round_idx + 1, wait_sec)
            await asyncio.sleep(wait_sec)

        for sel in PROMPT_SELECTORS:
            try:
                el = page.locator(sel).first
                if await el.is_visible(timeout=2000):
                    await el.click(timeout=2000)
                    await asyncio.sleep(0.3)
                    # Clear existing text
                    await page.keyboard.press("Control+a")
                    await asyncio.sleep(0.1)
                    # Type the prompt via keyboard (works for both Slate and textarea)
                    await page.keyboard.type(prompt, delay=15)
                    logger.info("Prompt typed via: %s (round %d)", sel, round_idx + 1)
                    return
            except Exception:
                continue

        # JS fallback: find composer by scanning for hint text near bottom of page
        try:
            found = await page.evaluate("""(prompt) => {
                const visible = (el) => {
                    if (!el) return false;
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && r.width >= 100 && r.height >= 16;
                };
                // Find Slate editor or contenteditable in bottom half of page
                const sels = [
                    '[data-slate-editor="true"][contenteditable="true"]',
                    '[role="textbox"][aria-multiline="true"] [contenteditable="true"]',
                    '[contenteditable="true"]',
                    'textarea:not([name*="recaptcha"])',
                ];
                for (const s of sels) {
                    const els = document.querySelectorAll(s);
                    for (const el of els) {
                        if (!visible(el)) continue;
                        const r = el.getBoundingClientRect();
                        // Composer is in the bottom portion of the page
                        if (r.top >= window.innerHeight * 0.4) {
                            el.focus();
                            el.click();
                            return true;
                        }
                    }
                }
                return false;
            }""", prompt)

            if found:
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(prompt, delay=15)
                logger.info("Prompt typed via JS fallback (round %d)", round_idx + 1)
                return
        except Exception as e:
            logger.debug("JS fallback failed: %s", e)

    # Debug: log page state before failing
    try:
        title = await page.title()
        body_text = await page.evaluate(
            "document.body?.innerText?.substring(0, 500) || ''"
        )
        logger.error("Prompt editor not found — page title: %s", title)
        logger.error("Page text preview: %s", body_text[:300])
        # Log all editable elements on page
        editables = await page.evaluate("""() => {
            const els = document.querySelectorAll(
                'textarea, [contenteditable="true"], [role="textbox"], [data-slate-editor]'
            );
            return Array.from(els).map(el => ({
                tag: el.tagName,
                role: el.getAttribute('role'),
                slate: el.getAttribute('data-slate-editor'),
                ce: el.getAttribute('contenteditable'),
                visible: getComputedStyle(el).display !== 'none',
                rect: el.getBoundingClientRect().toJSON(),
            }));
        }""")
        logger.error("Editable elements on page: %s", editables)
    except Exception:
        pass

    raise RuntimeError("Failed to find prompt editor after %d rounds" % MAX_ROUNDS)


# Flow Video aspect ratios map to Radix trigger id suffixes.
# 16:9 is the panel default; 1:1 / 4:3 / 3:4 exist only in image mode.
RATIO_IDS = {"9:16": "PORTRAIT", "16:9": "LANDSCAPE"}


async def _set_aspect_ratio(page, ratio: str):
    """Set video aspect ratio via the Radix chip panel.

    Flow Video exposes only 9:16 (PORTRAIT) and 16:9 (LANDSCAPE). 16:9 is
    the default, so the only case that actually opens the panel is 9:16.
    Any other value (incl. ``"1:1"``, which is image-only) is logged and
    ignored — the video generates at the default 16:9.

    Selector reference: ``docs/FLOW_UI_REFERENCE.md`` §Aspect Ratio UI.
    B1a research: ``docs/session-reports/2026-04-17_B1a_aspect-ratio-research.md``.

    Flow (per B1a):
      1. click chip button ``button[aria-haspopup="menu"]`` (text like "Video … xN")
      2. wait for ``[role="menu"][data-state="open"]``
      3. ensure Video tab is active (``[id$="-trigger-VIDEO"]``)
      4. click the ratio trigger ``[id$="-trigger-{PORTRAIT|LANDSCAPE}"]``
         using ``Locator.click`` — JS ``el.click()`` does NOT fire Radix
         pointerdown and leaves data-state unchanged.
      5. wait for data-state="active" on that trigger
      6. dismiss the panel via click-outside (Escape would close the whole
         composer — see B8 LP model-selector fix for the same lesson)
      7. verify by re-reading the chip innerText — it updates to contain
         ``crop_9_16`` / ``crop_16_9`` and is the canonical post-close truth.
    """
    if not ratio or ratio == "16:9":
        logger.info("Using default aspect ratio 16:9 (no panel interaction)")
        return

    if ratio not in RATIO_IDS:
        logger.warning(
            "Aspect ratio %r unsupported for video (only 9:16 / 16:9). "
            "Falling back to default 16:9.",
            ratio,
        )
        return

    suffix = RATIO_IDS[ratio]

    chip_btn = page.locator('button[aria-haspopup="menu"]').filter(
        has_text=re.compile(r"video.*x\d", re.IGNORECASE),
    ).first
    await chip_btn.click(timeout=3000)

    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="visible", timeout=3000,
    )

    video_tab = page.locator('[id$="-trigger-VIDEO"]').first
    if await video_tab.get_attribute("data-state") != "active":
        await video_tab.click(timeout=2000)
        await page.wait_for_function(
            '() => document.querySelector(\'[id$="-trigger-VIDEO"]\')?.dataset.state === "active"',
            timeout=2000,
        )

    trigger = page.locator(f'[id$="-trigger-{suffix}"]').first
    await trigger.click(timeout=3000)

    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{suffix}"]\')?.dataset.state === "active"',
        timeout=3000,
    )

    # Click-outside to close. Top-left viewport is a safe dead zone outside
    # both the bottom-center composer and the bottom-right chip panel.
    await page.mouse.click(10, 10)
    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="hidden", timeout=2000,
    )

    expected_icon = "crop_9_16" if ratio == "9:16" else "crop_16_9"
    chip_text = await chip_btn.inner_text(timeout=2000)
    if expected_icon not in chip_text:
        logger.warning(
            "Aspect ratio set to %s but chip verify failed: chip_text=%r expected substring %r",
            ratio, chip_text, expected_icon,
        )
    else:
        logger.info("Aspect ratio set to %s (chip verified: %s)", ratio, expected_icon)


async def _count_visible_cards(page) -> int:
    """Count visible media cards."""
    try:
        return await page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            const tiles = document.querySelectorAll('[data-tile-id]');
            return Math.max(videos.length, tiles.length);
        }""")
    except Exception:
        return 0
