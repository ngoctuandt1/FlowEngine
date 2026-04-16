"""Job dispatcher -- routes job type to the correct handler.

Each handler is currently a STUB that logs what it would do,
sleeps briefly to simulate work, and returns a mock result dict.
Real handlers will call into ``flow.operations.*`` once that
layer is built.
"""

import asyncio
import logging
from typing import Callable, Coroutine

from worker.profile_manager import ProfileManager
from worker.project_lock import ProjectLock

logger = logging.getLogger(__name__)

SIMULATE_WORK_SEC = 2.0


# ======================================================================
# Stub handlers
# ======================================================================

async def handle_text_to_video(job: dict) -> dict:
    """Stub: text-to-video generation."""
    logger.info(
        "[STUB] text-to-video | prompt=%r model=%s profile=%s",
        job.get("prompt", ""), job.get("model"), job.get("profile"),
    )
    await asyncio.sleep(SIMULATE_WORK_SEC)
    return {
        "project_url": "https://flow.example.com/project/stub-001",
        "media_id": "stub-media-t2v-001",
        "edit_url": "https://flow.example.com/project/stub-001/edit/stub-media-t2v-001",
        "output_files": ["/output/stub_t2v.mp4"],
        "generation_id": "gen-stub-t2v-001",
    }


async def handle_extend(job: dict) -> dict:
    """Stub: extend-video."""
    logger.info(
        "[STUB] extend-video | edit_url=%s profile=%s",
        job.get("edit_url") or job.get("project_url"), job.get("profile"),
    )
    await asyncio.sleep(SIMULATE_WORK_SEC)
    return {
        "project_url": job.get("project_url", ""),
        "media_id": job.get("media_id", "stub-media-ext-001"),
        "edit_url": job.get("edit_url", ""),
        "output_files": ["/output/stub_extend.mp4"],
        "generation_id": "gen-stub-ext-001",
    }


async def handle_insert(job: dict) -> dict:
    """Stub: insert-object."""
    logger.info(
        "[STUB] insert-object | bbox=%s prompt=%r profile=%s",
        job.get("bbox"), job.get("prompt", ""), job.get("profile"),
    )
    await asyncio.sleep(SIMULATE_WORK_SEC)
    return {
        "project_url": job.get("project_url", ""),
        "media_id": job.get("media_id", "stub-media-ins-001"),
        "edit_url": job.get("edit_url", ""),
        "output_files": ["/output/stub_insert.mp4"],
        "generation_id": "gen-stub-ins-001",
    }


async def handle_remove(job: dict) -> dict:
    """Stub: remove-object."""
    logger.info(
        "[STUB] remove-object | bbox=%s profile=%s",
        job.get("bbox"), job.get("profile"),
    )
    await asyncio.sleep(SIMULATE_WORK_SEC)
    return {
        "project_url": job.get("project_url", ""),
        "media_id": job.get("media_id", "stub-media-rm-001"),
        "edit_url": job.get("edit_url", ""),
        "output_files": ["/output/stub_remove.mp4"],
        "generation_id": "gen-stub-rm-001",
    }


async def handle_camera(job: dict) -> dict:
    """Stub: camera-move."""
    logger.info(
        "[STUB] camera-move | direction=%s profile=%s",
        job.get("direction"), job.get("profile"),
    )
    await asyncio.sleep(SIMULATE_WORK_SEC)
    return {
        "project_url": job.get("project_url", ""),
        "media_id": job.get("media_id", "stub-media-cam-001"),
        "edit_url": job.get("edit_url", ""),
        "output_files": ["/output/stub_camera.mp4"],
        "generation_id": "gen-stub-cam-001",
    }


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
            # Should not normally happen because the server already checks,
            # but defend against it anyway.
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
        result = await handler(job)

        # Attach common fields to result
        result["status"] = "completed"
        result.setdefault("profile", profile)
        return result

    except Exception as exc:
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
