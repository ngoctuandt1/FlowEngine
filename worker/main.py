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
from worker.dispatcher import (
    dispatch_batch_l1_same_project,
    dispatch_batch_l2_siblings,
    dispatch_batch_l3_siblings,
    dispatch_job,
)
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
# When ALLOW_SAME_PROFILE_CONCURRENCY=1, the dispatcher clones the profile
# directory per Chrome launch (FLOW_USE_BASE_PROFILE=0 path) so the
# legacy "1 job per profile" cap no longer applies. Set this to 1
# alongside MAX_CONCURRENT_JOBS≥2 + FLOW_USE_BASE_PROFILE=0 to allow N
# concurrent Chromes on the same Google account / profile.
ALLOW_SAME_PROFILE_CONCURRENCY = os.getenv(
    "ALLOW_SAME_PROFILE_CONCURRENCY", "0"
).strip() in ("1", "true", "yes")

# FLOW_BATCH_DISPATCH=1 enables the batch path (PRD §2.2). Default OFF.
# When ON, the worker peeks for L1 t2v siblings after each L1 claim and
# fans out one Chrome over up to FLOW_BATCH_L1_MAX jobs.
FLOW_BATCH_DISPATCH = os.getenv("FLOW_BATCH_DISPATCH", "0").strip() == "1"
try:
    FLOW_BATCH_L1_MAX = max(1, int(os.getenv("FLOW_BATCH_L1_MAX", "3").strip() or 3))
except ValueError:
    FLOW_BATCH_L1_MAX = 3
try:
    FLOW_BATCH_L2_MAX = max(1, int(os.getenv("FLOW_BATCH_L2_MAX", "3").strip() or 3))
except ValueError:
    FLOW_BATCH_L2_MAX = 3
try:
    FLOW_BATCH_L3_MAX = max(1, int(os.getenv("FLOW_BATCH_L3_MAX", "3").strip() or 3))
except ValueError:
    FLOW_BATCH_L3_MAX = 3

L2_BATCH_OPS = {"extend-video", "camera-move", "insert-object", "remove-object"}

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

    async def _maybe_claim_l1_siblings(claimed: dict) -> list[dict]:
        """Peek + claim up to FLOW_BATCH_L1_MAX-1 sibling L1 t2v jobs.

        Only invoked when FLOW_BATCH_DISPATCH=1 and the just-claimed job
        is an L1 text-to-video. Returns the claimed sibling jobs in
        FIFO order. Empty list when there are none, or when the peek /
        claim API fails — caller falls back to single-job dispatch.
        """
        slots = max(0, FLOW_BATCH_L1_MAX - 1)
        if slots <= 0:
            return []
        try:
            peeked = await api.list_pending_l1_siblings(
                project_url=None,
                profile=claimed.get("profile") or None,
                limit=slots + 1,  # over-fetch so we can drop the just-claimed one
            )
        except Exception:
            logger.warning("list_pending_l1_siblings failed", exc_info=True)
            return []

        siblings: list[dict] = []
        for cand in peeked:
            cid = cand.get("id")
            if not cid or cid == claimed.get("id"):
                continue
            try:
                claimed_sibling = await api.claim_job_by_id(
                    cid, profile=claimed.get("profile") or None,
                )
            except Exception:
                logger.warning("claim_job_by_id failed for %s", cid, exc_info=True)
                continue
            if claimed_sibling is None:
                continue
            siblings.append(claimed_sibling)
            if len(siblings) >= slots:
                break
        return siblings

    async def _maybe_claim_l2_siblings(claimed: dict) -> list[dict]:
        """Peek + claim up to FLOW_BATCH_L2_MAX-1 sibling L2 jobs sharing
        the same `parent_job_id` and profile. Returns claimed siblings in
        FIFO order. Empty list on any peek/claim failure.
        """
        slots = max(0, FLOW_BATCH_L2_MAX - 1)
        parent_id = claimed.get("parent_job_id") or ""
        if slots <= 0 or not parent_id:
            return []
        try:
            peeked = await api.list_pending_l2_siblings(
                parent_job_id=parent_id,
                profile=claimed.get("profile") or None,
                limit=slots + 1,
            )
        except Exception:
            logger.warning("list_pending_l2_siblings failed", exc_info=True)
            return []

        siblings: list[dict] = []
        for cand in peeked:
            cid = cand.get("id")
            if not cid or cid == claimed.get("id"):
                continue
            if (cand.get("type") or "") not in L2_BATCH_OPS:
                continue
            try:
                claimed_sibling = await api.claim_job_by_id(
                    cid, profile=claimed.get("profile") or None,
                )
            except Exception:
                logger.warning("claim_job_by_id failed for %s", cid, exc_info=True)
                continue
            if claimed_sibling is None:
                continue
            siblings.append(claimed_sibling)
            if len(siblings) >= slots:
                break
        return siblings

    async def _maybe_claim_l3_siblings(claimed: dict) -> list[dict]:
        """Peek + claim up to FLOW_BATCH_L3_MAX-1 sibling L3+ jobs sharing
        the same direct ``parent_job_id`` and profile.
        """
        slots = max(0, FLOW_BATCH_L3_MAX - 1)
        parent_id = claimed.get("parent_job_id") or ""
        if slots <= 0 or not parent_id:
            return []
        try:
            peeked = await api.list_pending_l3_siblings(
                parent_job_id=parent_id,
                profile=claimed.get("profile") or None,
                limit=slots + 1,
            )
        except Exception:
            logger.warning("list_pending_l3_siblings failed", exc_info=True)
            return []

        siblings: list[dict] = []
        for cand in peeked:
            cid = cand.get("id")
            if not cid or cid == claimed.get("id"):
                continue
            if (cand.get("type") or "") not in L2_BATCH_OPS:
                continue
            try:
                claimed_sibling = await api.claim_job_by_id(
                    cid, profile=claimed.get("profile") or None,
                )
            except Exception:
                logger.warning("claim_job_by_id failed for %s", cid, exc_info=True)
                continue
            if claimed_sibling is None:
                continue
            siblings.append(claimed_sibling)
            if len(siblings) >= slots:
                break
        return siblings

    async def run_claimed_batch_l3(jobs: list[dict]) -> None:
        if len(jobs) == 1:
            await run_claimed_job(jobs[0])
            return
        ids = [j.get("id") for j in jobs]
        profile = jobs[0].get("profile") or ""
        logger.info("Dispatching L3 batch %s on profile %s", ids, profile)
        try:
            results = await dispatch_batch_l3_siblings(
                jobs, profile_mgr, project_lock,
            )
        except Exception as exc:
            logger.exception("L3 batch dispatch crashed for jobs %s", ids)
            results = [
                {"job_id": j.get("id"), "status": "failed", "error": str(exc)}
                for j in jobs
            ]
        for r in results:
            jid = r.pop("job_id", None) or r.get("id")
            if not jid:
                continue
            try:
                await api.update_job(jid, r)
                logger.info("L3 batch job %s -> %s", jid, r.get("status"))
            except Exception:
                logger.error("Failed to report L3 batch result for %s", jid, exc_info=True)

    async def run_claimed_batch_l2(jobs: list[dict]) -> None:
        if len(jobs) == 1:
            await run_claimed_job(jobs[0])
            return
        ids = [j.get("id") for j in jobs]
        profile = jobs[0].get("profile") or ""
        logger.info("Dispatching L2 batch %s on profile %s", ids, profile)
        try:
            results = await dispatch_batch_l2_siblings(
                jobs, profile_mgr, project_lock,
            )
        except Exception as exc:
            logger.exception("L2 batch dispatch crashed for jobs %s", ids)
            results = [
                {"job_id": j.get("id"), "status": "failed", "error": str(exc)}
                for j in jobs
            ]
        for r in results:
            jid = r.pop("job_id", None) or r.get("id")
            if not jid:
                continue
            try:
                await api.update_job(jid, r)
                logger.info("L2 batch job %s -> %s", jid, r.get("status"))
            except Exception:
                logger.error("Failed to report L2 batch result for %s", jid, exc_info=True)

    async def run_claimed_batch(jobs: list[dict]) -> None:
        if len(jobs) == 1:
            await run_claimed_job(jobs[0])
            return
        ids = [j.get("id") for j in jobs]
        profile = jobs[0].get("profile") or ""
        logger.info("Dispatching L1 batch %s on profile %s", ids, profile)
        try:
            results = await dispatch_batch_l1_same_project(
                jobs, profile_mgr, project_lock,
            )
        except Exception as exc:
            logger.exception("Batch dispatch crashed for jobs %s", ids)
            results = [
                {"job_id": j.get("id"), "status": "failed", "error": str(exc)}
                for j in jobs
            ]
        for r in results:
            jid = r.pop("job_id", None) or r.get("id")
            if not jid:
                continue
            try:
                await api.update_job(jid, r)
                logger.info("Batch job %s -> %s", jid, r.get("status"))
            except Exception:
                logger.error("Failed to report batch result for %s", jid, exc_info=True)

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
            if result.get("requeue"):
                # Burn-recovery success path: dispatcher signaled this job
                # should re-enter the queue on the freshly-warmed profile
                # rather than terminating as failed. Reset to pending and
                # clear claim metadata so claim_next_job picks it up again.
                await api.update_job(
                    job_id,
                    {
                        "status": "pending",
                        "worker_id": None,
                        "claimed_at": None,
                        "error": None,
                    },
                )
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

            # In same-profile concurrency mode the busy/available split no
            # longer maps 1:1 to Chrome instances — each dispatch clones
            # the profile dir to a temp path so multiple jobs can share a
            # single registered profile name. Feed the claim API the raw
            # profile list (busy or not) up to the remaining concurrency
            # budget; the per-project lock and claim SQL still enforce
            # safe ordering.
            if ALLOW_SAME_PROFILE_CONCURRENCY:
                available = list(profile_mgr.profiles.keys())
            else:
                available = profile_mgr.get_available()
            if not available or len(in_flight) >= max_concurrent:
                await wait_for_capacity()
                continue

            slots = max_concurrent - len(in_flight)
            if ALLOW_SAME_PROFILE_CONCURRENCY:
                # Repeat the available pool until we fill the remaining
                # slots — server claim_next_job picks one job per call;
                # the client fires up to `slots` claim requests below.
                claim_profiles = (available * slots)[:slots]
            else:
                claim_profiles = available[:slots]
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

            # FLOW_BATCH_DISPATCH=1: opportunistically claim L1 t2v siblings
            # so 1 Chrome handles up to FLOW_BATCH_L1_MAX jobs in a single
            # /project/{id} session. Default OFF preserves the legacy 1-1-1.
            siblings: list[dict] = []
            if (
                FLOW_BATCH_DISPATCH
                and job.get("job_level", 1) == 1
                and job.get("type") == "text-to-video"
                and (job.get("project_url") or "") == ""
            ):
                siblings = await _maybe_claim_l1_siblings(job)
                for sib in siblings:
                    sp = sib.get("profile") or ""
                    if sp:
                        profile_mgr.mark_busy(sp, sib.get("id", "?"))

            l2_siblings: list[dict] = []
            l3_siblings: list[dict] = []
            claimed_level = job.get("job_level", 1)
            if (
                FLOW_BATCH_DISPATCH
                and not siblings
                and claimed_level >= 2
                and (job.get("type") or "") in L2_BATCH_OPS
                and (job.get("parent_job_id") or "") != ""
            ):
                if claimed_level == 2:
                    l2_siblings = await _maybe_claim_l2_siblings(job)
                    sib_pool = l2_siblings
                else:  # L3+: siblings disambiguated by direct parent_job_id
                    l3_siblings = await _maybe_claim_l3_siblings(job)
                    sib_pool = l3_siblings
                for sib in sib_pool:
                    sp = sib.get("profile") or ""
                    if sp:
                        profile_mgr.mark_busy(sp, sib.get("id", "?"))

            if siblings:
                batch_jobs = [job, *siblings]
                task = asyncio.create_task(run_claimed_batch(batch_jobs))
            elif l2_siblings:
                batch_jobs = [job, *l2_siblings]
                task = asyncio.create_task(run_claimed_batch_l2(batch_jobs))
            elif l3_siblings:
                batch_jobs = [job, *l3_siblings]
                task = asyncio.create_task(run_claimed_batch_l3(batch_jobs))
            else:
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
