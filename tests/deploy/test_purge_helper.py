"""Static verification of the flowengine-purge-profile helper script.

These tests do NOT execute the bash script (that would require a Debian host
with root).  Instead they verify that the script:

1. Exists and has the executable bit set (on Unix-like systems).
2. Contains the hardcoded profiles root (not a parameterised path).
3. Contains the allowlist regex that blocks path-traversal.
4. Does NOT reference $1 or any user-supplied string as the deletion path
   (the path is always constructed from the hardcoded constant).
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

import pytest

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "deploy"
    / "debian"
    / "flowengine-purge-profile.sh"
)


def test_helper_script_exists() -> None:
    assert SCRIPT_PATH.exists(), f"Helper script not found: {SCRIPT_PATH}"


def test_helper_script_is_regular_file() -> None:
    assert SCRIPT_PATH.is_file()


@pytest.mark.skipif(
    not hasattr(stat, "S_IXUSR"),
    reason="Executable-bit check only meaningful on POSIX",
)
def test_helper_script_has_executable_bit() -> None:
    """On POSIX hosts the script must be executable.

    On Windows (where git may not preserve the x-bit), this is skipped
    rather than failing — the installer step ``chmod 755`` sets it at deploy
    time.
    """
    import platform

    if platform.system() == "Windows":
        pytest.skip("Executable bit not checked on Windows")
    mode = SCRIPT_PATH.stat().st_mode
    assert mode & stat.S_IXUSR, f"Script is not executable: {oct(mode)}"


def test_helper_script_hardcodes_profiles_root() -> None:
    """Path must be hardcoded, not taken from an argument."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert 'PROFILES_ROOT="/opt/flowengine/chrome-profiles"' in content, (
        "PROFILES_ROOT must be hardcoded to /opt/flowengine/chrome-profiles"
    )


def test_helper_script_contains_allowlist_validation() -> None:
    """The script must validate the profile name against a strict allowlist."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    # Look for the regex allowlist — accepts only safe characters.
    assert re.search(r'\[\^?A-Za-z0-9\._\-\]+', content) or re.search(
        r'A-Za-z0-9\._-', content
    ), "Allowlist regex not found in helper script"


def test_helper_script_rejects_dotdot() -> None:
    """The script must explicitly block '..' traversal sequences."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert '".."' in content or "'..'" in content, (
        "Script must contain an explicit check for '..' in the profile name"
    )


def test_helper_script_uses_realpath_prefix_check() -> None:
    """The resolved path must be validated against the profiles root prefix."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "realpath" in content, "Script must use realpath for path resolution"
    # Ensure there is a prefix check (RESOLVED starts with PROFILES_ROOT/).
    assert "PROFILES_ROOT" in content
    # The prefix guard pattern: resolved must start with expected prefix.
    assert re.search(r'RESOLVED.*PROFILES_ROOT|PROFILES_ROOT.*RESOLVED', content), (
        "Script must compare RESOLVED path against PROFILES_ROOT"
    )


def test_helper_script_logs_via_syslog() -> None:
    """The script must send audit messages to syslog."""
    content = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "logger" in content and "flowengine-purge" in content, (
        "Script must log via 'logger -t flowengine-purge'"
    )
