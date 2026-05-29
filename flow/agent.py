"""Per-project Google Flow Agent-mode controls."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Literal


def _keep_agent() -> bool:
    """When set, FlowEngine does NOT disable/sabotage Flow's agent mode.

    The 2026-05 Flow redesign made the agent composer the ONLY generation path:
    generation flows through `flowCreationAgent:streamChat`. The legacy
    agent-disable + session-blocker logic (built to preserve the old toolbar)
    actively breaks generation now, so it must be skipped in the new flow.
    """
    return os.environ.get("FLOW_KEEP_AGENT", "0") == "1"

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from flow.navigation import extract_project_id
from flow.reverse_api import (
    log_reverse_api_disabled,
    redact_reverse_api_value,
    redacted_error,
    reverse_api_preferred,
)


logger = logging.getLogger(__name__)

AGENT_DISABLED = "AGENT_TOGGLE_STATE_DISABLED"
AGENT_ENABLED = "AGENT_TOGGLE_STATE_ENABLED"
AGENT_UPDATE_MASK = "agent_toggle_state"
AGENT_METHOD_API = "reverseAPI"
AGENT_METHOD_DOM = "DOM"

AgentDisableStatus = Literal[
    "already_off",
    "toggled_off_api",
    "toggled_off_dom",
    "unavailable",
    "failed_nonfatal",
]

AgentDetectionState = Literal["on", "off", "missing", "unknown"]


@dataclass(frozen=True)
class AgentRestoreToken:
    """Opt-in token callers can pass to :func:`restore_agent_state`."""

    project_id: str
    previous_state: AgentDetectionState
    target_url: str | None = None
    method: str | None = None


@dataclass(frozen=True)
class AgentDisableResult:
    """Structured Agent disable result."""

    status: AgentDisableStatus
    profile: str
    project_id: str | None
    previous_detection_state: AgentDetectionState
    method: str | None = None
    restoration_token: AgentRestoreToken | None = None
    message: str = ""
    api_status: int | None = None

    @property
    def restoration_token_available(self) -> bool:
        return self.restoration_token is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "profile": self.profile,
            "project_id": self.project_id,
            "previous_detection_state": self.previous_detection_state,
            "method": self.method,
            "restoration_token_available": self.restoration_token_available,
            "message": self.message,
            "api_status": self.api_status,
        }


@dataclass(frozen=True)
class AgentRestoreResult:
    """Structured opt-in Agent restore result."""

    status: str
    profile: str
    project_id: str
    method: str | None = None
    message: str = ""
    api_status: int | None = None


@dataclass(frozen=True)
class _AgentDetection:
    state: AgentDetectionState
    aria_pressed: str | None = None
    class_name: str | None = None
    text: str | None = None


_AGENT_BUTTON_TEXT_RE = re.compile(r"(^|\s)Agent(\s|$)", re.IGNORECASE)
_NORMAL_MODE_CHIP_RE = re.compile(
    r"(\b(?:video|image|veo|gemini|nano|banana|omni)\b|\bx\s*\d+\b)",
    re.IGNORECASE,
)
_EDIT_ACTION_BUTTON_RE = re.compile(
    r"\b(Extend|Insert|Remove|Camera|Mở rộng|Chèn|Xoá|Xóa)\b",
    re.IGNORECASE,
)
_COMPOSER_TEXTBOX_SELECTORS = (
    '[data-slate-editor="true"][contenteditable="true"]',
    '[role="textbox"][contenteditable="true"]',
    '[contenteditable="true"]',
    "textarea",
)

_AUTH_PROBE_SCRIPT = r"""
(() => {
  if (window.__flowAgentAuthProbeInstalled) return;
  window.__flowAgentAuthProbeInstalled = true;
  window.__flowAgentAuthProbe = window.__flowAgentAuthProbe || { tokens: [], requests: [] };

  const headersObj = (headers) => {
    const out = {};
    try {
      if (!headers) return out;
      if (headers.forEach) {
        headers.forEach((value, key) => { out[key] = value; });
      } else if (Array.isArray(headers)) {
        for (const [key, value] of headers) out[key] = value;
      } else {
        Object.assign(out, headers);
      }
    } catch (error) {}
    return out;
  };

  const remember = (entry) => {
    try {
      const url = String(entry.url || '');
      if (!url.startsWith('https://aisandbox-pa.googleapis.com/')) return;
      const headers = entry.headers || {};
      const authorization = headers.Authorization || headers.authorization || '';
      const hasBearer = String(authorization).startsWith('Bearer ');
      window.__flowAgentAuthProbe.requests.push({
        url,
        method: entry.method || 'GET',
        hasBearer,
        ts: Date.now(),
      });
      if (hasBearer) {
        window.__flowAgentAuthProbe.tokens.push({
          authorization,
          url,
          method: entry.method || 'GET',
          ts: Date.now(),
        });
        if (window.__flowAgentAuthProbe.tokens.length > 20) {
          window.__flowAgentAuthProbe.tokens.splice(0, window.__flowAgentAuthProbe.tokens.length - 20);
        }
      }
      if (window.__flowAgentAuthProbe.requests.length > 50) {
        window.__flowAgentAuthProbe.requests.splice(0, window.__flowAgentAuthProbe.requests.length - 50);
      }
    } catch (error) {}
  };

  const originalFetch = window.fetch;
  window.fetch = function(input, init) {
    try {
      const request = input instanceof Request ? input : null;
      remember({
        url: request ? request.url : String(input),
        method: (init && init.method) || (request && request.method) || 'GET',
        headers: Object.assign(
          {},
          headersObj(request && request.headers),
          headersObj(init && init.headers)
        ),
      });
    } catch (error) {}
    return originalFetch.apply(this, arguments);
  };

  const originalOpen = XMLHttpRequest.prototype.open;
  const originalSend = XMLHttpRequest.prototype.send;
  const originalSetRequestHeader = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function(method, url) {
    this.__flowAgentAuthProbeRequest = { method, url, headers: {} };
    return originalOpen.apply(this, arguments);
  };
  XMLHttpRequest.prototype.setRequestHeader = function(key, value) {
    try {
      if (this.__flowAgentAuthProbeRequest) {
        this.__flowAgentAuthProbeRequest.headers[key] = value;
      }
    } catch (error) {}
    return originalSetRequestHeader.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function() {
    try {
      if (this.__flowAgentAuthProbeRequest) remember(this.__flowAgentAuthProbeRequest);
    } catch (error) {}
    return originalSend.apply(this, arguments);
  };
})()
"""


def extract_agent_project_id(url: str | None) -> str | None:
    """Extract Flow project id from project/edit URLs."""

    return extract_project_id(url or "")


async def install_agent_auth_probe(page: Any) -> None:
    """Install same-page fetch/XHR auth capture for future Flow API calls."""

    try:
        await page.add_init_script(_AUTH_PROBE_SCRIPT)
    except Exception:
        pass
    try:
        await page.evaluate(_AUTH_PROBE_SCRIPT)
    except Exception:
        pass


async def disable_agent_mode_if_active(
    page: Any,
    *,
    profile_name: str = "",
    project_id: str | None = None,
    target_url: str | None = None,
    log: logging.Logger | None = None,
    allow_reverse_api: bool = True,
) -> AgentDisableResult:
    """Disable Agent mode for the current Flow project if it is active."""

    active_logger = log or logger
    if _keep_agent():
        active_logger.info("disable_agent_mode_if_active: skipped (FLOW_KEEP_AGENT=1)")
        return AgentDisableResult(
            status="unavailable",
            profile=profile_name,
            project_id=project_id,
            previous_detection_state="unknown",
            method="none",
            message="Agent kept active (FLOW_KEEP_AGENT=1)",
        )
    resolved_project_id = project_id or extract_agent_project_id(target_url or page.url)
    if not resolved_project_id:
        result = AgentDisableResult(
            status="unavailable",
            profile=profile_name,
            project_id=None,
            previous_detection_state="unknown",
            message="No Flow project id in target URL; Agent state not mutated",
        )
        _log_agent_result(active_logger, result)
        return result

    effective_reverse_api = allow_reverse_api and reverse_api_preferred()
    if allow_reverse_api and not effective_reverse_api:
        log_reverse_api_disabled(
            active_logger,
            operation="agent-disable",
            metadata={"project_id": resolved_project_id, "profile": profile_name},
        )
    if effective_reverse_api:
        await install_agent_auth_probe(page)

    detection = await _detect_agent_state(page)
    previous_state = detection.state
    if previous_state == "off":
        result = AgentDisableResult(
            status="already_off",
            profile=profile_name,
            project_id=resolved_project_id,
            previous_detection_state=previous_state,
            message="Agent chip already off",
        )
        _log_agent_result(active_logger, result)
        return result

    _on_edit_page = "/edit/" in str(target_url or getattr(page, "url", "") or "")
    if previous_state == "missing" and not _on_edit_page and await _normal_composer_visible(
        page,
        project_id=resolved_project_id,
    ):
        # On a project page (not /edit/) with no Agent button and normal composer
        # visible → agent mode is genuinely off.
        result = AgentDisableResult(
            status="unavailable",
            profile=profile_name,
            project_id=resolved_project_id,
            previous_detection_state=previous_state,
            message="Agent chip unavailable; normal composer visible",
        )
        _log_agent_result(active_logger, result)
        return result
    # On /edit/ pages: agent mode can be active (hiding L2 toolbar) without showing
    # an Agent toggle button. Always try the reverse API path in this case.

    api_error: str | None = None
    if effective_reverse_api:
        try:
            token = await _wait_for_bearer_token(page)
            if token:
                api_result = await _patch_agent_state(
                    page,
                    resolved_project_id,
                    token,
                    AGENT_DISABLED,
                )
                if 200 <= api_result["status"] < 300:
                    # PATCH toggles the default but doesn't remove existing sessions.
                    # Delete sessions so the next page load shows no agent overlay.
                    # Failures are non-fatal — we still proceed to _refresh_project_view.
                    try:
                        deleted = await _delete_agent_sessions(
                            page, resolved_project_id, token, active_logger
                        )
                    except Exception as _del_exc:
                        active_logger.info(
                            "Agent session delete skipped (non-fatal): %s", _del_exc
                        )
                        deleted = 0
                    active_logger.info(
                        "Agent API disable: PATCH 200 OK, deleted %d session(s) for %s",
                        deleted,
                        resolved_project_id[:20],
                    )
                    # Block session re-creation BEFORE reload so JS can't
                    # create a new session during the /edit/ page load.
                    await _install_agent_session_blocker(page)
                    await _refresh_project_view(page, target_url or page.url)
                    if await _agent_confirmed_off(page, project_id=resolved_project_id):
                        result = AgentDisableResult(
                            status="toggled_off_api",
                            profile=profile_name,
                            project_id=resolved_project_id,
                            previous_detection_state=previous_state,
                            method=AGENT_METHOD_API,
                            restoration_token=_restore_token_if_needed(
                                resolved_project_id,
                                previous_state,
                                target_url or page.url,
                                AGENT_METHOD_API,
                            ),
                            message=f"Agent disabled via reverseAPI + session delete ({deleted} session(s))",
                            api_status=api_result["status"],
                        )
                        _log_agent_mutation(active_logger, result)
                        return result
                    api_error = "reverseAPI succeeded but UI did not confirm normal composer"
                else:
                    api_error = (
                        f"reverseAPI returned HTTP {api_result['status']}: "
                        f"{redact_reverse_api_value(str(api_result.get('text') or '')[:240])}"
                    )
            else:
                api_error = "reverseAPI unavailable: no Bearer token captured from page context"
        except Exception as exc:
            api_error = f"reverseAPI failed: {redacted_error(exc)}"

    dom_result = await _disable_via_dom(
        page,
        profile_name=profile_name,
        project_id=resolved_project_id,
        previous_state=previous_state,
        target_url=target_url or page.url,
        api_error=api_error,
        log=active_logger,
    )
    if dom_result.status == "failed_nonfatal":
        _log_agent_result(active_logger, dom_result)
    return dom_result


async def restore_agent_state(
    page: Any,
    token: AgentRestoreToken | None,
    *,
    profile_name: str = "",
    log: logging.Logger | None = None,
    allow_reverse_api: bool = True,
) -> AgentRestoreResult:
    """Opt-in restore helper. Caller must pass a token from disable result."""

    active_logger = log or logger
    if token is None:
        return AgentRestoreResult(
            status="unavailable",
            profile=profile_name,
            project_id="",
            message="No restoration token supplied",
        )
    if token.previous_state != "on":
        return AgentRestoreResult(
            status="already_restored",
            profile=profile_name,
            project_id=token.project_id,
            message="Previous Agent state was not on",
        )

    effective_reverse_api = allow_reverse_api and reverse_api_preferred()
    if allow_reverse_api and not effective_reverse_api:
        log_reverse_api_disabled(
            active_logger,
            operation="agent-restore",
            metadata={"project_id": token.project_id, "profile": profile_name},
        )
    if effective_reverse_api:
        await install_agent_auth_probe(page)

    if effective_reverse_api:
        bearer = await _wait_for_bearer_token(page)
        if bearer:
            api_result = await _patch_agent_state(
                page,
                token.project_id,
                bearer,
                AGENT_ENABLED,
            )
            if 200 <= api_result["status"] < 300:
                result = AgentRestoreResult(
                    status="restored_api",
                    profile=profile_name,
                    project_id=token.project_id,
                    method=AGENT_METHOD_API,
                    message="Agent restored via reverseAPI",
                    api_status=api_result["status"],
                )
                _log_agent_restore_mutation(
                    active_logger,
                    result,
                    previous_state=token.previous_state,
                    restoration_token_available=True,
                )
                return result

    detection = await _detect_agent_state(page)
    if detection.state == "off":
        button = await _agent_button(page, timeout_ms=3000)
        if button is not None:
            await button.click(timeout=5000)
            await asyncio.sleep(1)
            if (await _detect_agent_state(page)).state == "on":
                result = AgentRestoreResult(
                    status="restored_dom",
                    profile=profile_name,
                    project_id=token.project_id,
                    method=AGENT_METHOD_DOM,
                    message="Agent restored via DOM click",
                )
                _log_agent_restore_mutation(
                    active_logger,
                    result,
                    previous_state=token.previous_state,
                    restoration_token_available=True,
                )
                return result

    return AgentRestoreResult(
        status="unavailable",
        profile=profile_name,
        project_id=token.project_id,
        message="Agent restore unavailable",
    )


def _restore_token_if_needed(
    project_id: str,
    previous_state: AgentDetectionState,
    target_url: str | None,
    method: str,
) -> AgentRestoreToken | None:
    if previous_state != "on":
        return None
    return AgentRestoreToken(
        project_id=project_id,
        previous_state=previous_state,
        target_url=target_url,
        method=method,
    )


async def _agent_button(page: Any, *, timeout_ms: int = 3000) -> Any | None:
    locator = page.locator("button").filter(has_text=_AGENT_BUTTON_TEXT_RE).last
    try:
        await locator.wait_for(state="visible", timeout=timeout_ms)
        return locator
    except Exception:
        return None


async def _detect_agent_state(page: Any) -> _AgentDetection:
    button = await _agent_button(page)
    if button is None:
        return _AgentDetection(state="missing")

    try:
        aria_pressed = await button.get_attribute("aria-pressed", timeout=1000)
    except Exception:
        aria_pressed = None
    try:
        class_name = await button.get_attribute("class", timeout=1000)
    except Exception:
        class_name = None
    try:
        text = (await button.inner_text(timeout=1000)).strip()
    except Exception:
        text = None

    if aria_pressed == "true":
        return _AgentDetection("on", aria_pressed, class_name, text)
    if aria_pressed == "false":
        return _AgentDetection("off", aria_pressed, class_name, text)

    highlighted = await _button_looks_highlighted(button)
    if highlighted is True:
        return _AgentDetection("on", aria_pressed, class_name, text)
    if highlighted is False:
        return _AgentDetection("off", aria_pressed, class_name, text)
    return _AgentDetection("unknown", aria_pressed, class_name, text)


async def _button_looks_highlighted(button: Any) -> bool | None:
    try:
        return await button.evaluate(
            """(el) => {
                const className = String(el.className || '').toLowerCase();
                if (/\b(active|selected|pressed|checked)\b/.test(className)) return true;
                if (el.getAttribute('aria-selected') === 'true') return true;
                if (el.getAttribute('aria-checked') === 'true') return true;
                if (el.getAttribute('data-state') === 'active') return true;
                const style = window.getComputedStyle(el);
                const bg = style.backgroundColor || '';
                const color = style.color || '';
                if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                    if (color && bg !== color) return true;
                }
                return null;
            }"""
        )
    except Exception:
        return None


async def _normal_composer_visible(
    page: Any,
    *,
    timeout_ms: int = 1500,
    project_id: str | None = None,
) -> bool:
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    while asyncio.get_event_loop().time() < deadline:
        if await _composer_visible_once(page, project_id=project_id):
            return True
        await asyncio.sleep(0.1)
    return False


async def _composer_visible_once(
    page: Any,
    *,
    project_id: str | None = None,
) -> bool:
    if project_id:
        current_project_id = extract_agent_project_id(getattr(page, "url", None))
        if current_project_id != project_id:
            return False

    try:
        # On /edit/ pages the L2 action toolbar (Extend/Insert/Remove/Camera) is
        # the indicator that the normal editor is active (agent mode off). Check
        # this first so we don't block on the text-composer check below, which is
        # only present on /project/ pages.
        current_url = str(getattr(page, "url", "") or "")
        if "/edit/" in current_url:
            edit_action_visible = await page.locator(
                "button, [role='button']"
            ).filter(has_text=_EDIT_ACTION_BUTTON_RE).first.is_visible(timeout=500)
            if edit_action_visible:
                return True

        text_input_visible = False
        for selector in _COMPOSER_TEXTBOX_SELECTORS:
            locator = page.locator(selector).first
            try:
                if await locator.is_visible(timeout=300):
                    text_input_visible = True
                    break
            except Exception:
                continue

        if not text_input_visible:
            return False

        normal_mode_chip_visible = await page.locator("button, [role='button']").filter(
            has_text=_NORMAL_MODE_CHIP_RE
        ).first.is_visible(timeout=500)
        if normal_mode_chip_visible:
            return True

        submit_icon_visible = await page.locator(
            "button:has(i:text-is('arrow_forward'))"
        ).first.is_visible(timeout=500)
        edit_action_visible = await page.locator("button, [role='button']").filter(
            has_text=_EDIT_ACTION_BUTTON_RE
        ).first.is_visible(timeout=500)
        return bool(submit_icon_visible and edit_action_visible)
    except Exception:
        return False


async def _wait_for_bearer_token(page: Any, *, timeout_ms: int = 5000) -> str | None:
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    while asyncio.get_event_loop().time() < deadline:
        token = await _latest_bearer_token(page)
        if token:
            return token
        await asyncio.sleep(0.2)
    return None


async def _latest_bearer_token(page: Any) -> str | None:
    try:
        token = await page.evaluate(
            "window.__flowAgentAuthProbe?.tokens?.at(-1)?.authorization || null"
        )
    except Exception:
        return None
    if isinstance(token, str) and token.startswith("Bearer "):
        return token
    return None


async def _patch_agent_state(
    page: Any,
    project_id: str,
    bearer_token: str,
    state: str,
) -> dict[str, Any]:
    return await page.evaluate(
        """async ({ projectId, bearerToken, state, updateMask }) => {
            const url = `https://aisandbox-pa.googleapis.com/v1/projects/${projectId}/agentInfo?updateMask=${updateMask}`;
            const response = await fetch(url, {
                method: 'PATCH',
                headers: { Authorization: bearerToken },
                body: JSON.stringify({ agentToggleState: state }),
            });
            const text = await response.text();
            return { status: response.status, ok: response.ok, text };
        }""",
        {
            "projectId": project_id,
            "bearerToken": bearer_token,
            "state": state,
            "updateMask": AGENT_UPDATE_MASK,
        },
    )


async def _delete_agent_sessions(
    page: Any,
    project_id: str,
    bearer_token: str,
    log: logging.Logger,
) -> int:
    """List and DELETE all flowCreationAgent sessions for the project.

    The agentInfo toggle-state PATCH (200 OK) does not prevent existing sessions
    from being loaded on subsequent navigations. Deleting the sessions removes
    the agent overlay entirely so the edit page loads without the agent UI.

    Returns count of successfully deleted sessions (0 on any list/parse error).
    """
    list_result = await page.evaluate(
        """async ({ projectId, bearerToken }) => {
            const url = `https://aisandbox-pa.googleapis.com/v1/flowCreationAgent/sessions?projectId=${projectId}`;
            try {
                const resp = await fetch(url, { headers: { Authorization: bearerToken } });
                const text = await resp.text();
                return { status: resp.status, text };
            } catch (err) {
                return { status: 0, text: String(err) };
            }
        }""",
        {"projectId": project_id, "bearerToken": bearer_token},
    )

    if not isinstance(list_result, dict) or list_result.get("status") != 200:
        log.info(
            "_delete_agent_sessions: list returned status=%s",
            list_result.get("status") if isinstance(list_result, dict) else "?",
        )
        return 0

    try:
        data = json.loads(list_result["text"])
    except Exception as exc:
        log.info("_delete_agent_sessions: JSON parse failed: %s", exc)
        return 0

    # Response may use "sessions" or other keys — collect all dicts with a name/id
    sessions_raw = data.get("sessions") or data.get("agentSessions") or []
    if not isinstance(sessions_raw, list):
        sessions_raw = []

    session_ids: list[str] = []
    for entry in sessions_raw:
        if not isinstance(entry, dict):
            continue
        # "name" field is like "projects/{pid}/agentSessions/{sid}" or just the ID
        name = entry.get("name") or ""
        sid = name.split("/")[-1] if "/" in name else name
        if not sid:
            sid = entry.get("sessionId") or entry.get("id") or ""
        if sid:
            session_ids.append(str(sid))

    if not session_ids:
        log.info(
            "_delete_agent_sessions: no sessions found for project %s", project_id[:20]
        )
        return 0

    log.info(
        "_delete_agent_sessions: deleting %d session(s) for project %s",
        len(session_ids),
        project_id[:20],
    )

    deleted = 0
    for sid in session_ids:
        try:
            del_result = await page.evaluate(
                """async ({ sessionId, bearerToken }) => {
                    const url = `https://aisandbox-pa.googleapis.com/v1/flowCreationAgent/sessions/${sessionId}`;
                    try {
                        const resp = await fetch(url, {
                            method: 'DELETE',
                            headers: { Authorization: bearerToken },
                        });
                        const text = await resp.text();
                        return { status: resp.status, ok: resp.ok, text };
                    } catch (err) {
                        return { status: 0, ok: false, text: String(err) };
                    }
                }""",
                {"sessionId": sid, "bearerToken": bearer_token},
            )
            status = del_result.get("status") if isinstance(del_result, dict) else 0
            if status and 200 <= status < 300:
                deleted += 1
                log.info(
                    "_delete_agent_sessions: deleted session %s (status=%d)",
                    sid[:20],
                    status,
                )
            else:
                log.info(
                    "_delete_agent_sessions: DELETE session %s returned status=%d",
                    sid[:20],
                    status,
                )
        except Exception as exc:
            log.info(
                "_delete_agent_sessions: DELETE session %s failed: %s",
                sid[:20],
                exc,
            )

    return deleted


async def install_agent_session_blocker(page: Any) -> None:
    """Public entry-point: install agent-mode route blockers before /edit/ navigation.

    Installs two Playwright route intercepts:
    1. GET /v1/projects/*/agentInfo → returns agentToggleState=DISABLED so Flow JS
       renders the normal L2 editor instead of the agent overlay.
    2. GET/POST /flowCreationAgent/sessions → returns empty sessions so no agent
       conversation is loaded or created.

    Call this BEFORE page.goto() on any /edit/ URL. Route handlers persist across
    page.goto() calls on the same page object so one install covers all subsequent
    navigations on this FlowClient page.
    """
    if _keep_agent():
        logger.info("install_agent_session_blocker: skipped (FLOW_KEEP_AGENT=1)")
        return
    logger.warning(
        "install_agent_session_blocker: installing route blockers on page %s",
        getattr(page, "url", "<unknown>")[:80],
    )
    await _install_agent_info_blocker(page)
    await _install_agent_session_blocker(page)


async def uninstall_agent_session_blocker(page: Any) -> None:
    """Remove agent session/info route blockers so the agent edit UI can work.

    2026-05 redesign: the old L2 toolbar is gone; agent sessions must be
    allowed for 'Describe your edits' submissions to reach the server.
    Call this before navigating to any /edit/ URL where agent editing is
    needed (e.g. L2 operations via submit_via_agent_edit_ui).
    """
    unroute_fn = getattr(page, "unroute", None)
    if not callable(unroute_fn):
        return
    removed = 0
    for pattern in (
        "**/flowCreationAgent/sessions**",
        "**/v1/projects/*/agentInfo**",
        "**/agentInfo**",           # actual pattern used by _install_agent_info_blocker
        "**/flowAgent/applets**",
        "**/flowAgent/savedSharedApplets**",
    ):
        try:
            await unroute_fn(pattern)
            removed += 1
        except Exception:
            pass  # unroute raises if no handler was registered for this pattern
    if removed:
        logger.info("uninstall_agent_session_blocker: route blockers removed (%d patterns)", removed)
    else:
        logger.debug("uninstall_agent_session_blocker: no active blockers to remove")


_AGENT_INFO_DISABLED_RESPONSE = json.dumps(
    {"agentToggleState": AGENT_DISABLED}
)


async def _install_agent_info_blocker(page: Any) -> None:
    """Intercept aisandbox-pa.googleapis.com requests to suppress agent-mode UI.

    R17 diagnostic revealed the actual agent-mode trigger is NOT agentInfo GET
    (no such GET is made) but rather:
      GET /v1/flowAgent/applets           → returns editing applets list
      GET /v1/flowAgent/savedSharedApplets → returns saved shared applets

    When either returns a non-empty list the Flow SPA renders the "agent editing
    panel" which REPLACES the traditional L2 toolbar (Extend/Remove/Camera/Insert).
    Returning empty lists prevents that panel from loading.

    We also keep the agentInfo GET intercept for forward-compat in case Flow adds
    a GET-based toggle check later.  PATCH requests always pass through so our own
    _patch_agent_state / disable_agent_mode_if_active PATCH calls still reach the
    server.
    """
    async def _agentinfo_handler(route, request):
        if request.method == "GET":
            logger.warning(
                "_agent_info_blocker: intercepted GET agentInfo → returning DISABLED url=%s",
                request.url[:100],
            )
            await route.fulfill(
                status=200,
                content_type="application/json",
                body=_AGENT_INFO_DISABLED_RESPONSE,
            )
        else:
            await route.continue_()

    async def _applets_handler(route, request):
        if request.method == "GET":
            logger.warning(
                "_agent_applets_blocker: intercepted GET %s → returning empty list",
                request.url[request.url.rfind("/") + 1:request.url.rfind("/") + 40],
            )
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"applets": []}',
            )
        else:
            await route.continue_()

    async def _saved_applets_handler(route, request):
        if request.method == "GET":
            logger.warning(
                "_agent_applets_blocker: intercepted GET savedSharedApplets → returning empty",
            )
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"savedSharedApplets": []}',
            )
        else:
            await route.continue_()

    try:
        route_fn = getattr(page, "route", None)
        if callable(route_fn):
            await route_fn("**/agentInfo**", _agentinfo_handler)
            logger.warning("_install_agent_info_blocker: agentInfo route handler registered OK")
            await route_fn("**/flowAgent/applets**", _applets_handler)
            logger.warning("_install_agent_info_blocker: flowAgent/applets handler registered OK")
            await route_fn("**/flowAgent/savedSharedApplets**", _saved_applets_handler)
            logger.warning("_install_agent_info_blocker: flowAgent/savedSharedApplets handler registered OK")
    except Exception as exc:
        logger.warning("_install_agent_info_blocker: route install failed (non-fatal): %s", exc)


async def _install_agent_session_blocker(page: Any) -> None:
    """Intercept flowCreationAgent/sessions to prevent JS from re-creating agent sessions.

    After deleting existing sessions the browser JS will try to POST a new one
    on the next /edit/ navigation.  This route handler short-circuits that loop:
    GET returns an empty list (no sessions → agent mode not applied), POST/PATCH
    return 200 without reaching the server, DELETE passes through so our own
    cleanup calls still work.

    Registered routes persist across page.goto() calls on the same page object,
    so one install covers all subsequent navigations on this FlowClient page.
    """
    async def _handler(route, request):
        if request.method == "DELETE":
            await route.continue_()
        elif request.method == "GET":
            logger.warning(
                "_agent_session_blocker: intercepted GET sessions (returning empty list) url=%s",
                request.url[:100],
            )
            await route.fulfill(
                status=200,
                content_type="application/json",
                body='{"sessions": []}',
            )
        else:
            logger.warning(
                "_agent_session_blocker: intercepted %s sessions (returning {}) url=%s",
                request.method,
                request.url[:100],
            )
            await route.fulfill(status=200, content_type="application/json", body='{}')

    try:
        route_fn = getattr(page, "route", None)
        if callable(route_fn):
            await route_fn("**/flowCreationAgent/sessions**", _handler)
            logger.warning("_install_agent_session_blocker: route handler registered OK")
    except Exception as exc:
        logger.warning("_install_agent_session_blocker: route install failed (non-fatal): %s", exc)


async def _refresh_project_view(page: Any, target_url: str | None) -> None:
    refresh_url = target_url or page.url
    try:
        await page.goto(refresh_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)
    except Exception:
        try:
            await page.reload(wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
        except Exception:
            pass


async def _agent_confirmed_off(
    page: Any,
    *,
    project_id: str | None = None,
) -> bool:
    detection = await _detect_agent_state(page)
    if detection.state == "off":
        return await _normal_composer_visible(
            page,
            timeout_ms=5000,
            project_id=project_id,
        )
    if detection.state == "missing":
        return await _normal_composer_visible(
            page,
            timeout_ms=5000,
            project_id=project_id,
        )
    return False


async def _disable_via_dom(
    page: Any,
    *,
    profile_name: str,
    project_id: str,
    previous_state: AgentDetectionState,
    target_url: str | None,
    api_error: str | None,
    log: logging.Logger,
) -> AgentDisableResult:
    button = await _agent_button(page, timeout_ms=5000)
    if button is not None:
        detection = await _detect_agent_state(page)
        if detection.state == "off":
            if api_error and not await _normal_composer_visible(
                page,
                timeout_ms=5000,
                project_id=project_id,
            ):
                raise RuntimeError(
                    "Agent disable failed after reverseAPI error: "
                    f"profile={profile_name} project_id={project_id} "
                    f"previous_detection_state={previous_state} api_error={api_error} "
                    "normal_composer_visible=false"
                )
            status: AgentDisableStatus = "failed_nonfatal" if api_error else "already_off"
            result = AgentDisableResult(
                status=status,
                profile=profile_name,
                project_id=project_id,
                previous_detection_state=previous_state,
                method=AGENT_METHOD_DOM if api_error else None,
                message=api_error or "Agent chip already off after recheck",
            )
            _log_agent_result(log, result)
            return result

        if detection.state in ("on", "unknown"):
            try:
                response_seen = False
                try:
                    async with page.expect_response(
                        lambda response: (
                            f"/v1/projects/{project_id}/agentInfo" in response.url
                            and f"updateMask={AGENT_UPDATE_MASK}" in response.url
                            and response.request.method == "PATCH"
                        ),
                        timeout=15000,
                    ):
                        await button.click(timeout=5000)
                    response_seen = True
                except PlaywrightTimeoutError:
                    pass

                if await _wait_for_dom_disabled(page, project_id=project_id):
                    result = AgentDisableResult(
                        status="toggled_off_dom",
                        profile=profile_name,
                        project_id=project_id,
                        previous_detection_state=previous_state,
                        method=AGENT_METHOD_DOM,
                        restoration_token=_restore_token_if_needed(
                            project_id,
                            previous_state,
                            target_url,
                            AGENT_METHOD_DOM,
                        ),
                        message=(
                            "Agent disabled via DOM fallback"
                            + (f" after {api_error}" if api_error else "")
                            + ("; PATCH observed" if response_seen else "; PATCH not observed")
                        ),
                    )
                    _log_agent_mutation(log, result)
                    return result
            except Exception as exc:
                api_error = f"{api_error}; DOM fallback failed: {exc}" if api_error else str(exc)

    if await _normal_composer_visible(page, timeout_ms=5000, project_id=project_id):
        result = AgentDisableResult(
            status="failed_nonfatal" if api_error else "unavailable",
            profile=profile_name,
            project_id=project_id,
            previous_detection_state=previous_state,
            method=AGENT_METHOD_DOM if api_error else None,
            message=(api_error or "Agent chip unavailable") + "; normal composer visible",
        )
        _log_agent_result(log, result)
        return result

    raise RuntimeError(
        "Agent disable failed: "
        f"profile={profile_name} project_id={project_id} "
        f"previous_detection_state={previous_state} "
        f"api_error={api_error or 'none'} normal_composer_visible=false"
    )


async def _wait_for_dom_disabled(
    page: Any,
    *,
    timeout_ms: int = 10000,
    project_id: str | None = None,
) -> bool:
    deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
    while asyncio.get_event_loop().time() < deadline:
        if await _agent_confirmed_off(page, project_id=project_id):
            return True
        await asyncio.sleep(0.2)
    return False


def _log_agent_mutation(log: logging.Logger, result: AgentDisableResult) -> None:
    log.info(
        "Agent disable mutation | profile=%s project_id=%s previous=%s method=%s "
        "restore_token=%s status=%s api_status=%s message=%s",
        result.profile,
        result.project_id,
        result.previous_detection_state,
        result.method,
        result.restoration_token_available,
        result.status,
        result.api_status,
        result.message,
    )


def _log_agent_restore_mutation(
    log: logging.Logger,
    result: AgentRestoreResult,
    *,
    previous_state: AgentDetectionState,
    restoration_token_available: bool,
) -> None:
    log.info(
        "Agent restore mutation | profile=%s project_id=%s previous=%s method=%s "
        "restore_token=%s status=%s api_status=%s message=%s",
        result.profile,
        result.project_id,
        previous_state,
        result.method,
        restoration_token_available,
        result.status,
        result.api_status,
        result.message,
    )


def _log_agent_result(log: logging.Logger, result: AgentDisableResult) -> None:
    log.info(
        "Agent disable result | profile=%s project_id=%s previous=%s method=%s "
        "restore_token=%s status=%s api_status=%s message=%s",
        result.profile,
        result.project_id,
        result.previous_detection_state,
        result.method,
        result.restoration_token_available,
        result.status,
        result.api_status,
        result.message,
    )
