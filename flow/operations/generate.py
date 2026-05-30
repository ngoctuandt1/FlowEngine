"""Text-to-Video generation — Level 1 operation."""

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from flow.navigation import flow_url, extract_project_id
from flow.characters import validate_character_tags
from flow.login import is_login_page, handle_login_redirect
from flow.agent import disable_agent_mode_if_active, install_agent_auth_probe
from flow.landing import (
    dismiss_flow_marketing_landing,
    dismiss_pointer_intercepting_overlays,
    recover_from_flow_canvas_page,
)
from flow.model_selector import canonicalize_video_model_key, select_model, DEFAULT_MODEL
from flow.selector_chain import click_first_visible
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video
from flow.failure_capture import message_with_failure_capture
from flow.operations._base import failure_kind_from_error, resolve_final_media_id
from flow.operations._l1_status_poll import download_via_url, poll_status_via_api

try:
    from flow.operations._base import CreditBudgetExceeded
except ImportError:
    class CreditBudgetExceeded(Exception):
        def __init__(self, cost=None, budget=None, message: str | None = None):
            synthesized = message or f"credit preview cost {cost} exceeds budget {budget}"
            super().__init__(synthesized)
            self.cost = cost
            self.budget = budget
            self.error_kind = "credit_budget_exceeded"

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
    voice_asset_id: str | None = None,
    free_mode: bool = True,
    known_characters=None,
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
    character_resolution = validate_character_tags(prompt, known_characters)
    if character_resolution.resolved:
        setattr(
            client,
            "_resolved_characters",
            [reference.__dict__ for reference in character_resolution.resolved],
        )

    page = client.page
    capture_ready = _install_t2v_capture_if_enabled(client)
    if voice_asset_id and capture_ready:
        logger.info(
            "Skipping T2V reverse replay because voice attach has no captured request body support"
        )
        capture_ready = False
    if capture_ready:
        replay_result = await _try_t2v_replay_from_template(client, prompt)
        if replay_result is not None:
            return replay_result

    locale = ""  # Will detect from URL

    # === Step 1: Navigate to Flow homepage ===
    logger.info("Step 1: Navigate to Flow homepage")
    homepage = flow_url(locale)
    # Install auth probe before first navigation so Bearer tokens are captured
    # on the project page load (triggered by New Project click).
    await install_agent_auth_probe(page)
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

    # Flow auto-starts Agent mode on new projects if the account has agent
    # settings enabled. The Agent UI hides the standard Video/Image/Frames
    # composer mode selector. Disable it before waiting for the composer so
    # _ensure_video_composer_mode sees the normal chip layout.
    if project_id:
        try:
            await disable_agent_mode_if_active(
                page,
                profile_name=client.profile_name,
                target_url=project_url_full,
                log=logger,
            )
        except Exception as _agent_exc:
            logger.warning("generate: agent disable non-fatal: %s", _agent_exc)

    # Wait for project editor (Slate composer) to fully render
    # The Slate.js editor can take a few seconds to initialize after page load
    logger.info("Waiting for project editor to fully render...")
    await _wait_for_composer(page)
    await dismiss_pointer_intercepting_overlays(page, logger)

    # === Step 3: Select model ===
    canonical_model = canonicalize_video_model_key(model, free_mode=free_mode)
    logger.info("Step 2.5: Force composer Video mode before model selection")
    await _ensure_video_composer_mode(page)
    logger.info("Step 2.6: Force output count = x1 before model credit preview")
    await _set_output_count(page, 1)
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
    await dismiss_pointer_intercepting_overlays(page, logger)
    await _type_prompt(page, prompt)

    if voice_asset_id:
        logger.info("Step 5.5: Attach voice asset %s via composer UI", voice_asset_id)
        await _select_voice_asset(page, voice_asset_id)

    # === Step 6: Verify count/credits, count baseline cards, clear captures, submit ===
    logger.info("Step 6: Verify L1 count and credit preview before submit")
    await _guard_l1_submit(page)
    logger.info("Step 6.1: Submit generation")
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


_COMPOSER_MENU_BUTTON_SELECTOR = 'button[aria-haspopup="menu"]'
_OPEN_COMPOSER_MENU_SELECTOR = '[role="menu"][data-state="open"]'
_VOICE_ASSET_PICKER_SELECTOR = (
    '[role="dialog"]:visible, '
    '[role="menu"][data-state="open"]:visible, '
    '[data-radix-popper-content-wrapper]:visible'
)
_AI_LOCATOR_TRUE_VALUES = {"1", "true", "yes", "on"}
_COMPOSER_VIDEO_TAB_AI_CACHE_KEY = "flow.operations.generate.composer_video_tab"
_COUNT_TOKEN_RE = re.compile(r"(?:x[1-4]|[1-4]x)", re.IGNORECASE)
_MODE_OR_MODEL_RE = re.compile(
    r"video|frames?|ingredients?|image|veo|omni|imagen|banana",
    re.IGNORECASE,
)
_ASPECT_TEXT_RE = re.compile(r"16\s*:?\s*9|9\s*:?\s*16|landscape|portrait|square", re.IGNORECASE)


def _max_credits_per_job() -> int:
    return int(os.environ.get("FLOW_MAX_CREDITS_PER_JOB", "10"))


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


def _normalize_composer_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _composer_chip_score(text: str | None) -> int:
    normalized = _normalize_composer_text(text)
    if not normalized:
        return 0

    score = 0
    if _COUNT_TOKEN_RE.search(normalized):
        score += 40
    if _MODE_OR_MODEL_RE.search(normalized):
        score += 35
    if _ASPECT_TEXT_RE.search(normalized):
        score += 10
    return score


async def _collect_composer_menu_button_candidates(page) -> list[dict]:
    try:
        candidates = await page.evaluate(
            """(selector) => {
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && parseFloat(style.opacity || '1') > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                return Array.from(document.querySelectorAll(selector)).map((element, index) => {
                    const rect = element.getBoundingClientRect();
                    const text = (element.innerText || element.textContent || '').trim();
                    const iconText = Array.from(element.querySelectorAll('i, svg [aria-label], [class*="icon" i]'))
                        .map((child) => (child.innerText || child.textContent || child.getAttribute('aria-label') || '').trim())
                        .filter(Boolean);
                    return {
                        index,
                        text,
                        iconText,
                        visible: visible(element),
                        dataState: element.getAttribute('data-state') || '',
                        ariaExpanded: element.getAttribute('aria-expanded') || '',
                        rect: { top: rect.top, left: rect.left, width: rect.width, height: rect.height },
                    };
                }).filter((candidate) => candidate.visible);
            }""",
            _COMPOSER_MENU_BUTTON_SELECTOR,
        )
    except Exception as exc:
        logger.debug("composer menu candidate collection failed: %s", exc)
        return []

    for candidate in candidates:
        candidate["text"] = _normalize_composer_text(candidate.get("text"))
        candidate["score"] = _composer_chip_score(candidate.get("text"))
    return candidates


async def _composer_menu_is_open(page) -> bool:
    candidates = await _collect_composer_menu_button_candidates(page)
    if any(
        candidate.get("dataState") == "open" or candidate.get("ariaExpanded") == "true"
        for candidate in candidates
    ):
        return True
    if candidates:
        return False
    try:
        return await page.locator(_OPEN_COMPOSER_MENU_SELECTOR).first.is_visible(timeout=300)
    except Exception:
        return False


async def _wait_for_composer_menu_open(page, timeout_ms: int = 2500) -> bool:
    try:
        await page.locator(_OPEN_COMPOSER_MENU_SELECTOR).first.wait_for(
            state="visible",
            timeout=timeout_ms,
        )
        return True
    except Exception:
        return False


# Project-level "Add Media" / composer entry-point selectors. In Flow's
# newer collapsed project view the composer chip (with its Video/Frames
# tabs) is NOT mounted until the prompt box is revealed — only project
# toolbar buttons are visible (more_vert, filter_list, add, settings_2).
# Clicking the "add" / "Add Media" entry point mounts the composer.
# Text/aria variants are preferred over the bare icon button so we don't
# accidentally trigger an unrelated overflow control; the bare-icon
# fallback is last and is file-chooser-guarded by the caller.
_COMPOSER_REVEAL_BUTTON_SELECTORS = (
    "button[aria-label*='Add Media' i]",
    "button:has(i:text-is('add')):has-text('Add Media')",
    "button:has(i:text-is('add')):has-text('Add media')",
    "[role='button']:has(i:text-is('add')):has-text('Add')",
    "button:has(i:text-is('add'))",
)


async def _collect_visible_menu_button_texts(page) -> list[str]:
    """Return trimmed text of every visible composer/menu button (diagnostics)."""
    try:
        return await page.evaluate(
            r"""(selector) => {
                const visible = (el) => {
                    const s = getComputedStyle(el);
                    const r = el.getBoundingClientRect();
                    return s.display !== 'none' && s.visibility !== 'hidden'
                        && parseFloat(s.opacity || '1') > 0
                        && r.width > 0 && r.height > 0;
                };
                return Array.from(document.querySelectorAll(selector))
                    .filter(visible)
                    .map((el) => (el.innerText || el.textContent || '').trim())
                    .filter((text) => text.length > 0);
            }""",
            _COMPOSER_MENU_BUTTON_SELECTOR,
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("composer menu button text probe failed: %s", exc)
        return []


async def _try_reveal_collapsed_composer(page) -> bool:
    """Click the project-level "Add Media" entry point to mount the composer.

    Fires ONLY when no scored composer chip candidate exists — i.e. the
    project view is genuinely collapsed and shows only project-toolbar
    buttons. This must NOT affect normal flows where the chip is already
    mounted (the caller gates on an empty scored-candidate list).

    Guards against opening a file chooser: if clicking the bare-icon
    fallback triggers an OS file picker, we cancel it (set no files) so we
    don't leave a modal blocking the page. Returns True if a real composer
    chip appears after the click.
    """
    for selector in _COMPOSER_REVEAL_BUTTON_SELECTORS:
        try:
            button = page.locator(selector).first
            if not await button.is_visible(timeout=1000):
                continue
        except Exception as exc:
            logger.debug("composer reveal selector %s not usable: %s", selector, exc)
            continue

        bare_icon = selector == "button:has(i:text-is('add'))"
        try:
            if bare_icon:
                # The bare "add" button may open a file chooser instead of
                # mounting the composer. Detect that and cancel it.
                try:
                    async with page.expect_file_chooser(timeout=1500) as chooser_info:
                        await button.click(timeout=3000)
                    chooser = await chooser_info.value
                    logger.info(
                        "composer reveal: bare 'add' button opened a file chooser; "
                        "cancelling (wrong target for reveal)"
                    )
                    try:
                        await chooser.set_files([])
                    except Exception:
                        pass
                    continue
                except Exception:
                    # No file chooser -> the click landed on a non-upload
                    # control; fall through to the chip re-check below.
                    pass
            else:
                await button.click(timeout=3000)
            logger.info("composer reveal: clicked entry point via %s", selector)
        except Exception as exc:
            logger.debug("composer reveal click failed for %s: %s", selector, exc)
            continue

        await asyncio.sleep(1.0)
        revealed = [
            candidate
            for candidate in await _collect_composer_menu_button_candidates(page)
            if candidate.get("score", 0) > 0
        ]
        if revealed:
            logger.info(
                "composer reveal succeeded via %s; composer chip now present",
                selector,
            )
            return True
        logger.debug("composer reveal via %s did not expose a chip; trying next", selector)

    return False


async def _capture_composer_menu_failure(page) -> None:
    """Best-effort forensic capture (screenshot + FULL DOM) at the raise site."""
    capture_dir = os.environ.get("FLOW_ERROR_CAPTURE_DIR", "")
    if not capture_dir or os.environ.get("FLOW_ERROR_CAPTURE", "1") == "0":
        return
    try:
        os.makedirs(capture_dir, exist_ok=True)
        ts = int(time.time())
        await page.screenshot(path=os.path.join(capture_dir, f"{ts}_composer_menu_fail.png"))
        html = await page.content()
        with open(
            os.path.join(capture_dir, f"{ts}_composer_menu_fail.full.html"),
            "w",
            encoding="utf-8",
        ) as fh:
            fh.write(html)
        logger.error(
            "composer-menu fail forensics: %s_composer_menu_fail.{png,full.html}", ts
        )
    except Exception as exc:
        logger.debug("composer-menu forensic capture failed: %s", exc)


async def _open_composer_menu_by_role_text(page, *, purpose: str = "composer"):
    already_open = await _composer_menu_is_open(page)
    candidates = await _collect_composer_menu_button_candidates(page)
    scored_candidates = [candidate for candidate in candidates if candidate.get("score", 0) > 0]

    # Reveal-on-collapse: when the project view is collapsed, the composer
    # chip is not mounted and only project-toolbar buttons are visible. No
    # candidate scores > 0 in that state. Attempt to reveal the composer by
    # clicking the "Add Media" entry point, then re-collect. This only fires
    # when the chip is genuinely absent, so normal (chip-present) flows are
    # unaffected. See R22 failure: visible buttons=[more_vert, filter_list,
    # add, settings_2, more_vert] (all project-level, no composer chip).
    if not scored_candidates and not already_open:
        logger.info(
            "composer chip absent for %s (visible menu buttons=%s); attempting reveal",
            purpose,
            await _collect_visible_menu_button_texts(page),
        )
        if await _try_reveal_collapsed_composer(page):
            candidates = await _collect_composer_menu_button_candidates(page)
            scored_candidates = [c for c in candidates if c.get("score", 0) > 0]

    scored_candidates.sort(
        key=lambda candidate: (
            candidate.get("dataState") == "open",
            candidate.get("ariaExpanded") == "true",
            candidate.get("score", 0),
        ),
        reverse=True,
    )

    for candidate in scored_candidates:
        button = page.locator(_COMPOSER_MENU_BUTTON_SELECTOR).nth(candidate["index"])
        try:
            candidate_is_open = (
                candidate.get("dataState") == "open"
                or candidate.get("ariaExpanded") == "true"
            )
            if already_open and not candidate_is_open:
                continue
            if not already_open and not candidate_is_open:
                await button.click(timeout=3000)
            if candidate_is_open or await _wait_for_composer_menu_open(page):
                logger.info(
                    "Opened composer menu for %s via text=%r score=%s",
                    purpose,
                    candidate.get("text"),
                    candidate.get("score"),
                )
                return button
        except Exception as exc:
            logger.debug(
                "Composer menu candidate failed for %s: text=%r error=%s",
                purpose,
                candidate.get("text"),
                exc,
            )

    fallback_button = page.locator(_COMPOSER_MENU_BUTTON_SELECTOR).first
    try:
        fallback_text = _normalize_composer_text(await fallback_button.inner_text(timeout=1000))
        fallback_score = _composer_chip_score(fallback_text)
        fallback_state = await fallback_button.get_attribute("data-state", timeout=1000)
        fallback_expanded = await fallback_button.get_attribute("aria-expanded", timeout=1000)
        fallback_is_open = fallback_state == "open" or fallback_expanded == "true"
        if fallback_score > 0 or already_open:
            if already_open and not fallback_is_open:
                raise RuntimeError("open composer menu trigger was not first button")
            if (
                not already_open
                and not fallback_is_open
            ):
                await fallback_button.click(timeout=3000)
            if fallback_is_open or await _wait_for_composer_menu_open(page):
                logger.info(
                    "Opened composer menu for %s via first visible text fallback=%r score=%s",
                    purpose,
                    fallback_text,
                    fallback_score,
                )
                return fallback_button
    except Exception as exc:
        logger.debug("Composer first-button fallback failed for %s: %s", purpose, exc)

    diagnostics = [
        {
            "text": candidate.get("text"),
            "score": candidate.get("score"),
            "state": candidate.get("dataState"),
            "expanded": candidate.get("ariaExpanded"),
            "icons": candidate.get("iconText"),
        }
        for candidate in candidates
    ]
    await _capture_composer_menu_failure(page)
    raise RuntimeError(
        f"Could not open composer menu for {purpose}; visible menu buttons={diagnostics}"
    )


async def _close_composer_menu_by_click_outside(page) -> None:
    if not await _composer_menu_is_open(page):
        return
    await page.mouse.click(10, 10)
    try:
        await page.locator(_OPEN_COMPOSER_MENU_SELECTOR).first.wait_for(
            state="hidden",
            timeout=2000,
        )
    except Exception:
        logger.debug("Composer menu did not report hidden after click-outside")


async def _find_open_composer_tab(page, label: str):
    tabs = page.locator(f'{_OPEN_COMPOSER_MENU_SELECTOR} [role="tab"]')
    observed_tabs = []
    try:
        count = await tabs.count()
    except Exception as exc:
        return None, None, [f"<tab-count-unavailable:{exc}>"]
    label_re = re.compile(rf"(^|\s){re.escape(label)}(\s|$)", re.IGNORECASE)
    for index in range(count):
        tab = tabs.nth(index)
        try:
            text = _normalize_composer_text(await tab.inner_text(timeout=1000))
            state = await tab.get_attribute("data-state")
            observed_tabs.append(f"{text!r}={state}")
            if label_re.search(text):
                return tab, state, observed_tabs
        except Exception as exc:
            observed_tabs.append(f"<unreadable:{exc}>")
    return None, None, observed_tabs


async def _ensure_video_composer_mode(page, *, keep_open: bool = False):
    from flow.ai_locator import ai_locate

    chip_btn = await _open_composer_menu_by_role_text(page, purpose="Video mode")
    tab, state, observed_tabs = await _find_open_composer_tab(page, "Video")
    if tab is None:
        legacy_tab = page.locator('[id$="-trigger-VIDEO"]').first
        try:
            legacy_state = await legacy_tab.get_attribute("data-state")
            if legacy_state != "active":
                await legacy_tab.click(timeout=2000)
                await page.wait_for_function(
                    '() => document.querySelector(\'[id$="-trigger-VIDEO"]\')?.dataset.state === "active"',
                    timeout=2000,
                )
            if not keep_open:
                await _close_composer_menu_by_click_outside(page)
            logger.info("Composer forced to Video mode via trigger fallback; tabs=%s", observed_tabs)
            return chip_btn
        except Exception as exc:
            if not _ai_locator_enabled():
                await _close_composer_menu_by_click_outside(page)
                raise RuntimeError(
                    f"Composer Video tab not found; observed tabs={observed_tabs}"
                ) from exc
            result = await ai_locate(
                page,
                (
                    "In the currently open Google Flow composer menu, find the Video "
                    "tab button only. Do not choose Frames, Ingredients, Image, upload "
                    "controls, output-count chips, aspect controls, model controls, or "
                    "any edit-view mode button."
                ),
                candidates=(),
                cache_key=_COMPOSER_VIDEO_TAB_AI_CACHE_KEY,
            )
            try:
                if await _click_ai_locator_result(page, result, timeout_ms=3000):
                    await asyncio.sleep(0.3)
                    logger.info(
                        "Composer forced to Video mode via AI locator; tabs=%s",
                        observed_tabs,
                    )
                    if not keep_open:
                        await _close_composer_menu_by_click_outside(page)
                    return chip_btn
            except Exception as ai_exc:
                logger.debug("Composer Video tab AI fallback failed: %s", ai_exc)
            await _close_composer_menu_by_click_outside(page)
            raise RuntimeError(
                f"Composer Video tab not found; observed tabs={observed_tabs}"
            ) from exc

    if state != "active":
        primary_error = None
        try:
            await tab.click(timeout=3000)
            await asyncio.sleep(0.3)
            tab, state, observed_tabs = await _find_open_composer_tab(page, "Video")
        except Exception as exc:
            primary_error = exc
        used_ai_fallback = False
        if state != "active":
            if _ai_locator_enabled():
                result = await ai_locate(
                    page,
                    (
                        "In the currently open Google Flow composer menu, find the Video "
                        "tab button only. Do not choose Frames, Ingredients, Image, upload "
                        "controls, output-count chips, aspect controls, model controls, or "
                        "any edit-view mode button."
                    ),
                    candidates=(),
                    cache_key=_COMPOSER_VIDEO_TAB_AI_CACHE_KEY,
                )
                try:
                    if await _click_ai_locator_result(page, result, timeout_ms=3000):
                        await asyncio.sleep(0.3)
                        tab, state, observed_tabs = await _find_open_composer_tab(page, "Video")
                        used_ai_fallback = True
                except Exception as exc:
                    logger.debug("Composer Video tab AI fallback failed: %s", exc)
            if state == "active":
                logger.info("Composer forced to Video mode via AI locator; tabs=%s", observed_tabs)
            elif primary_error is not None and not _ai_locator_enabled():
                await _close_composer_menu_by_click_outside(page)
                raise primary_error
            else:
                await _close_composer_menu_by_click_outside(page)
                raise RuntimeError(f"Composer Video tab did not become active; observed tabs={observed_tabs}")
        if not used_ai_fallback:
            logger.info("Composer forced to Video mode; tabs=%s", observed_tabs)

    if not keep_open:
        await _close_composer_menu_by_click_outside(page)
    return chip_btn



# Radix id-suffix map for VIDEO sub-mode tabs.
# FRAMES and INGREDIENTS are NOT top-level tabs — they only render after the
# VIDEO top-level tab becomes active. Live-verified 2026-05-30:
#   Frames:      button[role='tab'][id$='-trigger-VIDEO_FRAMES']
#   Ingredients: button[role='tab'][id$='-trigger-VIDEO_REFERENCES']
_VIDEO_SUBTAB_ID_SUFFIX: dict[str, str] = {
    "frames": "VIDEO_FRAMES",
    "ingredients": "VIDEO_REFERENCES",
}


async def _select_video_composer_subtab(page, label: str) -> None:
    """Activate a VIDEO sub-mode tab (Frames or Ingredients) in the composer.

    Flow's Frames and Ingredients modes are VIDEO sub-tabs — they only appear
    after the VIDEO top-level tab is active.  We therefore:
      1. Ensure VIDEO is active (keep_open=True so the menu stays open).
      2. Wait ~1 s for the sub-tab DOM nodes to render.
      3. Try the known Radix id-suffix first (most reliable).
      4. Fall back to text-based scan if the id-based locator misses.
    """
    await _ensure_video_composer_mode(page, keep_open=True)

    # Sub-tabs render after VIDEO becomes active — give them time.
    await asyncio.sleep(1.0)

    id_suffix = _VIDEO_SUBTAB_ID_SUFFIX.get(label.lower())
    tab = None
    state = None
    observed_tabs: list[str] = []

    if id_suffix:
        id_selector = f'[id$="-trigger-{id_suffix}"]'
        try:
            loc = page.locator(id_selector).first
            state = await loc.get_attribute("data-state", timeout=2000)
            if state is not None:
                tab = loc
                logger.debug(
                    "Composer sub-tab %r found via id selector %s (state=%s)",
                    label, id_selector, state,
                )
        except Exception as exc:
            logger.debug(
                "Composer sub-tab id-selector %s lookup failed: %s; falling back to text scan",
                id_selector, exc,
            )

    if tab is None:
        tab, state, observed_tabs = await _find_open_composer_tab(page, label)

    if tab is None:
        await _close_composer_menu_by_click_outside(page)
        raise RuntimeError(
            f"Composer sub-tab not found: {label!r}; observed tabs={observed_tabs}"
        )

    if state != "active":
        await tab.click(timeout=3000)
        await asyncio.sleep(0.3)
        # Re-check state: prefer id-based re-read when available.
        if id_suffix:
            try:
                state = await page.locator(f'[id$="-trigger-{id_suffix}"]').first.get_attribute(
                    "data-state", timeout=1500
                )
            except Exception:
                _, state, observed_tabs = await _find_open_composer_tab(page, label)
        else:
            _, state, observed_tabs = await _find_open_composer_tab(page, label)

        if state != "active":
            await _close_composer_menu_by_click_outside(page)
            raise RuntimeError(
                f"Composer sub-tab did not become active: {label!r}; observed tabs={observed_tabs}"
            )

    logger.info("Composer sub-tab active: %s", label)


async def _select_voice_asset(page, voice_asset_id: str) -> None:
    """Attach a Flow voice preset through the composer asset picker UI."""

    voice_id = str(voice_asset_id or "").strip()
    if not voice_id:
        return

    await _open_voice_asset_picker(page)
    await _activate_voice_picker_tab(page)
    await _click_voice_asset_option(page, voice_id)
    await _verify_voice_asset_selected(page, voice_id)


async def _open_voice_asset_picker(page) -> None:
    selectors = [
        "button[aria-label*='Add Media' i]",
        "button[title*='Add Media' i]",
        "button:has(i:text-is('add'))",
        "button:has-text('add')",
    ]
    last_error: Exception | None = None
    for selector in selectors:
        try:
            button = page.locator(selector).first
            if not await button.is_visible(timeout=800):
                continue
            await button.click(timeout=3000)
            await page.locator(_VOICE_ASSET_PICKER_SELECTOR).first.wait_for(
                state="visible",
                timeout=3000,
            )
            return
        except Exception as exc:
            last_error = exc
            logger.debug("Voice picker trigger failed via %s: %s", selector, exc)
    raise RuntimeError("Could not open voice asset picker") from last_error


async def _activate_voice_picker_tab(page) -> None:
    picker = page.locator(_VOICE_ASSET_PICKER_SELECTOR).first
    tab = picker.locator(
        '[role="tab"]:has-text("Voices"), '
        'button:has-text("voice_selection"), '
        'button:has-text("Voices")'
    ).first
    try:
        await tab.click(timeout=3000)
        await asyncio.sleep(0.2)
        state = await tab.get_attribute("data-state", timeout=1000)
        if state not in (None, "active"):
            raise RuntimeError(f"Voices tab state is {state!r}")
    except Exception as exc:
        observed = await _voice_picker_diagnostics(page)
        raise RuntimeError(f"Voice picker Voices tab not found; observed={observed}") from exc


async def _click_voice_asset_option(page, voice_asset_id: str) -> None:
    escaped = re.escape(voice_asset_id)
    picker = page.locator(_VOICE_ASSET_PICKER_SELECTOR).first
    option = picker.locator(
        f'[data-media-id="{voice_asset_id}"], '
        f'[data-id="{voice_asset_id}"], '
        f'button:has-text("{voice_asset_id}"), '
        f'[role="option"]:has-text("{voice_asset_id}")'
    ).first
    try:
        await option.click(timeout=4000)
        await asyncio.sleep(0.4)
        return
    except Exception as exc:
        observed = await _voice_picker_diagnostics(page)
        raise RuntimeError(
            f"Voice asset {voice_asset_id!r} not selectable; observed={observed}; pattern={escaped}"
        ) from exc


async def _verify_voice_asset_selected(page, voice_asset_id: str) -> None:
    try:
        selected = await page.evaluate(
            """(voiceId) => {
                const visible = (element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && parseFloat(style.opacity || '1') > 0
                        && rect.width > 0
                        && rect.height > 0;
                };
                const selectedTokens = [
                    '[aria-selected="true"]',
                    '[data-state="checked"]',
                    '[data-state="active"]',
                    '[data-selected="true"]',
                ];
                return Array.from(document.querySelectorAll('button, [role="option"], [role="gridcell"], [data-media-id], [data-id]'))
                    .filter(visible)
                    .some((element) => {
                        const text = (element.innerText || element.textContent || '').trim();
                        const mediaId = element.getAttribute('data-media-id') || element.getAttribute('data-id') || '';
                        if (mediaId !== voiceId && text !== voiceId) return false;
                        return selectedTokens.some((selector) => element.matches(selector) || element.querySelector(selector));
                    });
            }""",
            voice_asset_id,
        )
    except Exception as exc:
        raise RuntimeError(f"Voice asset selection verification failed: {voice_asset_id}") from exc

    if not selected:
        candidates = await _voice_picker_diagnostics(page)
        raise RuntimeError(
            f"Voice asset selected state not verified for {voice_asset_id!r}; observed={candidates}"
        )


async def _voice_picker_diagnostics(page) -> list[dict]:
    try:
        return await page.evaluate(
            """(selector) => Array.from(document.querySelectorAll(selector))
                .slice(0, 20)
                .map((element) => ({
                    text: (element.innerText || element.textContent || '').trim().slice(0, 120),
                    role: element.getAttribute('role') || '',
                    state: element.getAttribute('data-state') || '',
                    selected: element.getAttribute('aria-selected') || '',
                    mediaId: element.getAttribute('data-media-id') || element.getAttribute('data-id') || '',
                }))""",
            f"{_VOICE_ASSET_PICKER_SELECTOR} button, {_VOICE_ASSET_PICKER_SELECTOR} [role='tab'], {_VOICE_ASSET_PICKER_SELECTOR} [role='option']",
        )
    except Exception as exc:
        return [{"error": str(exc)}]


async def _read_credit_preview_cost(page) -> int | None:
    try:
        result = await page.evaluate(
            r"""() => {
                const text = document.body?.innerText || '';
                const patterns = [
                    /will\s+use\s+(\d+)\s+credits?/i,
                    /(?:cost|costs|requires?|use|uses)\s+(\d+)\s+credits?/i,
                    /(\d+)\s+credits?/i,
                    /(\d+)\s+tín\s*dụng/i,
                    /(\d+)\s+tin\s*dung/i,
                ];
                for (const pattern of patterns) {
                    const match = text.match(pattern);
                    if (match) return parseInt(match[1], 10);
                }
                return null;
            }"""
        )
    except Exception as exc:
        logger.debug("Credit preview read failed: %s", exc)
        return None
    return int(result) if result is not None else None


async def _verify_credits(page, *, budget: int | None = None) -> int | None:
    resolved_budget = _max_credits_per_job() if budget is None else budget
    cost = await _read_credit_preview_cost(page)
    if cost is None:
        logger.info("Credit preview not found after model/count selection")
        return None
    if cost > resolved_budget:
        logger.warning("Credit budget exceeded before submit: cost %d > budget %d", cost, resolved_budget)
        raise CreditBudgetExceeded(cost=cost, budget=resolved_budget)
    logger.info("Credit preview OK before submit: %d <= %d", cost, resolved_budget)
    return cost


async def _verify_l1_output_count(page, count: int = 1) -> str:
    candidates = await _collect_composer_menu_button_candidates(page)
    scored_candidates = [candidate for candidate in candidates if candidate.get("score", 0) > 0]
    scored_candidates.sort(key=lambda candidate: candidate.get("score", 0), reverse=True)
    if not scored_candidates:
        raise RuntimeError(
            f"L1 output count verification failed: no visible composer count chip; candidates={candidates}"
        )

    chip_text = scored_candidates[0].get("text", "")
    if not _chip_text_matches_output_count(chip_text, count):
        raise RuntimeError(
            f"L1 output count verification failed: expected x{count}/1x, composer chip text={chip_text!r}; refusing to submit"
        )
    return chip_text


async def _guard_l1_submit(page) -> None:
    chip_text = await _verify_l1_output_count(page, 1)
    logger.info("L1 output count verified before submit: %s", chip_text)
    budget = _max_credits_per_job()
    cost = await _verify_credits(page, budget=budget)
    if cost is not None and cost > budget:
        raise CreditBudgetExceeded(cost=cost, budget=budget)


async def _verify_frames_upload_affordances(page, timeout_sec: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            affordances = await page.evaluate(
                """() => {
                    const visible = (element) => {
                        const style = getComputedStyle(element);
                        const rect = element.getBoundingClientRect();
                        return style.display !== 'none'
                            && style.visibility !== 'hidden'
                            && rect.width > 0
                            && rect.height > 0;
                    };
                    const hasUploadNearLabel = (label) => {
                        const nodes = Array.from(document.querySelectorAll('*')).filter((element) => {
                            return visible(element) && (element.textContent || '').trim() === label;
                        });
                        return nodes.some((node) => {
                            let current = node;
                            for (let depth = 0; current && depth < 6; depth += 1, current = current.parentElement) {
                                if (current.querySelector?.('input[type="file"], button, [role="button"]')) return true;
                            }
                            return false;
                        });
                    };
                    return { start: hasUploadNearLabel('Start'), end: hasUploadNearLabel('End') };
                }"""
            )
            if affordances and affordances.get("start") and affordances.get("end"):
                logger.info("Frames upload affordances verified: Start and End")
                return
        except Exception:
            pass
        await asyncio.sleep(0.4)
    raise RuntimeError("Frames mode verification failed: Start/End upload affordances not visible")


async def _verify_ingredients_upload_affordance(page, timeout_sec: float = 5.0) -> None:
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            visible = await page.evaluate(
                """() => Array.from(document.querySelectorAll('button, [role="button"]')).some((element) => {
                    const style = getComputedStyle(element);
                    const rect = element.getBoundingClientRect();
                    const text = (element.innerText || element.textContent || '').trim().toLowerCase();
                    return style.display !== 'none'
                        && style.visibility !== 'hidden'
                        && rect.width > 0
                        && rect.height > 0
                        && (text === '+' || text.includes('add') || text.includes('upload'));
                })"""
            )
            if visible:
                logger.info("Ingredients upload affordance verified")
                return
        except Exception:
            pass
        await asyncio.sleep(0.4)
    raise RuntimeError("Ingredients mode verification failed: upload affordance not visible")


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

    # Open the composer chip by role + visible text/current count/model, then
    # keep icon strings only as diagnostic text in failure logs. Flow's 2026-05
    # composer moved these controls inside one Radix menu, so acceptance hinges
    # on a real menu-open result, not exact Material ligatures.
    chip_btn = await _open_composer_menu_by_role_text(page, purpose="aspect ratio")

    # Radix DropdownMenu trigger reflects open/closed via `data-state`.
    # A preceding interaction (model-selector dropdown dismiss, DOM
    # focus-trap reset) can leave the aspect chip in the open state
    # before we arrive — clicking it then TOGGLES the menu CLOSED
    # and the subsequent `wait_for("[role=\"menu\"][data-state=\"open\"]")`
    # times out (B19, Tier 2 Run 3-6).
    # Only click to open when the trigger is currently closed.
    await _ensure_video_composer_mode(page, keep_open=True)

    trigger = page.locator(f'[id$="-trigger-{suffix}"]').first
    await trigger.click(timeout=3000)

    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{suffix}"]\')?.dataset.state === "active"',
        timeout=3000,
    )

    # Click-outside to close. Top-left viewport is a safe dead zone outside
    # both the bottom-center composer and the bottom-right chip panel.
    await page.mouse.click(10, 10)
    try:
        await page.locator(_OPEN_COMPOSER_MENU_SELECTOR).first.wait_for(
            state="hidden", timeout=2000,
        )
    except Exception:
        logger.debug("Aspect ratio menu did not report hidden after click-outside")

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

    chip_btn = await _open_composer_menu_by_role_text(page, purpose="output count")
    await _ensure_video_composer_mode(page, keep_open=True)

    trigger = page.locator(f'[id$="-trigger-{count}"]').first
    await trigger.click(timeout=3000)

    await page.wait_for_function(
        f'() => document.querySelector(\'[id$="-trigger-{count}"]\')?.dataset.state === "active"',
        timeout=3000,
    )

    await page.mouse.click(10, 10)
    try:
        await page.locator(_OPEN_COMPOSER_MENU_SELECTOR).first.wait_for(
            state="hidden", timeout=2000,
        )
    except Exception:
        logger.debug("Output count menu did not report hidden after click-outside")

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
