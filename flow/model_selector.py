"""Model selector -- pick Flow video models with rollout-safe aliases."""

import asyncio
import json
import re
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Primary video registry. Keep LP labels out of this data; legacy LP strings
# stay in alias/fallback compatibility data below during rollout.
MODEL_REGISTRY = {
    "omni-flash": {"display_label": "Omni Flash", "tier": "paid"},
    "veo-3.1-lite": {"display_label": "Veo 3.1 - Lite", "tier": "free"},
    "veo-3.1-fast": {"display_label": "Veo 3.1 - Fast", "tier": "free"},
    "veo-3.1-quality": {"display_label": "Veo 3.1 - Quality", "tier": "free"},
}

# Backwards-compatible input aliases for stored jobs and older callers.
MODEL_ALIASES = {
    "veo-3.1-lite-lp": "veo-3.1-lite",
    "veo-3.1-fast-lp": "veo-3.1-fast",
}

# Legacy UI labels tolerated while rollout completes. Canonical labels are
# always searched first, so LP labels are fallback-only.
LEGACY_MODEL_FALLBACK_LABELS = {
    "veo-3.1-lite": ["Veo 3.1 - Lite [Lower Priority]"],
    "veo-3.1-fast": ["Veo 3.1 - Fast [Lower Priority]"],
}

# Model name mapping (user-facing -> what to look for in DOM)
MODEL_MAP = {
    key: metadata["display_label"] for key, metadata in MODEL_REGISTRY.items()
}

DEFAULT_MODEL = "veo-3.1-lite"
DEFAULT_MAX_CREDITS_PER_JOB = 10
_AI_LOCATOR_TRUE_VALUES = {"1", "true", "yes", "on"}
_MODEL_CHIP_AI_CACHE_KEY = "flow.model_selector.model_chip"
_MODEL_DROPDOWN_AI_CACHE_KEY = "flow.model_selector.model_dropdown"


def _ai_locator_enabled() -> bool:
    return os.getenv("FLOW_AI_LOCATOR_ENABLED", "false").lower() in _AI_LOCATOR_TRUE_VALUES


async def _click_ai_locator_result(page, result, *, timeout_ms: int = 3000) -> bool:
    if result.selector:
        target = page.locator(result.selector).first
        await target.click(timeout=timeout_ms)
        return True
    if result.coordinates:
        await page.mouse.click(*result.coordinates)
        return True
    return False


class _FallbackCreditBudgetExceeded(ValueError):
    def __init__(
        self,
        cost: Optional[int] = None,
        budget: Optional[int] = None,
        message: Optional[str] = None,
    ):
        synthesized_message = message or f"cost {cost} exceeds budget {budget}"
        super().__init__(synthesized_message)
        self.cost = cost
        self.budget = budget
        self.message = synthesized_message
        self.error_kind = "credit_budget_exceeded"


_MODEL_VARIANT_TOKENS = frozenset({"fast", "lite", "quality", "lower", "priority"})
_MODEL_ITEM_SELECTORS = (
    "menuitem, [role='menuitem'], [role='option'], "
    "button, [role='button'], [role='listbox'] button"
)


def _normalize_model_text(text: str) -> str:
    """Collapse punctuation/whitespace so Veo variant matching is format-stable."""
    normalized = re.sub(r"[^a-z0-9]+", " ", text.lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_model_family(text: str) -> str:
    """Strip variant markers so cross-phase LP -> Lite matching stays version-aware."""
    tokens = [
        token
        for token in _normalize_model_text(text).split()
        if token not in _MODEL_VARIANT_TOKENS
    ]
    return " ".join(tokens)


def _normalize_profile_name(profile: str | None) -> str:
    if profile is None:
        return "unknown"
    profile_name = profile.strip()
    return profile_name or "unknown"


def _dedupe_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped


def canonicalize_video_model_key(model: str | None, *, free_mode: bool = True) -> str:
    """Return primary video registry key, preserving rollout alias compatibility."""
    original = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    canonical = MODEL_ALIASES.get(original, original)
    if canonical != original:
        logger.warning(
            "Video model alias used: original=%s canonical=%s",
            original,
            canonical,
        )

    if canonical not in MODEL_REGISTRY:
        logger.warning(
            "Unknown video model '%s'; falling back to %s",
            original,
            DEFAULT_MODEL,
        )
        return DEFAULT_MODEL

    if free_mode and MODEL_REGISTRY[canonical]["tier"] == "paid":
        logger.warning(
            "Paid video model '%s' cannot be selected in free_mode; falling back to %s",
            canonical,
            DEFAULT_MODEL,
        )
        return DEFAULT_MODEL

    return canonical


def _candidate_filters_for_model(model_key: str, target_text: str) -> list[tuple[str, re.Pattern[str]]]:
    variant = target_text.rsplit("-", 1)[-1].strip() if "-" in target_text else target_text
    candidates: list[tuple[str, re.Pattern[str]]] = [
        (target_text, re.compile(re.escape(target_text), re.IGNORECASE)),
        (variant, re.compile(re.escape(variant), re.IGNORECASE)),
    ]
    candidates.extend(
        (label, re.compile(re.escape(label), re.IGNORECASE))
        for label in LEGACY_MODEL_FALLBACK_LABELS.get(model_key, [])
    )

    deduped: list[tuple[str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for label, pattern in candidates:
        if label in seen:
            continue
        seen.add(label)
        deduped.append((label, pattern))
    return deduped


def _rank_model_choice(item: tuple[object, str]) -> tuple[int, str]:
    text = item[1]
    return (1 if "Lower Priority" in text else 0, _normalize_model_text(text))


def _max_credits_per_job() -> int:
    raw = os.getenv("FLOW_MAX_CREDITS_PER_JOB", str(DEFAULT_MAX_CREDITS_PER_JOB))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid FLOW_MAX_CREDITS_PER_JOB=%r; using default %d",
            raw,
            DEFAULT_MAX_CREDITS_PER_JOB,
        )
        return DEFAULT_MAX_CREDITS_PER_JOB
    if value < 0:
        logger.warning(
            "Invalid negative FLOW_MAX_CREDITS_PER_JOB=%r; using default %d",
            raw,
            DEFAULT_MAX_CREDITS_PER_JOB,
        )
        return DEFAULT_MAX_CREDITS_PER_JOB
    return value


def _credit_budget_exceeded_class():
    # Unit A merges before Unit D in Wave 1, so import lazily and use a plain
    # ValueError-derived fallback until flow.operations._base.CreditBudgetExceeded exists.
    try:
        from flow.operations._base import CreditBudgetExceeded

        return CreditBudgetExceeded
    except ImportError:
        return _FallbackCreditBudgetExceeded


def _extract_model_label(raw_text: str) -> str | None:
    for line in raw_text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if not cleaned:
            continue
        normalized = _normalize_model_text(cleaned)
        if normalized.startswith("veo") or normalized.startswith("imagen") or normalized.startswith("nano"):
            return cleaned
    return None


def _build_free_model_failure_message(
    reason: str,
    *,
    visible_models: list[str] | None = None,
    profile: str | None = None,
) -> str:
    return (
        f"free_model_select_failed: {reason}. "
        f"Profile={_normalize_profile_name(profile)} "
        f"Visible={json.dumps(_dedupe_texts(visible_models or []), ensure_ascii=False)}. "
        "Manual intervention needed."
    )


async def _collect_visible_model_labels(page) -> list[str]:
    try:
        items = page.locator(_MODEL_ITEM_SELECTORS)
        count = await items.count()
    except Exception:
        return []

    labels: list[str] = []
    for index in range(count):
        try:
            item = items.nth(index)
            if not await item.is_visible(timeout=0):
                continue
            text = (await item.inner_text()).strip()
        except Exception:
            continue

        label = _extract_model_label(text)
        if label:
            labels.append(label)

    return _dedupe_texts(labels)


def _raise_ambiguous_free_model_error(
    base_name: str,
    candidate_text: str,
    visible_variants: list[str],
    profile: str | None = None,
) -> None:
    logger.error(
        "Ambiguous free model variants for '%s' under '%s': %s",
        base_name,
        candidate_text,
        visible_variants,
    )
    raise RuntimeError(
        _build_free_model_failure_message(
            f"Ambiguous {candidate_text} model variants for requested model '{base_name}'",
            visible_models=visible_variants,
            profile=profile,
        )
    )


async def _pick_free_model_item(
    items,
    count: int,
    base_name: str,
    candidate_text: str,
    profile: str | None = None,
):
    """Resolve the best free-mode locator match without guessing across variants."""
    normalized_base = _normalize_model_text(base_name)
    normalized_family = _normalize_model_family(base_name)
    exact_matches = []
    family_matches = []
    visible_variants = []

    for i in range(count):
        item = items.nth(i)
        item_text = (await item.inner_text()).strip()
        visible_variants.append(item_text)
        normalized_item = _normalize_model_text(item_text)

        if normalized_base and normalized_base in normalized_item:
            exact_matches.append((item, item_text))
            continue

        if normalized_family and normalized_family in normalized_item:
            family_matches.append((item, item_text))

    if exact_matches:
        return sorted(exact_matches, key=_rank_model_choice)[0]

    if len(family_matches) == 1:
        return family_matches[0]

    non_lp_family_matches = [
        match for match in family_matches if "Lower Priority" not in match[1]
    ]
    if len(non_lp_family_matches) == 1:
        return non_lp_family_matches[0]

    if len(family_matches) > 1 or count > 1:
        _raise_ambiguous_free_model_error(
            base_name,
            candidate_text,
            visible_variants,
            profile=profile,
        )

    if visible_variants:
        return items.first, visible_variants[0]

    return items.first, ""


async def select_model(
    page,
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
    *,
    profile: str | None = None,
) -> bool:
    """Select the specified model in the Flow UI.

    Steps:
    1. Find and click the model selector chip/button (shows current model name)
    2. Wait for the dropdown menu to appear
    3. Find the target model menuitem
    4. Click it
    5. Verify selection via credit footer text within configured budget

    Args:
        page: Playwright page
        model: Model key from MODEL_MAP or legacy alias
        free_mode: If True, use canonical free baseline unless caller passes
            a canonical non-paid model
        profile: Chrome profile name for surfaced operator alerts

    Returns True if model was selected successfully.
    """
    from flow.ai_locator import ai_locate

    # Resolve target model text. free_mode no longer coerces canonical models
    # back to retired LP aliases; the canonical baseline is Veo 3.1 Lite.
    model_key = canonicalize_video_model_key(model, free_mode=free_mode)
    target_text = MODEL_MAP[model_key]
    logger.info("Selecting model: %s", target_text)
    last_visible_model_labels: list[str] = []

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
        # Image / Frames / Ingredients modes persist per-account; their chip
        # icons differ (crop_square for Image, crop_free for Frames,
        # chrome_extension for Ingredients). Live MCP probe 2026-04-20:
        # ngoctuandt20 composer opened in Image mode after a t2i run, chip
        # text "🍌 Nano Banana Pro\ncrop_square\nx1" — none of the crop_16_9
        # / crop_9_16 / crop_1_1 / arrow_drop_down selectors matched, so the
        # chip never opened and Flow silently generated an image instead
        # of a video. Covering all four chip icons lets select_model find
        # + open the chip regardless of persisted mode; `_ensure_video_mode`
        # then flips to Video via the role=tab inside the open dropdown.
        "button[aria-haspopup='menu']:has(i:text-is('crop_square'))",
        "button[aria-haspopup='menu']:has(i:text-is('crop_free'))",
        "button[aria-haspopup='menu']:has(i:text-is('chrome_extension'))",
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

    if not opened and _ai_locator_enabled():
        result = await ai_locate(
            page,
            (
                "Open the Google Flow composer settings/model chip. Target the "
                "visible chip button near the composer that opens the Radix menu "
                "containing Image, Video, Frames, Ingredients, aspect ratio, output "
                "count, and model controls."
            ),
            candidates=(),
            cache_key=_MODEL_CHIP_AI_CACHE_KEY,
        )
        try:
            if await _click_ai_locator_result(page, result, timeout_ms=3000):
                opened = True
                logger.info("Opened model dropdown via AI locator")
        except Exception as exc:
            logger.debug("AI locator model chip click failed: %s", exc)

    if not opened:
        if free_mode:
            logger.warning(
                "Could not find model selector chip in free mode — aborting to avoid default paid model"
            )
            raise RuntimeError(
                _build_free_model_failure_message(
                    "Could not open model selector chip in free mode - aborting to avoid default paid model",
                    visible_models=last_visible_model_labels,
                    profile=profile,
                )
            )
        logger.warning("Could not find model selector chip — will proceed with default model")
        return False

    # Step 2: Wait for panel to appear
    await asyncio.sleep(0.5)

    # Step 2.2: Flow's settings chip dropdown is a combined panel — mode
    # tabs (Image / Video / Frames / Ingredients) on top, then aspect +
    # count + model. If a previous run on this account left the mode on
    # Image (or Frames / Ingredients), the chip opens there and the Veo
    # model options aren't reachable. Click the Video tab before picking
    # a model so we always operate in the right context.
    await _ensure_video_mode(page)

    # Step 2.5: Switch to Video tab
    # The model panel has TWO tabs: "Hình ảnh"/"Image" and "Video".
    # Veo models only appear under the Video tab.
    if any(kw in target_text.lower() for kw in ("veo", "video")):
        await _switch_to_video_tab(page)

    # Step 2.7: Check if LP items already visible BEFORE opening dropdown.
    # In extend mode, the model panel may already show LP options directly
    # without needing to click the Veo dropdown. Clicking it would TOGGLE
    # the dropdown closed, hiding the LP items.
    base_name = target_text.split(" [")[0].strip()  # "Veo 3.1 - Fast"
    free_model_candidates = _candidate_filters_for_model(model_key, target_text)

    # Pre-check: are fallback LP items already visible? Legacy-only optimization.
    dropdown_opened = False
    if LEGACY_MODEL_FALLBACK_LABELS.get(model_key):
        try:
            lp_items = page.locator(_MODEL_ITEM_SELECTORS).filter(
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
    free_model_candidate_found = False
    last_free_mode_error: Exception | None = None
    for attempt in range(3):
        if attempt > 0:
            logger.info("Model select retry %d, waiting 1.5s...", attempt + 1)
            await asyncio.sleep(1.5)

        try:
            if free_mode:
                for candidate_text, candidate_pattern in free_model_candidates:
                    items = page.locator(_MODEL_ITEM_SELECTORS).filter(
                        has_text=candidate_pattern
                    )
                    count = await items.count()
                    logger.info(
                        "Free model search (attempt %d): found %d items matching '%s'",
                        attempt + 1,
                        count,
                        candidate_text,
                    )

                    if count == 0:
                        continue

                    free_model_candidate_found = True
                    selected_item, selected_text = await _pick_free_model_item(
                        items,
                        count,
                        base_name,
                        candidate_text,
                        profile=profile,
                    )
                    last_visible_model_labels = await _collect_visible_model_labels(page)
                    if not last_visible_model_labels and selected_text:
                        last_visible_model_labels = [selected_text]

                    await selected_item.click(timeout=3000, force=True)
                    logger.info("Selected free model: %s", selected_text.strip()[:80])
                    await asyncio.sleep(0.5)
                    try:
                        ok = await _verify_credits(page)
                    finally:
                        await _close_model_panel_with_timeout(page, dropdown_opened)
                    if not ok:
                        raise RuntimeError(
                            _build_free_model_failure_message(
                                "Free model selection exceeded configured credit budget - aborting to avoid unintended paid generation",
                                visible_models=last_visible_model_labels,
                                profile=profile,
                            )
                        )
                    return True

                # Debug: log what model options are visible before retrying.
                await _debug_model_options(page)
                visible_model_labels = await _collect_visible_model_labels(page)
                if visible_model_labels:
                    last_visible_model_labels = visible_model_labels
                continue

            else:
                # Non-LP: match base model name
                items = page.locator(_MODEL_ITEM_SELECTORS).filter(
                    has_text=re.compile(re.escape(base_name), re.IGNORECASE)
                )
                if await items.first.is_visible(timeout=2000):
                    await items.first.click(timeout=3000, force=True)
                    logger.info("Selected model: %s", target_text)
                    await asyncio.sleep(0.5)
                    await _close_model_panel_with_timeout(page, dropdown_opened)
                    return True

        except Exception as e:
            logger.warning("Model select attempt %d failed: %s", attempt + 1, e)
            if free_mode:
                visible_model_labels = await _collect_visible_model_labels(page)
                if visible_model_labels:
                    last_visible_model_labels = visible_model_labels
                last_free_mode_error = e
                if isinstance(e, _credit_budget_exceeded_class()):
                    raise

    if free_mode:
        logger.warning(
            "Playwright selectors failed for free model selection — trying JS fallback"
        )
        js_ok = await _select_model_js(
            page,
            base_name,
            [candidate_text for candidate_text, _ in free_model_candidates],
            profile=profile,
        )
        if js_ok:
            visible_model_labels = await _collect_visible_model_labels(page)
            if visible_model_labels:
                last_visible_model_labels = visible_model_labels
            try:
                ok = await _verify_credits(page)
            finally:
                await _close_model_panel_with_timeout(page, dropdown_opened)
            if not ok:
                raise RuntimeError(
                    _build_free_model_failure_message(
                        "Free model selection exceeded configured credit budget after JS fallback - aborting to avoid unintended paid generation",
                        visible_models=last_visible_model_labels,
                        profile=profile,
                    )
                )
            return True

        visible_model_labels = await _collect_visible_model_labels(page)
        if visible_model_labels:
            last_visible_model_labels = visible_model_labels
        await _close_model_panel_with_timeout(page, dropdown_opened)
        if not free_model_candidate_found:
            if last_visible_model_labels:
                raise RuntimeError(
                    _build_free_model_failure_message(
                        f"Requested free model '{target_text}' not found in dropdown - Flow UI changed unexpectedly",
                        visible_models=last_visible_model_labels,
                        profile=profile,
                    )
                )

            raise RuntimeError(
                _build_free_model_failure_message(
                    "Free model search exhausted with no visible model options",
                    visible_models=last_visible_model_labels,
                    profile=profile,
                )
            )

        raise RuntimeError(
            _build_free_model_failure_message(
                "Failed to select free model after all attempts",
                visible_models=last_visible_model_labels,
                profile=profile,
            )
        ) from last_free_mode_error

    # All attempts exhausted — try JS fallback
    logger.warning("Playwright selectors failed — trying JS fallback for model selection")
    js_ok = await _select_model_js(page, base_name)
    if js_ok:
        await _close_model_panel_with_timeout(page, dropdown_opened)
        return True

    # Close menu
    await _close_model_panel_with_timeout(page, dropdown_opened)

    logger.error("Failed to select model after all attempts")
    return False


async def _ensure_video_mode(page) -> None:
    """Flip the composer to Video mode via the settings-chip dropdown tabs.

    Flow's settings chip opens a combined DropdownMenuContent with four
    ``button[role="tab"]`` elements — image / Video / Frames / Ingredients
    — plus aspect + count + model below. The persisted mode is remembered
    per account; a previous t2i run leaves the chip on Image, which hides
    the Veo model options entirely.

    Precondition: caller has just clicked the chip and ``aria-expanded``
    is true. This helper:

      1. Waits briefly for the dropdown to paint.
      2. Looks up the Video tab by innerText (``videocam\\nVideo`` — the
         icon ligature is on the same button so we just substring-match
         on 'Video' after stripping whitespace).
      3. If ``data-state`` is already 'active', no-ops.
      4. Otherwise clicks the tab, then sleeps to let the menu re-render.

    Silently no-ops when no ``role=tab`` elements are present — older
    composer variants (L2 edit panels with ``_switch_to_video_tab``
    semantics) use a different structure and are handled downstream.
    """
    await asyncio.sleep(0.4)
    try:
        tabs = page.locator('[role="menu"][data-state="open"] button[role="tab"]')
        count = await tabs.count()
        if count == 0:
            return

        video_tab = None
        prev_modes = []
        for i in range(count):
            tab = tabs.nth(i)
            try:
                txt = (await tab.inner_text()).strip()
                state = await tab.get_attribute("data-state")
                prev_modes.append(f"{txt!r}={state}")
                # Normalize 'videocam\nVideo' → 'video' match
                if re.search(r"(^|\s)video(\s|$)", txt, re.IGNORECASE):
                    video_tab = (tab, state)
            except Exception:
                continue

        if video_tab is None:
            logger.debug("_ensure_video_mode: no Video tab found in %s", prev_modes)
            return

        tab, state = video_tab
        if state == "active":
            logger.debug("_ensure_video_mode: already active (tabs=%s)", prev_modes)
            return

        await tab.click(timeout=2000)
        await asyncio.sleep(0.6)
        logger.info("_ensure_video_mode: switched to Video tab (was %s)", prev_modes)
    except Exception as e:
        logger.debug("_ensure_video_mode failed silently: %s", e)


async def _verify_credits(page, max_cost: int | None = None) -> bool:
    """Verify previewed credit cost fits configured per-job budget."""
    budget = _max_credits_per_job() if max_cost is None else max_cost
    try:
        result = await page.evaluate(
            """() => {
            const body = document.body.innerText || '';

            // Pattern 1: "will use X credits" / "t?n X t?n d?ng"
            const en = body.match(/will use (\\d+) credits?/i);
            if (en) return { cost: parseInt(en[1]), source: 'en_will_use' };
            const vi = body.match(/t?n (\\d+) t?n d?ng/i);
            if (vi) return { cost: parseInt(vi[1]), source: 'vi_ton' };

            // Pattern 2: "X credits" / "X t?n d?ng" near model text
            const credits = body.match(/(\\d+)\\s*credits?/i);
            if (credits) return { cost: parseInt(credits[1]), source: 'en_credits' };
            const tinDung = body.match(/(\\d+)\\s*t?n d?ng/i);
            if (tinDung) return { cost: parseInt(tinDung[1]), source: 'vi_tin_dung' };

            return null;
        }"""
        )

        if result is not None:
            cost = result["cost"]
            if cost <= budget:
                logger.info(
                    "Credit verification OK: %d credits <= budget %d (via %s)",
                    cost,
                    budget,
                    result["source"],
                )
                return True
            logger.warning(
                "Credit budget exceeded: cost %d > budget %d (via %s)",
                cost,
                budget,
                result["source"],
            )
            raise _credit_budget_exceeded_class()(cost=cost, budget=budget)

        raise _credit_budget_exceeded_class()(cost=None, budget=budget)
    except Exception as e:
        if isinstance(e, _credit_budget_exceeded_class()):
            raise
        logger.warning("Credit verify error: %s", e)
        raise _credit_budget_exceeded_class()(cost=None, budget=budget) from e


async def _open_model_dropdown(page) -> bool:
    """Click the model name button inside the panel to open model list.

    After switching to Video tab, the panel shows the current model:
      "Veo 3.1 - Fast arrow_drop_down" (264px wide button)
    OR if LP was previously selected:
      "Veo 3.1 - Fast [Lower Priority] arrow_drop_down"

    Clicking this opens the actual dropdown with LP model options.
    """
    from flow.ai_locator import ai_locate

    # Playwright: find button with "Veo" text + "arrow_drop_down" (the model name button)
    # First pass: prefer non-LP button (standard model name).
    # Second pass: accept LP button too (account remembered LP selection).
    #
    # B20 final (2026-04-19): pin the match on the canonical model-chip anchor
    # per docs/FLOW_BUTTON_EXACT.md §1.6 — `aria-haspopup='menu'` + exact
    # `arrow_drop_down` ligature — and narrow to Veo-prefixed buttons via a
    # compiled regex filter. A bare substring filter on the Veo keyword was
    # the B20 root cause: it matched any visible button whose textContent
    # happened to include "Veo" (e.g. a video-library label reflecting
    # a previously-selected model in an unrelated panel).
    try:
        veo_btns = page.locator(
            "button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))"
        ).filter(has_text=re.compile(r"^Veo", re.IGNORECASE))
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

    if _ai_locator_enabled():
        result = await ai_locate(
            page,
            (
                "In the open Google Flow composer settings panel on the Video tab, "
                "find the visible current video model dropdown button with arrow_drop_down. "
                "Do not choose model menu items, output count, aspect ratio, or mode tabs."
            ),
            candidates=(),
            cache_key=_MODEL_DROPDOWN_AI_CACHE_KEY,
        )
        try:
            if await _click_ai_locator_result(page, result, timeout_ms=3000):
                logger.info("Opened model dropdown via AI locator")
                await asyncio.sleep(1.0)
                return True
        except Exception as exc:
            logger.debug("AI locator model dropdown click failed: %s", exc)

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


async def _close_model_panel_with_timeout(
    page,
    dropdown_was_opened: bool = True,
    timeout_sec: float = 1.5,
) -> None:
    """Best-effort wrapper so panel dismissal can't wedge the whole job."""
    try:
        await asyncio.wait_for(
            _close_model_panel(page, dropdown_was_opened),
            timeout=timeout_sec,
        )
    except Exception as exc:
        logger.warning("Model panel close timed out/skipped: %s", exc)


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


async def _select_model_js(
    page,
    base_name: str,
    candidate_texts: list[str] | None = None,
    profile: str | None = None,
) -> bool:
    """JS fallback: click model option by scanning visible text.

    When ``candidate_texts`` is set, probe canonical labels first, then legacy
    compatibility labels before falling back to requested family/version match.
    """
    normalized_base = _normalize_model_text(base_name)
    normalized_family = _normalize_model_family(base_name)

    try:
        result = await page.evaluate("""(args) => {
            const normalize = (text) => (text || '')
                .toLowerCase()
                .replace(/[^a-z0-9]+/g, ' ')
                .replace(/\\s+/g, ' ')
                .trim();
            const candidateTexts = args.candidateTexts || [];
            const clickable = document.querySelectorAll(
                'menuitem, [role="menuitem"], [role="option"], button, [role="button"]'
            );
            const phases = candidateTexts.length ? candidateTexts : [''];

            for (const candidateText of phases) {
                const normalizedCandidate = normalize(candidateText);
                const matches = [];

                for (const el of clickable) {
                    const text = (el.innerText || '').trim();
                    const normalizedText = normalize(text);
                    const rect = el.getBoundingClientRect();
                    if (!normalizedText || rect.width < 30 || rect.height < 20) continue;

                    if (normalizedCandidate) {
                        if (!normalizedText.includes(normalizedCandidate)) continue;
                    } else if (!normalizedText.includes(args.normalizedBase)) {
                        continue;
                    }

                    matches.push({
                        el,
                        text: text.substring(0, 80),
                        normalizedText,
                    });
                }

                const exact = matches.find(
                    (match) => args.normalizedBase && match.normalizedText.includes(args.normalizedBase)
                );
                if (exact) {
                    const exactMatches = matches
                        .filter((match) => args.normalizedBase && match.normalizedText.includes(args.normalizedBase))
                        .sort((a, b) => Number(a.normalizedText.includes('lower priority')) - Number(b.normalizedText.includes('lower priority')));
                    exactMatches[0].el.click();
                    return {
                        status: 'clicked',
                        clickedText: exactMatches[0].text,
                        candidateText,
                    };
                }

                const familyMatches = matches.filter(
                    (match) => args.normalizedFamily && match.normalizedText.includes(args.normalizedFamily)
                );
                if (familyMatches.length === 1) {
                    familyMatches[0].el.click();
                    return {
                        status: 'clicked',
                        clickedText: familyMatches[0].text,
                        candidateText,
                    };
                }

                const nonLpFamilyMatches = familyMatches.filter(
                    (match) => !match.normalizedText.includes('lower priority')
                );
                if (nonLpFamilyMatches.length === 1) {
                    nonLpFamilyMatches[0].el.click();
                    return {
                        status: 'clicked',
                        clickedText: nonLpFamilyMatches[0].text,
                        candidateText,
                    };
                }

                if (familyMatches.length > 1 || matches.length > 1) {
                    return {
                        status: 'ambiguous',
                        candidateText,
                        visibleTexts: matches.map((match) => match.text),
                    };
                }

                if (matches.length === 1) {
                    matches[0].el.click();
                    return {
                        status: 'clicked',
                        clickedText: matches[0].text,
                        candidateText,
                    };
                }
            }

            return {status: 'none'};
        }""", {
            "candidateTexts": candidate_texts or [],
            "normalizedBase": normalized_base,
            "normalizedFamily": normalized_family,
        })
    except Exception as e:
        logger.warning("JS model select failed: %s", e)
        return False

    if result and result.get("status") == "ambiguous":
        visible_variants = result.get("visibleTexts") or []
        candidate_text = result.get("candidateText") or "free model"
        _raise_ambiguous_free_model_error(
            base_name,
            candidate_text,
            visible_variants,
            profile=profile,
        )

    if result and result.get("status") == "clicked":
        logger.info("Selected model via JS fallback: %s", result.get("clickedText"))
        await asyncio.sleep(0.5)
        return True

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
    """Read the currently selected model from the UI chip.

    B20 final (2026-04-19): use the canonical model-chip anchor per
    ``docs/FLOW_BUTTON_EXACT.md §1.6`` — ``aria-haspopup='menu'`` + exact
    ``arrow_drop_down`` Material Icon ligature — combined with a compiled
    regex filter anchored on ``^Veo``. A fuzzy substring selector on the
    Veo keyword collided with unrelated buttons whose textContent
    reflected a previous model name (same class as the B20 root cause
    absorbed by B26 for ``'Video'``).
    """
    try:
        chip = page.locator(
            "button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))"
        ).filter(has_text=re.compile(r"^Veo", re.IGNORECASE)).first
        if await chip.is_visible(timeout=1000):
            return (await chip.inner_text()).strip()
    except Exception:
        pass
    return None
