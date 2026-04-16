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
    # The model chip button contains text like "Video x1" or model name
    chip_selectors = [
        "button:has-text('Veo')",
        "button:has-text('Video')",
        "button:has-text('Videox1')",
        "[role='button']:has-text('Veo')",
        "[role='listbox']",
    ]

    opened = False
    for sel in chip_selectors:
        try:
            chip = page.locator(sel).first
            if await chip.is_visible(timeout=2000):
                await chip.click(timeout=2000)
                opened = True
                break
        except Exception:
            continue

    if not opened:
        logger.warning("Could not find model selector chip")
        return False

    # Step 2: Wait for menu to appear
    await asyncio.sleep(0.5)

    # Step 3: Find target model in menu
    try:
        # Look for menuitem containing the target text
        menu_item = page.locator(
            "menuitem, [role='menuitem'], [role='option']"
        ).filter(
            has_text=re.compile(
                re.escape(target_text.split("[")[0].strip()), re.IGNORECASE
            )
        )

        # If looking for LP, specifically look for "Lower Priority"
        if "Lower Priority" in target_text:
            menu_item = page.locator(
                "menuitem, [role='menuitem'], [role='option']"
            ).filter(has_text=re.compile(r"Lower Priority", re.IGNORECASE))

            # If multiple LP models, pick the one matching our base model name
            base_name = target_text.split(" [")[0]  # "Veo 3.1 - Fast"
            items = menu_item
            count = await items.count()
            for i in range(count):
                item_text = await items.nth(i).inner_text()
                if base_name.lower() in item_text.lower():
                    await items.nth(i).click(timeout=2000)
                    logger.info("Selected LP model: %s", item_text.strip())
                    await asyncio.sleep(0.5)
                    return await _verify_credits(page, expected=0)

            # Fallback: just click first LP model
            if count > 0:
                text = await items.first.inner_text()
                await items.first.click(timeout=2000)
                logger.info("Selected first LP model: %s", text.strip())
                await asyncio.sleep(0.5)
                return await _verify_credits(page, expected=0)
        else:
            # Non-LP model
            if await menu_item.first.is_visible(timeout=2000):
                await menu_item.first.click(timeout=2000)
                logger.info("Selected model: %s", target_text)
                await asyncio.sleep(0.5)
                return True

    except Exception as e:
        logger.error("Failed to select model: %s", e)

    # Close menu
    try:
        await page.keyboard.press("Escape")
    except Exception:
        pass

    return False


async def _verify_credits(page, expected: int = 0) -> bool:
    """Verify credit cost matches expected value."""
    try:
        # Look for credit text:
        #   EN: "Generating will use X credits"
        #   VI: "Qua trinh tao se ton X tin dung"
        text = await page.evaluate(
            """() => {
            const body = document.body.innerText || '';
            const en = body.match(/will use (\\d+) credits?/i);
            const vi = body.match(/tốn (\\d+) tín dụng/i);
            return en ? en[1] : (vi ? vi[1] : null);
        }"""
        )

        if text is not None:
            cost = int(text)
            if cost == expected:
                logger.info("Credit verification OK: %d credits", cost)
                return True
            logger.warning("Credit mismatch: expected %d, got %d", expected, cost)
            return False

        logger.warning("Could not find credit text -- assuming OK")
        return True
    except Exception as e:
        logger.warning("Credit verify error: %s", e)
        return True


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
