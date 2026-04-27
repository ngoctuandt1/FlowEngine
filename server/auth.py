"""Worker-endpoint authentication.

The dashboard surfaces (``/api/jobs``, ``/api/profiles``, ``/api/uploads``,
the static frontend) are intentionally open — they sit behind whatever
network gate / nginx basic-auth the operator chooses.

But ``/api/worker/*`` is privileged: it lets a caller claim a job,
update its status, or post results back. Anyone who can reach the
server over the public internet must NOT be able to hit those routes.

This module enforces a Bearer-token check against the ``API_KEY`` env
var. The worker (``worker/remote_api.py``) already sends
``Authorization: Bearer $API_KEY``; we just need to verify it server-side.

Setting ``API_KEY=dev-key`` (the default) keeps localhost development
ergonomic, but production deploys MUST set a strong random value via
``.env`` / systemd ``Environment=`` directives.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import HTTPException, Request

from server.config import API_KEY

logger = logging.getLogger(__name__)

_WARNED_DEFAULT_KEY = False


def _is_default_key(key: str) -> bool:
    return key in {"", "dev-key", "changeme"}


async def require_worker_token(request: Request) -> None:
    """Raise 401 unless the request bears a valid worker token.

    Wired in via ``Depends(require_worker_token)`` on the worker router so
    every endpoint under ``/api/worker/*`` is gated. The check is
    constant-time to deny a timing-attack oracle on the API key.
    """
    global _WARNED_DEFAULT_KEY

    if _is_default_key(API_KEY) and not _WARNED_DEFAULT_KEY:
        # One-shot warning per process so the log isn't flooded but the
        # operator notices on the first worker call.
        logger.warning(
            "API_KEY is unset or set to the default — /api/worker/* is "
            "effectively open. Set a strong random API_KEY in production."
        )
        _WARNED_DEFAULT_KEY = True

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {API_KEY}"

    # Constant-time compare so attackers can't time the prefix match.
    if not secrets.compare_digest(auth, expected):
        # In dev (default key) we still allow unauth'd worker calls so
        # `python run_worker.py` JustWorks. The startup warning above is
        # the operator's signal to lock this down before exposing the
        # server publicly.
        if _is_default_key(API_KEY):
            return
        raise HTTPException(status_code=401, detail="Invalid worker token")
