"""Media-ID parsing and normalisation utilities."""

from __future__ import annotations

import re
from urllib.parse import unquote

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

_STRIP_SUFFIXES = ("_upsampled", "_720p", "_1080p")


def normalize_media_id(mid: str) -> str:
    """URL-decode, strip query noise and known quality suffixes.

    >>> normalize_media_id("abc123_upsampled")
    'abc123'
    >>> normalize_media_id("https://x.com?name=def456&foo=1")
    'def456'
    """
    mid = str(mid or "").strip()
    if not mid:
        return ""

    # URL-decode
    try:
        mid = unquote(mid)
    except Exception:
        pass

    # If a full URL was passed, pull out the ``name=`` parameter.
    if "name=" in mid and ("http://" in mid or "https://" in mid or "?" in mid or "&" in mid):
        m = re.search(r"[?&]name=([^&#]+)", mid)
        if m:
            mid = m.group(1)

    # Drop trailing query/fragment noise.
    mid = mid.split("&", 1)[0].split("#", 1)[0].strip()

    # Strip well-known quality suffixes.
    for suf in _STRIP_SUFFIXES:
        if mid.endswith(suf):
            mid = mid[: -len(suf)]

    return mid


# ---------------------------------------------------------------------------
# Extraction from URLs
# ---------------------------------------------------------------------------

def media_id_from_url(url: str) -> str | None:
    """Extract the ``name=`` parameter from a media redirect URL.

    >>> media_id_from_url("https://labs.google/fx/api/trpc/media.getMediaUrlRedirect?name=abc123")
    'abc123'
    >>> media_id_from_url("https://example.com/page") is None
    True
    """
    m = re.search(r"[?&]name=([A-Za-z0-9._%-]+)", str(url or ""))
    if not m:
        return None
    raw = m.group(1)
    try:
        raw = unquote(raw)
    except Exception:
        pass
    return raw.strip() or None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# Standard UUID: 8-4-4-4-12 hex
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
# Compact hex string (Google sometimes uses 24-64 hex chars without dashes)
_HEX_RE = re.compile(r"^[0-9a-f]{24,64}$", re.IGNORECASE)


def looks_like_media_id(s: str) -> bool:
    """Return True if *s* resembles a valid media UUID.

    >>> looks_like_media_id("a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    True
    >>> looks_like_media_id("abc")
    False
    """
    s = str(s or "").strip()
    if not s:
        return False
    if _UUID_RE.match(s):
        return True
    if _HEX_RE.match(s):
        return True
    return False
