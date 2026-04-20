"""URL construction and extraction helpers for Google Flow."""

from __future__ import annotations

import logging
import re

FLOW_BASE = "https://labs.google/fx"
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------

def flow_url(locale: str = "") -> str:
    """Return the Flow homepage URL, optionally localised.

    >>> flow_url()
    'https://labs.google/fx/tools/flow'
    >>> flow_url("vi")
    'https://labs.google/fx/vi/tools/flow'
    """
    if locale:
        return f"{FLOW_BASE}/{locale}/tools/flow"
    return f"{FLOW_BASE}/tools/flow"


def project_url(project_id: str, locale: str = "") -> str:
    """Return the project grid URL for *project_id*.

    >>> project_url("abc-123")
    'https://labs.google/fx/tools/flow/project/abc-123'
    """
    base = flow_url(locale)
    return f"{base}/project/{project_id}"


def edit_url(project_id: str, media_id: str, locale: str = "") -> str:
    """Return the media-edit URL inside a project.

    >>> edit_url("proj-1", "media-2")
    'https://labs.google/fx/tools/flow/project/proj-1/edit/media-2'
    """
    base = flow_url(locale)
    return f"{base}/project/{project_id}/edit/{media_id}"


# ---------------------------------------------------------------------------
# URL extractors
# ---------------------------------------------------------------------------

_PROJECT_RE = re.compile(
    r"/project/([0-9a-f-]{20,64})", re.IGNORECASE
)
_MEDIA_RE = re.compile(
    r"/edit/([0-9a-f-]{20,64})", re.IGNORECASE
)
_LOCALE_RE = re.compile(
    r"labs\.google/fx/([a-z]{2}(?:-[a-z]{2})?)/tools/flow", re.IGNORECASE
)


def extract_project_id(url: str) -> str | None:
    """Extract project UUID from a Flow URL.

    >>> extract_project_id("https://labs.google/fx/tools/flow/project/abc-123/edit/m1")
    'abc-123'
    >>> extract_project_id("https://example.com") is None
    True
    """
    m = _PROJECT_RE.search(str(url or ""))
    return m.group(1) if m else None


def extract_media_id(url: str) -> str | None:
    """Extract media UUID from a Flow ``/edit/{id}`` URL.

    >>> extract_media_id("https://labs.google/fx/tools/flow/project/p1/edit/m2")
    'm2'
    """
    m = _MEDIA_RE.search(str(url or ""))
    return m.group(1) if m else None


def detect_locale(url: str) -> str:
    """Detect locale prefix from a Flow URL.

    Returns ``''`` for English (no prefix) or a locale code like ``'vi'``.

    >>> detect_locale("https://labs.google/fx/vi/tools/flow")
    'vi'
    >>> detect_locale("https://labs.google/fx/tools/flow")
    ''
    """
    m = _LOCALE_RE.search(str(url or ""))
    return m.group(1).lower() if m else ""


def is_flow_url(url: str) -> bool:
    """Return True if *url* is on the Flow application domain.

    >>> is_flow_url("https://labs.google/fx/tools/flow/project/p1")
    True
    >>> is_flow_url("https://google.com")
    False
    """
    return bool(
        re.search(
            r"labs\.google/fx(?:/[a-z]{2}(?:-[a-z]{2})?)?/tools/flow",
            str(url or "").lower(),
        )
    )


async def find_latest_tile_slug(page, timeout_ms: int = 3000) -> str | None:
    """Return the newest gallery tile slug, or ``None`` if unavailable.

    The project/history gallery exposes stable ``data-tile-id`` attributes on
    rendered tiles. When Flow keeps the stale parent route mounted after an
    operation completes, the newest output clip still appears as the last tile
    in DOM order. This helper cross-checks that tile and extracts its slug from
    either ``data-tile-id`` or a descendant ``/edit/{slug}`` link.
    """
    selector = "[data-tile-id]"
    try:
        await page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
    except Exception:
        return None

    try:
        result = await page.evaluate(
            """() => {
                const tiles = Array.from(document.querySelectorAll('[data-tile-id]'));
                if (!tiles.length) return {slug: null, ambiguous: false};

                const tile = tiles[tiles.length - 1];
                const rawTileId = tile.getAttribute('data-tile-id') || '';
                const attrSlug = rawTileId.startsWith('fe_id_')
                    ? rawTileId.slice(6)
                    : rawTileId;
                const hrefNode = tile.querySelector('a[href*="/edit/"]') || tile.closest('a[href*="/edit/"]');
                const href = hrefNode?.getAttribute('href') || hrefNode?.href || '';
                const hrefMatch = href.match(/\\/edit\\/([0-9a-f-]{20,64})/i);
                const hrefSlug = hrefMatch ? hrefMatch[1] : null;

                const valid = (value) => /^[0-9a-f-]{20,64}$/i.test(value || '');
                const candidates = [attrSlug, hrefSlug]
                    .filter(valid)
                    .map((value) => value.toLowerCase());
                const unique = Array.from(new Set(candidates));

                if (!unique.length) return {slug: null, ambiguous: false};
                if (unique.length > 1) return {slug: null, ambiguous: true};
                return {slug: unique[0], ambiguous: false};
            }"""
        )
    except Exception as exc:
        logger.debug("Latest tile slug lookup failed: %s", exc)
        return None

    if not isinstance(result, dict) or result.get("ambiguous"):
        return None
    slug = result.get("slug")
    return slug if isinstance(slug, str) and slug else None
