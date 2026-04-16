"""Retry logic for Flow operations -- exponential backoff on transient failures."""

import asyncio
import logging
import functools

logger = logging.getLogger(__name__)

# Errors that are worth retrying
TRANSIENT_ERRORS = [
    "timeout",
    "no_signal_timeout",
    "navigation",
    "Target closed",
    "Target page, context or browser has been closed",
    "net::ERR_",
    "Page crashed",
    "frame was detached",
    "Execution context was destroyed",
]

# Errors that should NOT be retried
PERMANENT_ERRORS = [
    "NO_CREDITS",
    "POLICY",
    "blocked_403",
    "blocked_429",
    "ALL_FAILED",
    "No profile assigned",
    "Unknown job type",
]

DEFAULT_MAX_RETRIES = 2
DEFAULT_BASE_DELAY = 5.0  # seconds
DEFAULT_MAX_DELAY = 60.0


def is_transient(error: str) -> bool:
    """Check if an error message indicates a transient (retryable) failure."""
    error_lower = str(error).lower()
    # Check permanent first
    for p in PERMANENT_ERRORS:
        if p.lower() in error_lower:
            return False
    # Check transient
    for t in TRANSIENT_ERRORS:
        if t.lower() in error_lower:
            return True
    # Default: not transient
    return False


async def with_retry(
    coro_func,
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    job_id: str = "",
    **kwargs,
):
    """Execute an async function with retry on transient failures.

    Args:
        coro_func: Async function to call
        *args, **kwargs: Arguments passed to coro_func
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries (seconds)
        max_delay: Maximum delay between retries
        job_id: For logging context

    Returns: Result from coro_func
    Raises: Last exception if all retries exhausted
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            return await coro_func(*args, **kwargs)
        except Exception as exc:
            last_error = exc
            error_msg = str(exc)

            if attempt >= max_retries:
                logger.error(
                    "Job %s: all %d attempts exhausted. Last error: %s",
                    job_id, max_retries + 1, error_msg,
                )
                raise

            if not is_transient(error_msg):
                logger.error(
                    "Job %s: permanent error (no retry): %s",
                    job_id, error_msg,
                )
                raise

            delay = min(base_delay * (2 ** attempt), max_delay)
            logger.warning(
                "Job %s: attempt %d/%d failed (%s). Retrying in %.0fs...",
                job_id, attempt + 1, max_retries + 1, error_msg[:80], delay,
            )
            await asyncio.sleep(delay)

    raise last_error  # Should not reach here


def retryable(max_retries: int = DEFAULT_MAX_RETRIES, base_delay: float = DEFAULT_BASE_DELAY):
    """Decorator version of with_retry for handler functions.

    Usage:
        @retryable(max_retries=2)
        async def my_handler(job: dict) -> dict:
            ...
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await with_retry(
                func, *args,
                max_retries=max_retries,
                base_delay=base_delay,
                job_id=kwargs.get("job", {}).get("id", args[0].get("id", "") if args else ""),
                **kwargs,
            )
        return wrapper
    return decorator
