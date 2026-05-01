"""Shared FLOW_PROFILE_LIST_FILE resolution helpers."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PROFILE_LIST_FILE = (
    Path(__file__).resolve().parent / "profiles_ultra.txt"
).as_posix()


def configured_profile_list_file(*, default: str | Path | None = None) -> Path:
    """Return the env override or the repo-local default as a resolved path."""
    configured = (os.environ.get("FLOW_PROFILE_LIST_FILE") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    fallback = Path(default or DEFAULT_PROFILE_LIST_FILE)
    return fallback.expanduser().resolve()


def profile_list_file_not_found_message(path: Path) -> str:
    return (
        f"FLOW_PROFILE_LIST_FILE not found: {path}.\n"
        "Set FLOW_PROFILE_LIST_FILE env to your credentials file "
        "(5-field format: profile|email|password|2fa_secret|recovery).\n"
        "See docs/PROJECT_SPINE.md Quickstart."
    )


def resolve_profile_list_file(
    path: str | Path | None = None,
    *,
    default: str | Path | None = None,
) -> Path:
    """Resolve and validate the credential file path."""
    resolved = (
        configured_profile_list_file(default=default)
        if path is None
        else Path(path).expanduser().resolve()
    )
    if not resolved.exists():
        raise FileNotFoundError(profile_list_file_not_found_message(resolved))
    return resolved
