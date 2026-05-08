"""Burn-and-replace helpers for Chrome profiles hit by reCAPTCHA."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flow.login import PROFILE_LIST_FILE

logger = logging.getLogger(__name__)

_FILESYSTEM_UNSAFE_CHARS = re.compile(r'[\/\\:*?"<>|]')


@dataclass(frozen=True)
class CredEntry:
    profile_name: str
    email: str
    password: str
    totp_secret: str = ""
    recovery: str = ""


class ProfileSwapper:
    """Swap a burned Chrome profile out for the next unwarmed credential."""

    def __init__(self, profile_base_dir: Path, credentials_file: Path) -> None:
        self.profile_base_dir = Path(profile_base_dir).expanduser().resolve()
        self.credentials_file = Path(
            credentials_file or PROFILE_LIST_FILE
        ).expanduser().resolve()
        self.repo_root = Path(__file__).resolve().parents[1]

    def derive_profile_name(self, email: str) -> str:
        """Use the email local-part as the profile name, replacing only
        filesystem-reserved path characters with ``_`` so
        ``flow.login._load_credentials`` can still match the raw
        ``email.split("@")[0]`` alias.
        """
        local_part = email.split("@", 1)[0].strip()
        sanitized = _FILESYSTEM_UNSAFE_CHARS.sub("_", local_part)
        return sanitized or "profile"

    def available_credentials(self) -> list[CredEntry]:
        credentials_path = self._credentials_path()
        if not credentials_path.exists():
            logger.error("Credential file not found: %s", credentials_path)
            return []

        entries: list[CredEntry] = []
        seen_profiles: set[str] = set()
        for raw_line in credentials_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [part.strip() for part in line.split("|")]
            if len(parts) < 3:
                continue

            profile_name = self.derive_profile_name(parts[1])
            if profile_name in seen_profiles or self._has_burned_archive(profile_name):
                continue

            entries.append(
                CredEntry(
                    profile_name=profile_name,
                    email=parts[1],
                    password=parts[2],
                    totp_secret=parts[3] if len(parts) > 3 else "",
                    recovery=parts[4] if len(parts) > 4 else "",
                )
            )
            seen_profiles.add(profile_name)

        return entries

    def mark_burned(self, profile_name: str) -> Path:
        source = self.profile_base_dir / profile_name
        if not source.exists():
            existing = self._latest_burned_path(profile_name)
            if existing is not None:
                return existing
            return source

        self.profile_base_dir.mkdir(parents=True, exist_ok=True)
        burned_path = self._unique_burned_path(profile_name)
        moved_path = Path(shutil.move(str(source), str(burned_path)))
        logger.warning("Burned profile archived: %s -> %s", source, moved_path)
        return moved_path

    def pick_next_fresh(self) -> Optional[str]:
        for entry in self.available_credentials():
            if not (self.profile_base_dir / entry.profile_name).exists():
                return entry.profile_name
        return None

    def kill_chrome_for_profile(self, profile_name: str) -> int:
        """Kill any Chrome / Playwright drivers using this profile's --user-data-dir.

        Returns count of processes killed. Best-effort; never raises.
        """
        target = str((self.profile_base_dir / profile_name).resolve())
        user_data_arg = f"--user-data-dir={target}".encode()
        killed = 0
        # Walk /proc on Linux to find pids whose cmdline has an exact
        # --user-data-dir=<path> argument (NUL-separated), avoiding
        # substring matches like "foo" matching "foobar".
        proc_root = Path("/proc")
        if not proc_root.exists():
            return 0
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes()
            except OSError:
                continue
            if not cmdline:
                continue
            # Split on NUL to get individual argv entries and require exact match
            if user_data_arg not in cmdline.split(b"\x00"):
                continue
            pid = int(entry.name)
            try:
                os.kill(pid, 9)
                killed += 1
            except OSError:
                pass
        if killed:
            time.sleep(1.5)
            logger.warning(
                "Killed %d chrome process(es) for profile %s before wipe",
                killed,
                profile_name,
            )
        return killed

    # Path to the privileged helper installed on Debian production hosts.
    # The helper validates its argument and runs as root via sudoers drop-in.
    _PURGE_HELPER = "/usr/local/bin/flowengine-purge-profile"

    def _sudo_purge(self, profile_name: str) -> bool:
        """Invoke the privileged helper to remove a root-owned profile dir.

        Returns True only when the target directory no longer exists after the
        helper exits (regardless of the helper's own exit code — the helper may
        also remove .burned-* archives which we check separately).
        """
        target = self.profile_base_dir / profile_name
        logger.warning(
            "Falling back to privileged purge for %s (PermissionError on rmtree)",
            target,
        )
        try:
            result = subprocess.run(
                ["sudo", "-n", self._PURGE_HELPER, profile_name],
                check=False,
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception(
                "Privileged purge invocation failed for profile %s", profile_name
            )
            return False

        if target.exists():
            logger.error(
                "Privileged purge returned %d but %s still exists",
                result.returncode,
                target,
            )
            return False

        logger.info(
            "Privileged purge succeeded for profile %s (helper exit=%d)",
            profile_name,
            result.returncode,
        )
        return True

    def wipe_profile(self, profile_name: str) -> bool:
        """Hard-delete the profile dir AND any sibling .burned-* archives.

        Pre-kills any chrome process locking the dir. Used for the
        same-account re-warm flow per user policy: when a profile burns,
        we wipe it cleanly and re-warm under the same name (no rotation).

        When ``shutil.rmtree`` raises ``PermissionError`` (e.g. root-owned
        files left by a root-launched Chrome session), falls back to invoking
        ``sudo -n /usr/local/bin/flowengine-purge-profile`` which runs as root
        via a sudoers(5) drop-in and handles the deletion.

        Returns ``True`` when the **main profile directory** has been
        successfully removed (the critical path).  Burned-archive cleanup
        failures are degraded-but-non-blocking: they are logged as errors but
        do NOT cause this method to return ``False``.
        """
        self.kill_chrome_for_profile(profile_name)
        target = self.profile_base_dir / profile_name
        if target.exists():
            try:
                shutil.rmtree(target)
                logger.warning("Wiped profile dir: %s", target)
            except PermissionError:
                if not self._sudo_purge(profile_name):
                    return False
            except OSError:
                logger.exception("Failed to wipe profile dir: %s", target)
                return False
        # Also clear any historical .burned-* archives so the next warm
        # has zero residual state, including OS keyring-cached automation
        # signals tied to that profile path.
        for archive in self.profile_base_dir.glob(f"{profile_name}.burned-*"):
            try:
                shutil.rmtree(archive)
                logger.info("Removed burned archive: %s", archive)
            except PermissionError:
                # Best-effort: burned archives are not critical — log and continue.
                logger.warning(
                    "PermissionError removing burned archive %s; invoking privileged purge",
                    archive,
                )
                try:
                    subprocess.run(
                        ["sudo", "-n", self._PURGE_HELPER, archive.name],
                        check=False,
                        timeout=30,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    logger.exception(
                        "Privileged purge of burned archive %s failed", archive
                    )
                else:
                    # Verify the archive is actually gone — the helper may have
                    # exited 0 without removing it (e.g. wrong sudoers rule).
                    # A leftover archive keeps _has_burned_archive() returning
                    # True, which silently blocks future re-warm attempts for
                    # this profile name.
                    if archive.exists():
                        logger.error(
                            "Sudo fallback failed to remove burned archive: %s",
                            archive,
                        )
            except OSError:
                logger.exception("Failed to remove burned archive: %s", archive)
        return True

    def wipe_and_rewarm(
        self,
        profile_name: str,
        timeout: int = 180,
    ) -> bool:
        """Same-account recovery: kill+wipe+re-warm under the SAME profile name.

        Returns True if Cookies file lands at expected path after warm.
        Use this instead of swap_burned() when policy is single-account.
        """
        if not self.wipe_profile(profile_name):
            return False
        return self.warm_new_profile(profile_name, timeout=timeout)

    def warm_new_profile(self, profile_name: str, timeout: int = 180) -> bool:
        env = os.environ.copy()
        # Keep warm_profile on this swapper's profile root, provide a DISPLAY
        # for headful Chrome under Xvfb, force the real-Chrome path, point
        # login lookup at the same credential file, and preserve base-profile
        # bootstrap behavior.
        env.setdefault("CHROME_USER_DATA_DIR", str(self.profile_base_dir))
        env.setdefault("DISPLAY", os.environ.get("DISPLAY", ":99"))
        env.setdefault("FLOW_REAL_CHROME", "1")
        env.setdefault("FLOW_PROFILE_LIST_FILE", str(self.credentials_file))
        env.setdefault("FLOW_USE_BASE_PROFILE", "1")
        # warm_profile.py invokes _find_chrome_executable() which only looks at
        # Windows install paths by default. On Linux we need to point at the
        # system Chrome explicitly so the headful CDP launcher succeeds.
        # Fall back to the conventional Debian path if the operator did not
        # set FLOW_WARM_CHROME_PATH at the worker process level.
        if not env.get("FLOW_WARM_CHROME_PATH"):
            for candidate in ("/usr/bin/google-chrome", "/usr/bin/chromium"):
                if Path(candidate).exists():
                    env["FLOW_WARM_CHROME_PATH"] = candidate
                    break
        try:
            result = subprocess.run(
                [sys.executable, "scripts/warm_profile.py", profile_name],
                cwd=self.repo_root,
                env=env,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.error("Warm profile timed out for %s", profile_name)
            return False
        except OSError:
            logger.exception("Warm profile launch failed for %s", profile_name)
            return False

        cookies_path = self.profile_base_dir / profile_name / "Default" / "Cookies"
        return result.returncode == 0 and cookies_path.exists()

    def swap_burned(self, old_name: str) -> Optional[str]:
        self.mark_burned(old_name)
        new_name = self.pick_next_fresh()
        if new_name is None:
            logger.error("No fresh credentials left after burning %s", old_name)
            return None
        if not self.warm_new_profile(new_name):
            logger.error("Warm failed for replacement profile %s", new_name)
            return None
        return new_name

    def _credentials_path(self) -> Path:
        override = (os.environ.get("FLOW_PROFILE_LIST_FILE") or "").strip()
        if override:
            return Path(override).expanduser().resolve()
        return self.credentials_file

    def _has_burned_archive(self, profile_name: str) -> bool:
        return self._latest_burned_path(profile_name) is not None

    def _latest_burned_path(self, profile_name: str) -> Path | None:
        matches = sorted(self.profile_base_dir.glob(f"{profile_name}.burned-*"))
        if not matches:
            return None
        return matches[-1]

    def _unique_burned_path(self, profile_name: str) -> Path:
        timestamp = int(time.time())
        candidate = self.profile_base_dir / f"{profile_name}.burned-{timestamp}"
        suffix = 1
        while candidate.exists():
            candidate = self.profile_base_dir / f"{profile_name}.burned-{timestamp}-{suffix}"
            suffix += 1
        return candidate
