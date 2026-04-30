"""Failure-capture helpers for Flow error paths."""

import json
import logging
import os
import platform
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_LINUX_CAPTURE_DIR = Path("/opt/flowengine/error-captures")
_DEFAULT_OTHER_CAPTURE_DIR = Path("./error-captures")
_SECRET_PATTERNS = (
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"password=[^&\s\"'>]+", re.IGNORECASE),
    re.compile(r'("password"\s*:\s*")[^"]+(")', re.IGNORECASE),
)
_DATA_URL_RE = re.compile(r"data:[^\"'\s>]+", re.IGNORECASE)


def _default_capture_dir() -> Path:
    if platform.system() == "Linux":
        return _DEFAULT_LINUX_CAPTURE_DIR
    return _DEFAULT_OTHER_CAPTURE_DIR


def _capture_enabled() -> bool:
    return os.environ.get("FLOW_ERROR_CAPTURE", "").strip() != "0"


def _sanitize_job_id_short(job_id: str) -> str:
    raw = str(job_id or "")[:8].lower()
    cleaned = re.sub(r"[^a-z0-9_-]+", "_", raw).strip("._-")
    return cleaned or "unknown"


def _sanitize_kind(kind: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", str(kind or "").lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    cleaned = cleaned[:30].strip("_")
    return cleaned or "failure"


def _page_is_open(page) -> bool:
    if page is None:
        return False

    is_closed = getattr(page, "is_closed", None)
    if is_closed is None:
        return True

    try:
        return not (is_closed() if callable(is_closed) else bool(is_closed))
    except Exception:
        return False


def _redact_secrets(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith('("password"'):
            redacted = pattern.sub(r"\1***REDACTED***\2", redacted)
        else:
            redacted = pattern.sub("***REDACTED***", redacted)
    return redacted


def _redact_url(url: str) -> str:
    if not url:
        return url

    patterns = (
        (
            r"([?&#](key|access_token|signature|auth|password|api_key|token)=)[^&#]*",
            r"\1***REDACTED***",
        ),
    )
    out = str(url)
    for pattern, replacement in patterns:
        out = re.sub(pattern, replacement, out, flags=re.IGNORECASE)
    return out


def _stringify_preview(value) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)

    return _redact_secrets(_redact_url(text))[:200]


def _serialize_calls(client, extra: Optional[dict]) -> list[dict]:
    serialized: list[dict] = []
    calls = getattr(client, "_calls", []) or []
    for call in calls[-20:]:
        entry = {
            "url": _redact_url(call.get("url") or ""),
            "status": call.get("status"),
            "method": call.get("method", ""),
            "ts": call.get("ts"),
            "body_preview": _stringify_preview(call.get("body")),
        }
        serialized.append(entry)
    return serialized


def _sanitize_html_snippet(html: str) -> str:
    return _DATA_URL_RE.sub("data:***REDACTED***", html or "")[:5000]


async def capture_failure(
    client,
    job_id: str,
    kind: str,
    extra: Optional[dict] = None,
) -> Optional[Path]:
    """Best-effort: screenshot page + dump last 20 _calls + save HTML snippet.

    Returns Path of the .png file if screenshot succeeded, else None
    (siblings .network.json/.html may still exist on disk). Capture failure must NOT
    propagate because the caller is already in an error path.

    Files written:
      <FLOW_ERROR_CAPTURE_DIR>/<unix_ts>_<job_id_short>_<kind>.png
      <FLOW_ERROR_CAPTURE_DIR>/<unix_ts>_<job_id_short>_<kind>.network.json
      <FLOW_ERROR_CAPTURE_DIR>/<unix_ts>_<job_id_short>_<kind>.html
    """
    if not _capture_enabled():
        return None

    try:
        capture_dir = Path(os.environ.get("FLOW_ERROR_CAPTURE_DIR", "") or _default_capture_dir())
        capture_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("capture_failure: could not prepare capture dir: %s", exc)
        return None

    try:
        ts = int(time.time())
        prefix = f"{ts}_{_sanitize_job_id_short(job_id)}_{_sanitize_kind(kind)}"
        png_path = capture_dir / f"{prefix}.png"
        network_path = capture_dir / f"{prefix}.network.json"
        html_path = capture_dir / f"{prefix}.html"
        page = getattr(client, "page", None)
        wrote_png = False
        wrote_network = False
        wrote_html = False

        if _page_is_open(page):
            try:
                await page.screenshot(path=str(png_path), full_page=False, timeout=5000)
                wrote_png = True
            except Exception as exc:
                logger.warning("capture_failure: screenshot failed for %s: %s", prefix, exc)

        try:
            network_path.write_text(
                json.dumps(_serialize_calls(client, extra), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            wrote_network = True
        except Exception as exc:
            logger.warning("capture_failure: network dump failed for %s: %s", prefix, exc)

        if _page_is_open(page):
            try:
                html_path.write_text(
                    _sanitize_html_snippet(await page.content()),
                    encoding="utf-8",
                )
                wrote_html = True
            except Exception as exc:
                logger.warning("capture_failure: html dump failed for %s: %s", prefix, exc)

        if not (wrote_png or wrote_network or wrote_html):
            return None
        return png_path if wrote_png else None
    except Exception as exc:
        logger.warning("capture_failure: unexpected failure for %s/%s: %s", job_id, kind, exc)
        return None
