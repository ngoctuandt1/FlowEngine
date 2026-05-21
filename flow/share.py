"""Flow share-link UI automation."""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from urllib.parse import urlparse

from playwright.async_api import TimeoutError as PlaywrightTimeoutError


logger = logging.getLogger(__name__)

HTTPS_URL_RE = re.compile(r"https://[^\s\"'<>]+")
FLOW_SHARE_HOSTS = frozenset({"labs.google"})
FLOW_SHARE_PATH_RE = re.compile(r"^/fx/tools/flow/project/[^/]+/share/[^/]+/?$")
SHARE_BUTTON_SELECTORS = (
    "button:has-text('Share')",
    "button:has(i:text-is('share'))",
    "[role='button']:has-text('Share')",
)
COPY_LINK_SELECTORS = (
    "button:has-text('Copy link')",
    "button:has-text('Copy')",
    "[role='button']:has-text('Copy link')",
    "[role='button']:has-text('Copy')",
)
MODAL_SELECTORS = (
    "[role='dialog']:has-text('Copy link')",
    "[role='dialog']:has-text('Sharing allows')",
    "[role='dialog']:has-text('Share')",
)


class FlowShareError(RuntimeError):
    """Base class for Flow share automation failures."""


class FlowShareButtonNotFound(FlowShareError):
    """Raised when current Flow surface has no share affordance."""


class FlowShareLinkNotFound(FlowShareError):
    """Raised when the share modal does not expose an HTTPS link."""


@dataclass(frozen=True)
class FlowShareLink:
    """Copyable Flow share link plus redaction-safe token."""

    url: str
    token: str


def _extract_https_url(text: str | None) -> str | None:
    if not text:
        return None
    for match in HTTPS_URL_RE.finditer(text):
        candidate = match.group(0).rstrip(".,;)]")
        parsed = urlparse(candidate)
        if (
            parsed.scheme == "https"
            and parsed.netloc.lower() in FLOW_SHARE_HOSTS
            and FLOW_SHARE_PATH_RE.fullmatch(parsed.path)
        ):
            return candidate
    return None


def _token_from_url(url: str) -> str:
    parsed = urlparse(url)
    token = parsed.fragment or parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if not token:
        token = parsed.netloc
    return token[:128]


async def _click_first_visible(page, selectors: tuple[str, ...], timeout_ms: int) -> str | None:
    for selector in selectors:
        try:
            locator = page.locator(selector).first
            if not await locator.is_visible(timeout=min(timeout_ms, 1000)):
                continue
            await locator.click(timeout=timeout_ms)
            return selector
        except Exception:
            continue
    return None


async def _wait_for_share_modal(page, timeout_ms: int):
    deadline_slice_ms = max(500, min(timeout_ms, 2500))
    for selector in MODAL_SELECTORS:
        try:
            locator = page.locator(selector).first
            await locator.wait_for(state="visible", timeout=deadline_slice_ms)
            return locator
        except Exception:
            continue
    raise FlowShareLinkNotFound("Flow share modal did not open")


async def _clipboard_text(page) -> str | None:
    try:
        return await page.evaluate("navigator.clipboard.readText()")
    except Exception:
        return None


async def copy_flow_share_link(page, *, timeout_ms: int = 10_000) -> FlowShareLink:
    """Open Flow share modal, click Copy link, return one HTTPS URL.

    Uses UI and clipboard only. It intentionally does not call the captured
    ``flowAgent:shareApplet`` literal because discovery only proved tool-share
    static JS references, not project/job share mutation bodies.
    """
    share_selector = await _click_first_visible(page, SHARE_BUTTON_SELECTORS, timeout_ms)
    if share_selector is None:
        logger.info("Flow share button unavailable on current surface")
        raise FlowShareButtonNotFound("Flow share button unavailable")

    logger.info("Flow share button clicked")
    modal = await _wait_for_share_modal(page, timeout_ms)

    clipboard_before = await _clipboard_text(page)

    copy_selector = await _click_first_visible(page, COPY_LINK_SELECTORS, timeout_ms)
    if copy_selector is not None:
        logger.info("Flow share copy action clicked")

    clipboard_after = await _clipboard_text(page)
    candidates: list[str | None] = []
    if clipboard_after != clipboard_before:
        candidates.append(clipboard_after)
    try:
        candidates.append(await modal.inner_text(timeout=min(timeout_ms, 2000)))
    except (PlaywrightTimeoutError, TypeError):
        pass
    except Exception:
        logger.debug("Flow share modal text unavailable", exc_info=True)

    for candidate in candidates:
        share_url = _extract_https_url(candidate)
        if share_url:
            logger.info("Flow share link captured")
            return FlowShareLink(url=share_url, token=_token_from_url(share_url))

    raise FlowShareLinkNotFound("Flow share modal did not expose an expected Flow share URL")
