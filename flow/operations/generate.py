"""Text-to-Video generation — Level 1 operation."""

import asyncio
import logging
import os
import re
from pathlib import Path

from flow.navigation import flow_url, extract_project_id
from flow.login import is_login_page, handle_login_redirect
from flow.landing import dismiss_flow_marketing_landing, recover_from_flow_canvas_page
from flow.model_selector import canonicalize_video_model_key, select_model, DEFAULT_MODEL
from flow.selector_chain import click_first_visible
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video
from flow.failure_capture import message_with_failure_capture
from flow.operations._base import failure_kind_from_error, resolve_final_media_id
from flow.operations._l1_status_poll import download_via_url, poll_status_via_api

logger = logging.getLogger(__name__)

install_t2v_request_capture = None
get_t2v_request_template = None
replay_t2v_via_inflate = None
_T2V_API_IMPORT_ERROR = None


# Locale-independent selectors for the homepage "+ New project" CTA (B18).
# Ordered by stability — most locale-independent first. Live DOM evidence:
# docs/FLOW_UI_REFERENCE.md §Homepage New Project Button.
#
# Ground truth button structure (same on VI + EN profiles):
#   <button>
#     <i class="google-symbols">add_2</i>  ← Material Icon ligature, stable
#     Dự án mới | New project                ← localized, unstable
#     <div data-type="button-overlay"/>
#   </button>
# aria-label is EMPTY, href is EMPTY, no role / id / data-testid.
# The only stable locale-independent signal is the Material Icon ligature
# text "add_2" inside the <i class="google-symbols"> child.
NEW_PROJECT_SELECTORS = [
    # Icon-first (locale-independent, unique on homepage): only the
    # new-project button contains Material Icon "add_2". Live probe on
    # ngoctuandt20 homepage: 1 button with add_2 text; other icons on
    # page are "edit"/"delete" on existing project cards.
    "button:has(i.google-symbols):has-text('add_2')",
    "button:has(i:has-text('add_2'))",
    "button:has-text('add_2')",
    # Text variants (bilingual + conjugations — defense in depth).
    "button:has-text('Dự án mới')",
    "button:has-text('New project')",
    "button:has-text('Dự án')",
    "button:has-text('Tạo dự án')",
    "button:has-text('Tạo mới')",
    "a:has-text('Dự án mới')",
    "a:has-text('New project')",
    "[role='button']:has-text('Dự án mới')",
    "[role='button']:has-text('New project')",
    # Aria-label fallback (observed EMPTY on live DOM but kept for
    # future-proofing if Flow adds accessible names).
    "[aria-label*='new project' i]",
    "[aria-label*='dự án' i]",
    "[aria-label*='new' i][aria-label*='project' i]",
]


def _reverse_t2v_enabled() -> bool:
    return os.getenv("FLOW_T2V_VIA_REVERSE", "0") == "1"


def _install_t2v_capture_if_enabled(client) -> bool:
    if not _reverse_t2v_enabled():
        return False
    if install_t2v_request_capture is None:
        logger.info(
            "FLOW_T2V_VIA_REVERSE=1 but generate_api unavailable; "
            "continuing UI path (%s)",
            _T2V_API_IMPORT_ERROR,
        )
        return False
    try:
        install_t2v_request_capture(client)
    except Exception as exc:
        logger.info(
            "T2V request capture install failed; continuing UI path: %s",
            exc,
        )
        return False
    logger.info(
        "T2V reverse capture enabled for telemetry; pure replay remains "
        "blocked by reCAPTCHA, UI path remains fallback"
    )
    return True


def _current_t2v_template(client):
    if get_t2v_request_template is None:
        return None
    try:
        return get_t2v_request_template(client)
    except Exception as exc:
        logger.info(
            "T2V request template unavailable; continuing UI path: %s",
            exc,
        )
        return None


def _extract_t2v_replay_gen_ids(replay_result) -> list[str]:
    if isinstance(replay_result, str) and replay_result:
        return [replay_result]
    if isinstance(replay_result, list):
        return [str(item) for item in replay_result if item]
    if not isinstance(replay_result, dict):
        return []
    gen_ids = replay_result.get("gen_ids") or replay_result.get("generation_ids") or []
    gen_id = replay_result.get("gen_id") or replay_result.get("generation_id")
    if gen_id:
        gen_ids = [gen_id, *gen_ids]
    media_id = replay_result.get("media_id")
    if media_id:
        gen_ids = [media_id, *gen_ids]
    unique_gen_ids = []
    for gen_id_value in gen_ids:
        if gen_id_value and gen_id_value not in unique_gen_ids:
            unique_gen_ids.append(str(gen_id_value))
    return unique_gen_ids


def _record_t2v_replay_media_id(client, media_id: str) -> None:
    recorder = getattr(client, "_record_media_id", None)
    if callable(recorder):
        recorder(media_id, source="t2v_replay", url="t2v-replay")
        return
    events = getattr(client, "_media_id_events", None)
    if isinstance(events, list) and media_id not in {
        event.get("mid") or event.get("media_id")
        for event in events
        if isinstance(event, dict)
    }:
        events.append({"mid": media_id, "source": "t2v_replay", "url": "t2v-replay"})


def _replay_download_dir(client) -> Path:
    client_dir = getattr(client, "download_dir", None)
    if isinstance(client_dir, (str, os.PathLike)):
        try:
            return Path(client_dir)
        except TypeError:
            pass
    env_dir = os.environ.get("FLOW_DOWNLOAD_DIR")
    if env_dir:
        return Path(env_dir)
    return Path("downloads")


def _safe_filename_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_") or "media"


async def _finalize_t2v_replay_result(
    client,
    *,
    gen_id: str,
    project_id: str | None,
    locale: str,
) -> dict:
    poll_result = await poll_status_via_api(
        client,
        gen_ids=[gen_id],
        project_id=project_id,
    )
    status = poll_result.get(gen_id) or next(iter(poll_result.values()), {})
    if status.get("status") != "completed":
        raise RuntimeError(
            f"text-to-video replay status not completed for gen_id={gen_id}: "
            f"{status.get('status')} {status.get('error') or ''}".strip()
        )
    media_id = status.get("media_id") or gen_id
    media_url = status.get("media_url")
    if not media_url:
        raise RuntimeError(f"text-to-video replay missing media_url for gen_id={gen_id}")

    _record_t2v_replay_media_id(client, media_id)
    download_dir = _replay_download_dir(client)
    try:
        download_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.debug(
            "T2V replay finalize: download dir mkdir failed (%s) for %s; "
            "continuing -- download_via_url will surface the real error",
            exc,
            download_dir,
        )
    out_path = download_dir / f"t2v_replay_{_safe_filename_token(media_id)}.mp4"
    saved_path = await download_via_url(
        client,
        url=media_url,
        out_path=str(out_path),
    )
    if not saved_path:
        raise RuntimeError(
            f"text-to-video replay direct download returned empty path for media_id={media_id}"
        )

    project_url = f"{flow_url(locale)}/project/{project_id}" if project_id else getattr(client.page, "url", "")
    edit_url_val = (
        f"{flow_url(locale)}/project/{project_id}/edit/{media_id}"
        if project_id
        else getattr(client.page, "url", "")
    )
    return {
        "project_url": project_url,
        "media_id": media_id,
        "edit_url": edit_url_val,
        "output_files": [saved_path],
        "generation_id": getattr(client, "_gen_id", None),
        "profile": client.profile_name,
    }


async def _try_t2v_replay_from_template(client, prompt: str) -> dict | None:
    if not _reverse_t2v_enabled() or replay_t2v_via_inflate is None:
        return None
    template = _current_t2v_template(client)
    if template is None:
        return None
    try:
        # Direct solo T2V replay is still reCAPTCHA-gated; the available
        # replay helper piggybacks on Flow's UI-validated inflate path.
        replay_result = await replay_t2v_via_inflate(client, [prompt])
        replay_gen_ids = _extract_t2v_replay_gen_ids(replay_result)
        if not replay_gen_ids:
            raise RuntimeError("T2V reverse replay returned no gen_id")
        current_url = getattr(client.page, "url", "")
        locale = "vi" if "/vi/" in current_url else ""
        project_id = extract_project_id(current_url)
        logger.info(
            "T2V reverse replay accepted (gen_ids=%s) -- finalizing via status API + direct URL download",
            replay_gen_ids,
        )
        return await _finalize_t2v_replay_result(
            client,
            gen_id=replay_gen_ids[0],
            project_id=project_id,
            locale=locale,
        )
    except RuntimeError as exc:
        logger.warning(
            "T2V reverse replay failed; falling back to UI path: %s",
            exc,
        )
        return None


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
    capture_ready = _install_t2v_capture_if_enabled(client)
    if capture_ready:
        replay_result = await _try_t2v_replay_from_template(client, prompt)
        if replay_result is not None:
            return replay_result

    locale = ""  # Will detect from URL

    # === Step 1: Navigate to Flow homepage ===
    logger.info("Step 1: Navigate to Flow homepage")
    homepage = flow_url(locale)
    await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
    try:
        await page.wait_for_selector(
            "text=/New project|Dự án mới|Tạo dự án/",
            state="attached",
            timeout=4000,
        )
    except Exception:
        pass  # marketing landing or slow load — recovery logic below handles it

    # Handle Google login redirect if needed
    current = page.url
    if is_login_page(current):
        logger.warning("Redirected to Google login — attempting auto-resolve")
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            message = "Google login required — profile session expired."
            message = await message_with_failure_capture(
                client,
                "google_login_required",
                message,
            )
            raise RuntimeError(message)
        # Re-navigate after login resolution
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

    # Detect locale from final URL
    if "/vi/" in current:
        locale = "vi"
    logger.info(f"On Flow homepage: {current}, locale={locale or 'en'}")

    # === Step 2: Click "+ New project" ===
    logger.info("Step 2: Create new project")

    async def _recover_homepage_login_redirect() -> bool:
        current_url = page.url
        if not is_login_page(current_url):
            return False
        logger.warning("Login redirect before Create click — attempting auto-resolve")
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            message = "Google login required — profile session expired."
            message = await message_with_failure_capture(
                client,
                "google_login_required",
                message,
            )
            raise RuntimeError(message)
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it
        logger.info("Homepage restored after login redirect: %s", page.url)
        return True

    # Flow sometimes serves the marketing landing ("Create with Flow" CTA)
    # instead of the editor home — even for logged-in sessions. Click the
    # hero CTA (not the nav scroll-anchor sharing the same text — issue
    # #48) to bounce into the authenticated app before searching for the
    # "+ New project" button.
    # The tile is variant-specific: sometimes rendered with visible text
    # "New project", sometimes icon-only with accessible-name only. Probe
    # both; role-based matcher matches the exact click path we use below.
    # Playwright's text engine does NOT union via comma — `text=A, text=B`
    # is parsed as a single literal "A, text=B" query that never matches,
    # which previously made `wait_for_selector` burn its full timeout (15s
    # on the final probe = the bulk of the 18s gap between "Step 2" and
    # "Clicked new project" observed on warm sessions, 2026-04-25). Use a
    # regex `text=/.../` so the alternation is honoured by the text engine.
    _NEW_PROJECT_TEXT_SELECTOR = (
        "text=/New project|Dự án mới|Tạo dự án/"
    )
    _NEW_PROJECT_ROLE_NAMES = ("New project", "Dự án mới", "Tạo dự án")

    async def _new_project_button_attached(timeout_ms: int = 1000) -> bool:
        try:
            await page.wait_for_selector(
                _NEW_PROJECT_TEXT_SELECTOR,
                state="attached",
                timeout=timeout_ms,
            )
            return True
        except Exception:
            pass
        per_name = max(250, timeout_ms // len(_NEW_PROJECT_ROLE_NAMES))
        for name in _NEW_PROJECT_ROLE_NAMES:
            try:
                btn = page.get_by_role("button", name=name).filter(visible=True).first
                if await btn.is_visible(timeout=per_name):
                    return True
            except Exception:
                continue
        return False

    if not await _new_project_button_attached(timeout_ms=1000):
        await _recover_homepage_login_redirect()
        await dismiss_flow_marketing_landing(
            page, logger, _new_project_button_attached
        )
        if not await _new_project_button_attached(timeout_ms=1000):
            await recover_from_flow_canvas_page(page, logger, homepage)

    if not await _new_project_button_attached(timeout_ms=15000):
        await _recover_homepage_login_redirect()
        logger.warning("New-project button did not attach within 15s — continuing")

    # Dismiss any welcome/onboarding overlay first
    await _dismiss_overlays(page)

    new_project_clicked = False

    # Prefer Playwright's accessible-name role lookup — survives Material
    # Icon ligature renames (e.g. add_2 → SVG) and matches the visible
    # button even when hidden duplicates exist earlier in DOM order.
    role_candidates = ["New project", "Dự án mới", "Tạo dự án"]
    for name in role_candidates:
        try:
            btn = page.get_by_role("button", name=name).filter(visible=True).first
            if await btn.is_visible(timeout=2000):
                try:
                    await btn.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                await btn.click(timeout=5000)
                new_project_clicked = True
                logger.info("Clicked new project via role=button name=%r", name)
                break
        except Exception:
            continue

    if not new_project_clicked:
        for sel in NEW_PROJECT_SELECTORS:
            try:
                btn = page.locator(sel).locator("visible=true").first
                if await btn.is_visible(timeout=2000):
                    try:
                        await btn.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    await btn.click(timeout=5000)
                    new_project_clicked = True
                    logger.info("Clicked new project via: %s", sel)
                    break
            except Exception:
                continue

    # Last-resort: match the tile purely by its visible text. Flow renders
    # "+ New project" as a <div> without role on some variants, so neither
    # get_by_role nor tag-restricted CSS can see it. get_by_text is tag-
    # agnostic; we then walk up to the clickable ancestor.
    if not new_project_clicked:
        for text in ("New project", "Dự án mới", "Tạo dự án"):
            try:
                tile = page.get_by_text(text, exact=True).first
                if await tile.is_visible(timeout=2000):
                    try:
                        await tile.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        await tile.click(timeout=5000)
                    except Exception:
                        # Text node may not itself be clickable — try the
                        # nearest ancestor with a click handler.
                        handle = await tile.element_handle()
                        if handle is not None:
                            await page.evaluate(
                                """(el) => {
                                    let cur = el;
                                    for (let i = 0; i < 6 && cur; i++) {
                                        const r = cur.getBoundingClientRect();
                                        if (r.width > 80 && r.height > 40) {
                                            cur.click();
                                            return;
                                        }
                                        cur = cur.parentElement;
                                    }
                                    el.click();
                                }""",
                                handle,
                            )
                    new_project_clicked = True
                    logger.info("Clicked new project via get_by_text(%r)", text)
                    break
            except Exception:
                continue

    if not new_project_clicked:
        try:
            title = await page.title()
            body_text = await page.evaluate("document.body?.innerText?.substring(0, 500) || ''")
            logger.error("Page title: %s", title)
            logger.error("Page URL at failure: %s", page.url)
            logger.error("Page text preview: %s", body_text[:300])
        except Exception:
            pass
        try:
            import os as _os
            from datetime import datetime as _dt
            screens_dir = _os.path.join(_os.getcwd(), "debug_screens")
            _os.makedirs(screens_dir, exist_ok=True)
            ts = _dt.now().strftime("%Y%m%d_%H%M%S")
            path = _os.path.join(screens_dir, f"new_project_btn_missing_{ts}.png")
            await page.screenshot(path=path, full_page=True)
            logger.error("Saved failure screenshot: %s", path)
        except Exception as e:
            logger.error("Failed to save screenshot: %s", e)
        message = "Failed to find '+ New project' button on Flow homepage"
        message = await message_with_failure_capture(
            client,
            "new_project_button_not_found",
            message,
        )
        raise RuntimeError(message)

    # Wait for project editor to load — URL may contain /project/ or just change
    try:
        await page.wait_for_url("**/project/**", timeout=20000)
    except Exception:
        # Fallback: wait for URL to change from homepage
        await asyncio.sleep(1)
        logger.warning("URL pattern wait failed, current: %s", page.url[:100])

    # Check if "Create" click redirected to Google login (session expired)
    current = page.url
    if is_login_page(current):
        logger.warning("Login redirect after Create click — handling")
        login_ok = await handle_login_redirect(
            page, timeout=90, profile_name=client.profile_name, client=client,
        )
        if not login_ok:
            message = "Google login required — profile session expired."
            message = await message_with_failure_capture(
                client,
                "google_login_required",
                message,
            )
            raise RuntimeError(message)
        # Re-navigate to homepage and retry project creation
        await page.goto(homepage, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(
                "text=/New project|Dự án mới|Tạo dự án/",
                state="attached",
                timeout=4000,
            )
        except Exception:
            pass  # marketing landing or slow load — recovery logic below handles it

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
            await asyncio.sleep(1)

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
    canonical_model = canonicalize_video_model_key(model, free_mode=free_mode)
    logger.info(f"Step 3: Select model ({canonical_model})")
    await select_model(page, model=canonical_model, free_mode=free_mode, profile=client.profile_name)

    # === Step 4: Aspect ratio ===
    # The aspect ratio is typically set in the model options panel
    # For now, we set it during model selection or skip if not critical
    logger.info(f"Step 4: Aspect ratio = {aspect_ratio}")
    await _set_aspect_ratio(page, aspect_ratio)

    # === Step 4.5: Force output count to x1 ===
    # B35 (2026-04-19): Flow account default may be x2/x3/x4 → multiplies
    # LP credit cost AND mints multiple clips per submit (ambiguous
    # media_id extraction). Engine always pins x1. See
    # memory/feedback_output_count_x1.md + SPEC §D.4 B35.
    logger.info("Step 4.5: Force output count = x1")
    try:
        await _set_output_count(page, 1)
    except Exception as e:
        logger.warning(
            "Failed to force output count x1: %s — submit may use account default (potential credit leak)",
            e,
        )

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
        failure_kind="submit_not_confirmed",
    )

    if not confirmed:
        message = "Submit not confirmed — generation may not have started"
        message = await message_with_failure_capture(
            client,
            "submit_not_confirmed",
            message,
        )
        raise RuntimeError(message)

    logger.info("Submit confirmed, waiting for generation...")

    # === Step 7: Wait for completion ===
    logger.info("Step 7: Wait for completion")
    result = await wait_for_completion(client, job_type="text-to-video")

    if not result.get("done"):
        error = result.get("error", "unknown")
        message = f"Generation failed: {error}"
        message = await message_with_failure_capture(
            client,
            failure_kind_from_error("text-to-video", error),
            message,
        )
        raise RuntimeError(message)

    logger.info("Generation complete!")

    # === Step 8: Extract metadata ===
    current_url = page.url
    captured_media_ids = result.get("media_ids") or []
    fallback_media_id = captured_media_ids[0] if captured_media_ids else None
    media_id = await resolve_final_media_id(page, fallback=fallback_media_id)

    # Build edit_url
    edit_url_val = None
    if media_id and project_id:
        base = flow_url(locale)
        edit_url_val = f"{base}/project/{project_id}/edit/{media_id}"

    # === Step 9: Download ===
    logger.info("Step 8: Download video")
    download_media_ids = captured_media_ids or ([media_id] if media_id else [])
    proj_url = f"{flow_url(locale)}/project/{project_id}" if project_id else project_url_full
    output_files = await download_video(
        client,
        media_ids=download_media_ids,
        prefix="t2v",
        metadata={
            "job_type": "text-to-video",
            "prompt": prompt,
            "media_id": media_id or "",
            "project_url": proj_url or "",
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        message = "text-to-video: no output file captured - download pipeline returned empty list"
        message = await message_with_failure_capture(
            client,
            "text_to_video_no_output_file",
            message,
        )
        raise RuntimeError(message)

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
    after login or UI updates. Live DOM probe on a healthy ngoctuandt20
    homepage found no visible overlays — this function is defensive only
    and must no-op cleanly on overlay-free pages. In particular it does
    NOT press Escape unconditionally (pressing Escape with a composer
    focused elsewhere in the app has been observed to close unrelated UI
    — see B8 LP model-selector lesson).
    """
    # Probe for real overlay presence before acting.
    try:
        has_overlay = await page.evaluate(
            """() => {
                const overlayish = document.querySelectorAll(
                    '[role="dialog"], [role="alertdialog"], '
                    + '[aria-modal="true"], '
                    + '[class*="overlay" i], [class*="backdrop" i], '
                    + '[class*="scrim" i], [class*="modal" i]'
                );
                for (const el of overlayish) {
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    if (s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity) > 0
                        && r.width > 50 && r.height > 50) {
                        return true;
                    }
                }
                return false;
            }"""
        )
    except Exception:
        has_overlay = False

    if not has_overlay:
        return

    logger.info("Overlay detected on homepage — attempting dismiss")

    # Close button patterns — try localised confirm buttons first.
    CLOSE_SELECTORS = [
        "button[aria-label*='close' i]",
        "button[aria-label*='dismiss' i]",
        "button:has-text('Got it')",
        "button:has-text('Đã hiểu')",
        "button:has-text('OK')",
        "[role='button'][aria-label*='close' i]",
        "button:has(i:has-text('close'))",
    ]

    matched = await click_first_visible(
        page,
        CLOSE_SELECTORS,
        is_visible_timeout_ms=1000,
        click_timeout_ms=2000,
        on_match=lambda sel: logger.info("Dismissed overlay via: %s", sel),
    )
    if matched is not None:
        await asyncio.sleep(1)
        return

    # Last resort: Escape (only runs if an overlay WAS detected — reduces
    # risk of dismissing unrelated UI).
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.5)
    except Exception:
        pass


async def _wait_for_composer(page, timeout_sec: float = 15.0):
    """Wait until the Slate.js composer editor is visible."""
    COMPOSER_SELECTORS = [
        "[data-slate-editor='true']",
        "[role='textbox'][aria-multiline='true']",
        "[data-testid='composer_input']",
    ]
    timeout_ms = int(timeout_sec * 1000)
    # Try each selector with the full timeout budget. First match wins.
    for sel in COMPOSER_SELECTORS:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=timeout_ms)
            logger.info("Composer ready via: %s", sel)
            return
        except Exception:
            continue

    # Fallback: check placeholder text
    try:
        await page.wait_for_function(
            "() => /what do you want to create|bạn muốn tạo gì/i.test(document.body?.innerText || '')",
            timeout=timeout_ms,
        )
        logger.info("Composer ready (placeholder text detected)")
        return
    except Exception:
        pass

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

    # Debug: log page state + capture screenshot before failing
    try:
        title = await page.title()
        body_text = await page.evaluate(
            "document.body?.innerText?.substring(0, 500) || ''"
        )
        logger.error("Prompt editor not found — page title: %s", title)
        logger.error("Page URL at failure: %s", page.url)
        logger.error("Page text preview: %s", body_text[:300])
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

    try:
        import os as _os
        from datetime import datetime as _dt
        screens_dir = _os.path.join(_os.getcwd(), "debug_screens")
        _os.makedirs(screens_dir, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        path = _os.path.join(screens_dir, f"prompt_editor_missing_{ts}.png")
        await page.screenshot(path=path, full_page=True)
        logger.error("Saved failure screenshot: %s", path)
    except Exception as e:
        logger.error("Failed to save screenshot: %s", e)

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

    # Locate the aspect-ratio chip by its Material Icon ligature, not by
    # surrounding text. The chip always contains an `<i class="google-symbols">`
    # whose text is the crop ligature (`crop_9_16` or `crop_16_9`). The
    # surrounding label (model name: "Video", "Veo 3.1 Fast LP",
    # "🍌 Nano Banana Pro", …) varies per account/session and is
    # locale-dependent — relying on it breaks whenever the model changes
    # or the browser locale flips to VI.
    #
    # We match via CSS `:has-text("crop_9_16"|"crop_16_9")` on the
    # button's own textContent, which is `"Videocrop_16_9x1"` (or
    # `"<model>crop_9_16x1"`). The crop ligature is a stable substring
    # regardless of the surrounding label, so a CSS `:has-text` substring
    # match is sufficient — no regex gymnastics, no newline edge cases
    # (`innerText` vs `textContent` diverge on block children). This
    # avoids the `has=<nested-locator>` path that previously failed to
    # resolve against the real DOM (B19, Tier 2 Run 3/4).
    chip_btn = page.locator(
        'button[aria-haspopup="menu"]:has-text("crop_9_16"), '
        'button[aria-haspopup="menu"]:has-text("crop_16_9")'
    ).first

    # Radix DropdownMenu trigger reflects open/closed via `data-state`.
    # A preceding interaction (model-selector dropdown dismiss, DOM
    # focus-trap reset) can leave the aspect chip in the open state
    # before we arrive — clicking it then TOGGLES the menu CLOSED
    # and the subsequent `wait_for("[role=\"menu\"][data-state=\"open\"]")`
    # times out (B19, Tier 2 Run 3-6).
    # Only click to open when the trigger is currently closed.
    current_state = await chip_btn.get_attribute("data-state", timeout=2000)
    if current_state != "open":
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


def _chip_text_matches_output_count(chip_text: str, count: int) -> bool:
    """Return True when the composer chip confirms the requested output count."""
    chip_text_l = chip_text.lower()
    if count == 1:
        # Flow UI builds observed on Linux 2026-04-30 render single-output as
        # either `1x` or `x1`; keep explicit x2/x3/x4 rejects to catch leaks.
        return (
            ("1x" in chip_text_l or "x1" in chip_text_l)
            and "x2" not in chip_text_l
            and "x3" not in chip_text_l
            and "x4" not in chip_text_l
        )
    return f"x{count}" in chip_text_l


async def _set_output_count(page, count: int = 1):
    """B35: force Flow composer count chip to x{count} (default x1).

    Flow's per-account default may be x2/x3/x4 — submitting without setting
    this burns 2-4× LP credit per job AND mints multiple clips per submit
    (ambiguous `media_id` extraction). Engine must always pin x1.

    Selector reference: ``docs/FLOW_UI_REFERENCE.md`` §Model Chip Panel row 4
    (Quantity tablist). Same Radix-tab pattern as aspect ratio (B1a): trigger
    IDs end with ``-trigger-{N}`` for N in 1..4.

    Flow (mirrors ``_set_aspect_ratio`` exactly):
      1. open chip panel if ``data-state != "open"``
      2. click ``[id$="-trigger-{count}"]`` via real ``Locator.click`` (Radix
         needs pointerdown — JS ``.click()`` leaves data-state unchanged).
      3. wait for ``data-state="active"`` on that trigger
      4. click-outside to close
      5. verify chip innerText contains ``x{count}``
    """
    if count < 1 or count > 4:
        raise ValueError(f"count must be 1..4, got {count}")

    chip_btn = page.locator(
        'button[aria-haspopup="menu"]:has-text("crop_9_16"), '
        'button[aria-haspopup="menu"]:has-text("crop_16_9")'
    ).first

    current_state = await chip_btn.get_attribute("data-state", timeout=2000)
    if current_state != "open":
        await chip_btn.click(timeout=3000)

    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="visible", timeout=3000,
    )

    trigger = page.locator(f'[id$="-trigger-{count}"]').first
    await trigger.click(timeout=3000)

    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{count}"]\')?.dataset.state === "active"',
        timeout=3000,
    )

    await page.mouse.click(10, 10)
    await page.locator('[role="menu"][data-state="open"]').wait_for(
        state="hidden", timeout=2000,
    )

    chip_text = await chip_btn.inner_text(timeout=2000)
    expected = f"x{count}"
    if not _chip_text_matches_output_count(chip_text, count):
        logger.warning(
            "Output count set to x%d but chip verify failed: chip_text=%r expected substring %r",
            count, chip_text, expected,
        )
    else:
        logger.info("Output count set to x%d (chip verified)", count)


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


try:  # guarded so hybrid wiring runs before sibling revAPI modules land.
    from flow.operations.generate_api import (
        get_t2v_request_template as _get_t2v_request_template,
        install_t2v_request_capture as _install_t2v_request_capture,
        replay_t2v_via_inflate as _replay_t2v_via_inflate,
    )
except Exception as exc:  # pragma: no cover - tested by monkeypatched fallback
    _T2V_API_IMPORT_ERROR = exc
else:
    install_t2v_request_capture = _install_t2v_request_capture
    get_t2v_request_template = _get_t2v_request_template
    replay_t2v_via_inflate = _replay_t2v_via_inflate
    _T2V_API_IMPORT_ERROR = None
