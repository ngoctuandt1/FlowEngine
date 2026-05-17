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
from worker.dispatcher import dispatch_batch, dispatch_job
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
# Opt-in batch-claim wire: when set, the worker asks the server for up to
# ``MAX_CONCURRENT_JOBS`` jobs per /claim round-trip and routes any
# multi-job result through dispatch_batch (multi-tab orchestrator).
# Default off — single-claim path is the live-verified baseline.
FLOW_CLAIM_BATCH_ENABLED = os.getenv(
    "FLOW_CLAIM_BATCH_ENABLED", "0"
).strip().lower() in ("1", "true", "yes")
# When ALLOW_SAME_PROFILE_CONCURRENCY=1, the dispatcher clones the profile
# directory per Chrome launch (FLOW_USE_BASE_PROFILE=0 path) so the
# legacy "1 job per profile" cap no longer applies. Set this to 1
# alongside MAX_CONCURRENT_JOBS≥2 + FLOW_USE_BASE_PROFILE=0 to allow N
# concurrent Chromes on the same Google account / profile.
ALLOW_SAME_PROFILE_CONCURRENCY = os.getenv(
    "ALLOW_SAME_PROFILE_CONCURRENCY", "0"
).strip().lower() in ("1", "true", "yes")
FLOW_USE_BASE_PROFILE = os.getenv("FLOW_USE_BASE_PROFILE", "0").strip().lower() in ("1", "true", "yes")
FLOW_BROWSER_POOL = os.getenv("FLOW_BROWSER_POOL", "0").strip().lower() in ("1", "true", "yes")
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

    Defensive against OSError: symlink loops, permission denied, and
    offline network drives all degrade to ``False`` so preflight surfaces
    a readable diagnostic rather than crashing.
    """
    try:
        if not profile_dir.is_dir():
            return False
        # Modern Chrome stores the active profile in <user-data-dir>/Default/
        candidates = [
            profile_dir / "Default" / "Cookies",
            profile_dir / "Default" / "Network" / "Cookies",
        ]
        for p in candidates:
            try:
                if p.is_file() and p.stat().st_size > 0:
                    return True
            except OSError:
                continue
        return False
    except OSError as exc:
        logger.warning("profile-warm check failed for %s: %s", profile_dir, exc)
        return False


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
    # Track in-flight work at JOB granularity, not asyncio.Task granularity:
    # a single batch task may carry N jobs, so ``len(in_flight)`` undercounts
    # capacity and lets the loop over-claim past MAX_CONCURRENT_JOBS. Each
    # entry maps task -> number of jobs that task is dispatching.
    in_flight_job_count: dict[asyncio.Task[None], int] = {}
    # Profiles undergoing burn/wipe-rewarm: excluded from same-profile claims
    # until recovery completes, preventing a new job from cloning a half-dead dir.
    _draining: set[str] = set()
    _l1_rotation_offset = 0
    # Default cap = MAX_CONCURRENT_JOBS bounded by profile count (legacy:
    # one Chrome per profile dir). With ALLOW_SAME_PROFILE_CONCURRENCY,
    # the cap is MAX_CONCURRENT_JOBS regardless of profile count — the
    # dispatcher is expected to clone each profile to a per-job temp dir
    # (FLOW_USE_BASE_PROFILE=0) so multiple Chromes don't share a
    # single user-data-dir.
    if ALLOW_SAME_PROFILE_CONCURRENCY:
        max_concurrent = max(1, MAX_CONCURRENT_JOBS)
    else:
        max_concurrent = max(
            1,
            min(len(profile_mgr.profiles), MAX_CONCURRENT_JOBS),
        )

    logger.info(
        "Starting claim loop  server=%s  worker=%s  profiles=%s  poll=%ds  max=%d",
        SERVER_URL, WORKER_ID, WORKER_PROFILES, POLL_INTERVAL_SEC, max_concurrent,
    )

    async def report_result(job: dict, result: dict) -> None:
        job_id = job.get("id", "?")
        profile = job.get("profile", "")
        try:
            if result.get("requeue"):
                # Burn-recovery success path: block the profile during the API
                # call so the claim loop cannot re-acquire it mid-update.
                # Must discard AFTER the await to avoid a permanent-draining
                # deadlock: wipe-rewarm keeps the same profile name in
                # profile_mgr, so without the discard below the claim loop
                # would exclude this profile forever.
                if profile:
                    _draining.add(profile)
                try:
                    await api.update_job(
                        job_id,
                        {
                            "status": "pending",
                            "worker_id": None,
                            "claimed_at": None,
                            "error": None,
                        },
                    )
                finally:
                    if profile:
                        _draining.discard(profile)
                logger.info(
                    "Job %s requeued after burn-recovery: %s",
                    job_id,
                    result.get("error_message", ""),
                )
            else:
                await api.update_job(job_id, result)
                logger.info("Job %s result sent -> %s", job_id, result.get("status"))
        except Exception:
            logger.error("Failed to report result for job %s", job_id, exc_info=True)

    async def run_claimed_job(job: dict) -> None:
        """Single-job path: dispatch one job, release its profile, report."""
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
                _draining.discard(profile)

        await report_result(job, result)

    async def run_claimed_batch(jobs: list[dict]) -> None:
        """Batch path: dispatch a server-claimed batch via ``dispatch_batch``.

        ``dispatch_batch`` returns one result dict per input job (in order),
        each enriched with ``job_id``. Profile release is handled inside
        the batch dispatchers (multitab/L1-fresh) so we only mirror the
        legacy single-path mark_available for jobs whose profile is still
        present on the returned dict.
        """
        job_ids = [j.get("id") for j in jobs]
        try:
            results = await dispatch_batch(jobs, profile_mgr, project_lock)
        except Exception as exc:
            logger.exception("Batch dispatch crashed for jobs %s", job_ids)
            results = [
                {"status": "failed", "error": str(exc), "job_id": j.get("id")}
                for j in jobs
            ]
        finally:
            # Defensive: ensure every claimed profile is released even if a
            # batch dispatcher raised before its own cleanup ran.
            for j in jobs:
                profile = j.get("profile", "")
                if profile:
                    profile_mgr.mark_available(profile)
                    _draining.discard(profile)

        # Pair results with input jobs by job_id (dispatch_batch preserves
        # order, but match defensively in case a future dispatcher reorders).
        by_id = {r.get("job_id"): r for r in results if r.get("job_id")}
        for job in jobs:
            jid = job.get("id")
            result = by_id.get(jid) or {
                "status": "failed",
                "error": "batch dispatch returned no result",
            }
            await report_result(job, result)

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

    def _jobs_in_flight() -> int:
        return sum(in_flight_job_count.values())

    def _purge_done() -> None:
        done_tasks = {task for task in in_flight if task.done()}
        for task in done_tasks:
            in_flight.discard(task)
            in_flight_job_count.pop(task, None)

    try:
        while not _shutdown.is_set():
            _purge_done()

            now = datetime.now(UTC)
            if now - last_heartbeat >= heartbeat_delta:
                try:
                    await api.heartbeat()
                    last_heartbeat = now
                except Exception:
                    logger.warning("Heartbeat failed", exc_info=True)

            # In same-profile concurrency mode the busy/available split no
            # longer maps 1:1 to Chrome instances — each dispatch clones
            # the profile dir to a temp path so multiple jobs can share a
            # single registered profile name. Feed the claim API the raw
            # profile list (busy or not) up to the remaining concurrency
            # budget; the per-project lock and claim SQL still enforce
            # safe ordering.
            if ALLOW_SAME_PROFILE_CONCURRENCY:
                # Exclude profiles mid-burn/wipe so we don't clone a half-dead dir.
                all_profiles = [p for p in profile_mgr.profiles.keys() if p not in _draining]
                # Rotate order each iteration for L1 load balancing across profiles.
                if all_profiles:
                    _l1_rotation_offset = (_l1_rotation_offset + 1) % len(all_profiles)
                    available = all_profiles[_l1_rotation_offset:] + all_profiles[:_l1_rotation_offset]
                else:
                    available = []
            else:
                available = profile_mgr.get_available()
            jobs_active = _jobs_in_flight()
            if not available or jobs_active >= max_concurrent:
                await wait_for_capacity()
                continue

            slots = max_concurrent - jobs_active
            if ALLOW_SAME_PROFILE_CONCURRENCY:
                # Repeat the rotated pool until we fill the remaining slots.
                claim_profiles = (available * slots)[:slots]
            else:
                claim_profiles = available[:slots]
            # Opt-in batch claim: ask the server for up to ``slots`` jobs in
            # one round-trip. Only activates when explicitly enabled AND
            # there is room for more than one job — otherwise the single-
            # claim wire (live-verified baseline) is used.
            use_batch = FLOW_CLAIM_BATCH_ENABLED and slots > 1
            claimed_jobs: list[dict] = []
            try:
                if use_batch:
                    claimed_jobs = await api.claim_batch(claim_profiles, batch_size=slots)
                else:
                    single = await api.claim_job(claim_profiles)
                    if single is not None:
                        claimed_jobs = [single]
            except Exception:
                logger.warning("Claim request failed", exc_info=True)
                claimed_jobs = []

            if not claimed_jobs:
                await wait_for_capacity()
                continue

            for j in claimed_jobs:
                jid = j.get("id", "?")
                jtype = j.get("type", "?")
                jprofile = j.get("profile", "")
                logger.info("Claimed job %s [%s] profile=%s", jid, jtype, jprofile)
                if jprofile:
                    profile_mgr.mark_busy(jprofile, jid)

            if len(claimed_jobs) > 1:
                task = asyncio.create_task(run_claimed_batch(claimed_jobs))
            else:
                task = asyncio.create_task(run_claimed_job(claimed_jobs[0]))
            in_flight.add(task)
            # Record job-granularity weight so capacity accounting reflects
            # actual concurrent jobs, not just outstanding asyncio tasks.
            in_flight_job_count[task] = len(claimed_jobs)

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

    # Validate ALLOW_SAME_PROFILE_CONCURRENCY mode compatibility.
    if ALLOW_SAME_PROFILE_CONCURRENCY:
        if FLOW_USE_BASE_PROFILE:
            logger.critical(
                "ALLOW_SAME_PROFILE_CONCURRENCY=1 is incompatible with "
                "FLOW_USE_BASE_PROFILE=1: multiple Chromes would share the same "
                "--user-data-dir and corrupt each other's session. "
                "Set FLOW_USE_BASE_PROFILE=0 or disable same-profile concurrency."
            )
            sys.exit(1)
        if FLOW_BROWSER_POOL:
            logger.critical(
                "ALLOW_SAME_PROFILE_CONCURRENCY=1 is incompatible with "
                "FLOW_BROWSER_POOL=1: the pool serialises one client per profile, "
                "negating same-profile concurrency. Disable one or the other."
            )
            sys.exit(1)

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
