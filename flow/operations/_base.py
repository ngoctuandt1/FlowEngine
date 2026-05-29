"""Shared base for Level-2 operations (extend, insert, remove, camera).

All Level-2 ops navigate to the video's edit URL, perform an action,
wait, download, and return metadata.
"""

import asyncio
import inspect
import logging
import os
import time
from types import SimpleNamespace

from flow.failure_capture import message_with_failure_capture
from flow.navigation import (
    detect_locale,
    extract_media_id,
    extract_project_id,
    find_latest_tile_slug,
    flow_url,
)
from flow.agent import (
    disable_agent_mode_if_active,
    install_agent_auth_probe,
    install_agent_session_blocker,
    uninstall_agent_session_blocker,
)
from flow.landing import recover_from_flow_landing
from flow.login import is_login_page, handle_login_redirect
from flow.submit import submit_with_confirmation
from flow.wait import wait_for_completion
from flow.download import download_video
from flow.reverse_api import (
    ReverseApiOutcome,
    env_flag,
    log_reverse_api_disabled,
    log_reverse_api_unavailable,
    reverse_api_preferred,
    run_reverse_api_first,
)


class LeafLockoutError(RuntimeError):
    """Raised when Flow's SPA auto-navigates to a leaf extend-output clip.

    On projects with an existing extend chain, the SPA may land on the
    leaf (most recent extend output) whose Camera/Insert/Remove buttons
    are disabled by Flow's own UI rules — only Extend is available on
    extend-output clips.

    Both recovery strategies failed:
    1. JS MouseEvent dispatch on the target tile (_activate_clip_tile)
    2. Real Playwright .click() on [data-tile-id] (_click_video_tile edit branch)

    This is a UI-state issue, not a profile/reCAPTCHA problem.
    DO NOT trigger ProfileSwapper on this error.

    Attributes:
        target_media_id: The media UUID the job was trying to edit.
        current_url: The page URL when both recovery attempts failed.
        current_media_id: The media UUID reflected in the current URL/DOM.
        op_type: The operation type (e.g. "insert-object", "camera-move").
    """

    def __init__(
        self,
        *,
        target_media_id: str,
        current_url: str,
        current_media_id: str,
        op_type: str,
    ) -> None:
        self.target_media_id = target_media_id
        self.current_url = current_url
        self.current_media_id = current_media_id
        self.op_type = op_type
        super().__init__(
            f"b28_leaf_lockout: {op_type} on target={target_media_id[:20]} "
            f"but SPA is on leaf={current_media_id[:20] if current_media_id else 'unknown'} "
            f"url={current_url[:80]}; tile activation failed on both JS-dispatch and click paths"
        )


L2_PAYWALL_BANNER_TEXT = "Video editing is only available for paid subscribers"
_L2_SILENT_HIDE_EDITOR_MOUNT_TIMEOUT_MS = 1000
_L2_SILENT_HIDE_PAINT_WAIT_MS = 8000
_L2_TOOLBAR_TOKENS = (
    "Extend",
    "Insert",
    "Remove",
    "Camera",
    "Mở rộng",
    "Chèn",
    "Xoá",
    "Máy quay",
    "keyboard_double_arrow_right",
    "add_box",
    "ink_eraser",
    "videocam",
)
_L2_SILENT_HIDE_OPERATIONS = {
    "extend-video",
    "insert-object",
    "remove-object",
    "camera-move",
}
# 2026-05 Flow redesign: traditional toolbar replaced by AI agent editing panel.
# Detected by a visible "Describe your edit(s)" contenteditable input.  When
# this UI is present the editing operations ARE available — just via text
# command instead of toolbar buttons.  We must NOT raise L2PaywallError here.
_AGENT_EDIT_UI_TOKENS = ("Describe your edit", "Describe your edits")


class L2PaywallError(RuntimeError):
    """Raised when Flow gates L2 editing behind the paid tier."""

    error_kind = "paid_tier_required"

    def __init__(
        self,
        *,
        operation: str,
        profile: str | None = None,
        message: str = L2_PAYWALL_BANNER_TEXT,
    ) -> None:
        self.operation = operation
        self.profile = profile or ""
        self.message = message
        super().__init__(message)


class CreditBudgetExceeded(ValueError):
    """Raised before submit when a job would exceed configured credits."""

    error_kind = "credit_budget_exceeded"

    def __init__(self, *, cost: int | float, budget: int | float) -> None:
        self.cost = cost
        self.budget = budget
        self.message = (
            f"cost {_format_credit_amount(cost)} exceeds budget "
            f"{_format_credit_amount(budget)}"
        )
        super().__init__(self.message)


class L2ReverseApiPostAcceptError(RuntimeError):
    """Raised after reverse API accepts an L2 submit but finalization fails."""

    error_kind = "reverse_api_post_accept_failed"

    def __init__(
        self,
        *,
        operation: str,
        media_id: str,
        cause: BaseException,
    ) -> None:
        self.operation = operation
        self.media_id = media_id
        self.cause = cause
        message = (
            f"{operation} reverse API accepted media_id={media_id}; "
            f"UI fallback disabled after accepted submit: {cause}"
        )
        super().__init__(message)


logger = logging.getLogger(__name__)


def l2_reverse_api_enabled(operation_env_var: str | None = None) -> bool:
    """Return whether L2 reverse API should be attempted for this operation."""

    if not reverse_api_preferred():
        return False
    if not operation_env_var:
        return True
    return env_flag(operation_env_var, default=True)


def l2_reverse_api_template_has_auth(template: dict | None) -> bool:
    if not isinstance(template, dict):
        return False
    headers = template.get("headers")
    if not isinstance(headers, dict):
        return False
    for name, value in headers.items():
        if str(name).lower() == "authorization" and str(value).strip():
            return True
    return False


def is_fatal_l2_reverse_api_error(exc: BaseException) -> bool:
    """Validation/paywall/budget errors must not fall back to UI."""

    if isinstance(
        exc,
        (L2PaywallError, CreditBudgetExceeded, L2ReverseApiPostAcceptError, ValueError),
    ):
        return True
    if getattr(exc, "error_kind", "") in {
        "paid_tier_required",
        "credit_budget_exceeded",
        "reverse_api_post_accept_failed",
    }:
        return True
    text = str(exc).lower()
    fatal_tokens = (
        "paid_tier_required",
        L2_PAYWALL_BANNER_TEXT.lower(),
        "credit_budget_exceeded",
        "paywall",
    )
    return any(token in text for token in fatal_tokens)


def _l2_reverse_api_inflight_state(client) -> dict:
    state = getattr(client, "_l2_reverse_api_inflight", None)
    if isinstance(state, dict):
        return state
    state = {}
    setattr(client, "_l2_reverse_api_inflight", state)
    return state


def _mark_l2_reverse_api_inflight(
    client,
    *,
    operation: str,
    media_id: str,
    status: str,
    error: str = "",
) -> None:
    if not media_id:
        return
    state = _l2_reverse_api_inflight_state(client)
    entry = {
        "operation": operation,
        "media_id": media_id,
        "status": status,
    }
    if error:
        entry["error"] = error
    state[media_id] = entry


async def finalize_l2_reverse_api_after_accept(
    client,
    *,
    operation: str,
    media_id: str,
    finalize_call,
):
    """Finalize accepted L2 reverse submit without allowing UI fallback."""

    _mark_l2_reverse_api_inflight(
        client,
        operation=operation,
        media_id=media_id,
        status="accepted",
    )
    try:
        result = finalize_call()
        if inspect.isawaitable(result):
            result = await result
    except L2ReverseApiPostAcceptError:
        raise
    except Exception as exc:
        _mark_l2_reverse_api_inflight(
            client,
            operation=operation,
            media_id=media_id,
            status="post_accept_failed",
            error=str(exc),
        )
        raise L2ReverseApiPostAcceptError(
            operation=operation,
            media_id=media_id,
            cause=exc,
        ) from exc

    _mark_l2_reverse_api_inflight(
        client,
        operation=operation,
        media_id=media_id,
        status="completed",
    )
    return result


async def run_l2_reverse_api_first(
    *,
    operation: str,
    call,
    log: logging.Logger | None = None,
    available: bool = True,
    unavailable_reason: str = "captured template unavailable",
    metadata: dict | None = None,
    timeout_sec: float | None = None,
) -> ReverseApiOutcome:
    """Central L2 reverse-API preference gate with fatal-error policy."""

    return await run_reverse_api_first(
        operation=operation,
        call=call,
        log=log or logger,
        available=available,
        unavailable_reason=unavailable_reason,
        metadata=metadata,
        timeout_sec=timeout_sec,
        is_fatal_error=is_fatal_l2_reverse_api_error,
    )


def log_l2_reverse_api_unavailable(
    log: logging.Logger,
    *,
    operation: str,
    reason: str,
    metadata: dict | None = None,
) -> None:
    log_reverse_api_unavailable(
        log,
        operation=operation,
        reason=reason,
        metadata=metadata,
    )


def log_l2_reverse_api_disabled(
    log: logging.Logger,
    *,
    operation: str,
    metadata: dict | None = None,
) -> None:
    log_reverse_api_disabled(log, operation=operation, metadata=metadata)

_CHAIN_CHILD_NO_NEW_MEDIA_KIND = "chain_child_no_new_media"


class _TileLookupInconclusive(RuntimeError):
    """Raised when test doubles cannot answer Playwright locator counts."""


async def _capture_chain_child_no_new_media(page) -> None:
    """Best-effort forensic capture for strict chain-child media misses."""
    try:
        from flow.diagnostics import capture_failure
    except Exception as exc:
        logger.warning("%s: capture import failed: %s", _CHAIN_CHILD_NO_NEW_MEDIA_KIND, exc)
        return

    try:
        result = capture_failure(
            page,
            kind=_CHAIN_CHILD_NO_NEW_MEDIA_KIND,
            logger=logger,
        )
        if inspect.isawaitable(result):
            await result
        return
    except TypeError:
        pass
    except Exception as exc:
        logger.warning("%s: capture failed: %s", _CHAIN_CHILD_NO_NEW_MEDIA_KIND, exc)
        return

    try:
        result = capture_failure(
            SimpleNamespace(page=page),
            "unknown",
            _CHAIN_CHILD_NO_NEW_MEDIA_KIND,
        )
        if inspect.isawaitable(result):
            await result
    except Exception as exc:
        logger.warning("%s: capture failed: %s", _CHAIN_CHILD_NO_NEW_MEDIA_KIND, exc)


def _short_value(value: str | None, limit: int) -> str:
    if value is None:
        return "None"
    return str(value)[:limit]


def _format_credit_amount(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def failure_kind_from_error(job_type: str, error: str) -> str:
    text = str(error).split("[cap=", 1)[0].strip().lower()
    for token in (
        "blocked_403",
        "blocked_429",
        "no_signal_timeout",
        "timeout",
        "all_failed",
        "no_credits",
        "policy",
    ):
        if token in text:
            return token
    return f"{job_type.replace('-', '_')}_failed"


async def _locator_is_visible(locator, *, timeout_ms: int = 500) -> bool:
    try:
        target = getattr(locator, "first", locator)
        visible = target.is_visible(timeout=timeout_ms)
    except TypeError:
        try:
            target = getattr(locator, "first", locator)
            visible = target.is_visible()
        except Exception:
            return False
    except Exception:
        return False

    try:
        if inspect.isawaitable(visible):
            visible = await visible
    except Exception:
        return False
    return visible if isinstance(visible, bool) else False


async def _text_is_visible(
    page,
    text: str,
    *,
    exact: bool = True,
    timeout_ms: int = 500,
) -> bool:
    getter = getattr(page, "get_by_text", None)
    if not callable(getter):
        return False
    try:
        locator = getter(text, exact=exact)
    except TypeError:
        try:
            locator = getter(text)
        except Exception:
            return False
    except Exception:
        return False
    return await _locator_is_visible(locator, timeout_ms=timeout_ms)


async def _role_is_visible(
    page,
    role: str,
    *,
    name: str,
    timeout_ms: int = 500,
) -> bool:
    getter = getattr(page, "get_by_role", None)
    if not callable(getter):
        return False
    try:
        locator = getter(role, name=name)
    except TypeError:
        try:
            locator = getter(role, name)
        except Exception:
            return False
    except Exception:
        return False
    return await _locator_is_visible(locator, timeout_ms=timeout_ms)


async def _upgrade_cta_is_visible(page, *, timeout_ms: int = 500) -> bool:
    if await _role_is_visible(page, "button", name="Upgrade", timeout_ms=timeout_ms):
        return True
    if await _role_is_visible(page, "link", name="Upgrade", timeout_ms=timeout_ms):
        return True

    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False
    for selector in (
        "button:has-text('Upgrade')",
        "a:has-text('Upgrade')",
        "[role='button']:has-text('Upgrade')",
        "[role='link']:has-text('Upgrade')",
    ):
        try:
            if await _locator_is_visible(locator(selector), timeout_ms=timeout_ms):
                return True
        except Exception:
            continue
    return False


async def _assert_l2_available(page, op_name: str, profile: str | None) -> None:
    """Raise on either (a) Flow's explicit paid-tier L2 banner + Upgrade CTA,
    or (b) the 2026-05 silent-hide variant where the entire L2 toolbar is
    absent from the edit view.

    Live-probed 2026-05-24 on free-tier ``ngoctuandt20``: the edit view loads
    a generated video with no paywall banner AND no Extend / Insert / Remove
    / Camera affordances. Without this fallback path the per-op handlers
    raise generic ``RuntimeError("Failed to find <X> button")`` and the job
    record never gets the canonical ``error_kind=paid_tier_required``.
    """
    banner_visible = await _text_is_visible(page, L2_PAYWALL_BANNER_TEXT, timeout_ms=500)
    if banner_visible:
        await asyncio.sleep(0.25)
        if not await _text_is_visible(page, L2_PAYWALL_BANNER_TEXT, timeout_ms=250):
            banner_visible = False

    if banner_visible:
        if not await _upgrade_cta_is_visible(page, timeout_ms=500):
            logger.info(
                "L2 paywall banner visible for op=%s profile=%s, but Upgrade CTA missing",
                op_name,
                profile or "",
            )
            return
        raise L2PaywallError(operation=op_name, profile=profile)

    if op_name not in _L2_SILENT_HIDE_OPERATIONS:
        return

    current_url = str(getattr(page, "url", "") or "")
    if "/edit/" not in current_url:
        return

    if not await _editor_mounted(
        page, timeout_ms=_L2_SILENT_HIDE_EDITOR_MOUNT_TIMEOUT_MS
    ):
        return

    toolbar_visible = await _l2_toolbar_visible_after_paint(
        page, timeout_ms=_L2_SILENT_HIDE_PAINT_WAIT_MS
    )

    if not toolbar_visible:
        current_url = str(getattr(page, "url", "") or "")
        if "/edit/" not in current_url:
            return
        # 2026-05 UI: check for new agent editing panel ("Describe your edit/edits").
        # If present, editing is available via text command — not a paywall block.
        for _agent_token in _AGENT_EDIT_UI_TOKENS:
            if await _text_is_visible(page, _agent_token, exact=False, timeout_ms=1000):
                logger.info(
                    "_assert_l2: new agent edit UI detected (token=%r) for op=%s — "
                    "editing available via 'Describe your edit' interface",
                    _agent_token,
                    op_name,
                )
                return
        # Dump visible buttons + screenshot to diagnose what's actually on screen
        try:
            eval_fn = getattr(page, "evaluate", None)
            if callable(eval_fn):
                buttons = await eval_fn(
                    "() => Array.from(document.querySelectorAll('button,[role=\"button\"]'))"
                    ".filter(el => el.offsetParent !== null)"
                    ".slice(0, 30)"
                    ".map(el => [el.textContent?.trim().slice(0,40), el.getAttribute('title'), el.getAttribute('aria-label')].filter(Boolean).join('|'))"
                )
                logger.warning("_assert_l2: visible-buttons url=%s buttons=%s", current_url[-60:], buttons)
        except Exception as _dump_exc:
            logger.warning("_assert_l2: button dump failed: %s", _dump_exc)
        try:
            import os as _os, time as _time
            screenshot_fn = getattr(page, "screenshot", None)
            if callable(screenshot_fn):
                _cap_dir = _os.environ.get("FLOW_ERROR_CAPTURE_DIR", "/tmp")
                _ts = int(_time.time())
                _path = f"{_cap_dir}/{_ts}_l2_absent_{op_name.replace('/', '_')}.png"
                await screenshot_fn(path=_path, full_page=False)
                logger.warning("_assert_l2: screenshot saved %s", _path)
        except Exception as _sc_exc:
            logger.warning("_assert_l2: screenshot failed: %s", _sc_exc)
        logger.info(
            "L2 toolbar absent for op=%s profile=%s — treating as paid_tier_required (2026-05 silent-hide variant)",
            op_name,
            profile or "",
        )
        raise L2PaywallError(
            operation=op_name,
            profile=profile,
            message="L2 editing controls absent (free-tier silent gating)",
        )


async def _l2_toolbar_visible_after_paint(page, *, timeout_ms: int) -> bool:
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)
    while True:
        for token in _L2_TOOLBAR_TOKENS:
            if await _l2_toolbar_token_visible(page, token, timeout_ms=100):
                return True

        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.25, remaining))


async def _l2_toolbar_token_visible(page, token: str, *, timeout_ms: int) -> bool:
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return True
    selectors = (
        f"button[title='{token}']",
        f"button[aria-label='{token}']",
        f"button:has-text('{token}')",
        f"[role='button']:has-text('{token}')",
        f"button:has(i:text-is('{token}'))",
        f"[role='button']:has(i:text-is('{token}'))",
    )
    for selector in selectors:
        try:
            if await _locator_is_visible(locator(selector), timeout_ms=timeout_ms):
                return True
        except Exception:
            continue
    return False


async def agent_edit_ui_present(page, *, timeout_ms: int = 2000) -> bool:
    """Return True if the 2026-05 'Describe your edit(s)' agent interface is visible."""
    for token in _AGENT_EDIT_UI_TOKENS:
        if await _text_is_visible(page, token, exact=False, timeout_ms=timeout_ms):
            return True
    # Also check for the contenteditable div without relying on placeholder text
    locator = getattr(page, "locator", None)
    if callable(locator):
        try:
            ce = locator("[contenteditable='true']").first
            if await _locator_is_visible(ce, timeout_ms=500):
                return True
        except Exception:
            pass
    return False


# Generate API endpoints that confirm a real agent-edit submission fired.
# After pressing Enter we wait for one of these to be requested; if none
# fires the Enter went nowhere (or only opened the asset picker) and we
# must fail fast instead of waiting out the 180s no_signal_timeout.
_AGENT_SUBMIT_REQUEST_TOKENS = (
    "batchasync",
    "generatecontent",
    ":generate",
    "/generate",
    "generatevideo",
    "runagent",
)

# Visible markers that an asset picker / media-search modal is open over
# the composer (R22: "No results found" + "Search assets" + asset tabs).
# Used to decide whether an Escape press is warranted — Escape would
# otherwise close the editor dialog entirely.
_AGENT_ASSET_PICKER_TEXT_MARKERS = (
    "No results found",
    "Search assets",
    "Upload media",
)


async def _agent_asset_picker_open(page, *, timeout_ms: int = 400) -> bool:
    """Return True when an asset-picker / media-search modal is visible.

    Detected via either a visible ``[role='dialog']`` or one of the
    distinctive asset-picker text markers (R22 forensic). Only when this
    is True should the caller press Escape to recover; a blind Escape
    can close the whole editor.
    """
    if await _role_is_visible(page, "dialog", name="", timeout_ms=timeout_ms):
        return True
    for marker in _AGENT_ASSET_PICKER_TEXT_MARKERS:
        if await _text_is_visible(page, marker, exact=False, timeout_ms=timeout_ms):
            return True
    return False


async def _wait_for_generate_request(page, *, timeout_ms: int) -> bool:
    """Wait up to *timeout_ms* for a generate/batchAsync request to fire.

    Returns True once a request whose URL matches one of
    ``_AGENT_SUBMIT_REQUEST_TOKENS`` is observed. Returns False on timeout
    or when the page lacks a usable ``wait_for_event`` (test doubles).
    """
    waiter = getattr(page, "wait_for_event", None)
    if not callable(waiter):
        return False

    def _is_generate(request) -> bool:
        try:
            url = str(getattr(request, "url", "") or "").lower()
        except Exception:
            return False
        return any(token in url for token in _AGENT_SUBMIT_REQUEST_TOKENS)

    try:
        result = waiter("request", predicate=_is_generate, timeout=timeout_ms)
        if inspect.isawaitable(result):
            await result
        return True
    except Exception:
        return False


async def _press_editor_enter(page, editor) -> None:
    """Focus the agent composer editor and press Enter to submit."""
    await editor.click(timeout=3000)
    await asyncio.sleep(0.2)
    keyboard = getattr(page, "keyboard", None)
    if keyboard is None:
        raise RuntimeError("page.keyboard unavailable")
    await keyboard.press("Enter")


async def _capture_submit_failure(page) -> None:
    """Screenshot the page on submit failure (timestamp-based, env-gated)."""
    _cap_dir = os.environ.get("FLOW_ERROR_CAPTURE_DIR", "")
    if not _cap_dir or os.environ.get("FLOW_ERROR_CAPTURE", "1") == "0":
        return
    try:
        os.makedirs(_cap_dir, exist_ok=True)
        _fname = os.path.join(_cap_dir, f"{int(time.time())}_submit_fail.png")
        await page.screenshot(path=_fname)
        logger.error("submit_via_agent_edit_ui: submit failed, screenshot: %s", _fname)
    except Exception as _e:
        logger.debug("submit_via_agent_edit_ui: screenshot failed: %s", _e)


async def submit_via_agent_edit_ui(page, command: str) -> bool:
    """Type *command* into the 'Describe your edit(s)' input and submit it.

    The 2026-05 Flow agent composer is a standard chat-style input that
    submits on the Enter key — NOT via a toolbar button. R22 forensic
    capture showed the previous button-based submit was clicking the
    ``add_2`` add-media button, which opens an asset picker and fires zero
    generate API calls (the job then died on a 180s no_signal_timeout).

    Submit flow:
      1. Type the command into ``[contenteditable='true']``.
      2. Focus the editor and press Enter (primary submit path).
      3. If an asset picker opened (Enter hit the wrong target), press
         Escape once, refocus, and retry Enter.
      4. Verify a generate/batchAsync request actually fired within ~4s.
         If none did, log a warning and return False so the caller fails
         fast with an accurate signal.

    Returns True only when a generate request was observed after Enter.
    The caller is responsible for waiting for the generation result.
    """
    locator = getattr(page, "locator", None)
    if not callable(locator):
        return False

    # Find the single contenteditable editor.
    editor = locator("[contenteditable='true']").first
    if not await _locator_is_visible(editor, timeout_ms=3000):
        logger.warning("submit_via_agent_edit_ui: contenteditable not visible")
        return False

    # Type the command (keep existing select-all + keyboard typing logic).
    try:
        await editor.click(timeout=3000)
        await asyncio.sleep(0.3)
        keyboard = getattr(page, "keyboard", None)
        if keyboard:
            await keyboard.press("Control+a")
            await asyncio.sleep(0.1)
        await editor.type(command, delay=30)
        logger.info("submit_via_agent_edit_ui: typed command=%r", command[:60])
        await asyncio.sleep(0.3)
    except Exception as exc:
        logger.warning("submit_via_agent_edit_ui: typing failed: %s", exc)
        return False

    # Primary submit: Enter key. Retry once if an asset picker pops open.
    submitted_via_enter = False
    for attempt in range(2):
        try:
            await _press_editor_enter(page, editor)
        except Exception as exc:
            logger.warning(
                "submit_via_agent_edit_ui: Enter press failed (attempt %d): %s",
                attempt + 1,
                exc,
            )
            break

        await asyncio.sleep(0.3)

        # Only press Escape when an asset picker is actually open — a blind
        # Escape would otherwise close the editor dialog entirely.
        if await _agent_asset_picker_open(page):
            logger.warning(
                "submit_via_agent_edit_ui: asset picker opened after Enter "
                "(attempt %d) — pressing Escape and retrying",
                attempt + 1,
            )
            kb = getattr(page, "keyboard", None)
            if kb is not None:
                try:
                    await kb.press("Escape")
                    await asyncio.sleep(0.3)
                except Exception as exc:
                    logger.debug(
                        "submit_via_agent_edit_ui: Escape press failed: %s", exc
                    )
            continue

        submitted_via_enter = True
        break

    if not submitted_via_enter:
        logger.warning(
            "submit_via_agent_edit_ui: could not submit via Enter (asset "
            "picker kept reopening or Enter failed)"
        )
        await _capture_submit_failure(page)
        return False

    # Verify generation actually started — a generate/batchAsync request
    # must fire. Without this, an Enter that silently did nothing would
    # leave the caller to wait out the full 180s no_signal_timeout.
    if await _wait_for_generate_request(page, timeout_ms=4000):
        logger.info("submit_via_agent_edit_ui: generate request observed after Enter")
        return True

    logger.warning(
        "submit_via_agent_edit_ui: no generate request observed after Enter"
    )
    await _capture_submit_failure(page)
    return False


def _op_name_from_button_texts(button_texts: list[str]) -> str:
    labels = {str(text).strip().lower() for text in button_texts}
    if labels & {"extend", "mở rộng", "mo rong", "má»Ÿ rá»™ng"}:
        return "extend-video"
    if labels & {"insert", "chen", "chèn", "chÃ¨n"}:
        return "insert-object"
    if labels & {"remove", "delete", "xoá", "xóa", "xoÃ¡", "xÃ³a"}:
        return "remove-object"
    if "camera" in labels:
        return "camera-move"
    return "level-2 operation"


async def navigate_to_edit(
    client,
    job: dict,
    *,
    skip_toolbar_check: bool = False,
) -> tuple[str, str, str]:
    """Navigate to the video edit page.

    Uses edit_url if available, otherwise constructs from project_url + media_id.

    skip_toolbar_check: set True for reverse-API callers that don't need the
    DOM toolbar (Extend/Insert/Remove/Camera buttons) to be visible.

    Returns (edit_url, project_id, locale).
    """
    page = client.page

    edit_url_val = job.get("edit_url") or ""
    project_url_val = job.get("project_url") or ""
    media_id = job.get("media_id") or ""

    # Build edit URL if not directly provided
    if not edit_url_val and project_url_val and media_id:
        locale = detect_locale(project_url_val)
        project_id = extract_project_id(project_url_val)
        if project_id:
            from flow.navigation import edit_url as build_edit_url
            edit_url_val = build_edit_url(project_id, media_id, locale)

    if not edit_url_val:
        message = (
            f"Cannot navigate: no edit_url, project_url={project_url_val}, media_id={media_id}"
        )
        message = await message_with_failure_capture(client, "no_edit_url", message)
        raise RuntimeError(message)

    # Strategy: direct goto(edit_url) is the fast path. On EN-locale Google
    # accounts the Flow SPA mounts the editor on /edit/{media_id} without
    # needing the project grid preloaded — verified 2026-04-19 by
    # `scripts/probe_direct_edit_url.py` on `ngoctuandt20` post-language-
    # switch (submit chip, model chip, textarea all present on direct goto).
    #
    # Fallback: if the SPA bounces to /project/{id} (rare — e.g. temporary
    # locale flap or media moved), the `/edit/ not in page.url` block below
    # falls through to tile-click on the project grid.
    target_url = edit_url_val
    logger.info("Navigating to edit URL: %s", target_url[:100])
    # Install auth probe BEFORE navigation so it captures agent session Bearer
    # tokens during page load (add_init_script runs before page scripts).
    await install_agent_auth_probe(page)
    # Remove any existing session blockers (may have been installed by L1
    # generate/upscale) so the agent "Describe your edits" UI can submit.
    # The old L2 toolbar no longer exists; sessions must be unblocked for
    # submit_via_agent_edit_ui to fire a generate request.
    await uninstall_agent_session_blocker(page)
    await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(3)

    # Handle login redirect if needed
    current = page.url
    if is_login_page(current):
        logger.warning("Login redirect on edit navigation — resolving")
        profile_name = getattr(client, "profile_name", "") or ""
        login_ok = await handle_login_redirect(
            page, timeout=60, profile_name=profile_name, client=client,
        )
        if not login_ok:
            message = "Google login required — session expired"
            message = await message_with_failure_capture(
                client,
                "google_login_required",
                message,
            )
            raise RuntimeError(message)
        await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

    # Detect homepage redirect — means project doesn't belong to this account
    await _recover_editor_landing(page, target_url)
    current = page.url
    op_name = job.get("type") or "level-2 operation"
    profile_name = job.get("profile") or getattr(client, "profile_name", "") or ""

    # Flow auto-starts an Agent session on every project/edit navigate (2026-05
    # rollout). The Agent UI replaces the standard L2 toolbar (Extend/Insert/
    # Remove/Camera) and renders agent sessions as [data-tile-id] nodes that
    # don't match video media_ids. Disable agent mode before any toolbar or
    # tile check so the normal editor view is guaranteed.
    try:
        await disable_agent_mode_if_active(
            page,
            profile_name=profile_name,
            target_url=target_url,
            log=logger,
        )
        # Re-capture URL after potential agent-disable refresh.
        current = page.url
    except Exception as _agent_exc:
        logger.warning(
            "navigate_to_edit: agent disable non-fatal for %s: %s",
            profile_name,
            _agent_exc,
        )

    if not skip_toolbar_check:
        await _assert_l2_available(page, op_name, profile_name)

    if "/project/" not in current and "/edit/" not in current:
        # Landed on Flow homepage instead of project page
        logger.error(
            "Project not accessible — redirected to homepage. "
            "URL: %s  profile: %s  target: %s",
            current[:100], getattr(client, "profile_name", "?"), target_url[:100],
        )
        message = (
            f"Project not accessible for profile {getattr(client, 'profile_name', '?')} "
            f"— wrong account or project deleted"
        )
        message = await message_with_failure_capture(
            client,
            "project_not_accessible",
            message,
        )
        raise RuntimeError(message)

    # If we're on the project page (not edit), click a video tile
    if "/edit/" not in page.url:
        logger.info("On project view — clicking video tile to enter edit mode")
        entered = await _click_video_tile(page, job.get("media_id", ""))
        if not entered:
            # Last resort: try direct edit URL
            logger.info("Tile click failed — trying direct edit URL: %s", edit_url_val[:80])
            await page.goto(edit_url_val, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(5)
            await _recover_editor_landing(page, target_url)
            if not skip_toolbar_check:
                await _assert_l2_available(page, op_name, profile_name)

    # Verify we're in edit mode for the right media
    current = page.url
    if not skip_toolbar_check:
        await _assert_l2_available(page, op_name, profile_name)
    if "/edit/" not in current:
        # B29 (2026-04-19): when an L1 /edit/{media_id} points at a media
        # that's been consumed by a sibling extend, the SPA strips `/edit/`
        # and leaves us on /project/ — the tile-click fallback then can't
        # recover. Surface this as a B22-inheritance hint so operators
        # look at the claim-time ancestor rather than hunt DOM changes.
        logger.error("SPA stripped /edit/ segment. URL: %s", current[:100])
        message = (
            f"SPA stripped /edit/ segment → {current}. "
            f"Media may be stale post-sibling-extend. Check B22 inheritance."
        )
        message = await message_with_failure_capture(
            client,
            "spa_stripped_edit_route",
            message,
        )
        raise RuntimeError(message)

    # B32 (2026-04-19): job.media_id is the SEMANTIC target (what the
    # operation wants to edit), which after B30 walk-up may differ from
    # the direct parent's edit_url media (e.g. L3 insert after L2 extend
    # inherits L1 grandparent's media_id but parent's edit_url). If the
    # URL's media differs from the target, activate the target clip via
    # history-panel tile click — Flow's SPA then enables Insert/Remove/
    # Camera on the target clip even though the URL still shows the
    # extend-output. Verified live 2026-04-19 on project 513d580b (B32
    # probe session): clicking [data-tile-id="fe_id_{target_media_id}"]
    # in the right sidebar flips all 4 mode buttons from disabled →
    # enabled without changing page.url.
    current_media = extract_media_id(current)
    # Flow's SPA sometimes accepts `page.goto(/edit/{X})` by updating the URL
    # without remounting the editor (observed 2026-04-23 on L2 remove directly
    # after L2 insert: URL = target, but <video> never rendered → 15s timeout).
    # Activating the target tile forces the SPA to re-hydrate the editor state,
    # so do it unconditionally when media_id is known — not only when the URL
    # disagrees. Idempotent when the tile is already active.
    if media_id:
        if current_media and current_media != media_id:
            logger.info(
                "URL media differs from target: url=%s target=%s — activating target tile",
                current_media[:20], media_id[:20],
            )
        activated = await _activate_clip_tile(page, media_id)
        if not activated and current_media != media_id:
            # Primary recovery failed. Attempt secondary: Playwright real click
            # on [data-tile-id="fe_id_{media_id}"] inside the /edit/ sidebar.
            # This differs from the /project/ branch _click_video_tile call
            # above — here the page IS on /edit/ but the wrong clip is active.
            # We need _click_video_tile to navigate-within-editor, not to enter
            # edit mode. The function searches for matching data-tile-id/link so
            # it works in both /project/ and /edit/ contexts.
            logger.info(
                "JS-dispatch tile activation failed for media=%s — trying real click fallback",
                media_id[:20],
            )
            clicked = await _click_video_tile(page, media_id)
            if clicked:
                # Verify the switch landed on the right media after click
                await asyncio.sleep(1)
                post_click_media = extract_media_id(page.url)
                if post_click_media and post_click_media == media_id:
                    logger.info(
                        "Real-click fallback succeeded for media=%s", media_id[:20]
                    )
                else:
                    # URL may not change on sidebar-only tile activation (same
                    # as JS dispatch path). The click still fired — trust it and
                    # let click_action_button's _wait_button_enabled decide.
                    logger.info(
                        "Real-click fallback dispatched for media=%s "
                        "(URL still shows %s — button poll will confirm)",
                        media_id[:20],
                        (post_click_media or "unknown")[:20],
                    )
            else:
                # Both activation paths failed. Before hard-failing, check
                # whether the editor mounted anyway — goto(/edit/{media_id})
                # often redirects to /edit/{routing_slug} (different string,
                # same video). If the editor IS mounted the routing resolved
                # correctly and we can proceed; click_action_button's button-
                # enabled poll will catch any remaining state issue.
                if await _editor_mounted(page, timeout_ms=5000):
                    logger.info(
                        "Tile activation failed for media=%s but editor mounted"
                        " on %s — routing-slug redirect; proceeding",
                        media_id[:20], page.url[:80],
                    )
                else:
                    # Editor is not mounted — we are genuinely stuck on the
                    # wrong clip. Hard-fail with a specific error (B28 pattern:
                    # Camera/Insert/Remove greyed out on a leaf tile).
                    current_media_now = extract_media_id(page.url) or current_media or ""
                    op_type = job.get("type", "unknown")
                    message = (
                        f"b28_leaf_lockout_{media_id}: {op_type} cannot activate "
                        f"target clip tile; SPA stuck on leaf {current_media_now[:20]}"
                    )
                    message = await message_with_failure_capture(
                        client,
                        "b28_leaf_lockout",
                        message,
                    )
                    raise LeafLockoutError(
                        target_media_id=media_id,
                        current_url=page.url,
                        current_media_id=current_media_now,
                        op_type=op_type,
                    )

    # B39 (2026-04-23): the URL-strip branch above catches the SPA bouncing
    # to /project/ on stale media, but Flow's other failure mode keeps
    # /edit/{stale_media_id} in the URL with an un-mounted editor — no
    # <video>, no mode buttons. Observed on insert-object whose parent
    # media had been consumed by a sibling extend. Catch it here with a
    # bounded video wait and recover by clicking the semantic target tile.
    # Deep extend chains can reorder the rail, so first-tile recovery may
    # select an L1 root instead of the L<N-1> parent clip.
    if not await _editor_mounted(page, timeout_ms=15000):
        if media_id:
            logger.warning(
                "Editor did not mount after nav to %s — clicking tile for media=%s; first-tile click only when media_id unknown",
                edit_url_val[:80],
                media_id[:20],
            )
            try:
                tile = await _find_tile_by_media_id(page, media_id)
            except _TileLookupInconclusive:
                logger.debug(
                    "Media-id tile lookup inconclusive for media=%s; "
                    "trying existing media-id click path",
                    media_id[:20],
                )
                recovered = await _click_video_tile(page, media_id)
                recovery_name = f"media-id tile recovery for {media_id}"
            else:
                if tile is None:
                    # Strict media-id match failed. Allow first-tile fallback
                    # ONLY when the project rail is shallow (≤1 tile = L2 case
                    # where the only tile IS the L1 root we want). At ≥2 tiles,
                    # use the last tile only when rail size exactly matches the
                    # known chain depth; otherwise refuse to avoid wrong-clip.
                    try:
                        tile_count = await page.locator("[data-tile-id]").count()
                    except Exception:
                        tile_count = 0
                    ancestor_count = len(job.get("ancestor_media_ids") or [])
                    expected_count = ancestor_count + 1
                    if tile_count <= 1:
                        logger.warning(
                            "Media-id %s not found, but rail has %d tile(s); "
                            "falling back to first-tile click (safe at shallow rail)",
                            media_id[:20], tile_count,
                        )
                        recovered = await _click_video_tile(page, "")
                        recovery_name = f"first-tile fallback for {media_id}"
                    elif tile_count == expected_count:
                        logger.warning(
                            "Media-id %s not matched but rail size (%d) matches "
                            "chain depth; clicking last tile as best-guess target",
                            media_id[:20], tile_count,
                        )
                        last_tile = page.locator("[data-tile-id]").last
                        await last_tile.click(timeout=3000)
                        await asyncio.sleep(3)
                        recovered = True
                        recovery_name = (
                            f"last-tile fallback (rail size match) for {media_id}"
                        )
                    else:
                        message = (
                            f"Editor did not mount for {edit_url_val}; target tile for media_id "
                            f"{media_id} not found in project rail ({tile_count} tiles present). "
                            f"Refusing first-tile recovery because deep-chain rails may be reordered. "
                            f"Parent media may be stale (consumed by sibling op)."
                        )
                        message = await message_with_failure_capture(
                            client,
                            "editor_not_mounted",
                            message,
                        )
                        raise RuntimeError(message)
                else:
                    try:
                        await tile.click(timeout=3000)
                        logger.info(
                            "Clicked media-id matched tile for editor recovery: %s",
                            media_id[:20],
                        )
                        await asyncio.sleep(3)
                        recovered = True
                    except Exception as exc:
                        logger.warning(
                            "Media-id tile recovery click failed for media=%s: %s",
                            media_id[:20],
                            exc,
                        )
                        recovered = False
                    recovery_name = f"media-id tile recovery for {media_id}"
        else:
            logger.warning(
                "Editor did not mount after nav to %s — falling back to first-tile click; media_id unknown",
                edit_url_val[:80],
            )
            recovered = await _click_video_tile(page, "")
            recovery_name = "first-tile recovery"
        if not recovered or not await _editor_mounted(page, timeout_ms=15000):
            message = (
                f"Editor did not mount for {edit_url_val} and {recovery_name} "
                f"failed. Parent media may be stale (consumed by sibling op)."
            )
            message = await message_with_failure_capture(
                client,
                "editor_not_mounted",
                message,
            )
            raise RuntimeError(message)
        if media_id:
            await _activate_clip_tile(page, media_id)

    if not skip_toolbar_check:
        await _assert_l2_available(page, op_name, profile_name)

    locale = detect_locale(page.url)
    project_id = extract_project_id(page.url) or ""

    return edit_url_val, project_id, locale


async def _editor_mounted(page, timeout_ms: int = 15000) -> bool:
    """Return True when the /edit/ composer has rendered its <video>.

    Used as a post-navigation sanity check — distinguishes a truly
    mounted editor from the SPA's half-loaded state where the URL
    reads /edit/{media} but no editor DOM is present.

    Two independent mount signals (race; first to hit wins):

      1. ``<video>`` element visible — Flow's editor mounts a player
         tag for the current clip.
      2. The "Hide history" / "Ẩn lịch sử" toggle button — only
         present when the editor's history sidebar is up, which is a
         strong signal that the SPA is past its half-loaded state.

    Either is sufficient. Why both: on heavily-loaded projects (many
    L2 children, ~10–15s mount time observed on chain v4 2026-05-04),
    the ``<video>`` tag can lag behind the toggle's render — and the
    old 8 s timeout produced false negatives that retried 2–3× before
    eventually mounting. MCP probe 2026-05-05 measured the main
    player at ~5–9 s on a fresh navigate; the 15 s ceiling
    accommodates the slow path without changing the error semantics.
    """
    try:
        await page.locator(
            "video, button:has-text('Hide history'), button:has-text('Ẩn lịch sử')"
        ).first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False


async def _find_tile_by_media_id(page, media_id: str, timeout_ms: int = 5000):
    """Return a project/history tile locator matching ``media_id`` if present."""
    if not media_id:
        return None

    selectors = [
        f"[data-tile-id='fe_id_{media_id}']",
        f"[data-tile-id*='{media_id}']",
        f"a[href*='/edit/{media_id}']",
        f"[data-media-id='{media_id}']",
        f"[id$='-{media_id}']",
        f"[data-clip-id*='{media_id}']",
        f"xpath=//*[@*[contains(., '{media_id}')]]",
    ]
    interval_sec = 0.5
    attempts = max(1, int(timeout_ms / (interval_sec * 1000)) + 1)
    deadline = asyncio.get_running_loop().time() + (timeout_ms / 1000)

    for attempt in range(attempts):
        for selector in selectors:
            try:
                loc = page.locator(selector).first
                count_result = loc.count()
                if not inspect.isawaitable(count_result):
                    raise _TileLookupInconclusive(
                        f"locator count for {selector!r} was not awaitable"
                    )
                if await count_result > 0:
                    visible_result = loc.is_visible()
                    if not inspect.isawaitable(visible_result):
                        raise _TileLookupInconclusive(
                            f"locator visibility for {selector!r} was not awaitable"
                        )
                    if await visible_result:
                        return loc
            except _TileLookupInconclusive:
                raise
            except Exception as exc:
                logger.debug("Tile lookup selector failed %r: %s", selector, exc)
                continue

        if attempt == attempts - 1:
            break
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(interval_sec, remaining))
    return None


async def _recover_editor_landing(page, target_url: str) -> bool:
    """Recover if Flow rendered the marketing landing over the intended route."""
    return await recover_from_flow_landing(page, logger, target_url)


async def _activate_clip_tile(page, media_id: str, timeout_sec: float = 8.0) -> bool:
    """Click the history-panel clip tile for `media_id` to activate it.

    Used after `navigate_to_edit` when the URL's media differs from the
    semantic target (B30 walk-up case: L3 insert/remove/camera after an
    extend-video parent inherits L1's grandparent media_id but lands on
    the extend-output's edit URL).

    Live evidence 2026-04-19 (project 513d580b probe): the history panel
    renders each project clip as a `<div data-tile-id="fe_id_{media_id}">`
    at the right side. The tile has no button ancestor — a DOM-level
    click handler on the DIV switches the active clip. Dispatching a
    real MouseEvent (not `.click()` which may not trigger styled-
    components) re-enables Insert/Remove/Camera for the targeted clip.

    Change A (B28 2026-05-05): attachment timeout raised 3s → 8s to match
    `_wait_button_enabled`'s poll budget. On heavily-loaded projects (6+
    tiles in sidebar from prior runs) the history panel's DOM can take
    4-7s to fully render, causing the old 3s wait to abort before the tile
    attaches.

    Change B (B28 2026-05-05): after the JS MouseEvent sequence, poll up
    to 5s for ``extract_media_id(page.url)`` to equal `media_id`. If the
    URL still shows the wrong clip after 5s → return False so the caller
    can fall through to the real Playwright click fallback or raise
    ``LeafLockoutError``. This turns Change B from a diagnostic-only check
    into a genuine gate.

    Re-entry shortcut: if the URL already shows ``media_id`` before the JS
    dispatch (caller navigated directly to the right clip), return True
    immediately without polling.

    Args:
      page: Playwright Page inside the /edit/ composer.
      media_id: Target media UUID — typically from `job["media_id"]` after
        B30 walk-up (the nearest non-extend ancestor).
      timeout_sec: Seconds to wait for the tile DOM node to attach
        (default 8s, matches `_wait_button_enabled`'s poll window).

    Returns:
      True if the tile was found, the JS dispatch fired, AND the URL
      confirmed the switch to ``media_id`` within 5s (or the URL already
      showed ``media_id`` at entry).
      False if: (a) the tile was not found within ``timeout_sec``, (b) JS
      returned falsy (tile detached between locator resolve and evaluate),
      or (c) 5s elapsed without the URL reflecting ``media_id``.
    """
    if not media_id:
        return False
    # Change A: 8s attachment wait — matches _wait_button_enabled poll budget.
    # On projects with 6+ sidebar tiles the history panel DOM can lag 4-7s.
    try:
        tile = page.locator(f"[data-tile-id='fe_id_{media_id}']").first
        await tile.wait_for(state="attached", timeout=int(timeout_sec * 1000))
    except Exception:
        logger.debug("Clip tile not found for media=%s within %.1fs", media_id[:20], timeout_sec)
        return False
    try:
        # The tile is a <div> with no button ancestor; click() may miss
        # the custom handler. Dispatch a full pointer sequence via JS.
        ok = await page.evaluate(
            """(mid) => {
                const tile = document.querySelector(
                    `[data-tile-id="fe_id_${mid}"]`
                );
                if (!tile) return false;
                const rect = tile.getBoundingClientRect();
                const cx = rect.x + rect.width / 2;
                const cy = rect.y + rect.height / 2;
                for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click']) {
                    tile.dispatchEvent(new MouseEvent(type, {
                        bubbles: true, cancelable: true, view: window,
                        clientX: cx, clientY: cy, button: 0,
                    }));
                }
                return true;
            }""",
            media_id,
        )
        if not ok:
            # JS could not find the tile in the DOM (it may have detached
            # between wait_for(attached) and evaluate — race on SPA re-render).
            logger.warning(
                "Tile JS-dispatch returned false for media=%s (tile detached during evaluate?)",
                media_id[:20],
            )
            return False

        # Change B (r2 fix): after JS dispatch, URL confirmation is a hard gate.
        #
        # Re-entry shortcut: if the current URL already matches the target
        # media (caller navigated directly to the right edit URL), we are
        # already on the right clip — skip polling and return True immediately.
        if extract_media_id(page.url) == media_id:
            logger.info(
                "Activated clip tile (URL already matches) for media=%s", media_id[:20]
            )
            return True

        # Poll up to 5s for URL to confirm the switch. If Flow updates the
        # URL on tile activation we return True immediately. If 5s elapse
        # without the URL reflecting the target → return False so navigate_to_edit
        # can fall through to the real Playwright click fallback or raise
        # LeafLockoutError. The old "sidebar-only switch assumed" optimistic
        # path is removed — if JS dispatch fires on the wrong tile (stale
        # data-tile-id on SPA re-render) that optimistic path was the root
        # cause of the silent-fail pattern this fix addresses.
        for _ in range(20):  # 20 × 0.25s = 5s
            await asyncio.sleep(0.25)
            if extract_media_id(page.url) == media_id:
                logger.info("Activated clip tile (URL confirmed) for media=%s", media_id[:20])
                return True

        logger.debug(
            "Tile click fired but URL did not confirm switch to %s after 5s",
            media_id[:20],
        )
        return False
    except Exception as e:
        logger.warning("Tile click dispatch failed for media=%s: %s", media_id[:20], e)
    return False


async def wait_for_video_loaded(page, timeout_sec: float = 15.0):
    """Wait until a video element is visible on the edit page."""
    await _recover_editor_landing(page, page.url)
    try:
        video = page.locator("video").first
        await video.wait_for(state="visible", timeout=timeout_sec * 1000)
        logger.info("Video element loaded")
    except Exception:
        logger.warning("Video element not found after %.0fs — proceeding anyway", timeout_sec)


_MODE_ICON_BY_TITLE = {
    "Mở rộng": "keyboard_double_arrow_right",
    "Extend": "keyboard_double_arrow_right",
    "Chèn": "add_box",
    "Insert": "add_box",
    "Xoá": "ink_eraser",
    "Xóa": "ink_eraser",
    "Remove": "ink_eraser",
    "Delete": "ink_eraser",
    "Camera": "videocam",
}


async def _wait_button_enabled(btn, timeout_ms: int = 8000) -> bool:
    """Poll ``btn.is_enabled`` until True, or timeout.

    A freshly-mounted button on a backgrounded multi-tab page can flash
    ``disabled`` for a few hundred ms while React commits Flow's state
    update. The legacy single-`is_enabled` probe converted that window
    into a permanent ``extend-child lockout`` diagnostic. Polling absorbs
    the transient. Real lockouts (B28) keep the button disabled past
    the timeout and still propagate the diagnostic upward.
    """
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while True:
        try:
            if await btn.is_enabled():
                return True
        except Exception:
            pass
        if asyncio.get_event_loop().time() >= deadline:
            return False
        await asyncio.sleep(0.25)


async def click_action_button(
    page,
    button_texts: list[str],
    timeout_ms: int = 5000,
    *,
    client=None,
) -> bool:
    """Click a mode-switch button (Extend/Insert/Remove/Camera) on /edit/.

    Live-verified 2026-04-19 on VI profile: each mode button has a stable
    EXACT ``title`` attribute and an EXACT Material Icon ligature inside
    its ``<i>`` child. Title is the authoritative primary selector; icon
    is a locale-independent fallback.

    Identity (exact):
      * ``button[title="Mở rộng"]``  → icon ``keyboard_double_arrow_right``
      * ``button[title="Chèn"]``     → icon ``add_box``
      * ``button[title="Xoá"]``      → icon ``ink_eraser``
      * ``button[title="Camera"]``   → icon ``videocam``

    Do NOT use fuzzy ``:has-text`` — the Camera button's textContent is
    "videocam\\nCamera" and matched ``:has-text('videocam')`` in B26,
    causing a silent URL revert from /edit/ to /project/.

    Multi-tab note: in a backgrounded tab, Chrome throttles React
    rendering — a freshly-mounted button can briefly read disabled
    before Flow flushes the state update. ``_wait_button_enabled``
    polls for up to ~8s before raising the lockout diagnostic, so a
    transient disabled flash doesn't poison genuine multi-tab work.
    Real extend-child lockouts persist past the poll window and still
    surface as ``RuntimeError``.
    """
    # Pass 1 — exact title match (VI labels are unique, stable)
    await _assert_l2_available(
        page,
        _op_name_from_button_texts(button_texts),
        getattr(client, "profile_name", "") if client is not None else "",
    )

    for text in button_texts:
        try:
            btn = page.locator(f"button[title='{text}']").first
            if await btn.is_visible(timeout=1500):
                # B28 (2026-04-19): on extend-output /edit/{new_media} the
                # Insert/Remove/Camera buttons render but Flow sets them
                # disabled ("extend-child lockout"). Pre-B28 the click would
                # time out with a misleading "Failed to find button" error.
                # Raise early with the B22-inheritance diagnostic instead.
                if not await _wait_button_enabled(btn):
                    message = (
                        f"Mode button {text!r} disabled — extend-child lockout "
                        f"(FLOW_BUTTON_EXACT §5.1). Check B22 inheritance."
                    )
                    message = await message_with_failure_capture(
                        client,
                        "extend_child_lockout",
                        message,
                    )
                    raise RuntimeError(message)
                await btn.click(timeout=timeout_ms)
                logger.info("Clicked mode button via title=%r", text)
                await asyncio.sleep(0.5)
                return True
        except RuntimeError:
            raise
        except Exception:
            continue

    # Pass 2 — icon fallback (locale-independent).  Find the unique icon
    # for the requested mode, then click the ancestor <button>.
    for text in button_texts:
        icon = _MODE_ICON_BY_TITLE.get(text)
        if not icon:
            continue
        try:
            btn = page.locator(f"button:has(i:text-is('{icon}'))").first
            if await btn.is_visible(timeout=1500):
                if not await _wait_button_enabled(btn):
                    message = (
                        f"Mode button {text!r} disabled — extend-child lockout "
                        f"(FLOW_BUTTON_EXACT §5.1). Check B22 inheritance."
                    )
                    message = await message_with_failure_capture(
                        client,
                        "extend_child_lockout",
                        message,
                    )
                    raise RuntimeError(message)
                await btn.click(timeout=timeout_ms)
                logger.info("Clicked mode button via icon=%r (requested title=%r)", icon, text)
                await asyncio.sleep(0.5)
                return True
        except RuntimeError:
            raise
        except Exception:
            continue

    return False


async def _click_video_tile(page, media_id: str = "", timeout_sec: float = 10.0) -> bool:
    """Click a video tile in the project view to enter edit mode.

    When direct /edit/ URL navigation fails, the project view shows media
    tiles.  Clicking a tile navigates to /edit/{media_id}.

    Priority:
    1. If media_id given: JS click on tile whose link/data contains media_id
    2. First [data-tile-id] tile
    3. First video element
    """
    await asyncio.sleep(2)  # let project view render

    # Priority 1: click tile matching media_id via JS
    if media_id:
        try:
            clicked = await page.evaluate("""(targetId) => {
                // Look for links containing the media_id
                const links = document.querySelectorAll('a[href*="/edit/"]');
                for (const a of links) {
                    if (a.href.includes(targetId)) {
                        a.click();
                        return 'link:' + targetId.substring(0, 12);
                    }
                }
                // Look for tiles with data attributes matching media_id
                const tiles = document.querySelectorAll('[data-tile-id]');
                for (const tile of tiles) {
                    const tileId = tile.getAttribute('data-tile-id') || '';
                    if (tileId.includes(targetId) || targetId.includes(tileId)) {
                        tile.click();
                        return 'tile:' + tileId.substring(0, 12);
                    }
                }
                // Look for any element with media_id in attributes
                const all = document.querySelectorAll('[data-media-id], [data-id]');
                for (const el of all) {
                    const id = el.getAttribute('data-media-id') || el.getAttribute('data-id') || '';
                    if (id.includes(targetId)) {
                        el.click();
                        return 'data-id:' + id.substring(0, 12);
                    }
                }
                return null;
            }""", media_id)
            if clicked:
                logger.info("Clicked tile for media_id via JS: %s", clicked)
                await asyncio.sleep(3)
                if "/edit/" in page.url:
                    logger.info("Edit mode entered: %s", page.url[:100])
                    return True
        except Exception:
            pass

    # Priority 2: click first [data-tile-id] tile
    try:
        tile = page.locator("[data-tile-id]").first
        if await tile.is_visible(timeout=3000):
            await tile.click(timeout=3000)
            logger.info("Clicked first [data-tile-id] tile")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    # Priority 3: click first video element
    try:
        video = page.locator("video").first
        if await video.is_visible(timeout=3000):
            await video.click(timeout=3000)
            logger.info("Clicked video element")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    return False


async def draw_bbox_on_video(page, bbox: dict) -> bool:
    """Draw a bounding box on the Flow preview canvas via mouse drag.

    Shared between insert-object and remove-object ops. The caller must
    have already clicked Insert/Remove so the preview is in bbox-drawing
    mode.

    Target: the LARGEST visible `<canvas>` with `width ≥ 300`. On an L1
    project Flow's preview is a `<canvas width=598 height=336>` (CSS-sized
    ~479×269) centered on screen. The `<video>` tag exists on the page
    but is a 105×60 card-strip thumbnail — never target it (B2 regression).

    Verify: pointer-trust — no post-drag DOM check and no pixel sampling.
    Flow paints the bbox onto the canvas 2D bitmap (confirmed Tier1:
    `elementFromPoint` inside the visible bbox returns `<CANVAS>`), so the
    B2 union selector `svg rect, [class*="bbox" i], …` matches 0 elements
    regardless of drag success. Pixel sampling is also unreliable because
    the preview plays video frames continuously — `getImageData` deltas
    are noisy even without a drag. Pointer delivery onto the correct
    canvas rect is the load-bearing signal; if that happens, Flow accepts
    the region. See `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md`
    §7 for the Option A vs B decision rationale.

    Args:
        bbox: `{x, y, w, h}` normalized 0-1 relative to the canvas rect.
              Values outside [0, 1] → reject (return False). Overflow
              (`x+w>1` or `y+h>1`) is clamped to fit.

    Returns:
        True after the drag sequence completes on the target canvas.
        False on genuine pre-drag failures: no visible canvas ≥ 300×200,
        or any bbox key out of range. Caller logs a WARNING and continues
        (Flow falls back to its default region on unreliable bbox input).

    See `docs/FLOW_UI_REFERENCE.md` §Bbox Overlay UI for the live-DOM
    ground truth that drove this design.
    """
    # Step 1: Find the largest visible <canvas> (width ≥ 300). Flow's
    # preview canvas is the only one that size; card-strip canvases are
    # smaller thumbnails.
    canvas_rect = await page.evaluate("""() => {
        const canvases = Array.from(document.querySelectorAll('canvas'));
        let best = null;
        for (const c of canvases) {
            const r = c.getBoundingClientRect();
            if (r.width < 300 || r.height < 200) continue;
            const area = r.width * r.height;
            if (!best || area > best.area) {
                best = {left: r.left, top: r.top, width: r.width, height: r.height, area: area};
            }
        }
        return best;
    }""")

    if not canvas_rect:
        logger.error("Preview canvas not found (no visible <canvas> ≥ 300×200)")
        return False

    # Step 2: Validate bbox keys in [0, 1]
    for k in ("x", "y", "w", "h"):
        v = bbox.get(k, 0)
        if not (0 <= v <= 1):
            logger.error("bbox[%s]=%s out of range 0-1", k, v)
            return False

    x = bbox.get("x", 0.25)
    y = bbox.get("y", 0.25)
    w = bbox.get("w", 0.5)
    h = bbox.get("h", 0.5)

    # Step 3: Clamp overflow so bbox fits within canvas rect
    if x + w > 1:
        w = 1 - x
    if y + h > 1:
        h = 1 - y

    cl = canvas_rect["left"]
    ct = canvas_rect["top"]
    cw = canvas_rect["width"]
    ch = canvas_rect["height"]

    start_x = cl + x * cw
    start_y = ct + y * ch
    end_x = cl + (x + w) * cw
    end_y = ct + (y + h) * ch

    # Step 4: Mouse drag on the canvas — 5 interpolation steps (Flow needs a
    # real, gradual drag; a single move→down→up does not register).
    await page.mouse.move(start_x, start_y)
    await page.mouse.down()
    await asyncio.sleep(0.1)
    steps = 5
    for i in range(1, steps + 1):
        px = start_x + (end_x - start_x) * i / steps
        py = start_y + (end_y - start_y) * i / steps
        await page.mouse.move(px, py)
        await asyncio.sleep(0.05)
    await page.mouse.up()
    await asyncio.sleep(0.5)

    # Step 5: Pointer-trust. No post-drag verify (bbox is canvas-painted;
    # DOM selectors cannot detect it and pixel sampling is noisy — see
    # docstring + session report §7).
    logger.info(
        "Drew bbox on canvas: x=%.2f y=%.2f w=%.2f h=%.2f canvas=%dx%d",
        x, y, w, h, int(cw), int(ch),
    )
    return True


async def count_visible_cards(page) -> int:
    """Count visible media cards on page."""
    try:
        return await page.evaluate("""() => {
            const videos = document.querySelectorAll('video');
            const tiles = document.querySelectorAll('[data-tile-id]');
            return Math.max(videos.length, tiles.length);
        }""")
    except Exception:
        return 0


async def finalize_operation(
    client,
    job: dict,
    job_type: str,
    project_id: str,
    locale: str,
    download_prefix: str = "op",
) -> dict:
    """Common post-submit flow: wait -> download -> extract metadata -> return result.

    This is called after submit_with_confirmation() succeeds.
    """
    page = client.page
    submit_baseline = len(getattr(client, "_media_id_events", []))

    # Wait for completion
    logger.info("Waiting for %s completion...", job_type)
    result = await wait_for_completion(
        client,
        job_type=job_type,
        # TODO: propagate submit-time baseline from op caller.
        initial_media_count_at_submit=submit_baseline,
    )

    if not result.get("done"):
        error = result.get("error", "unknown")
        message = f"{job_type} failed: {error}"
        message = await message_with_failure_capture(
            client,
            failure_kind_from_error(job_type, error),
            message,
        )
        raise RuntimeError(message)

    logger.info("%s complete!", job_type)

    current_url = page.url
    parent_media_id = job.get("media_id")
    ancestor_media_ids = job.get("ancestor_media_ids") or []
    download_media_ids = result.get("media_ids") or []
    media_id = await resolve_final_media_id(
        page,
        fallback=parent_media_id,
        parent_media_id=parent_media_id,
        ancestor_media_ids=list(ancestor_media_ids),
        download_media_ids=download_media_ids,
        # SPEC INV-5 (docs/SPEC.md:103) mandates the full resolution
        # chain: network event → latest DOM tile → settled /edit/ route →
        # parent fallback. The previous strict=True early-raise short-
        # circuited paths 2-4 whenever a chain-child had no network
        # event, even though tile + settled-route fallback are healthy
        # recovery paths.
        strict=False,
    )

    # Build edit_url
    edit_url_val = None
    if media_id and project_id:
        base = flow_url(locale)
        edit_url_val = f"{base}/project/{project_id}/edit/{media_id}"

    # Download
    proj_url = job.get("project_url")
    if not proj_url and project_id:
        proj_url = f"{flow_url(locale)}/project/{project_id}"

    # Build the download list using the resolved canonical id. The resolver
    # filters out parent + ancestor slugs (SPEC INV-5 §2026-05-16) — if the
    # raw ``download_media_ids`` list contained only ancestor mids, blindly
    # passing it here would download the parent clip while the job record
    # stored the new child mid (output_files / media_id mismatch). Mirror
    # the resolver's exclusion logic and fall back to ``[media_id]`` when
    # filtering empties the list.
    exclusion: set[str] = set()
    if parent_media_id:
        exclusion.add(parent_media_id)
    for anc in ancestor_media_ids:
        if anc:
            exclusion.add(anc)
    filtered_download_ids = [mid for mid in download_media_ids if mid and mid not in exclusion]
    if filtered_download_ids:
        download_list = filtered_download_ids
    elif media_id:
        download_list = [media_id]
    else:
        download_list = []

    logger.info("Downloading %s result...", job_type)
    output_files = await download_video(
        client,
        media_ids=download_list,
        prefix=download_prefix,
        metadata={
            "job_type": job_type,
            "prompt": job.get("prompt", ""),
            "media_id": media_id or "",
            "project_url": proj_url or "",
            "profile": client.profile_name or "",
        },
    )
    if not output_files:
        message = f"{job_type}: no output file captured"
        message = await message_with_failure_capture(
            client,
            f"{job_type.replace('-', '_')}_no_output_file",
            message,
        )
        raise RuntimeError(message)

    return {
        "project_url": proj_url or "",
        "media_id": media_id,
        "edit_url": edit_url_val or current_url,
        "output_files": output_files,
        "generation_id": client._gen_id,
        "profile": client.profile_name,
    }


async def _extract_settled_route_media_id(page, fallback: str | None = None) -> str | None:
    """Poll briefly for a settled /edit/{slug} route before falling back."""
    for _ in range(12):
        media_id = extract_media_id(page.url)
        if media_id:
            return media_id
        await asyncio.sleep(0.25)
    return fallback


async def resolve_final_media_id(
    page,
    *,
    fallback: str | None = None,
    parent_media_id: str | None = None,
    ancestor_media_ids: list[str] | None = None,
    download_media_ids: list[str] | None = None,
    strict: bool = False,
) -> str | None:
    """Resolve the canonical media slug for a completed operation.

    Priority (live-verified 2026-04-23, B39; ancestor exclusion 2026-05-16):
    1. Network-captured generation mid — the first ``download_media_ids``
       slug that isn't ``parent_media_id`` AND isn't any ancestor in the
       chain. Filtering only against immediate parent left grandparent /
       L1-root UUIDs as false positives (live v6 L3/L6/L8/L10 returned
       L1's id when UI fallback fired after revAPI replay was
       rate-limited).
    2. Latest history tile slug when it isn't any ancestor — same
       exclusion logic.
    3. Settled ``/edit/{slug}`` route — last resort; may be stale on the
       extend-child path.
    4. ``fallback`` — used only when the URL never settles to an /edit/
       slug (typically the parent media, so callers can still build a
       usable edit_url).

    With ``strict=True`` and a parent media id, missing network media is
    fatal before tile/URL fallback because deep-chain DOM order can be stale.
    """
    download_media_ids = download_media_ids or []
    exclusion: set[str] = set()
    if parent_media_id:
        exclusion.add(parent_media_id)
    if ancestor_media_ids:
        exclusion.update(a for a in ancestor_media_ids if a)

    network_media_id = next(
        (mid for mid in download_media_ids if mid and mid not in exclusion),
        None,
    )
    if network_media_id:
        logger.info(
            "media_id from network events: %s (excluded %d ancestors)",
            network_media_id[:20],
            len(exclusion),
        )
        return network_media_id

    if strict and parent_media_id is not None:
        await _capture_chain_child_no_new_media(page)
        raise RuntimeError(
            "resolve_final_media_id: no new network media event for chain-child op "
            f"(parent={_short_value(parent_media_id, 20)}, "
            f"ancestors={len(exclusion)}, "
            "tile_observed=None, "
            f"url={_short_value(getattr(page, 'url', None), 80)}) "
            f"kind={_CHAIN_CHILD_NO_NEW_MEDIA_KIND}"
        )

    tile_media_id = await find_latest_tile_slug(page)
    if tile_media_id and tile_media_id not in exclusion:
        url_media_id = extract_media_id(page.url)
        logger.warning(
            "No new network mid; using latest tile slug: parent=%s url=%s tile=%s (excluded %d ancestors)",
            (parent_media_id or "")[:20],
            (url_media_id or "")[:20],
            tile_media_id[:20],
            len(exclusion),
        )
        return tile_media_id

    return await _extract_settled_route_media_id(page, fallback=fallback)
