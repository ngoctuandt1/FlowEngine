"""Auto wipe-and-rewarm a burned profile in any batch path.

The legacy worker `dispatch_job` already wraps each handler call with a
``RecaptchaError`` catch that triggers ``ProfileSwapper.wipe_and_rewarm``
when ``FLOW_BURN_RECOVERY_MODE=wipe``. The standalone batch scripts
(live verify, mass-gen) and the new inflate-batch / status-poll paths
bypass that wrapper, so they have to opt into the same recovery.

This module exposes a single retry helper that any caller can wrap
around a Chrome-bound coroutine. On ``RecaptchaError`` it:

  1. Closes the FlowClient cleanly (the caller's ``async with`` was
     already torn down by the exception, but we re-establish here).
  2. Stops any leftover Chrome processes for the profile.
  3. Calls ``ProfileSwapper.wipe_and_rewarm(profile)`` synchronously
     (warm_profile.py does the TOTP login + cookie persistence).
  4. Re-launches a fresh FlowClient bound to the same profile name and
     re-runs the original coroutine with the same arguments.

Default attempts = 1 retry per ``RecaptchaError`` so cascading burns
fail fast rather than spinning credits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Awaitable, Callable

from flow.recaptcha import RecaptchaError

logger = logging.getLogger(__name__)


_DEFAULT_RECOVERY_ATTEMPTS = 1
_RECOVERY_ENV = "FLOW_BURN_RECOVERY_MODE"


async def with_recaptcha_recovery(
    profile: str,
    coro_factory: Callable[[], Awaitable[Any]],
    *,
    attempts: int | None = None,
    on_recovered: Callable[[str], None] | None = None,
) -> Any:
    """Run ``coro_factory()`` with auto wipe+rewarm on ``RecaptchaError``.

    ``coro_factory`` must build a fresh awaitable each call — the same
    coroutine cannot be awaited twice. The caller's coroutine is
    typically::

        async def _go():
            client = FlowClient(profile_name=profile, ...)
            async with client:
                ...
            return result

    On a ``RecaptchaError`` raised inside ``_go()``:

      1. Optionally kill leftover Chrome processes for this profile
         (cleans up ``Singleton*`` locks before rewarming).
      2. Run ``ProfileSwapper.wipe_and_rewarm(profile)`` in a thread
         (it logs in via TOTP and persists fresh cookies).
      3. Retry up to ``attempts`` times.

    Honors the same env knob as ``worker.dispatcher`` —
    ``FLOW_BURN_RECOVERY_MODE`` is informational here (we always wipe
    in this helper because it's intended for single-profile batch
    paths; ``swap`` mode is handled by the worker pool).
    """
    if attempts is None:
        attempts = _DEFAULT_RECOVERY_ATTEMPTS

    last_exc: RecaptchaError | None = None
    for attempt in range(attempts + 1):
        try:
            return await coro_factory()
        except RecaptchaError as exc:
            last_exc = exc
            if attempt >= attempts:
                logger.error(
                    "with_recaptcha_recovery: %d/%d attempts exhausted, "
                    "raising RecaptchaError(kind=%s)",
                    attempt + 1, attempts + 1, getattr(exc, "kind", "?"),
                )
                raise
            kind = getattr(exc, "kind", None) or "unknown"
            logger.warning(
                "with_recaptcha_recovery: caught RecaptchaError(kind=%s) — "
                "wiping + rewarming profile %s (attempt %d/%d)",
                kind, profile, attempt + 1, attempts + 1,
            )
            ok = await _wipe_and_rewarm(profile)
            if not ok:
                logger.error(
                    "with_recaptcha_recovery: wipe+rewarm failed for %s; "
                    "giving up", profile,
                )
                raise
            if on_recovered:
                try:
                    on_recovered(profile)
                except Exception:
                    logger.exception("on_recovered callback raised")
            logger.info(
                "with_recaptcha_recovery: profile %s wipe+rewarm OK; retrying",
                profile,
            )
    if last_exc is not None:
        raise last_exc
    return None


async def prime_flow_session(profile: str) -> bool:
    """Ensure the profile has a usable Flow session before a batch run.

    Tries a minimal Flow visit first (cheap if cookies are already good).
    On any auth-bouncing or marketing-landing-persistent failure, falls
    back to wipe+rewarm + Flow OAuth seal.

    Use as the first step of a standalone batch script so the run does
    not enter ``submit_generate_l1`` only to die in the marketing
    landing → signin/identifier loop.

    Returns True when the Flow app is reachable, False otherwise.
    """
    if await _seal_flow_oauth_consent(profile):
        logger.info("prime_flow_session: existing cookies OK")
        return True
    logger.info("prime_flow_session: cookies insufficient — wipe+rewarm")
    return await _wipe_and_rewarm(profile)


async def _wipe_and_rewarm(profile: str) -> bool:
    profile_base_dir = Path(
        os.environ.get("CHROME_USER_DATA_DIR", "./chrome-profiles")
    ).expanduser().resolve()
    cred_file = _resolve_credentials_file()
    if not cred_file.is_file():
        logger.error("credentials file missing: %s", cred_file)
        return False

    _kill_chrome_for_profile(profile, profile_base_dir)

    try:
        from worker.profile_swapper import ProfileSwapper

        swapper = ProfileSwapper(
            profile_base_dir=profile_base_dir,
            credentials_file=cred_file,
        )
        ok = bool(
            await asyncio.to_thread(swapper.wipe_and_rewarm, profile)
        )
    except Exception:
        logger.exception("ProfileSwapper.wipe_and_rewarm crashed")
        return False
    if not ok:
        return False

    # warm_profile.py only drives Gmail login, so Flow's OAuth consent
    # cookies are not established. Visit labs.google/fx/tools/flow once
    # via a real Chrome to seal the Flow session before returning.
    # Without this, the next /flow nav lands on the marketing landing
    # which the live verify script can't dismiss (the "Create with Flow"
    # CTA bounces to the OAuth signin/identifier page).
    try:
        consented = await _seal_flow_oauth_consent(profile)
        if not consented:
            logger.warning("Flow OAuth consent step did not confirm; "
                           "proceeding anyway")
    except Exception:
        logger.exception("Flow OAuth consent step crashed; proceeding anyway")
    return True


async def _seal_flow_oauth_consent(profile: str) -> bool:
    """One-shot Flow visit on the freshly-warmed profile.

    Drives FlowClient just far enough to land on /project/ or the
    composer — enough for Google's OAuth flow to consent and persist
    the labs.google session cookies. Closes the client immediately.
    """
    from flow.client import FlowClient

    profile_base_dir = os.environ.get(
        "CHROME_USER_DATA_DIR", "./chrome-profiles"
    )
    download_dir = os.environ.get("FLOW_DOWNLOAD_DIR", "./downloads")
    client = FlowClient(
        profile_name=profile,
        profile_base_dir=profile_base_dir,
        download_dir=download_dir,
    )
    try:
        async with client:
            page = client.page
            try:
                await page.goto(
                    "https://labs.google/fx/tools/flow",
                    wait_until="domcontentloaded", timeout=30000,
                )
                await asyncio.sleep(3)
            except Exception as exc:
                logger.warning("seal: initial Flow goto failed: %s", exc)

            # If marketing landing showed up, click through; the gmail
            # cookies should drive the OAuth handshake near-instantly.
            from flow.landing import dismiss_flow_marketing_landing
            from flow.login import handle_login_redirect, is_login_page

            async def _flow_app_attached(timeout_ms: int = 1000) -> bool:
                try:
                    await page.wait_for_selector(
                        "text=/New project|Dự án mới|Tạo dự án/",
                        state="attached", timeout=timeout_ms,
                    )
                    return True
                except Exception:
                    return False

            if not await _flow_app_attached(2000):
                await dismiss_flow_marketing_landing(
                    page, logger, _flow_app_attached,
                )

            if is_login_page(page.url):
                logger.info("seal: login redirect — driving handle_login_redirect")
                await handle_login_redirect(
                    page, timeout=60, profile_name=profile, client=client,
                )
                await page.goto(
                    "https://labs.google/fx/tools/flow",
                    wait_until="domcontentloaded", timeout=30000,
                )
                await asyncio.sleep(3)
                if not await _flow_app_attached(8000):
                    await dismiss_flow_marketing_landing(
                        page, logger, _flow_app_attached,
                    )

            attached = await _flow_app_attached(8000)
            logger.info("seal: marketing CTA attached=%s url=%s",
                        attached, page.url[:120])
            if not attached:
                return False

            # Click "New project" to enter the actual app — that
            # navigation persists Flow's OAuth consent cookies in a way
            # the marketing landing alone does not. Verify by URL
            # containing /project/ within 15 s.
            clicked = False
            for name in ("New project", "Dự án mới", "Tạo dự án"):
                try:
                    btn = page.get_by_role(
                        "button", name=name,
                    ).filter(visible=True).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click(timeout=5000)
                        clicked = True
                        break
                except Exception:
                    continue
            if not clicked:
                logger.warning("seal: could not click 'New project'; "
                               "session may still be incomplete")
                return False
            try:
                await page.wait_for_url("**/project/**", timeout=20000)
            except Exception:
                logger.warning(
                    "seal: did not reach /project/ after click; "
                    "url=%s", page.url[:120],
                )
                return False
            logger.info("seal: reached /project/ — Flow OAuth fully sealed")
            await asyncio.sleep(2)
            return True
    except Exception as exc:
        logger.warning("seal: client lifecycle error: %s", exc)
        return False


def _resolve_credentials_file() -> Path:
    raw = os.environ.get("FLOW_PROFILE_LIST_FILE", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("./profiles_ultra.txt").resolve()


def _kill_chrome_for_profile(profile: str, profile_base_dir: Path) -> None:
    """Kill leftover Chromes pinned to this profile and clear singleton locks."""
    profile_dir = profile_base_dir / profile
    try:
        if os.name == "nt":
            subprocess.run(
                ["wmic", "process", "where",
                 f"commandline like '%{profile}%' and name='chrome.exe'",
                 "call", "terminate"],
                capture_output=True, timeout=10,
            )
        else:
            resolved = str(profile_dir.resolve())
            subprocess.run(
                ["pkill", "-f", f"--user-data-dir={resolved}"],
                capture_output=True, timeout=10,
            )
    except Exception as exc:
        logger.debug("Chrome kill skipped: %s", exc)
    for lock_name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = profile_dir / lock_name
        try:
            lock.unlink(missing_ok=True)
        except Exception:
            pass
