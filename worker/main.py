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

from dotenv import load_dotenv

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

    logger.info(
        "Starting claim loop  server=%s  worker=%s  profiles=%s  poll=%ds",
        SERVER_URL, WORKER_ID, WORKER_PROFILES, POLL_INTERVAL_SEC,
    )

    while not _shutdown.is_set():
        # --- Heartbeat ---
        now = datetime.now(UTC)
        if now - last_heartbeat >= heartbeat_delta:
            try:
                await api.heartbeat()
                last_heartbeat = now
            except Exception:
                logger.warning("Heartbeat failed", exc_info=True)

        # --- Check available profiles ---
        available = profile_mgr.get_available()
        if not available:
            logger.debug("No profiles available, sleeping %ds", POLL_INTERVAL_SEC)
            try:
                await asyncio.wait_for(
                    _shutdown.wait(), timeout=POLL_INTERVAL_SEC
                )
            except asyncio.TimeoutError:
                pass
            continue

        # --- Claim a job ---
        try:
            job = await api.claim_job(available)
        except Exception:
            logger.warning("Claim request failed", exc_info=True)
            job = None

        if job is None:
            try:
                await asyncio.wait_for(
                    _shutdown.wait(), timeout=POLL_INTERVAL_SEC
                )
            except asyncio.TimeoutError:
                pass
            continue

        job_id = job.get("id", "?")
        job_type = job.get("type", "?")
        job_profile = job.get("profile", "?")
        logger.info(
            "Claimed job %s [%s] profile=%s", job_id, job_type, job_profile,
        )

        # --- Dispatch ---
        result = await dispatch_job(job, profile_mgr, project_lock)

        # --- Report result ---
        try:
            await api.update_job(job_id, result)
            logger.info(
                "Job %s result sent -> %s", job_id, result.get("status"),
            )
        except Exception:
            logger.error(
                "Failed to report result for job %s", job_id, exc_info=True,
            )


async def run() -> None:
    """Initialise components and run the claim loop."""

    setup_logging()

    logger.info("=" * 60)
    logger.info("FlowEngine Worker  %s", WORKER_ID)
    logger.info("Server:   %s", SERVER_URL)
    logger.info("Profiles: %s", ", ".join(WORKER_PROFILES))
    logger.info("Poll:     %ds", POLL_INTERVAL_SEC)
    logger.info("=" * 60)

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

    try:
        await claim_loop(api, profile_mgr, project_lock)
    finally:
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
