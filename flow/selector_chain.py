"""Selector chain helpers — small abstractions over the
"try selector A, then B, then C" pattern repeated across operations.

Designed to be **opt-in**: existing call sites that have unique hooks
(scroll-into-view, JS fallback, post-click URL polling, custom logging)
should keep their bespoke loops. This helper covers only the simplest
shape — visible-then-click — which appears verbatim in defensive paths
like overlay dismissal.

Public surface kept small on purpose. If a caller needs scroll-into-view
or per-attempt logging, prefer extending the bespoke loop over bloating
this module — see CLAUDE.md "Don't add features beyond what the task
requires."
"""

from __future__ import annotations

import logging
from typing import Callable, Optional, Sequence


logger = logging.getLogger(__name__)


async def click_first_visible(
    page,
    selectors: Sequence[str],
    *,
    is_visible_timeout_ms: int = 1000,
    click_timeout_ms: int = 2000,
    on_match: Optional[Callable[[str], None]] = None,
) -> Optional[str]:
    """Try each selector in order; click the first visible match.

    Parameters
    ----------
    page:
        Playwright Page (real or mock).
    selectors:
        Ordered list of CSS / Playwright selectors to try.
    is_visible_timeout_ms:
        Per-selector ``locator.is_visible()`` timeout. Default 1s.
    click_timeout_ms:
        Click timeout for the matched selector. Default 2s.
    on_match:
        Optional callback invoked with the matched selector string.
        Use for caller-side logging — this helper does NOT log on its
        own to avoid noisy double-logging in transcripts.

    Returns
    -------
    Matched selector string on click success, or ``None`` if no
    selector was visible and clickable. Callers should branch on the
    return value to run their fallback (Escape press, JS click, etc.).

    Notes
    -----
    Exceptions raised by Playwright (Timeout, ElementHandle gone, etc.)
    are swallowed per-selector — the loop continues to the next
    candidate. Programmer errors (TypeError, AttributeError) propagate.
    """
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if not await btn.is_visible(timeout=is_visible_timeout_ms):
                continue
            await btn.click(timeout=click_timeout_ms)
        except Exception:
            continue
        if on_match is not None:
            try:
                on_match(sel)
            except Exception:
                logger.exception("on_match callback failed for selector %s", sel)
        return sel
    return None
