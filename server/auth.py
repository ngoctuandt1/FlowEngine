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
import os
import secrets

from fastapi import HTTPException, Request

from server.config import API_KEY

logger = logging.getLogger(__name__)

_WARNED_DEFAULT_KEY = False

# Tokens that are recognised as "this deploy did not set a real API key".
# Empty string covers ``API_KEY=`` and unset (config.py defaults to
# ``dev-key``, which we treat the same way). The other strings are the
# placeholders we've seen in onboarding docs / sample envs.
_DEFAULT_KEYS = frozenset({"", "dev-key", "changeme", "test", "secret"})


def _is_default_key(key: str) -> bool:
    return key in _DEFAULT_KEYS


def _production_mode() -> bool:
    """Heuristic: the dashboard gate is only set in real deploys.

    ``DASHBOARD_PASSWORD`` is required to put the dashboard on the public
    internet (see ``server/dashboard_auth.py``), so its presence is our
    most reliable signal that this process is serving non-local traffic.
    Local ``python run_server.py`` development leaves it unset.

    pytest sets ``PYTEST_CURRENT_TEST`` per test; the test suite reuses
    ``DASHBOARD_PASSWORD=test`` to exercise the dashboard middleware
    without intending to model a real deploy. Skip the hard-fail in that
    case so existing dashboard-auth tests don't trip on the worker-API
    guard.
    """
    if (os.environ.get("FLOW_FORCE_PRODUCTION_API_KEY_CHECK") or "").strip() == "1":
        return bool((os.environ.get("DASHBOARD_PASSWORD") or "").strip())
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return bool((os.environ.get("DASHBOARD_PASSWORD") or "").strip())


def _insecure_override() -> bool:
    """Explicit opt-in for the operator who really wants an open worker API.

    The default is to refuse to start in production with a dev-key, but we
    leave a clearly-named escape hatch so an operator behind their own VPN
    can still run if they accept the risk.
    """
    return (os.environ.get("FLOW_ALLOW_INSECURE_WORKER_API") or "").strip() == "1"


def assert_production_api_key() -> None:
    """Hard-fail server startup if a production deploy ships the dev key.

    Called from the FastAPI lifespan on boot. Quiet no-op in development
    (``DASHBOARD_PASSWORD`` unset) — there we keep the existing one-shot
    warning behaviour so ``python run_worker.py`` JustWorks locally.

    A production deploy that genuinely wants an open worker API can set
    ``FLOW_ALLOW_INSECURE_WORKER_API=1`` to downgrade the hard-fail to a
    CRITICAL log line. Anything else: refuse to start. ``/api/worker/*``
    is the only thing standing between the public internet and the
    ability to steal jobs / post fake results / exfiltrate prompts.
    """
    if not _is_default_key(API_KEY):
        return
    if not _production_mode():
        logger.warning(
            "API_KEY is unset or set to the default — /api/worker/* is "
            "open for local development. Set a strong random API_KEY "
            "before exposing this server publicly."
        )
        return
    if _insecure_override():
        logger.critical(
            "API_KEY is unset or default in production (DASHBOARD_PASSWORD "
            "is set) but FLOW_ALLOW_INSECURE_WORKER_API=1 — /api/worker/* "
            "is publicly reachable without auth. Set a strong API_KEY."
        )
        return
    raise RuntimeError(
        "Refusing to start: DASHBOARD_PASSWORD is set (production deploy) "
        "but API_KEY is unset or matches a known dev default "
        f"({sorted(k for k in _DEFAULT_KEYS if k)}). Set a strong random "
        "API_KEY environment variable, or set "
        "FLOW_ALLOW_INSECURE_WORKER_API=1 to override (NOT recommended). "
        "See server/auth.py."
    )


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
