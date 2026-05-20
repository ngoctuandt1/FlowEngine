"""Browser pool — keep one FlowClient alive per profile across jobs.

Motivation
----------
The default per-job lifecycle (``async with FlowClient(...)``) spends
~12-15 s each run on profile clone, Chrome launch, CDP connect, and
the initial homepage navigate. For a batch of N sequential jobs on the
same profile, that's ~N × 15 s of pure overhead.

The pool keeps one :class:`FlowClient` per profile warm between jobs:

  - First job on a profile: full start (same cost as before).
  - Subsequent jobs: reuse the running client. The handler is expected
    to navigate wherever it needs (homepage for L1, project/edit URL
    for L2+); per-job buffers are cleared by the pool via
    :meth:`FlowClient.reset_for_next_job`.
  - Crash or page-closed state is detected by :meth:`FlowClient.is_healthy`
    before handoff; an unhealthy client is torn down and replaced.

Opt-in via ``FLOW_BROWSER_POOL=1``. The default keeps the original
per-job lifecycle to avoid regressing stable deployments.

Concurrency
-----------
The :class:`~worker.profile_manager.ProfileManager` already serializes
jobs per profile, so only one caller will acquire a given profile at a
time. The pool still takes a per-profile :class:`asyncio.Lock` as a
defensive guard against future parallel usage.
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import AsyncIterator

from flow.client import FlowClient, reset_client_for_next_job

logger = logging.getLogger(__name__)


def _pool_enabled() -> bool:
    return os.environ.get("FLOW_BROWSER_POOL", "").strip().lower() in (
        "1", "true", "yes",
    )


class BrowserPool:
    """One-FlowClient-per-profile cache with lazy start + health checks."""

    def __init__(
        self,
        *,
        profile_base_dir: str,
        download_dir: str,
    ) -> None:
        self._profile_base_dir = profile_base_dir
        self._download_dir = download_dir
        self._clients: dict[str, FlowClient] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Protect dict mutations during concurrent first-touch.
        self._dict_lock = asyncio.Lock()

    def _make_client(self, profile: str) -> FlowClient:
        return FlowClient(
            profile_name=profile,
            profile_base_dir=self._profile_base_dir,
            download_dir=self._download_dir,
        )

    async def _lock_for(self, profile: str) -> asyncio.Lock:
        async with self._dict_lock:
            lock = self._locks.get(profile)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[profile] = lock
            return lock

    async def _discard_unhealthy(self, profile: str) -> None:
        """Drop + stop the current client for *profile* if it exists."""
        client = self._clients.pop(profile, None)
        if client is None:
            return
        try:
            await client.stop()
        except Exception:
            logger.warning(
                "Pool: stop() of broken client for %s failed", profile, exc_info=True
            )

    @asynccontextmanager
    async def lease(
        self, profile: str, *, reset_url: str | None = None
    ) -> AsyncIterator[FlowClient]:
        """Yield a ready FlowClient for *profile*, reusing or starting one.

        Parameters
        ----------
        profile:
            Chrome profile directory name.
        reset_url:
            If given, navigate the (reused) page to this URL before
            handing the client to the caller. Project/edit URLs run the
            per-job Agent-off reset hook before composer automation.
        """
        lock = await self._lock_for(profile)
        async with lock:
            client = self._clients.get(profile)

            # Discard unhealthy survivors before handing out.
            if client is not None and not client.is_healthy():
                logger.info(
                    "Pool: client for %s is unhealthy — restarting", profile
                )
                await self._discard_unhealthy(profile)
                client = None

            if client is None:
                client = self._make_client(profile)
                await client.start()
                self._clients[profile] = client
                logger.info("Pool: started fresh client for profile %s", profile)
            else:
                logger.info("Pool: reusing warm client for profile %s", profile)

            # Reset per-job state (buffers + optional nav) BEFORE handing
            # out. A failure here means the browser is compromised —
            # discard and bubble up so the caller can fall back or fail.
            try:
                await reset_client_for_next_job(client, target_url=reset_url)
            except Exception:
                logger.warning(
                    "Pool: reset failed for %s — discarding client", profile,
                    exc_info=True,
                )
                await self._discard_unhealthy(profile)
                raise

            try:
                yield client
            except Exception:
                # Handler raised — client *may* be compromised. Health-
                # check and discard if not reusable. Don't swallow.
                if not client.is_healthy():
                    logger.info(
                        "Pool: client for %s unhealthy after handler exception — discarding",
                        profile,
                    )
                    await self._discard_unhealthy(profile)
                raise
            # Success: leave the client in place for the next caller.

    async def close_all(self) -> None:
        """Stop every pooled client. Called on worker shutdown."""
        profiles = list(self._clients.keys())
        for profile in profiles:
            await self._discard_unhealthy(profile)
        logger.info("Pool: closed %d client(s)", len(profiles))


# ---------------------------------------------------------------------------
# Module-level singleton — wired by worker.main on startup.
# ---------------------------------------------------------------------------

_pool: BrowserPool | None = None


def init_pool(*, profile_base_dir: str, download_dir: str) -> BrowserPool | None:
    """Initialise the pool if ``FLOW_BROWSER_POOL=1``.

    Returns the pool instance (or ``None`` when disabled). Safe to call
    more than once — subsequent calls return the existing instance.
    """
    global _pool
    if not _pool_enabled():
        return None
    if _pool is None:
        _pool = BrowserPool(
            profile_base_dir=profile_base_dir,
            download_dir=download_dir,
        )
        logger.info(
            "BrowserPool enabled (profile_base_dir=%s, download_dir=%s)",
            profile_base_dir, download_dir,
        )
    return _pool


def get_pool() -> BrowserPool | None:
    """Return the active pool or ``None`` if pool mode is disabled."""
    return _pool


async def shutdown_pool() -> None:
    """Stop every pooled client and clear the singleton."""
    global _pool
    if _pool is None:
        return
    await _pool.close_all()
    _pool = None
