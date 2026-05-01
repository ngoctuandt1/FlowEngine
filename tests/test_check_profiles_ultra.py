from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_profiles_ultra.py"


def _write_profiles_file(path: Path, include_malformed: bool = True) -> Path:
    rows = [
        "C:/profiles/warmed-profile|warm.alias@example.com|pw1|JBSWY3DPEHPK3PXP|recover1@example.com",
        "C:/profiles/unwarmed-profile|unwarmed@example.com|pw2|bad-totp|recover2@example.com",
        "C:/profiles/burned-profile|burned@example.com|pw3||recover3@example.com",
        "C:/profiles/failed-profile|failed@example.com|pw4|JBSWY3DPEHPK3PXP|recover4@example.com",
    ]
    if include_malformed:
        rows.append("C:/profiles/bad-profile|not-an-email||JBSWY3DPEHPK3PXP|recover5@example.com")
    path.write_text("\n".join(rows), encoding="utf-8")
    return path


def _make_profile_base_dir(base_dir: Path) -> Path:
    warmed_cookies = base_dir / "warmed-profile" / "Default" / "Cookies"
    warmed_cookies.parent.mkdir(parents=True)
    warmed_cookies.write_bytes(b"cookies")

    burned_dir = base_dir / "burned-profile.burned-1714000000"
    burned_dir.mkdir(parents=True)

    failed_dir = base_dir / "failed-profile"
    failed_dir.mkdir(parents=True)
    return base_dir


def _run_script(
    tmp_path: Path,
    *,
    include_malformed: bool = True,
    args: list[str] | None = None,
    use_env_defaults: bool = False,
) -> subprocess.CompletedProcess[str]:
    profiles_file = _write_profiles_file(
        tmp_path / "profiles_ultra.txt",
        include_malformed=include_malformed,
    )
    profile_base_dir = _make_profile_base_dir(tmp_path / "chrome-profiles")

    env = os.environ.copy()
    env["FLOW_PROFILE_LIST_FILE"] = str(profiles_file)
    env["CHROME_USER_DATA_DIR"] = str(profile_base_dir)

    command = [sys.executable, str(SCRIPT_PATH)]
    if not use_env_defaults:
        command.extend(
            [
                "--profiles-file",
                str(profiles_file),
                "--profile-base-dir",
                str(profile_base_dir),
            ]
        )
    if args:
        command.extend(args)

    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_table_output_mentions_all_entries_and_uses_env_defaults(tmp_path: Path) -> None:
    result = _run_script(tmp_path, use_env_defaults=True)

    assert result.returncode == 1
    assert "warmed-profile" in result.stdout
    assert "unwarmed-profile" in result.stdout
    assert "burned-profile" in result.stdout
    assert "failed-profile" in result.stdout
    assert "bad-profile" in result.stdout
    assert "burned (1714000000)" in result.stdout
    assert "TOTP malformed" in result.stdout
    assert "Summary: warmed=1, warming-failed=1, burned=1, unwarmed=1, malformed=1, total=5" in result.stdout


def test_json_output_is_valid_and_has_expected_schema(tmp_path: Path) -> None:
    result = _run_script(tmp_path, args=["--json"])

    assert result.returncode == 1
    payload = json.loads(result.stdout)

    assert payload["profiles_file"].endswith("profiles_ultra.txt")
    assert payload["profile_base_dir"].endswith("chrome-profiles")
    assert payload["summary"] == {
        "burned": 1,
        "malformed": 1,
        "total": 5,
        "unwarmed": 1,
        "warmed": 1,
        "warming-failed": 1,
    }
    assert payload["parse_error_count"] == 1
    assert payload["todo"]
    assert len(payload["entries"]) == 5
    assert {
        "email",
        "line_number",
        "malformed",
        "notes",
        "profile_name",
        "status",
        "status_display",
        "totp",
        "warmed_at",
    } <= payload["entries"][0].keys()
    assert any(
        entry["profile_name"] == "burned-profile"
        and entry["status"] == "burned"
        and entry["status_display"] == "burned (1714000000)"
        for entry in payload["entries"]
    )
    assert any(
        entry["profile_name"] == "unwarmed-profile"
        and entry["totp"] == "TOTP malformed"
        for entry in payload["entries"]
    )


def test_exit_code_is_zero_when_all_entries_are_parseable(tmp_path: Path) -> None:
    result = _run_script(tmp_path, include_malformed=False)

    assert result.returncode == 0
    assert "malformed=0" in result.stdout
