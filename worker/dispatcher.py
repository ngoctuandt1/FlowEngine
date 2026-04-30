"""Job dispatcher -- routes job type to the correct handler."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Coroutine

from flow.recaptcha import RecaptchaError
from flow.retry import with_retry
from worker.browser_pool import get_pool
from worker.profile_manager import ProfileManager
from worker.project_lock import ProjectLock

logger = logging.getLogger(__name__)


def _resolve_repo_root() -> Path:
    """Return the shared repo root, including when running from a git worktree."""
    for base in Path(__file__).resolve().parents:
        git_marker = base / ".git"
        if git_marker.is_dir():
            return base
        if not git_marker.is_file():
            continue

        raw = git_marker.read_text(encoding="utf-8").strip()
        if not raw.startswith("gitdir:"):
            continue

        git_dir = Path(raw[7:].strip())
        if not git_dir.is_absolute():
            git_dir = (base / git_dir).resolve()

        if git_dir.parent.name == "worktrees":
            return git_dir.parents[2]
        return base

    return Path.cwd().resolve()


def _resolve_data_dir(env_var: str, default_name: str) -> Path:
    """Resolve data dirs consistently across normal checkouts and git worktrees."""
    raw_value = (os.environ.get(env_var) or "").strip()
    if raw_value:
        return Path(raw_value).expanduser().resolve()
    return (_resolve_repo_root() / default_name).resolve()


PROFILE_BASE_DIR = os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
DOWNLOAD_DIR = str(_resolve_data_dir("FLOW_DOWNLOAD_DIR", "downloads"))
UPLOAD_DIR = _resolve_data_dir("FLOW_UPLOAD_DIR", "uploads")


def _auto_replace_profiles_enabled() -> bool:
    value = (os.environ.get("FLOW_AUTO_REPLACE_PROFILES") or "1").strip()
    return value != "0"


def _profile_base_dir() -> Path:
    return Path(
        os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
    ).expanduser().resolve()


def _credentials_file_path() -> Path:
    return Path(
        os.environ.get("FLOW_PROFILE_LIST_FILE", "profiles_ultra.txt")
    ).expanduser().resolve()


# ======================================================================
# Shared: FlowClient lifecycle (pool-aware)
# ======================================================================

def _make_client(profile: str):
    """Create a fresh FlowClient for the given profile (ephemeral path).

    Use :func:`_client_lease` instead — it transparently routes through
    :class:`~worker.browser_pool.BrowserPool` when ``FLOW_BROWSER_POOL=1``.
    """
    from flow.client import FlowClient
    return FlowClient(
        profile_name=profile,
        profile_base_dir=PROFILE_BASE_DIR,
        download_dir=DOWNLOAD_DIR,
    )


@asynccontextmanager
async def _client_lease(profile: str):
    """Yield a ready FlowClient, pooled or ephemeral depending on env.

    When ``FLOW_BROWSER_POOL=1`` the same Chrome instance is reused
    across jobs for *profile* (buffers cleared between jobs). Otherwise
    a fresh FlowClient is started and stopped per call — the original
    behaviour. Handlers need no further awareness of the pool.
    """
    pool = get_pool()
    if pool is None:
        client = _make_client(profile)
        async with client:
            yield client
        return

    async with pool.lease(profile) as client:
        yield client


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


def _resolve_upload_path(path_value: str | None) -> str | None:
    """Resolve a server-relative uploads path to a local file path."""
    if not path_value:
        return None

    raw_text = str(path_value).strip()
    if not raw_text:
        return None

    raw_path = Path(raw_text).expanduser()
    if raw_path.is_absolute():
        resolved = raw_path.resolve()
    else:
        parts = list(raw_path.parts)
        if parts and parts[0].lower() == "uploads":
            parts = parts[1:]
        resolved = UPLOAD_DIR.joinpath(*parts).resolve()

    if not resolved.is_relative_to(UPLOAD_DIR):
        raise RuntimeError(
            f"Upload path escapes FLOW_UPLOAD_DIR: {path_value} (base={UPLOAD_DIR})"
        )

    return str(resolved)


def _resolve_upload_paths(path_values: list[str] | None) -> list[str]:
    """Resolve a list of server-relative uploads paths to local file paths."""
    if not path_values:
        return []
    return [
        resolved
        for path_value in path_values
        if (resolved := _resolve_upload_path(path_value)) is not None
    ]


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

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
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


async def handle_frames_to_video(job: dict) -> dict:
    """Frames-to-video: create new project from a start frame and optional end frame."""
    from flow.operations.frames_to_video import frames_to_video

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for frames-to-video job")

    logger.info(
        "frames-to-video START | start=%s end=%s model=%s profile=%s",
        job.get("start_image_path"),
        job.get("end_image_path"),
        job.get("model"),
        profile,
    )

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
        result = await frames_to_video(
            client,
            prompt=job.get("prompt", ""),
            start_image_path=_resolve_upload_path(job.get("start_image_path")),
            end_image_path=_resolve_upload_path(job.get("end_image_path")),
            model=job.get("model", "veo-3.1-fast-lp"),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
            free_mode=True,
        )

    logger.info(
        "frames-to-video DONE | files=%d media_id=%s",
        len(result.get("output_files", [])),
        result.get("media_id"),
    )
    return result


async def handle_text_to_image(job: dict) -> dict:
    """Text-to-image: create a new image project with optional reference image."""
    from flow.operations.image import text_to_image

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for text-to-image job")

    logger.info(
        "text-to-image START | ref=%s model=%s profile=%s",
        job.get("ref_image_path"),
        job.get("model"),
        profile,
    )

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
        result = await text_to_image(
            client,
            prompt=job.get("prompt", ""),
            ref_image_path=_resolve_upload_path(job.get("ref_image_path")),
            model=job.get("model", "nano-banana-pro"),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
        )

    logger.info(
        "text-to-image DONE | files=%d media_id=%s",
        len(result.get("output_files", [])),
        result.get("media_id"),
    )
    return result


async def handle_ingredients_to_video(job: dict) -> dict:
    """Ingredients-to-video: create a new video with one or more reference images."""
    from flow.operations.ingredients import ingredients_to_video

    profile = job.get("profile", "")
    if not profile:
        raise RuntimeError("No profile assigned for ingredients-to-video job")

    ingredient_image_paths = _resolve_upload_paths(job.get("ingredient_image_paths"))
    if not ingredient_image_paths:
        raise RuntimeError("ingredients-to-video requires at least one ingredient image")

    logger.info(
        "ingredients-to-video START | refs=%d model=%s profile=%s",
        len(ingredient_image_paths),
        job.get("model"),
        profile,
    )

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
        result = await ingredients_to_video(
            client,
            prompt=job.get("prompt", ""),
            ingredient_image_paths=ingredient_image_paths,
            model=job.get("model", "veo-3.1-fast-lp"),
            aspect_ratio=job.get("aspect_ratio", "16:9"),
            free_mode=True,
        )

    logger.info(
        "ingredients-to-video DONE | files=%d media_id=%s",
        len(result.get("output_files", [])),
        result.get("media_id"),
    )
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

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
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

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
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

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
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

    async with _client_lease(profile) as client:
        client._job_id = job["id"]
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
    "frames-to-video": handle_frames_to_video,
    "ingredients-to-video": handle_ingredients_to_video,
    "text-to-image": handle_text_to_image,
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
    *,
    manage_profile: bool = True,
) -> dict:
    """Route a claimed job to the correct handler.

    Manages project lock acquisition/release around the handler call.
    By default, it also manages profile busy/available state for direct callers.

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
    if manage_profile and profile:
        profile_manager.mark_busy(profile, job_id)

    # Level-2+ jobs operate on an existing project -> acquire lock
    needs_lock = job_level >= 2 and project_url
    if needs_lock:
        if not project_lock.acquire(project_url, job_id):
            if manage_profile and profile:
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

        if result.get("status") == "failed":
            return result

        # Attach common fields to successful results.
        result["status"] = "completed"
        result.setdefault("profile", profile)
        return result

    except RecaptchaError as exc:
        old_profile = profile
        kind = getattr(exc, "kind", None) or "unknown"
        url = getattr(exc, "url", None)
        error_message = f"recaptcha_{kind}_burned_{old_profile}"
        blocked_url = str(url or "")[:160]
        logger.error(
            "Job %s hit reCAPTCHA kind=%s profile=%s url=%s",
            job_id,
            kind,
            old_profile,
            blocked_url or "<unknown>",
        )
        if old_profile and _auto_replace_profiles_enabled():
            swap_failed_logged = False
            try:
                from worker.profile_swapper import ProfileSwapper

                swapper = ProfileSwapper(
                    profile_base_dir=_profile_base_dir(),
                    credentials_file=_credentials_file_path(),
                )
                new_profile = await asyncio.to_thread(
                    swapper.swap_burned,
                    old_profile,
                )
            except Exception:
                logger.exception(
                    "Profile burned, swap failed; pool exhausted or warm failed"
                )
                swap_failed_logged = True
                new_profile = None

            if new_profile:
                profile_manager.replace_profile(old_profile, new_profile)
                logger.info(
                    "Profile burned, auto-replaced: %s -> %s",
                    old_profile,
                    new_profile,
                )
                if manage_profile:
                    profile = ""
            else:
                if not swap_failed_logged:
                    logger.error(
                        "Profile burned, swap failed; pool exhausted or warm failed"
                    )
                profile_manager.remove_profile(old_profile)
                if manage_profile:
                    profile = ""
        elif old_profile:
            profile_manager.remove_profile(old_profile)
            if manage_profile:
                profile = ""
            logger.warning(
                "FLOW_AUTO_REPLACE_PROFILES=0; profile %s burned and removed from pool; manual recovery needed",
                old_profile,
            )
        return {
            "status": "failed",
            "error": error_message,
            "error_message": error_message,
        }

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
                    if result.get("status") == "failed":
                        return result
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
        if manage_profile and profile:
            profile_manager.mark_available(profile)
