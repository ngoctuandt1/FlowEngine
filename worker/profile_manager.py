"""Chrome profile tracker for the worker.

Tracks which Chrome profiles are available vs busy,
mapping each to its current job (if any).
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class ProfileManager:
    """Manages Chrome profile availability on this worker."""

    def __init__(self, chrome_user_data_dir: str, profile_names: list[str]):
        self.chrome_user_data_dir = chrome_user_data_dir
        self.profiles: dict[str, dict] = {}
        for name in profile_names:
            self.profiles[name] = {
                "status": "available",
                "current_job": None,
            }
        logger.info(
            "ProfileManager initialised with %d profile(s): %s",
            len(profile_names),
            ", ".join(profile_names),
        )

    def get_available(self) -> list[str]:
        """Return profile names that are not currently busy."""
        return [
            name
            for name, info in self.profiles.items()
            if info["status"] == "available"
        ]

    def mark_busy(self, name: str, job_id: str) -> None:
        """Mark a profile as busy with the given job."""
        if name not in self.profiles:
            logger.warning("Unknown profile %r, adding it dynamically", name)
            self.profiles[name] = {"status": "available", "current_job": None}
        self.profiles[name]["status"] = "busy"
        self.profiles[name]["current_job"] = job_id
        logger.info("Profile %s marked BUSY (job %s)", name, job_id)

    def mark_available(self, name: str) -> None:
        """Mark a profile as available (no longer running a job)."""
        if name in self.profiles:
            old_job = self.profiles[name]["current_job"]
            self.profiles[name]["status"] = "available"
            self.profiles[name]["current_job"] = None
            logger.info(
                "Profile %s marked AVAILABLE (was job %s)", name, old_job
            )
        else:
            logger.warning("mark_available: unknown profile %r", name)

    def is_available(self, name: str) -> bool:
        """Check whether a given profile is available."""
        info = self.profiles.get(name)
        if info is None:
            return False
        return info["status"] == "available"

    def get_current_job(self, name: str) -> Optional[str]:
        """Return the job_id a profile is running, or None."""
        info = self.profiles.get(name)
        if info is None:
            return None
        return info["current_job"]

    def replace_profile(self, old: str, new: str) -> None:
        """Atomically replace a burned profile with a fresh one."""
        old_info = self.profiles.pop(old, None)
        new_info = self.profiles.pop(new, None)
        if old_info is None:
            logger.warning(
                "replace_profile: old profile %r missing; appending %s",
                old,
                new,
            )
        self.profiles[new] = new_info or {
            "status": "available",
            "current_job": None,
        }
        logger.warning("Profile burned, swapped: %s -> %s", old, new)

    def __repr__(self) -> str:
        avail = self.get_available()
        busy = [n for n in self.profiles if n not in avail]
        return (
            f"<ProfileManager available={avail} busy={busy}>"
        )
