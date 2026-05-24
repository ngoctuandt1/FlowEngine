"""Ingredients-to-video - Level 1 operation with multiple image references.

Live-probed 2026-04-20 on the current Flow UI:
- New projects open in Image mode by default (`crop_square` chip).
- Switching the composer menu to `Video` -> `Ingredients` works via exact
  `[role='tab']:text-is(...)` selectors.
- The ingredients `+` button opens a compact menu with `Upload image`, not a
  full asset-picker tab panel. Uploading through the native file chooser adds
  media tiles to the left rail; those tiles are the reliable pre-submit proof
  that the references are attached.
"""

import asyncio
import logging
import os

from flow.download import download_video
from flow.landing import dismiss_flow_marketing_landing, recover_from_flow_canvas_page
from flow.login import handle_login_redirect, is_login_page
from flow.model_selector import DEFAULT_MODEL, select_model
from flow.navigation import extract_project_id, flow_url
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.operations._base import resolve_final_media_id
from flow.operations.frames_to_video import (
    COMPOSER_MENU_SELECTORS,
    NEW_PROJECT_SELECTORS,
    _click_new_project,
    _close_composer_menu,
    _extract_replay_media_ids,
    _finalize_l1_replay_result,
    _project_id_from_template_or_page,
    _resolve_image_input_path,
)
from flow.operations.generate import (
    _count_visible_cards,
    _dismiss_overlays,
    _ensure_video_composer_mode,
    _guard_l1_submit,
    _set_aspect_ratio,
    _set_output_count,
    _select_video_composer_subtab,
    _type_prompt,
    _verify_ingredients_upload_affordance,
    _wait_for_composer,
)

try:  # Wave-1 reverse API helper; guarded so default UI path is independent.
    from flow.operations.ingredients_api import (
        get_i2v_request_template,
        install_i2v_request_capture,
        replay_i2v_via_inflate,
    )
except Exception as exc:  # pragma: no cover - guarded fallback
    get_i2v_request_template = None
    install_i2v_request_capture = None
    replay_i2v_via_inflate = None
    _I2V_API_IMPORT_ERROR = exc
else:
    _I2V_API_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# 2026-05 Flow UI: the per-ingredient `+` button is rendered as
# `<button><i>add_2</i><span>Create</span></button>` (32×32, icon
# ligature `add_2`, NOT the legacy `add` icon). The sibling `add\nAdd
# Clip` button is a 28×28 timeline-add control and must be excluded —
# clicking it opens the wrong dialog. We anchor on the exact icon
# ligature `add_2` and accept the new label `Create`. Live-probed
# 2026-05-21 — see docs/livetest-2026-05-21/l1_ingredients_upload_probe.json.
INGREDIENT_PLUS_SELECTOR = (
    # Composer ingredient trigger: `<button><i>add_2</i><span>Create</span></button>`
    # at the bottom-left of the prompt composer (32×32 icon button). Live-probed
    # 2026-05-21 — see docs/livetest-2026-05-21/l1_ingredients_upload_probe.json.
    # We anchor on `add_2` icon AND require the button to contain the literal
    # "Create" text WITHOUT "New project" (the homepage `+ New project` button
    # also has icon `add_2`; if both are momentarily in the DOM we click the wrong
    # one). `has-text` matches substring, so the negation excludes homepage tile.
    "button:has(i:text-is('add_2')):has-text('Create'):not(:has-text('New project')):not([title*='Add Media'])"
)


def _reverse_i2v_enabled() -> bool:
    return os.getenv("FLOW_I2V_VIA_REVERSE", "0") == "1"


def _install_i2v_capture_if_enabled(client) -> bool:
    if not _reverse_i2v_enabled():
        return False
    if install_i2v_request_capture is None:
        logger.info(
            "FLOW_I2V_VIA_REVERSE=1 but ingredients_api unavailable; continuing UI path (%s)",
            _I2V_API_IMPORT_ERROR,
        )
        return False
    try:
        install_i2v_request_capture(client)
    except Exception as exc:
        logger.info("I2V request capture install failed; continuing UI path: %s", exc)
        return False
    return True


def _current_i2v_template(client):
    if get_i2v_request_template is None:
        return None
    try:
        return get_i2v_request_template(client)
    except Exception as exc:
        logger.info("I2V request template unavailable; continuing UI path: %s", exc)
        return None


async def ingredients_to_video(
    client,
    prompt: str,
    ingredient_image_paths: list[str],
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "16:9",
    free_mode: bool = True,
) -> dict:
    """Create a new video project from text plus ingredient image references."""
    if not ingredient_image_paths:
        raise RuntimeError("ingredients-to-video requires at least one ingredient image")
    ingredient_image_paths = [
        _resolve_image_input_path(path, label=f"Ingredient #{idx}")
        for idx, path in enumerate(ingredient_image_paths, start=1)
    ]

    page = client.page
    capture_ready = _install_i2v_capture_if_enabled(client)
    locale = ""
    homepage = flow_url(locale)

    if capture_ready and replay_i2v_via_inflate is not None:
        template = _current_i2v_template(client)
        if template is not None:
            try:
                if "/vi/" in str(getattr(page, "url", "")):
                    locale = "vi"
                replay_project_id = _project_id_from_template_or_page(template, getattr(page, "url", ""))
                client.clear_captures()
                replay_result = await replay_i2v_via_inflate(
                    client,
                    prompt,
                    ingredient_image_paths,
                )
                replay_media_ids = _extract_replay_media_ids(replay_result)
                if not replay_media_ids:
                    raise RuntimeError("I2V reverse-API replay returned no media_id")
                replay_media_id = replay_media_ids[0]
                replay_count = getattr(client, "_i2v_replay_count", 0) + 1
                setattr(client, "_i2v_replay_count", replay_count)
                logger.info(
                    "I2V replay submit accepted via reverse API "
                    "(count=%d media_ids=%s) -- finalizing via status API + direct URL download",
                    replay_count,
                    replay_media_ids,
                )
                return await _finalize_l1_replay_result(
                    client,
                    project_id=replay_project_id,
                    locale=locale,
                    replay_media_id=replay_media_id,
                    operation_label="ingredients-to-video",
                    replay_source="i2v_replay",
                    failure_prefix="i2v_replay",
                    download_prefix="ingredients",
                )
            except RuntimeError as exc:
                logger.warning(
                    "I2V reverse-API replay failed; falling back to UI path: %s",
                    exc,
                )

    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector(
            "text=/New project|Dự án mới|Tạo dự án/",
            state="attached",
            timeout=4000,
        )
    except Exception:
        pass  # marketing landing or slow load — recovery logic below handles it

    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it
        current = page.url

    if "/vi/" in current:
        locale = "vi"

    # 2026-05-21 hardening: Flow sometimes serves the marketing landing
    # ("Create with Flow" CTA) even for logged-in sessions — same surface
    # `text_to_video` already handles (see flow/operations/generate.py:444).
    # Without dismissing it, `_click_new_project` immediately fails because
    # the "+ New project" button isn't in the marketing DOM. Live-burned
    # by Bug B ingredients on s17524h173 right after a profile re-warm.
    async def _new_project_ready() -> bool:
        for selector in NEW_PROJECT_SELECTORS:
            try:
                if await page.locator(selector).first.is_visible(timeout=500):
                    return True
            except Exception:
                continue
        return False

    if not await _new_project_ready():
        try:
            await dismiss_flow_marketing_landing(page, logger, _new_project_ready)
        except Exception as exc:
            logger.warning("ingredients: marketing-landing dismissal failed: %s", exc)
        if not await _new_project_ready():
            try:
                await recover_from_flow_canvas_page(page, logger, homepage)
            except Exception as exc:
                logger.warning("ingredients: canvas recovery failed: %s", exc)

    await _dismiss_overlays(page)
    await _click_new_project(page)

    try:
        await page.wait_for_url("**/project/**", timeout=20000)
    except Exception:
        await asyncio.sleep(1)

    current = page.url
    if is_login_page(current):
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            raise RuntimeError("Google login required - profile session expired.")
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it
        await _click_new_project(page)
        try:
            await page.wait_for_url("**/project/**", timeout=20000)
        except Exception:
            await asyncio.sleep(1)

    project_url_full = page.url
    project_id = extract_project_id(project_url_full)

    await _wait_for_composer(page)
    await _ensure_video_composer_mode(page)
    await _set_output_count(page, 1)
    await select_model(page, model=model, free_mode=free_mode, profile=client.profile_name)
    await _ensure_video_composer_mode(page)
    await _set_output_count(page, 1)
    await _set_aspect_ratio(page, aspect_ratio)
    await _ensure_ingredients_mode(page)
    await _close_composer_menu(page)
    await _verify_ingredients_mode(page)
    await _verify_ingredients_upload_affordance(page)

    for expected_count, image_path in enumerate(ingredient_image_paths, start=1):
        await _upload_ingredient_with_retry(page, image_path, expected_count)

    await _ensure_uploaded_ingredient_count(page, expected=len(ingredient_image_paths))
    await _type_prompt(page, prompt)

    await _guard_l1_submit(page)
    before_cards = await _count_visible_cards(page)
    client.clear_captures()
    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        timeout_sec=15.0,
        prompt_text=prompt,
    )
    if not confirmed:
        raise RuntimeError("Submit not confirmed - generation may not have started")

    result = await wait_for_completion(client, job_type="ingredients-to-video")
    if not result.get("done"):
        raise RuntimeError(f"Generation failed: {result.get('error', 'unknown')}")

    current_url = page.url
    captured_media_ids = result.get("media_ids") or []
    fallback_media_id = captured_media_ids[0] if captured_media_ids else None
    media_id = await resolve_final_media_id(page, fallback=fallback_media_id)

    edit_url_val = None
    if media_id and project_id:
        edit_url_val = f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
    output_files = await download_video(
        client,
        media_ids=captured_media_ids or ([media_id] if media_id else []),
        prefix="ingredients",
        metadata={
            "job_type": "ingredients-to-video",
            "prompt": prompt,
            "media_id": media_id or "",
            "project_url": project_url,
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        raise RuntimeError("ingredients-to-video: no output file captured")

    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _ensure_ingredients_mode(page) -> None:
    await _select_video_composer_subtab(page, "Ingredients")


async def _click_exact_tab(page, label: str) -> None:
    tab = page.locator(f"[role='tab']:text-is('{label}')").first
    if not await tab.is_visible(timeout=3000):
        raise RuntimeError(f"Composer tab not found: {label}")
    if await tab.get_attribute("data-state") != "active":
        await tab.click(timeout=3000)
        await asyncio.sleep(0.3)


async def _upload_ingredient(page, image_path: str) -> None:
    """Upload a single ingredient image via the 2026-05 media picker.

    Flow's UI no longer exposes a direct file <input>; clicking the
    `add_2 Create` button opens a media picker dialog. The picker has
    an `Upload media` action that finally surfaces a file chooser.
    After upload the picker may show a "Notice" rights confirmation
    and an `Add to Prompt` button — both must be handled before the
    chip appears in the composer. Live-probed via
    `scripts/probe_l1_composer_uploads.py ingredients`; evidence at
    docs/livetest-2026-05-21/l1_ingredients_upload_probe.json.
    """
    plus_button = await _locate_ingredient_plus_button(page)
    await plus_button.click(timeout=3000)

    # Best-effort: the 2026-05 picker has sidebar tabs (All / Images /
    # Videos / Voices / Characters / Avatar / Uploads). The Upload-media
    # action is visible from any tab in current variants, but switching
    # to Uploads first matches the documented happy path. Failure is
    # silently ignored — the upload-button search below covers both
    # states.
    try:
        uploads_tab = page.locator(
            "[role='dialog'] [role='tab']:has-text('Uploads'), "
            "[role='dialog'] button:has-text('Uploads'), "
            "[role='tablist'] [role='tab']:has-text('Uploads')"
        ).last
        if await uploads_tab.is_visible(timeout=2000):
            await uploads_tab.click(timeout=2000)
            await asyncio.sleep(0.4)
    except Exception as exc:
        logger.debug("Uploads tab not visible or already active: %s", exc)

    # Mirror frames_to_video.py:_click_picker_upload_media — iterate
    # broader selector list, do NOT restrict to [role='dialog'] (some
    # picker variants render Upload media inside a Radix popover that
    # isn't tagged role=dialog). Wait inside the iteration so the
    # picker has time to paint.
    upload_selectors = [
        "button:has(i:text-is('upload')):has-text('Upload media')",
        "button:has-text('Upload media')",
        "[role='button']:has-text('Upload media')",
        "[role='menuitem']:has-text('Upload media')",
        "[role='menuitem']:text-is('Upload image')",
        "button:has(i:text-is('upload'))",
    ]
    last_error: Exception | None = None
    for attempt, selector in enumerate(upload_selectors):
        button = page.locator(selector).last
        try:
            if not await button.is_visible(timeout=3000 if attempt == 0 else 1000):
                continue
            async with page.expect_file_chooser(timeout=8000) as chooser_info:
                await button.click(timeout=3000)
            chooser = await chooser_info.value
            await chooser.set_files(image_path)
            await asyncio.sleep(0.5)
            logger.info("Ingredient upload used selector: %s", selector)
            break
        except Exception as exc:
            last_error = exc
            continue
    else:
        raise RuntimeError(
            "Ingredient upload action not found after clicking the + button"
        ) from last_error

    await _accept_ingredient_rights_notice(page)
    # 2026-05 picker requires the uploaded asset to finish uploading
    # AND auto-select before the bottom 'Add to Prompt' button is
    # enabled. Click-when-disabled is a no-op. Poll for the enabled
    # state before pressing it. ~20 s covers ing1.jpg (~5 s upload)
    # plus a slow network margin.
    await _wait_for_picker_commit_enabled(page, timeout_sec=20.0)
    await _commit_uploaded_tile_in_picker(page)
    await _click_add_to_prompt_if_present(page)
    await asyncio.sleep(0.5)


async def _wait_for_picker_commit_enabled(page, timeout_sec: float = 20.0) -> bool:
    """Poll until the picker's 'Add to Prompt' button is enabled.

    2026-05 picker (live-probed 2026-05-24): clicking 'Add to Prompt'
    while it's still disabled (no asset selected / upload pending) is
    a silent no-op, leaving the chip count at 0. Block until the
    button reports ``disabled === false`` or the timeout expires.

    Returns True if enabled state observed, False on timeout. Caller
    proceeds regardless — fallback paths still try the click.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            enabled = await page.evaluate(
                r"""() => {
                    const dialog = document.querySelector('[role="dialog"]');
                    if (!dialog) return false;
                    const btns = [...dialog.querySelectorAll('button')];
                    const cta = btns.find(b => /Add to Prompt/i.test(b.innerText || ''));
                    if (!cta) return false;
                    return !cta.disabled && cta.getAttribute('aria-disabled') !== 'true';
                }"""
            )
            if enabled:
                logger.info("Picker 'Add to Prompt' button enabled — upload settled")
                return True
        except Exception as exc:
            logger.debug("Commit-enabled poll error: %s", exc)
        await asyncio.sleep(0.5)
    logger.warning("Picker 'Add to Prompt' button stayed disabled within %.1fs", timeout_sec)
    return False


async def _commit_uploaded_tile_in_picker(page, timeout_sec: float = 4.0) -> None:
    """Click the just-uploaded tile inside the media picker.

    2026-05 picker schema (live-probed 2026-05-24, docs/livetest-2026-05-24/
    probe_findings.md): the dialog has no bottom Add/Confirm/Insert button.
    The only commit affordance is clicking the uploaded asset's tile in
    the picker grid; tile-click attaches the asset to the composer and
    closes the picker.

    Strategy:
      1. Wait up to ~timeout for an image tile to appear inside the
         picker dialog. We anchor on the grid container by looking for
         ``[role='dialog']`` descendants that are clickable + contain an
         ``<img>``; the earlier "No results found" state has no such
         tile.
      2. Click the first matching tile (newest uploads sort first).
      3. If after the click the picker is still visible, also try a
         primary "Add"-style button (some variants may add one once a
         selection exists).

    Best-effort: missing tile selector raises only when neither a tile
    nor an Add button is found within the timeout. Callers wrap this in
    the ingredient-count retry, so a transient miss still gets a 2nd
    chance.
    """
    tile_selector = (
        "[role='dialog'] [role='gridcell'] img, "
        "[role='dialog'] [role='option'] img, "
        "[role='dialog'] button:has(img):not(:has-text('Upload media')), "
        "[role='dialog'] [role='button']:has(img):not(:has-text('Upload media'))"
    )
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        # Diagnostic-first: enumerate candidate tiles via page.evaluate
        # so we ALWAYS know what's actually in the picker, even when
        # the click step throws. The previous try/except wrapped both
        # the selector resolution and the click; locator-evaluate
        # failures silently muted the candidate log on every retry.
        try:
            candidates = await page.evaluate(
                r"""(selector) => {
                    const out = [];
                    for (const el of document.querySelectorAll(selector)) {
                        const target = el.closest('button, [role="button"], [role="option"], [role="gridcell"]') || el;
                        const rect = target.getBoundingClientRect();
                        if (rect.width < 30 || rect.height < 30) continue;
                        const img = target.querySelector('img');
                        out.push({
                            tag: target.tagName,
                            role: target.getAttribute('role') || '',
                            text: (target.innerText || '').trim().slice(0, 80),
                            imgSrc: img ? (img.src || '').slice(0, 120) : '',
                            imgW: img ? img.naturalWidth : 0,
                            imgH: img ? img.naturalHeight : 0,
                            rect: {x: Math.round(rect.x), y: Math.round(rect.y),
                                   w: Math.round(rect.width), h: Math.round(rect.height)},
                        });
                        if (out.length >= 6) break;
                    }
                    return out;
                }""",
                tile_selector,
            )
        except Exception as exc:
            candidates = []
            logger.debug("Tile candidate enumeration failed: %s", exc)

        if candidates:
            logger.info("Picker tile candidates (%d): %s", len(candidates), candidates)
            try:
                tile = page.locator(tile_selector).first
                await tile.click(timeout=3000, force=True)
                await asyncio.sleep(0.5)
                logger.info("Clicked newly-uploaded tile to attach ingredient")
                try:
                    after = await page.evaluate(
                        "() => ({dialogOpen: !!document.querySelector('[role=\"dialog\"]'), "
                        "dialogText: (document.querySelector('[role=\"dialog\"]')?.innerText||'').slice(0,400), "
                        "imgCount: document.querySelectorAll('img').length, "
                        "composerImgs: [...document.querySelectorAll('img')].filter(i => { const r = i.getBoundingClientRect(); return r.width >= 40 && r.width <= 200; }).length})"
                    )
                    logger.info("Post-tile-click state: %s", after)
                except Exception as exc:
                    logger.debug("Post-click dump failed: %s", exc)
                return
            except Exception as exc:
                logger.warning("Tile click failed despite visible candidate: %s", exc)
        await asyncio.sleep(0.5)

    logger.warning(
        "No uploaded tile found inside picker within %.1fs", timeout_sec
    )
    # Diagnostic: dump picker DOM so next session has evidence to chase.
    try:
        dump = await page.evaluate(
            "() => { const d = document.querySelector('[role=\"dialog\"]'); "
            "if (!d) return {noDialog: true}; "
            "const btns = [...d.querySelectorAll('button')].map(b => ({label: (b.innerText||'').slice(0,40), hidden: b.offsetParent===null, disabled: b.disabled})); "
            "const imgs = [...d.querySelectorAll('img')].slice(0,10).map(i => ({src: (i.src||'').slice(0,80), w: i.naturalWidth, h: i.naturalHeight})); "
            "return {dialogText: (d.innerText||'').slice(0,800), buttons: btns, images: imgs}; }"
        )
        logger.warning("Picker dump on tile-not-found: %s", dump)
    except Exception:
        pass


async def _accept_ingredient_rights_notice(page) -> None:
    """Accept the 2026-05 'I have the rights' notice dialog if present.

    First media upload per session opens a Workspace rights-confirmation
    dialog. The button is labelled "I agree" (and may appear as
    [role='button']). Best-effort: missing dialog is a no-op.
    """
    try:
        dialog = page.locator("[role='dialog']:has-text('Notice')").last
        if not await dialog.is_visible(timeout=2000):
            return
        agree = dialog.locator(
            "button:has-text('I agree'), [role='button']:has-text('I agree')"
        ).last
        if await agree.is_visible(timeout=1500):
            await agree.click(timeout=3000)
            await asyncio.sleep(1.0)
            logger.info("Accepted ingredient upload rights notice")
    except Exception as exc:
        logger.debug("Ingredient rights notice not accepted or absent: %s", exc)


async def _click_add_to_prompt_if_present(page) -> None:
    """Click the picker's primary commit CTA if one is present after selection.

    2026-05 picker (live-probed 2026-05-24) commits via tile-click and
    has no bottom Add button by default. Older / future variants may
    surface a primary CTA labelled "Add to Prompt", "Add", "Insert",
    "Done", or "Use selected". Best-effort: missing button is a no-op.
    """
    candidate_selectors = (
        "[role='dialog'] button:has-text('Add to Prompt')",
        "[role='dialog'] button:has-text('Add to prompt')",
        "[role='dialog'] button:has-text('Use selected')",
        "[role='dialog'] button:has-text('Insert')",
        "[role='dialog'] button:has-text('Done')",
        "button:has-text('Add to Prompt')",
        "[role='button']:has-text('Add to Prompt')",
    )
    for selector in candidate_selectors:
        try:
            btn = page.locator(selector).last
            if await btn.is_visible(timeout=600):
                await btn.click(timeout=3000)
                await asyncio.sleep(0.6)
                logger.info("Clicked picker commit CTA via: %s", selector)
                return
        except Exception:
            continue
    logger.debug("No explicit picker commit CTA found after upload (likely auto-closed via tile-click)")


async def _upload_ingredient_with_retry(page, image_path: str, expected_count: int) -> None:
    before_count = await _count_uploaded_ingredients(page)
    await _upload_ingredient(page, image_path)
    after_count = await _wait_for_uploaded_ingredient_count(page, expected_count)
    if after_count >= expected_count and after_count > before_count:
        return

    logger.warning(
        "Ingredient chip did not appear after upload; retrying once for %s (expected=%d, before=%d, after=%d)",
        image_path,
        expected_count,
        before_count,
        after_count,
    )
    await _upload_ingredient(page, image_path)
    final_count = await _wait_for_uploaded_ingredient_count(page, expected_count)
    if final_count < expected_count:
        raise RuntimeError(
            f"Ingredient attach mismatch after retry: expected {expected_count}, found {final_count}"
        )


async def _locate_ingredient_plus_button(page):
    button = page.locator(INGREDIENT_PLUS_SELECTOR).first
    if await button.is_visible(timeout=3000):
        return button
    raise RuntimeError(f"Could not locate ingredient add button via selector: {INGREDIENT_PLUS_SELECTOR}")


async def _open_ingredients_composer_menu(page) -> None:
    for sel in COMPOSER_MENU_SELECTORS:
        try:
            chip = page.locator(sel).first
            if await chip.is_visible(timeout=2000):
                await chip.click(timeout=3000)
                await asyncio.sleep(0.15)
                return
        except Exception:
            continue
    try:
        fallback = page.locator("button[aria-haspopup='menu']").filter(has_text="x1").last
        if await fallback.is_visible(timeout=2000):
            await fallback.click(timeout=3000)
            await asyncio.sleep(0.15)
            return
    except Exception:
        pass
    raise RuntimeError("Could not open composer menu for ingredients-to-video")


async def _ensure_uploaded_ingredient_count(page, expected: int) -> None:
    visible_count = await _count_uploaded_ingredients(page)
    if visible_count >= expected:
        return
    raise RuntimeError(
        f"Ingredient attach mismatch: expected {expected}, found {visible_count}; refusing to submit"
    )


async def _wait_for_uploaded_ingredient_count(page, expected: int, timeout_sec: float = 60.0) -> int:
    visible_count = await _count_uploaded_ingredients(page)
    if visible_count >= expected:
        return visible_count

    # 2026-05 schema: composer chips are Flow media-redirect thumbnail
    # imgs (40-260 px) NOT carrying any "ingredient" attribute. Poll
    # the same JS predicate used by _count_uploaded_ingredients so the
    # wait condition matches what the assertion will see.
    poll_predicate = (
        r"(expected) => {"
        r"const isThumb = (img) => {"
        r"  if (img.closest('[role=\"dialog\"], nav, [role=\"navigation\"]')) return false;"
        r"  const rect = img.getBoundingClientRect();"
        r"  if (rect.width < 40 || rect.width > 260) return false;"
        r"  if (rect.height < 40 || rect.height > 260) return false;"
        r"  const src = img.src || '';"
        r"  return src.includes('mediaUrlRedirect') || src.includes('media.getMediaUrl')"
        r"      || src.startsWith('blob:') || src.startsWith('data:');"
        r"};"
        r"return Array.from(document.querySelectorAll('img')).filter(isThumb).length >= expected;"
        r"}"
    )
    try:
        await page.wait_for_function(
            poll_predicate,
            arg=expected,
            timeout=min(int(timeout_sec * 1000), 15000),
        )
    except Exception:
        await asyncio.sleep(2)  # fallback
    return await _count_uploaded_ingredients(page)


async def _verify_ingredients_mode(page, timeout_sec: float = 5.0) -> None:
    """Confirm Ingredients mode by checking the + add-ingredient button is visible."""
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            btn = page.locator(INGREDIENT_PLUS_SELECTOR).first
            if await btn.is_visible(timeout=500):
                logger.info("Ingredients mode verified: add-ingredient button present")
                return
        except Exception:
            pass
        await asyncio.sleep(0.4)
    logger.warning("_verify_ingredients_mode: add button not found after %.1fs — proceeding anyway", timeout_sec)


async def _count_uploaded_ingredients(page) -> int:
    """Count ingredient chips currently attached to the composer.

    2026-05 schema (live-probed 2026-05-24): the picker emits Flow
    `media.getMediaUrlRedirect?name=<asset-uuid>` thumbnail imgs that
    land in the composer after the "Add to Prompt" click. Legacy
    `[data-testid*="ingredient"]` / `.ingredient-item` selectors are
    not present in 2026-05 markup.

    Strategy:
      1. Prefer the legacy attribute selectors if Flow ever re-adds
         them (safe no-op when absent).
      2. Otherwise count visible imgs whose ``src`` references the
         Flow media-redirect endpoint AND that are sized 40-260 px in
         their layout box (composer thumbnails). Exclude imgs inside
         ``[role='dialog']`` (open picker) and inside ``nav`` /
         ``[role='navigation']`` (sidebar avatars).
    """
    return await page.evaluate(
        r"""() => {
            const legacy = [
                '[data-testid*="ingredient"]',
                '[aria-label*="ingredient" i]',
                '.ingredient-item',
            ];
            for (const sel of legacy) {
                const c = document.querySelectorAll(sel).length;
                if (c > 0) return c;
            }
            const isComposerThumb = (img) => {
                if (img.closest('[role="dialog"], nav, [role="navigation"]')) return false;
                const rect = img.getBoundingClientRect();
                if (rect.width < 40 || rect.width > 260) return false;
                if (rect.height < 40 || rect.height > 260) return false;
                const src = img.src || '';
                if (src.includes('mediaUrlRedirect') || src.includes('media.getMediaUrl')) return true;
                if (src.startsWith('blob:') || src.startsWith('data:')) return true;
                return false;
            };
            return Array.from(document.querySelectorAll('img')).filter(isComposerThumb).length;
        }"""
    )
