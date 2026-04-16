"""URL construction and extraction helpers for Google Flow."""

from __future__ import annotations

import re

FLOW_BASE = "https://labs.google/fx"

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
