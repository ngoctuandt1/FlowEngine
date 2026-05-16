"""Extend Video -- Level 2 operation.

Navigates to edit URL, clicks Extend, types prompt, selects model,
submits, waits, downloads.
"""

import asyncio
import logging
import os

from flow.failure_capture import message_with_failure_capture
from flow.model_selector import select_model, DEFAULT_MODEL
from flow.submit import submit_with_confirmation
from flow.operations._base import (
    navigate_to_edit,
    wait_for_video_loaded,
    click_action_button,
    count_visible_cards,
    finalize_operation,
)

try:  # C3 sibling PR; guarded so this branch lands independently.
    from flow.operations.extend_api import (
        get_extend_request_template,
        install_extend_request_capture,
        replay_extend_via_api,
    )
except Exception as exc:  # pragma: no cover - exercised via guarded fallback
    get_extend_request_template = None
    install_extend_request_capture = None
    replay_extend_via_api = None
    _EXTEND_API_IMPORT_ERROR = exc
else:
    _EXTEND_API_IMPORT_ERROR = None

logger = logging.getLogger(__name__)

# Extend button texts (EN + VI)
EXTEND_BUTTONS = ["Extend", "Mở rộng", "Mo rong", "extend"]

# Extend icon/selector fallbacks
EXTEND_ICON_SELECTORS = [
    "button:has(span:has-text('keyboard_double_arrow_right'))",
    "button:has(i:has-text('keyboard_double_arrow_right'))",
    "[aria-label*='Extend' i]",
    "[aria-label*='extend' i]",
    "button:has-text('keyboard_double_arrow_right')",
]


def _reverse_extend_enabled() -> bool:
    return os.getenv("FLOW_EXTEND_VIA_REVERSE", "0") == "1"


def _install_extend_capture_if_enabled(client) -> bool:
    if not _reverse_extend_enabled():
        return False
    if install_extend_request_capture is None:
        logger.info(
            "FLOW_EXTEND_VIA_REVERSE=1 but extend_api unavailable; "
            "continuing UI path (%s)",
            _EXTEND_API_IMPORT_ERROR,
        )
        return False
    try:
        install_extend_request_capture(client)
    except Exception as exc:
        logger.info(
            "Extend request capture install failed; continuing UI path: %s",
            exc,
        )
        return False
    return True


def _current_extend_template(client):
    if get_extend_request_template is None:
        return None
    try:
        return get_extend_request_template(client)
    except Exception as exc:
        logger.info(
            "Extend request template unavailable; continuing UI path: %s",
            exc,
        )
        return None


def _extract_replay_media_ids(replay_result) -> list[str]:
    if isinstance(replay_result, str) and replay_result:
        return [replay_result]
    if not isinstance(replay_result, dict):
        return []
    media_ids = replay_result.get("media_ids") or []
    media_id = replay_result.get("media_id")
    if media_id:
        media_ids = [media_id, *media_ids]
    unique_media_ids = []
    for media_id_value in media_ids:
        if media_id_value and media_id_value not in unique_media_ids:
            unique_media_ids.append(str(media_id_value))
    return unique_media_ids


def _record_media_id_event(client, media_id: str, *, source: str) -> None:
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        recorder(media_id, source=source, url="extend-replay")
        return
    events = getattr(client, "_media_id_events", None)
    if isinstance(events, list) and media_id not in {
        event.get("mid") or event.get("media_id")
        for event in events
        if isinstance(event, dict)
    }:
        events.append({"mid": media_id, "source": source, "url": "extend-replay"})


def _defer_replay_media_ids_for_wait(client, media_ids: list[str]) -> None:
    if not media_ids:
        return

    async def _record_after_wait_starts():
        await asyncio.sleep(0)
        for media_id_value in media_ids:
            _record_media_id_event(
                client,
                media_id_value,
                source="extend_replay_result",
            )

    asyncio.create_task(_record_after_wait_starts())


async def extend_video(
    client,
    job: dict,
    prompt: str = "",
    model: str = DEFAULT_MODEL,
    free_mode: bool = True,
) -> dict:
    """Execute extend-video operation.

    Steps:
    1. Navigate to edit URL
    2. Wait for video to load
    3. Click "Extend" button
    4. Type extend prompt (optional -- "What happens next?")
    5. Select LP model
    6. Submit and confirm
    7. Wait + Download + Return metadata

    Args:
        client: FlowClient instance
        job: Job dict with edit_url/project_url/media_id
        prompt: Extension prompt (optional)
        model: Model to use
        free_mode: Use LP (0 credits) model

    Returns: Result dict with project_url, media_id, edit_url, output_files, etc.
    """
    page = client.page
    capture_ready = _install_extend_capture_if_enabled(client)

    # Step 1: Navigate
    edit_url_val, project_id, locale = await navigate_to_edit(client, job)

    # Step 2: Wait for video
    await wait_for_video_loaded(page)

    # Hybrid reverse-API path: after L2 has captured a request template, L3+
    # can replay the extend submit and reuse the normal wait/download tail.
    if capture_ready:
        template = _current_extend_template(client)
        if template is not None and replay_extend_via_api is not None:
            try:
                client.clear_captures()
                replay_result = await replay_extend_via_api(
                    client,
                    parent_media_id=job["media_id"],
                    prompt=prompt,
                )
                replay_media_ids = _extract_replay_media_ids(replay_result)
                setattr(client, "_extend_replay_media_ids", replay_media_ids)
                _defer_replay_media_ids_for_wait(client, replay_media_ids)
                replay_count = getattr(client, "_extend_replay_count", 0) + 1
                setattr(client, "_extend_replay_count", replay_count)
                logger.info(
                    "Extend replay submit accepted via reverse API "
                    "(count=%d media_ids=%s)",
                    replay_count,
                    replay_media_ids,
                )
                return await finalize_operation(
                    client, job,
                    job_type="extend-video",
                    project_id=project_id,
                    locale=locale,
                    download_prefix="ext",
                )
            except RuntimeError as exc:
                logger.warning(
                    "Extend reverse-API replay failed; falling back to UI path: %s",
                    exc,
                )

    # Step 3: Ensure Extend panel open.
    #
    # Flow UI opens an edit URL with one of the 4 modes already selected
    # (usually Extend for videos with remaining extend budget). In that
    # state the Extend button is already the active mode — clicking it
    # again is a no-op at best or a toggle-close at worst, and the click
    # locator may not match because Flow marks the active mode specially.
    #
    # So: probe panel state FIRST. If already open → skip the click.
    # If not open → click Extend button, then re-verify.
    await asyncio.sleep(2)  # let action rail render

    panel_open = await _verify_extend_panel(page)
    if panel_open:
        logger.info("Extend panel already open — skipping Extend button click")
    else:
        clicked = await click_action_button(page, EXTEND_BUTTONS, client=client)
        if not clicked:
            # Try icon-based fallbacks
            for sel in EXTEND_ICON_SELECTORS:
                try:
                    icon_btn = page.locator(sel).first
                    if await icon_btn.is_visible(timeout=2000):
                        await icon_btn.click(timeout=3000)
                        clicked = True
                        logger.info("Clicked Extend via icon: %s", sel)
                        await asyncio.sleep(0.5)
                        break
                except Exception:
                    continue

        if not clicked:
            # JS fallback: scan for extend-like buttons
            try:
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button, [role="button"]');
                    for (const btn of btns) {
                        const text = (btn.innerText || '').toLowerCase();
                        if (text.includes('extend') || text.includes('mở rộng')
                            || text.includes('keyboard_double_arrow_right')) {
                            btn.click();
                            return true;
                        }
                    }
                    return false;
                }""")
                if clicked:
                    logger.info("Clicked Extend via JS fallback")
                    await asyncio.sleep(0.5)
            except Exception:
                pass

        if not clicked:
            # Debug: log visible buttons to help diagnose
            try:
                buttons = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button, [role="button"]');
                    return Array.from(btns).slice(0, 30).map(b => ({
                        text: (b.innerText || '').trim().substring(0, 60),
                        w: Math.round(b.getBoundingClientRect().width),
                        vis: getComputedStyle(b).display !== 'none',
                    })).filter(b => b.vis && b.w > 30);
                }""")
                logger.error("Extend button not found. Visible buttons: %s", buttons)
            except Exception:
                pass
            message = "Failed to find Extend button (panel was not already open)"
            message = await message_with_failure_capture(
                client,
                "extend_button_not_found",
                message,
            )
            raise RuntimeError(message)

        # Step 3.5: Verify extend panel opened after click
        await asyncio.sleep(1)
        panel_open = await _verify_extend_panel(page)
        if not panel_open:
            message = "Extend panel did not open after clicking Extend button"
            message = await message_with_failure_capture(
                client,
                "extend_panel_not_open",
                message,
            )
            raise RuntimeError(message)

    # Step 4: Type prompt (optional)
    if prompt:
        await _type_extend_prompt(page, prompt)

    # Step 5: Select model
    await select_model(page, model=model, free_mode=free_mode, profile=client.profile_name)

    # Step 6: Submit
    before_cards = await count_visible_cards(page)
    client.clear_captures()

    confirmed = await submit_with_confirmation(
        client,
        before_card_count=before_cards,
        prompt_text=prompt,
        failure_kind="extend_submit_not_confirmed",
    )
    if not confirmed:
        # Log page state for diagnosis
        try:
            url = page.url
            editors = await page.locator("[data-slate-editor='true']").count()
            logger.error(
                "Extend submit not confirmed. url=%s editors=%d",
                url[:100], editors,
            )
        except Exception:
            pass
        message = "Extend submit not confirmed — generation did not start"
        message = await message_with_failure_capture(
            client,
            "extend_submit_not_confirmed",
            message,
        )
        raise RuntimeError(message)

    # Step 7: Wait + Download + Return
    return await finalize_operation(
        client, job,
        job_type="extend-video",
        project_id=project_id,
        locale=locale,
        download_prefix="ext",
    )


async def _verify_extend_panel(page, timeout_sec: float = 5.0) -> bool:
    """Verify the extend panel opened by checking for a second Slate editor.

    The extend panel adds a new Slate editor (data-slate-editor) for the
    extend prompt. The main composer already has one, so we expect >= 2.
    Also checks for extend-specific UI: "Bắt đầu"/"Start" toggle, or
    scroll-state attribute on the panel.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            editors = await page.locator("[data-slate-editor='true']").count()
            if editors >= 2:
                logger.info("Extend panel verified: %d slate editors found", editors)
                return True
            # Also check for data-scroll-state="START" (extend panel attribute)
            panels = await page.locator("[data-scroll-state='START']").count()
            if panels >= 1:
                logger.info("Extend panel verified via data-scroll-state")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)

    # Log what we see for debugging
    try:
        editors = await page.locator("[data-slate-editor='true']").count()
        logger.error("Extend panel NOT detected: only %d slate editors", editors)
    except Exception:
        pass
    return False


async def _type_extend_prompt(page, prompt: str):
    """Type into the extend prompt field inside the extend panel.

    The extend panel is a dialog/overlay opened by clicking "Extend"/"Mở rộng".
    Its textbox has placeholder "What happens next?" (EN) / "Tiếp theo là gì?" (VI).
    Must NOT type into the main composer textbox at the bottom of the page.
    """
    # Method 1: target the extend panel's editor by data-scroll-state
    try:
        panel = page.locator("[data-scroll-state='START'] [data-slate-editor='true']")
        if await panel.count() > 0:
            el = panel.first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=2000)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(prompt, delay=20)
                logger.info("Extend prompt typed via data-scroll-state editor")
                return
    except Exception as e:
        logger.debug("data-scroll-state editor failed: %s", e)

    # The extend panel has a Slate.js editor (data-slate-editor="true").
    # The main composer ALSO has one, so we must pick the LAST one
    # (extend panel renders after composer in DOM).
    try:
        editors = page.locator("[data-slate-editor='true']")
        count = await editors.count()
        if count >= 2:
            # Last slate editor = extend panel (rendered after main composer)
            el = editors.nth(count - 1)
        elif count == 1:
            el = editors.first
        else:
            el = None

        if el and await el.is_visible(timeout=2000):
            await el.click(timeout=2000)
            await asyncio.sleep(0.3)
            await page.keyboard.press("Control+a")
            await asyncio.sleep(0.1)
            await page.keyboard.type(prompt, delay=20)
            logger.info("Extend prompt typed via slate editor (%d editors found)", count)
            return
    except Exception as e:
        logger.debug("Slate editor approach failed: %s", e)

    # Fallback: placeholder-based selectors
    for sel in [
        "[placeholder*='next' i]",
        "[placeholder*='tiếp' i]",
        "[placeholder*='tiep' i]",
        "[aria-label*='extend' i]",
    ]:
        try:
            el = page.locator(sel).first
            if await el.is_visible(timeout=1500):
                await el.click(timeout=2000)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(prompt, delay=20)
                logger.info("Extend prompt typed via: %s", sel)
                return
        except Exception:
            continue

    logger.warning("Could not find extend prompt editor -- proceeding without prompt")
