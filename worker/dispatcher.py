"""Job dispatcher -- routes job type to the correct handler.

All 5 operations use real Flow automation via Playwright.
"""

import asyncio
import logging
import os
from typing import Callable, Coroutine

from flow.retry import with_retry, is_transient
from worker.profile_manager import ProfileManager
from worker.project_lock import ProjectLock

logger = logging.getLogger(__name__)

PROFILE_BASE_DIR = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
DOWNLOAD_DIR = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")


# ======================================================================
# Shared: create FlowClient context for a job
# ======================================================================

def _make_client(profile: str):
    """Create a FlowClient for the given profile (use as async context manager)."""
    from flow.client import FlowClient
    return FlowClient(
        profile_name=profile,
        profile_base_dir=PROFILE_BASE_DIR,
        download_dir=DOWNLOAD_DIR,
    )


def _kill_chrome_for_profile(profile: str):
    """Kill any Chrome processes using this profile (Windows wmic).

    Called between AIgglog login and job retry to ensure profile is unlocked
    and cookies are flushed to disk.
    """
    import subprocess
    from pathlib import Path

    profile_dir = os.path.join(PROFILE_BASE_DIR, profile)

    try:
        subprocess.run(
            ["wmic", "process", "where",
             f"commandline like '%{profile}%' and name='chrome.exe'",
             "call", "terminate"],
            capture_output=True, timeout=10,
        )
    except Exception as e:
        logger.debug("Chrome kill for %s: %s", profile, e)

    # Remove lock files
    for lock_name in ("SingletonLock", "SingletonCookie"):
        lock = Path(profile_dir) / lock_name
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass

    logger.info("Chrome cleanup done for profile %s", profile)


# ======================================================================
# Handlers — all use real Flow automation
# ======================================================================

async def handle_text_to_video(job: dict) -> dict:
    """Text-to-video: create new project, generate video from prompt."""
    from flow.operations.generate import text_to_video

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for text-to-video job")

    logger.info(
        "text-to-video START | prompt=%r model=%s profile=%s",
        (job.get("prompt", ""))[:60], job.get("model"), profile,
    )

    async with _make_client(profile) as client:
        result = await text_to_video(
            client,
            prompt=job.get("prompt", ""),
            model=job.get("model", "veo-3.1-fast-lp"),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
            free_mode=True,
        )

    logger.info("text-to-video DONE | files=%d media_id=%s",
                len(result.get("output_files", [])), result.get("media_id"))
    return result


async def handle_extend(job: dict) -> dict:
    """Extend-video: navigate to edit URL, extend with prompt + LP model."""
    from flow.operations.extend import extend_video

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for extend-video job")

    logger.info(
        "extend-video START | edit_url=%s profile=%s",
        (job.get("edit_url") or job.get("project_url", ""))[:80], profile,
    )

    async with _make_client(profile) as client:
        result = await extend_video(
            client,
            job=job,
            prompt=job.get("prompt", ""),
            model=job.get("model", "veo-3.1-fast-lp"),
            free_mode=True,
        )

    logger.info("extend-video DONE | files=%d media_id=%s",
                len(result.get("output_files", [])), result.get("media_id"))
    return result


async def handle_insert(job: dict) -> dict:
    """Insert-object: navigate to edit URL, draw bbox, type prompt."""
    from flow.operations.insert import insert_object

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for insert-object job")

    logger.info(
        "insert-object START | bbox=%s prompt=%r profile=%s",
        job.get("bbox"), (job.get("prompt", ""))[:40], profile,
    )

    async with _make_client(profile) as client:
        result = await insert_object(
            client,
            job=job,
            prompt=job.get("prompt", ""),
            bbox=job.get("bbox"),
        )

    logger.info("insert-object DONE | files=%d media_id=%s",
                len(result.get("output_files", [])), result.get("media_id"))
    return result


async def handle_remove(job: dict) -> dict:
    """Remove-object: navigate to edit URL, draw bbox, no prompt."""
    from flow.operations.remove import remove_object

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for remove-object job")

    logger.info(
        "remove-object START | bbox=%s profile=%s",
        job.get("bbox"), profile,
    )

    async with _make_client(profile) as client:
        result = await remove_object(
            client,
            job=job,
            bbox=job.get("bbox"),
        )

    logger.info("remove-object DONE | files=%d media_id=%s",
                len(result.get("output_files", [])), result.get("media_id"))
    return result


async def handle_camera(job: dict) -> dict:
    """Camera-move: navigate to edit URL, pick camera preset."""
    from flow.operations.camera import camera_move

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for camera-move job")

    logger.info(
        "camera-move START | direction=%s profile=%s",
        job.get("direction"), profile,
    )

    async with _make_client(profile) as client:
        result = await camera_move(
            client,
            job=job,
            direction=job.get("direction", "Dolly in"),
        )

    logger.info("camera-move DONE | files=%d media_id=%s",
                len(result.get("output_files", [])), result.get("media_id"))
    return result


# ======================================================================
# Handler registry
# ======================================================================

HANDLER_MAP: dict[str, Callable[[dict], Coroutine]] = {
    "text-to-video": handle_text_to_video,
    "extend-video": handle_extend,
    "insert-object": handle_insert,
    "remove-object": handle_remove,
    "camera-move": handle_camera,
}


# ======================================================================
# Dispatcher
# ======================================================================

async def dispatch_job(
    job: dict,
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> dict:
    """Route a claimed job to the correct handler.

    Manages profile busy/available state and project lock
    acquisition/release around the handler call.

    Returns a result dict suitable for ``remote_api.update_job()``.
    """
    job_id: str = job["id"]
    job_type: str = job["type"]
    profile: str = job.get("profile", "")
    project_url: str = job.get("project_url") or ""
    job_level: int = job.get("job_level", 1)

    handler = HANDLER_MAP.get(job_type)
    if handler is None:
        logger.error("No handler for job type %r (job %s)", job_type, job_id)
        return {
            "status": "failed",
            "error": f"Unknown job type: {job_type}",
        }

    # --- Pre-dispatch bookkeeping ---
    if profile:
        profile_manager.mark_busy(profile, job_id)

    # Level-2+ jobs operate on an existing project -> acquire lock
    needs_lock = job_level >= 2 and project_url
    if needs_lock:
        if not project_lock.acquire(project_url, job_id):
            if profile:
                profile_manager.mark_available(profile)
            return {
                "status": "failed",
                "error": f"Could not acquire project lock for {project_url}",
            }

    try:
        logger.info(
            "Dispatching job %s [%s] on profile %s", job_id, job_type, profile
        )
        result = await with_retry(handler, job, max_retries=2, job_id=job_id)

        # Attach common fields to result
        result["status"] = "completed"
        result.setdefault("profile", profile)
        return result

    except Exception as exc:
        # --- Auto-login via AIgglog when session expired ---
        from flow.login import NeedAutoLogin, run_aigglog_sync
        if isinstance(exc, NeedAutoLogin) and profile:
            logger.warning(
                "Job %s needs auto-login for profile %s — running AIgglog.py",
                job_id, profile,
            )
            login_ok = await asyncio.to_thread(run_aigglog_sync, profile)
            if login_ok:
                # Kill any Chrome zombies left by AIgglog before retry.
                # AIgglog may not fully close Chrome → profile still locked.
                logger.info("AIgglog login OK — cleaning Chrome before retry")
                await asyncio.to_thread(_kill_chrome_for_profile, profile)
                await asyncio.sleep(3)  # Let Chrome fully exit and flush cookies

                logger.info("Retrying job %s after AIgglog login", job_id)
                try:
                    result = await with_retry(handler, job, max_retries=1, job_id=job_id)
                    result["status"] = "completed"
                    result.setdefault("profile", profile)
                    return result
                except Exception as retry_exc:
                    logger.exception("Retry after AIgglog failed for job %s", job_id)
                    return {
                        "status": "failed",
                        "error": f"Failed after AIgglog login: {retry_exc}",
                    }
            else:
                return {
                    "status": "failed",
                    "error": "AIgglog auto-login failed — manual login needed",
                }

        logger.exception("Handler %s failed for job %s", job_type, job_id)
        return {
            "status": "failed",
            "error": str(exc),
        }

    finally:
        # --- Post-dispatch cleanup ---
        if needs_lock:
            project_lock.release(project_url)
        if profile:
            profile_manager.mark_available(profile)
