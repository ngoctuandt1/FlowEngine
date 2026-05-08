"""Job dispatcher -- routes job type to the correct handler."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable, Coroutine

from flow.operations._base import LeafLockoutError
from flow.recaptcha import RecaptchaError
from flow.retry import with_retry
from profile_list import configured_profile_list_file
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
    return configured_profile_list_file()


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
    """Kill any Chrome processes using this profile (cross-platform).

    Called between AIgglog login and job retry to ensure profile is unlocked
    and cookies are flushed to disk.

    On Windows uses ``wmic`` to match by command-line; on Linux/macOS uses
    ``pkill -f`` against the profile path so only chrome processes whose
    ``--user-data-dir`` references this profile are terminated (matches the
    selective-kill rule from memory ``feedback_chrome_kill_selective.md``).
    """
    import platform
    import subprocess
    from pathlib import Path

    profile_dir = os.path.join(PROFILE_BASE_DIR, profile)

    try:
        if platform.system() == "Windows":
            subprocess.run(
                ["wmic", "process", "where",
                 f"commandline like '%{profile}%' and name='chrome.exe'",
                 "call", "terminate"],
                capture_output=True, timeout=10,
            )
        else:
            import re as _re
            resolved = Path(profile_dir).resolve()
            allowed_root = Path(PROFILE_BASE_DIR).resolve()
            if not resolved.is_relative_to(allowed_root):
                logger.warning(
                    "Chrome kill skipped: resolved path %r is outside allowed root %r",
                    resolved, allowed_root,
                )
            else:
                # pkill -f with re.escape prevents regex metacharacters in the
                # path from broadening the match beyond this profile's Chrome.
                escaped = _re.escape(f"--user-data-dir={resolved}")
                subprocess.run(
                    ["pkill", "-f", escaped],
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
        logger.error(
            "Job %s hit reCAPTCHA kind=%s profile=%s url=%s",
            job_id,
            kind,
            old_profile,
            str(url or "")[:160] or "<unknown>",
        )
        if old_profile and _auto_replace_profiles_enabled():
            swap_failed_logged = False
            recovery_mode = os.environ.get("FLOW_BURN_RECOVERY_MODE", "swap").strip().lower()
            try:
                from worker.profile_swapper import ProfileSwapper

                swapper = ProfileSwapper(
                    profile_base_dir=_profile_base_dir(),
                    credentials_file=_credentials_file_path(),
                )
                if recovery_mode == "wipe":
                    # Same-account recovery: kill chrome, wipe profile dir
                    # and any .burned-* archives, then re-warm under SAME
                    # name. Keeps single-account chains alive instead of
                    # rotating to a different Google account.
                    rewarmed = await asyncio.to_thread(
                        swapper.wipe_and_rewarm,
                        old_profile,
                    )
                    new_profile = old_profile if rewarmed else None
                else:
                    new_profile = await asyncio.to_thread(
                        swapper.swap_burned,
                        old_profile,
                    )
            except Exception:
                logger.exception(
                    "Profile burned, recovery failed; pool exhausted or warm failed"
                )
                swap_failed_logged = True
                new_profile = None

            if new_profile and new_profile != old_profile:
                profile_manager.replace_profile(old_profile, new_profile)
                logger.info(
                    "Profile burned, auto-replaced: %s -> %s",
                    old_profile,
                    new_profile,
                )
                if manage_profile:
                    profile = ""
            elif new_profile == old_profile:
                # wipe-and-rewarm path: same profile name reused. Mark it
                # available again so claim loop picks the fresh session.
                profile_manager.mark_available(old_profile)
                logger.info(
                    "Profile burned, wiped and re-warmed in place: %s",
                    old_profile,
                )
                if manage_profile:
                    profile = ""
                # Requeue this job so a fresh-session claim runs it again.
                # This honors the "all submitted jobs eventually complete"
                # contract under same-account burn-recovery.
                return {
                    "status": "pending",
                    "requeue": True,
                    "error": None,
                    "error_message": (
                        f"recaptcha_{kind}_requeued_after_wipe_rewarm"
                    ),
                }
            else:
                if not swap_failed_logged:
                    logger.error(
                        "Profile burned, recovery failed; pool exhausted or warm failed"
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

    except LeafLockoutError as exc:
        # B28 leaf-lockout: Flow's SPA landed on a leaf extend-output clip
        # whose Camera/Insert/Remove buttons are disabled by Flow's UI rules.
        # Both tile-activation paths (JS dispatch + real click) failed.
        # This is a UI-state issue — NOT a profile burn. Do NOT touch
        # ProfileSwapper here; the profile is healthy.
        error_message = (
            f"b28_leaf_lockout_{exc.target_media_id}: "
            f"{exc.op_type} tile activation failed on leaf "
            f"(url={exc.current_url[:60]}, "
            f"leaf_media={exc.current_media_id[:20] if exc.current_media_id else 'unknown'})"
        )
        logger.error(
            "Job %s B28 leaf-lockout: op=%s target=%s leaf=%s url=%s",
            job_id,
            exc.op_type,
            exc.target_media_id[:20],
            (exc.current_media_id or "unknown")[:20],
            exc.current_url[:80],
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
            project_lock.release(project_url, job_id)
        if manage_profile and profile:
            profile_manager.mark_available(profile)


# ======================================================================
# Batch dispatch (FLOW_BATCH_DISPATCH=1, default OFF)
# ======================================================================

async def dispatch_batch_l1_same_project(
    jobs: list[dict],
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> list[dict]:
    """Open one Chrome, batch-submit N L1 t2v jobs into a fresh project.

    PRD §3.2 Phase 1. Caller must guarantee:
      * all jobs share the same `profile`
      * all jobs are L1 `text-to-video` with `status='pending'`

    Returns one result dict per input job in the same order. Each result
    is shaped for `remote_api.update_job` (carries `job_id`, `status`, etc.)
    """
    if not jobs:
        return []
    if len(jobs) == 1:
        # Degenerate batch — fall back to legacy single-job path so we don't
        # carry a duplicate code surface for N=1.
        single = await dispatch_job(jobs[0], profile_manager, project_lock)
        single.setdefault("job_id", jobs[0].get("id"))
        return [single]

    profile = jobs[0].get("profile", "") or ""
    if not profile:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "no profile assigned"} for j in jobs]
    for j in jobs[1:]:
        if (j.get("profile") or "") != profile:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch profile mismatch"} for jj in jobs]

    # Mark every job's profile slot busy (single profile reused N times in
    # ProfileManager's accounting model — claim it under the FIRST job_id
    # so release at the end is symmetric).
    primary_job_id = jobs[0]["id"]
    profile_manager.mark_busy(profile, primary_job_id)

    from flow.operations._batch import batch_dispatch_l1_same_project

    try:
        async with _client_lease(profile) as client:
            client._job_id = primary_job_id
            try:
                results = await batch_dispatch_l1_same_project(client, jobs)
            except RecaptchaError as exc:
                # Whole batch is poisoned by a profile burn. Mark every job
                # failed with the standard recaptcha sentinel; let the caller
                # handle the single profile burn (one swap covers all jobs).
                kind = getattr(exc, "kind", None) or "unknown"
                err = f"recaptcha_{kind}_burned_{profile}"
                logger.error("Batch L1 hit reCAPTCHA kind=%s profile=%s",
                             kind, profile)
                # Trigger swap once for the burned profile (mirrors single-job logic).
                await _handle_burned_profile_for_batch(profile, profile_manager)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": err, "error_message": err} for j in jobs]
            except Exception as exc:
                logger.exception("Batch L1 unexpected failure: %s", exc)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": f"batch error: {exc}"} for j in jobs]

        # Normalize: ensure every result carries job_id + profile.
        out: list[dict] = []
        for j, r in zip(jobs, results):
            r.setdefault("job_id", j.get("id"))
            r.setdefault("profile", profile)
            out.append(r)
        return out
    finally:
        profile_manager.mark_available(profile)


async def _handle_burned_profile_for_batch(
    profile: str,
    profile_manager: ProfileManager,
) -> None:
    """Swap or wipe the burned profile for batch context.

    Mirrors the recovery branch of `dispatch_job` but without the per-job
    requeue handshake — batch caller marks all N jobs failed and the worker
    claim loop re-picks them later under the new (or wiped) profile.
    """
    if not _auto_replace_profiles_enabled():
        profile_manager.remove_profile(profile)
        return
    recovery_mode = os.environ.get("FLOW_BURN_RECOVERY_MODE", "swap").strip().lower()
    try:
        from worker.profile_swapper import ProfileSwapper

        swapper = ProfileSwapper(
            profile_base_dir=_profile_base_dir(),
            credentials_file=_credentials_file_path(),
        )
        if recovery_mode == "wipe":
            rewarmed = await asyncio.to_thread(swapper.wipe_and_rewarm, profile)
            new_profile = profile if rewarmed else None
        else:
            new_profile = await asyncio.to_thread(swapper.swap_burned, profile)
    except Exception:
        logger.exception("Batch profile burn recovery failed")
        new_profile = None

    if new_profile and new_profile != profile:
        profile_manager.replace_profile(profile, new_profile)
        logger.info("Batch profile burned, auto-replaced: %s -> %s",
                    profile, new_profile)
    elif new_profile == profile:
        profile_manager.mark_available(profile)
        logger.info("Batch profile burned, wiped and re-warmed in place: %s",
                    profile)
    else:
        profile_manager.remove_profile(profile)


async def dispatch_batch_l2_siblings(
    jobs: list[dict],
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> list[dict]:
    """Open one Chrome, batch-submit N L2 ops sharing one parent L1.

    PRD §4.2. Caller must guarantee:
      * all jobs share `profile`
      * all jobs share `parent_job_id`
      * all jobs are L2 ops (extend / camera / insert / remove)
      * the first job carries usable `edit_url` + `media_id` of the parent

    Returns one result dict per input job in the same order.
    """
    if not jobs:
        return []
    if len(jobs) == 1:
        single = await dispatch_job(jobs[0], profile_manager, project_lock)
        single.setdefault("job_id", jobs[0].get("id"))
        return [single]

    profile = jobs[0].get("profile", "") or ""
    if not profile:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "no profile assigned"} for j in jobs]
    parent_id = jobs[0].get("parent_job_id") or ""
    for j in jobs[1:]:
        if (j.get("profile") or "") != profile:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch profile mismatch"} for jj in jobs]
        if (j.get("parent_job_id") or "") != parent_id:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch parent mismatch"} for jj in jobs]

    parent_edit_url = jobs[0].get("edit_url") or ""
    parent_media_id = jobs[0].get("media_id") or ""
    if not parent_edit_url and not parent_media_id:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "missing parent edit_url + media_id"} for j in jobs]

    # The whole batch holds the parent's project_url under one lock — same
    # invariant as legacy dispatch_job per-job locking.
    project_url = jobs[0].get("project_url") or ""
    primary_job_id = jobs[0]["id"]
    if project_url:
        if not project_lock.acquire(project_url, primary_job_id):
            return [{"job_id": j.get("id"), "status": "failed",
                     "error": f"project locked: {project_url}"} for j in jobs]
    profile_manager.mark_busy(profile, primary_job_id)

    from flow.operations._batch import batch_dispatch_l2_siblings

    try:
        async with _client_lease(profile) as client:
            client._job_id = primary_job_id
            try:
                results = await batch_dispatch_l2_siblings(
                    client, parent_edit_url, parent_media_id, jobs,
                )
            except RecaptchaError as exc:
                kind = getattr(exc, "kind", None) or "unknown"
                err = f"recaptcha_{kind}_burned_{profile}"
                logger.error("Batch L2 hit reCAPTCHA kind=%s profile=%s",
                             kind, profile)
                await _handle_burned_profile_for_batch(profile, profile_manager)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": err, "error_message": err} for j in jobs]
            except Exception as exc:
                logger.exception("Batch L2 unexpected failure: %s", exc)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": f"batch error: {exc}"} for j in jobs]

        out: list[dict] = []
        for j, r in zip(jobs, results):
            r.setdefault("job_id", j.get("id"))
            r.setdefault("profile", profile)
            out.append(r)
        return out
    finally:
        if project_url:
            project_lock.release(project_url, primary_job_id)
        profile_manager.mark_available(profile)


async def dispatch_batch_l3_siblings(
    jobs: list[dict],
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> list[dict]:
    """Open one Chrome, batch-submit N L3+ ops sharing one direct L2/L3 parent.

    PRD §5. Caller must guarantee:
      * all jobs share `profile`
      * all jobs share `parent_job_id` (the direct L2 / L3 parent)
      * all jobs have ``job_level >= 3`` and are L2-op types
        (extend / camera / insert / remove)
      * the first job carries usable `edit_url` + `media_id` of the parent

    Mirrors :func:`dispatch_batch_l2_siblings` exactly — only the
    underlying orchestrator entry point differs (so the wiring stays
    honest across phases).
    """
    if not jobs:
        return []
    if len(jobs) == 1:
        single = await dispatch_job(jobs[0], profile_manager, project_lock)
        single.setdefault("job_id", jobs[0].get("id"))
        return [single]

    profile = jobs[0].get("profile", "") or ""
    if not profile:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "no profile assigned"} for j in jobs]
    parent_id = jobs[0].get("parent_job_id") or ""
    for j in jobs[1:]:
        if (j.get("profile") or "") != profile:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch profile mismatch"} for jj in jobs]
        if (j.get("parent_job_id") or "") != parent_id:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch parent mismatch"} for jj in jobs]

    parent_edit_url = jobs[0].get("edit_url") or ""
    parent_media_id = jobs[0].get("media_id") or ""
    if not parent_edit_url and not parent_media_id:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "missing parent edit_url + media_id"} for j in jobs]

    project_url = jobs[0].get("project_url") or ""
    primary_job_id = jobs[0]["id"]
    if project_url:
        if not project_lock.acquire(project_url, primary_job_id):
            return [{"job_id": j.get("id"), "status": "failed",
                     "error": f"project locked: {project_url}"} for j in jobs]
    profile_manager.mark_busy(profile, primary_job_id)

    from flow.operations._batch import batch_dispatch_l3_siblings

    try:
        async with _client_lease(profile) as client:
            client._job_id = primary_job_id
            try:
                results = await batch_dispatch_l3_siblings(
                    client, parent_edit_url, parent_media_id, jobs,
                )
            except RecaptchaError as exc:
                kind = getattr(exc, "kind", None) or "unknown"
                err = f"recaptcha_{kind}_burned_{profile}"
                logger.error("Batch L3 hit reCAPTCHA kind=%s profile=%s",
                             kind, profile)
                await _handle_burned_profile_for_batch(profile, profile_manager)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": err, "error_message": err} for j in jobs]
            except Exception as exc:
                logger.exception("Batch L3 unexpected failure: %s", exc)
                return [{"job_id": j.get("id"), "status": "failed",
                         "error": f"batch error: {exc}"} for j in jobs]

        out: list[dict] = []
        for j, r in zip(jobs, results):
            r.setdefault("job_id", j.get("id"))
            r.setdefault("profile", profile)
            out.append(r)
        return out
    finally:
        if project_url:
            project_lock.release(project_url, primary_job_id)
        profile_manager.mark_available(profile)


async def dispatch_batch_multitab(
    jobs: list[dict],
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> list[dict]:
    """Open one Chrome, run N L2+ ops each in its own tab concurrently.

    PRD §4.3. Caller guarantees:
      * all jobs share ``profile``
      * all jobs have ``job_level >= 2``
      * each job carries ``parent_edit_url`` + ``parent_media_id`` bound at
        claim time by ``server/db/job_store.py:claim_next_batch``

    Jobs with missing context fields are failed individually without
    poisoning the rest of the batch.  L1 jobs are failed defensively.
    ProjectLocks are acquired in lexicographic URL order to prevent
    deadlocks; each acquired lock is released on exit in reverse order.
    """
    if not jobs:
        return []

    # Defensive: L1 jobs must not reach this primitive.
    if any((j.get("job_level") or 1) < 2 for j in jobs):
        return [
            {"job_id": j.get("id"), "status": "failed",
             "error": "L1 job must not reach dispatch_batch_multitab"}
            for j in jobs
        ]

    # Profile coherence.
    profile = jobs[0].get("profile", "") or ""
    if not profile:
        return [{"job_id": j.get("id"), "status": "failed",
                 "error": "no profile assigned"} for j in jobs]
    for j in jobs[1:]:
        if (j.get("profile") or "") != profile:
            return [{"job_id": jj.get("id"), "status": "failed",
                     "error": "batch profile mismatch"} for jj in jobs]

    # Per-job pre-validation. Server claim binds the parent's edit_url +
    # media_id onto the job itself (see job_store.claim_next_batch step 1),
    # so we accept either the explicit `parent_*` keys or the bare aliases
    # — same fallback as `flow.operations._multitab.dispatch_op_in_new_tab`.
    # Both fields must resolve to non-empty: an empty `edit_url` blocks
    # navigation, and an empty `media_id` blocks the per-tab submit-response
    # canonicalization.
    runnable: list[dict] = []
    pre_failed: list[dict] = []
    for j in jobs:
        edit_url = j.get("parent_edit_url") or j.get("edit_url") or ""
        media_id = j.get("parent_media_id") or j.get("media_id") or ""
        if not edit_url or not media_id:
            pre_failed.append({"job_id": j.get("id"), "status": "failed",
                                "error": "missing parent_edit_url / parent_media_id"})
        else:
            runnable.append(j)

    if not runnable:
        return pre_failed

    # Acquire ProjectLocks in sorted URL order to prevent deadlocks.
    primary_job_id = runnable[0]["id"]
    distinct_urls = sorted(
        {j.get("project_url") or "" for j in runnable} - {""}
    )
    acquired: list[str] = []
    locked_failed: list[dict] = []
    blocked_urls: set[str] = set()

    for url in distinct_urls:
        if not project_lock.acquire(url, primary_job_id):
            logger.warning("dispatch_batch_multitab: project locked %s", url)
            blocked_urls.add(url)
            locked_failed.extend(
                {"job_id": j.get("id"), "status": "failed",
                 "error": f"project locked: {url}"}
                for j in runnable if (j.get("project_url") or "") == url
            )
        else:
            acquired.append(url)

    # Jobs with no project_url have no lock requirement and pass through.
    dispatching = [
        j for j in runnable
        if (j.get("project_url") or "") not in blocked_urls
    ]

    if not dispatching:
        for url in reversed(acquired):
            project_lock.release(url, primary_job_id)
        return pre_failed + locked_failed

    profile_manager.mark_busy(profile, primary_job_id)

    from flow.operations._multitab import batch_dispatch_ops_multitab

    try:
        async with _client_lease(profile) as client:
            client._job_id = primary_job_id

            # Translate to the shape batch_dispatch_ops_multitab expects
            # (see flow/operations/_multitab.py lines 21-36).
            op_jobs = [
                {
                    "id": j.get("id"),
                    "type": j.get("type"),
                    "parent_edit_url": (
                        j.get("parent_edit_url") or j.get("edit_url") or ""
                    ),
                    "parent_media_id": (
                        j.get("parent_media_id") or j.get("media_id") or ""
                    ),
                    "parent_project_url": (
                        j.get("parent_project_url") or j.get("project_url") or ""
                    ),
                    "prompt": j.get("prompt"),
                    "direction": j.get("direction"),
                    "bbox": j.get("bbox"),
                }
                for j in dispatching
            ]

            try:
                results = await batch_dispatch_ops_multitab(client, op_jobs)
            except RecaptchaError as exc:
                kind = getattr(exc, "kind", None) or "unknown"
                err = f"recaptcha_{kind}_burned_{profile}"
                logger.error("Batch multitab hit reCAPTCHA kind=%s profile=%s",
                             kind, profile)
                await _handle_burned_profile_for_batch(profile, profile_manager)
                return pre_failed + locked_failed + [
                    {"job_id": j.get("id"), "status": "failed",
                     "error": err, "error_message": err}
                    for j in dispatching
                ]
            except Exception as exc:
                logger.exception("Batch multitab unexpected failure: %s", exc)
                return pre_failed + locked_failed + [
                    {"job_id": j.get("id"), "status": "failed",
                     "error": f"batch error: {exc}"}
                    for j in dispatching
                ]

        dispatched_index: dict[object, dict] = {}
        for j, r in zip(dispatching, results):
            r.setdefault("job_id", j.get("id"))
            r.setdefault("profile", profile)
            dispatched_index[j.get("id")] = r

        # Reconstruct output preserving original jobs order.
        pre_failed_index = {d["job_id"]: d for d in pre_failed}
        locked_index = {d["job_id"]: d for d in locked_failed}
        out: list[dict] = []
        for j in jobs:
            jid = j.get("id")
            if jid in pre_failed_index:
                out.append(pre_failed_index[jid])
            elif jid in locked_index:
                out.append(locked_index[jid])
            else:
                out.append(dispatched_index[jid])
        return out

    finally:
        for url in reversed(acquired):
            project_lock.release(url, primary_job_id)
        profile_manager.mark_available(profile)


# Single source of truth for the L2-batch op-type set. Imported by
# worker/main.py — duplicating it there silently diverges routing if one
# side gains a new op type.
L2_BATCH_OPS: frozenset[str] = frozenset(
    {"extend-video", "camera-move", "insert-object", "remove-object"}
)
_L2_BATCH_OPS = L2_BATCH_OPS  # legacy alias, internal use


async def dispatch_batch(
    jobs: list[dict],
    profile_manager: ProfileManager,
    project_lock: ProjectLock,
) -> list[dict]:
    """Top-level routing entry for a server-claimed batch.

    PRD §4.2 routing table::

        N == 0                          → []
        N == 1                          → dispatch_job (legacy, full burn-recovery)
        all L1 t2v, no project_url      → dispatch_batch_l1_same_project
        all L2+ in L2_BATCH_OPS         → dispatch_batch_multitab
        mixed / unsupported             → sequential per-job dispatch_job

    The L1-fresh check MUST come before the L2+ check so that intent is
    explicit and future changes to either condition cannot silently
    overlap.
    """
    if not jobs:
        return []

    if len(jobs) == 1:
        single = await dispatch_job(jobs[0], profile_manager, project_lock)
        single.setdefault("job_id", jobs[0].get("id"))
        return [single]

    # All-L1 text-to-video fresh-project batch.
    if all(
        (j.get("job_level") or 1) == 1
        and j.get("type") == "text-to-video"
        and not (j.get("project_url") or "")
        for j in jobs
    ):
        return await dispatch_batch_l1_same_project(jobs, profile_manager, project_lock)

    # All L2+ ops supported by the multi-tab orchestrator.
    if all(
        (j.get("job_level") or 1) >= 2
        and (j.get("type") or "") in _L2_BATCH_OPS
        for j in jobs
    ):
        return await dispatch_batch_multitab(jobs, profile_manager, project_lock)

    # Mixed or unsupported batch — degrade gracefully to sequential dispatch.
    logger.warning(
        "dispatch_batch: mixed or unsupported batch (types=%s levels=%s); "
        "falling back to sequential dispatch_job",
        [j.get("type") for j in jobs],
        [j.get("job_level") for j in jobs],
    )
    results: list[dict] = []
    for j in jobs:
        r = await dispatch_job(j, profile_manager, project_lock)
        r.setdefault("job_id", j.get("id"))
        results.append(r)
    return results
