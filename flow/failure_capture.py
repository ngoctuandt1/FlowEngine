"""Helpers for non-blocking failure capture during Flow browser sessions."""

from __future__ import annotations


def capture_job_id(client) -> str:
    """Return the current job id stamped onto the client, or ``unknown``."""
    return str(getattr(client, "_job_id", "unknown") or "unknown")


def append_capture_suffix(message: str, capture_path: str | None) -> str:
    """Append ``[cap=...]`` once when a capture path is available."""
    if capture_path and "[cap=" not in message:
        return f"{message} [cap={capture_path}]"
    return message


def last_capture_for_kind(client, kind: str) -> str | None:
    """Return the last capture path when it was recorded for the same kind."""
    if client is None:
        return None
    last_kind = getattr(client, "_last_failure_kind", None)
    last_capture = getattr(client, "_last_failure_capture", None)
    if last_kind == kind and last_capture:
        return str(last_capture)
    return None


async def capture_failure_nonblocking(
    client,
    kind: str,
    *,
    extra: dict | None = None,
) -> str | None:
    """Attempt failure capture without ever blocking the error path."""
    if client is None:
        return None

    try:
        from flow.diagnostics import capture_failure
    except Exception:
        return None

    try:
        capture_path = await capture_failure(
            client,
            capture_job_id(client),
            kind,
            extra=extra,
        )
    except Exception:
        capture_path = None

    if capture_path:
        capture_text = (
            capture_path.as_posix()
            if hasattr(capture_path, "as_posix")
            else str(capture_path)
        )
        setattr(client, "_last_failure_capture", capture_text)
        setattr(client, "_last_failure_kind", kind)
        return capture_text

    return None


async def message_with_failure_capture(
    client,
    kind: str,
    message: str,
    *,
    extra: dict | None = None,
) -> str:
    """Return *message* with an optional cached or freshly captured suffix."""
    if "[cap=" in message:
        return message

    capture_path = last_capture_for_kind(client, kind)
    if capture_path is None:
        capture_path = await capture_failure_nonblocking(
            client,
            kind,
            extra=extra,
        )

    return append_capture_suffix(message, capture_path)
