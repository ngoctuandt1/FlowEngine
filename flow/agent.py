"""Per-project Google Flow Agent-mode controls."""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from flow.navigation import extract_project_id


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

    if previous_state == "missing" and await _normal_composer_visible(
        page,
        project_id=resolved_project_id,
    ):
        result = AgentDisableResult(
            status="unavailable",
            profile=profile_name,
            project_id=resolved_project_id,
            previous_detection_state=previous_state,
            message="Agent chip unavailable; normal composer visible",
        )
        _log_agent_result(active_logger, result)
        return result

    api_error: str | None = None
    if allow_reverse_api:
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
                            message="Agent disabled via reverseAPI",
                            api_status=api_result["status"],
                        )
                        _log_agent_mutation(active_logger, result)
                        return result
                    api_error = "reverseAPI succeeded but UI did not confirm normal composer"
                else:
                    api_error = (
                        f"reverseAPI returned HTTP {api_result['status']}: "
                        f"{str(api_result.get('text') or '')[:240]}"
                    )
            else:
                api_error = "reverseAPI unavailable: no Bearer token captured from page context"
        except Exception as exc:
            api_error = f"reverseAPI failed: {exc}"

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

    await install_agent_auth_probe(page)
    if allow_reverse_api:
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
