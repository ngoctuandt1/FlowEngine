"""Worker entry point -- claim loop + heartbeat.

Polls the FlowEngine server for available jobs, dispatches them
to the correct handler, and reports results back.

Configuration is loaded from environment variables (or a .env file).
"""

import asyncio
import logging
import os
import signal
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from worker.browser_pool import init_pool, shutdown_pool
from worker.dispatcher import dispatch_job
from worker.profile_manager import ProfileManager
from worker.project_lock import ProjectLock
from worker.remote_api import RemoteAPI

load_dotenv()

# ======================================================================
# Configuration
# ======================================================================

SERVER_URL = os.getenv("SERVER_URL", "http://localhost:8080")
WORKER_ID = os.getenv("WORKER_ID", "worker-1")
API_KEY = os.getenv("API_KEY", "")
CHROME_USER_DATA_DIR = os.getenv("CHROME_USER_DATA_DIR", "./chrome-profiles")
WORKER_PROFILES = [
    p.strip()
    for p in os.getenv("WORKER_PROFILES", "default").split(",")
    if p.strip()
]
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "5"))
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "1"))
HEARTBEAT_INTERVAL_SEC = 30

# ======================================================================
# Logging
# ======================================================================

LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s  %(message)s"
LOG_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def setup_logging() -> None:
    from server.config import setup_logging as _setup
    _setup("worker")


logger = logging.getLogger("worker")

# ======================================================================
# Shutdown flag
# ======================================================================

_shutdown = asyncio.Event()


def _handle_signal() -> None:
    """Set the shutdown flag on SIGINT / SIGTERM."""
    logger.info("Shutdown signal received -- finishing current work ...")
    _shutdown.set()


# ======================================================================
# Pre-flight checks
# ======================================================================

def _profile_looks_warm(profile_dir: Path) -> bool:
    """Heuristic: a "warm" Chrome profile has a Default/ subdir with a
    Cookies SQLite file. Empty / freshly-created dirs (e.g. when running
    from a worktree where chrome-profiles/ was auto-mkdir'd by tooling)
    fail this check.

    We deliberately don't open the Cookies DB — Chrome may be running and
    holding a lock. Existence + non-zero size is enough signal to catch
    the "wrong dir" mistake before burning a job.
    """
    if not profile_dir.is_dir():
        return False
    # Modern Chrome stores the active profile in <user-data-dir>/Default/
    candidates = [
        profile_dir / "Default" / "Cookies",
        profile_dir / "Default" / "Network" / "Cookies",
    ]
    return any(p.is_file() and p.stat().st_size > 0 for p in candidates)


def preflight_profiles(
    chrome_user_data_dir: str,
    profile_names: list[str],
) -> list[str]:
    """Return a list of human-readable problems with the configured
    profile dirs. Empty list = all good.

    Catches the most common DX trap: running the worker from a fresh
    git worktree where ``./chrome-profiles/<name>/`` exists but contains
    no cookies — the worker would otherwise march all the way to Flow
    and fail with the misleading "+ New project button missing" error.
    """
    base = Path(chrome_user_data_dir).expanduser().resolve()
    problems: list[str] = []
    if not base.is_dir():
        problems.append(
            f"CHROME_USER_DATA_DIR={base} does not exist. "
            f"Set the env var to an absolute path containing warmed profiles."
        )
        return problems
    for name in profile_names:
        profile_dir = base / name
        if not profile_dir.is_dir():
            problems.append(
                f"Profile '{name}' missing at {profile_dir}. "
                f"Run scripts/warm_profile.py {name} first."
            )
        elif not _profile_looks_warm(profile_dir):
            problems.append(
                f"Profile '{name}' at {profile_dir} has no cookies — "
                f"likely an empty / freshly-created dir. "
                f"Warm it via: python scripts/warm_profile.py {name}"
            )
    return problems


# ======================================================================
# Main loop
# ======================================================================

async def claim_loop(
    api: RemoteAPI,
    profile_mgr: ProfileManager,
    project_lock: ProjectLock,
) -> None:
    """Core claim-dispatch-update loop."""

    last_heartbeat = datetime.now(UTC)
    heartbeat_delta = timedelta(seconds=HEARTBEAT_INTERVAL_SEC)
    in_flight: set[asyncio.Task[None]] = set()
    max_concurrent = max(
        1,
        min(len(profile_mgr.profiles), MAX_CONCURRENT_JOBS),
    )

    logger.info(
        "Starting claim loop  server=%s  worker=%s  profiles=%s  poll=%ds  max=%d",
        SERVER_URL, WORKER_ID, WORKER_PROFILES, POLL_INTERVAL_SEC, max_concurrent,
    )

    async def run_claimed_job(job: dict) -> None:
        job_id = job.get("id", "?")
        profile = job.get("profile", "")
        try:
            result = await dispatch_job(
                job,
                profile_mgr,
                project_lock,
                manage_profile=False,
            )
        except Exception as exc:
            logger.exception("Dispatch crashed for job %s", job_id)
            result = {"status": "failed", "error": str(exc)}
        finally:
            if profile:
                profile_mgr.mark_available(profile)

        try:
            await api.update_job(job_id, result)
            logger.info("Job %s result sent -> %s", job_id, result.get("status"))
        except Exception:
            logger.error("Failed to report result for job %s", job_id, exc_info=True)

    async def wait_for_capacity() -> None:
        if not in_flight:
            try:
                await asyncio.wait_for(_shutdown.wait(), timeout=POLL_INTERVAL_SEC)
            except asyncio.TimeoutError:
                pass
            return

        shutdown_task = asyncio.create_task(_shutdown.wait())
        done, _ = await asyncio.wait(
            {*in_flight, shutdown_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if shutdown_task in done:
            return
        shutdown_task.cancel()
        await asyncio.gather(shutdown_task, return_exceptions=True)

    try:
        while not _shutdown.is_set():
            in_flight = {task for task in in_flight if not task.done()}

            now = datetime.now(UTC)
            if now - last_heartbeat >= heartbeat_delta:
                try:
                    await api.heartbeat()
                    last_heartbeat = now
                except Exception:
                    logger.warning("Heartbeat failed", exc_info=True)

            available = profile_mgr.get_available()
            if not available or len(in_flight) >= max_concurrent:
                await wait_for_capacity()
                continue

            claim_profiles = available[: max_concurrent - len(in_flight)]
            try:
                job = await api.claim_job(claim_profiles)
            except Exception:
                logger.warning("Claim request failed", exc_info=True)
                job = None

            if job is None:
                await wait_for_capacity()
                continue

            job_id = job.get("id", "?")
            job_type = job.get("type", "?")
            job_profile = job.get("profile", "")
            logger.info("Claimed job %s [%s] profile=%s", job_id, job_type, job_profile)

            if job_profile:
                profile_mgr.mark_busy(job_profile, job_id)
            task = asyncio.create_task(run_claimed_job(job))
            in_flight.add(task)

    finally:
        if in_flight:
            logger.info("Waiting for %d in-flight job(s) to finish", len(in_flight))
            await asyncio.gather(*in_flight, return_exceptions=True)


async def run() -> None:
    """Initialise components and run the claim loop."""

    setup_logging()

    logger.info("=" * 60)
    logger.info("FlowEngine Worker  %s", WORKER_ID)
    logger.info("Server:   %s", SERVER_URL)
    logger.info("Chrome:   %s", Path(CHROME_USER_DATA_DIR).resolve())
    logger.info("Profiles: %s", ", ".join(WORKER_PROFILES))
    logger.info("Poll:     %ds", POLL_INTERVAL_SEC)
    logger.info("=" * 60)

    # Fail fast on misconfigured profile dirs — the most common DX trap
    # is running from a worktree where ./chrome-profiles is empty.
    problems = preflight_profiles(CHROME_USER_DATA_DIR, WORKER_PROFILES)
    if problems:
        for p in problems:
            logger.error("Preflight: %s", p)
        logger.error(
            "Refusing to start. Set CHROME_USER_DATA_DIR to an absolute "
            "path with warmed profiles, or run scripts/warm_profile.py."
        )
        sys.exit(2)

    # Wire up graceful shutdown
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            # Windows does not support add_signal_handler for SIGTERM
            pass

    api = RemoteAPI(SERVER_URL, WORKER_ID, API_KEY)
    profile_mgr = ProfileManager(CHROME_USER_DATA_DIR, WORKER_PROFILES)
    project_lock = ProjectLock()
    init_pool(
        profile_base_dir=CHROME_USER_DATA_DIR,
        download_dir=os.getenv("FLOW_DOWNLOAD_DIR", "./downloads"),
    )

    try:
        await claim_loop(api, profile_mgr, project_lock)
    finally:
        await shutdown_pool()
        await api.close()
        logger.info("Worker shut down cleanly.")


def main() -> None:
    """Sync entry point (called by run_worker.py or ``python -m worker``)."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
