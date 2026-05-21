"""Shared reverse-API preference, logging, and fallback helpers."""

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})
DEFAULT_REVERSE_API_TIMEOUT_SEC = 30.0
SECRET_KEY_RE = re.compile(
    r"(?:^|[-_])(?:authorization|cookie|token|secret|credential|api[-_]?key|recaptcha)(?:$|[-_])",
    re.IGNORECASE,
)
BEARER_RE = re.compile(r"Bearer\s+[^\s,;\]}\)\"']+", re.IGNORECASE)
TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:authorization|cookie|token|secret|credential|api[-_]?key|recaptcha)[A-Za-z0-9_.-]*)\s*([:=])\s*([^\s,;\]}\)\"']+)"
)
SHARE_PATH_RE = re.compile(r"(/share/)[^/?#\s\"'<>]+", re.IGNORECASE)
SECRET_QUERY_RE = re.compile(
    r"(?i)([?&][^=&#\s]*(?:authorization|auth|cookie|token|secret|credential|key|recaptcha)[^=&#\s]*=)[^&#\s]+"
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReverseApiOutcome:
    """Result from one reverse-API-first attempt."""

    operation: str
    status: str
    attempted: bool
    result: Any = None
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"


def env_flag(name: str, *, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in TRUE_VALUES:
        return True
    if value in FALSE_VALUES:
        return False
    return default


def reverse_api_preferred() -> bool:
    """Return global reverse-API preference; default is on."""

    return env_flag("FLOW_PREFER_REVERSE_API", default=True)


def reverse_api_timeout_sec(*, default_sec: float = DEFAULT_REVERSE_API_TIMEOUT_SEC) -> float:
    raw_ms = os.getenv("FLOW_REVERSE_API_TIMEOUT_MS")
    if raw_ms:
        try:
            value = float(raw_ms) / 1000.0
            if value > 0:
                return value
        except ValueError:
            pass

    raw_sec = os.getenv("FLOW_REVERSE_API_TIMEOUT_SEC")
    if raw_sec:
        try:
            value = float(raw_sec)
            if value > 0:
                return value
        except ValueError:
            pass

    return default_sec


def redact_reverse_api_value(value: Any) -> Any:
    """Redact auth, bearer, token, cookie, and share-token material."""

    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if SECRET_KEY_RE.search(key_text):
                redacted[key_text] = "<redacted>"
            else:
                redacted[key_text] = redact_reverse_api_value(item)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [redact_reverse_api_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value

    text = str(value)
    text = BEARER_RE.sub("Bearer <redacted>", text)
    text = TOKEN_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}<redacted>",
        text,
    )
    text = SECRET_QUERY_RE.sub(lambda match: f"{match.group(1)}<redacted>", text)
    text = SHARE_PATH_RE.sub(lambda match: f"{match.group(1)}<redacted>", text)
    return text


def redacted_error(exc: BaseException | str) -> str:
    return str(redact_reverse_api_value(str(exc)))


def _safe_metadata(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    redacted = redact_reverse_api_value(dict(metadata))
    return redacted if isinstance(redacted, dict) else {}


def log_reverse_api_unavailable(
    log: logging.Logger,
    *,
    operation: str,
    reason: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    log.info(
        "reverse_api_unavailable | operation=%s reason=%s metadata=%s",
        operation,
        redact_reverse_api_value(reason),
        _safe_metadata(metadata),
    )


def log_reverse_api_disabled(
    log: logging.Logger,
    *,
    operation: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    log.info(
        "reverse_api_disabled | operation=%s metadata=%s",
        operation,
        _safe_metadata(metadata),
    )


async def run_reverse_api_first(
    *,
    operation: str,
    call: Callable[[], Awaitable[Any] | Any],
    log: logging.Logger | None = None,
    available: bool = True,
    unavailable_reason: str = "captured template unavailable",
    metadata: Mapping[str, Any] | None = None,
    timeout_sec: float | None = None,
    is_fatal_error: Callable[[BaseException], bool] | None = None,
) -> ReverseApiOutcome:
    """Run preferred reverse API once; return recoverable outcomes for UI fallback."""

    active_logger = log or logger
    safe_metadata = _safe_metadata(metadata)

    if not reverse_api_preferred():
        log_reverse_api_disabled(active_logger, operation=operation, metadata=safe_metadata)
        return ReverseApiOutcome(
            operation=operation,
            status="disabled",
            attempted=False,
            metadata=safe_metadata,
        )

    if not available:
        log_reverse_api_unavailable(
            active_logger,
            operation=operation,
            reason=unavailable_reason,
            metadata=safe_metadata,
        )
        return ReverseApiOutcome(
            operation=operation,
            status="unavailable",
            attempted=False,
            error=str(redact_reverse_api_value(unavailable_reason)),
            metadata=safe_metadata,
        )

    timeout = reverse_api_timeout_sec(
        default_sec=timeout_sec if timeout_sec is not None else DEFAULT_REVERSE_API_TIMEOUT_SEC
    )
    active_logger.info(
        "reverse_api_attempt | operation=%s timeout_sec=%.3f metadata=%s",
        operation,
        timeout,
        safe_metadata,
    )

    try:
        result = await asyncio.wait_for(_call_maybe_async(call), timeout=timeout)
    except asyncio.TimeoutError:
        message = f"reverse API timed out after {timeout:.3f}s"
        active_logger.warning(
            "reverse_api_recoverable_error | operation=%s error=%s metadata=%s",
            operation,
            message,
            safe_metadata,
        )
        return ReverseApiOutcome(
            operation=operation,
            status="recoverable_error",
            attempted=True,
            error=message,
            metadata=safe_metadata,
        )
    except Exception as exc:
        if is_fatal_error is not None and is_fatal_error(exc):
            active_logger.error(
                "reverse_api_fatal_error | operation=%s error=%s metadata=%s",
                operation,
                redacted_error(exc),
                safe_metadata,
            )
            raise
        message = redacted_error(exc)
        active_logger.warning(
            "reverse_api_recoverable_error | operation=%s error=%s metadata=%s",
            operation,
            message,
            safe_metadata,
        )
        return ReverseApiOutcome(
            operation=operation,
            status="recoverable_error",
            attempted=True,
            error=message,
            metadata=safe_metadata,
        )

    active_logger.info(
        "reverse_api_success | operation=%s metadata=%s",
        operation,
        safe_metadata,
    )
    return ReverseApiOutcome(
        operation=operation,
        status="success",
        attempted=True,
        result=result,
        metadata=safe_metadata,
    )


async def _call_maybe_async(call: Callable[[], Awaitable[Any] | Any]) -> Any:
    result = call()
    if inspect.isawaitable(result):
        return await result
    return result
