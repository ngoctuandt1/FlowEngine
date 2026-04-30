"""Burn-and-replace helpers for Chrome profiles hit by reCAPTCHA."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from flow.login import PROFILE_LIST_FILE

logger = logging.getLogger(__name__)

_UNICODE_FALLBACKS = str.maketrans(
    {
        "\u00df": "ss",
        "\u00e6": "ae",
        "\u00f0": "d",
        "\u00f8": "o",
        "\u0153": "oe",
        "\u00fe": "th",
        "\u0111": "d",
        "\u0142": "l",
    }
)


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
        local_part = email.split("@", 1)[0].strip().lower()
        local_part = local_part.split("+", 1)[0].replace(".", "")
        local_part = local_part.translate(_UNICODE_FALLBACKS)
        ascii_local = (
            unicodedata.normalize("NFKD", local_part)
            .encode("ascii", "ignore")
            .decode("ascii")
        )
        sanitized = re.sub(r"[^a-z0-9_]+", "_", ascii_local)
        sanitized = re.sub(r"_+", "_", sanitized).strip("_")
        return (sanitized or "profile")[:32]

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

    def warm_new_profile(self, profile_name: str, timeout: int = 180) -> bool:
        try:
            result = subprocess.run(
                ["python", "scripts/warm_profile.py", profile_name],
                cwd=self.repo_root,
                env=os.environ.copy(),
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
