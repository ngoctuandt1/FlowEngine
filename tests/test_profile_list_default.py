from __future__ import annotations

from pathlib import Path

import pytest

import profile_list as profile_list_mod
import scripts.check_profiles_ultra as check_profiles_ultra
from flow import login as login_mod
from worker import dispatcher as dispatcher_mod

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPECTED_DEFAULT = (REPO_ROOT / "profiles_ultra.txt").resolve()


def test_default_profile_list_points_inside_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLOW_PROFILE_LIST_FILE", raising=False)

    assert Path(login_mod.PROFILE_LIST_FILE).resolve() == EXPECTED_DEFAULT
    assert profile_list_mod.configured_profile_list_file(
        default=login_mod.PROFILE_LIST_FILE
    ) == EXPECTED_DEFAULT
    assert check_profiles_ultra._default_profiles_file() == EXPECTED_DEFAULT
    assert dispatcher_mod._credentials_file_path() == EXPECTED_DEFAULT


def test_flow_profile_list_env_override_wins(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    override = tmp_path / "custom_profiles.txt"
    override.write_text("# test credentials file", encoding="utf-8")
    monkeypatch.setenv("FLOW_PROFILE_LIST_FILE", str(override))

    assert profile_list_mod.configured_profile_list_file(
        default=login_mod.PROFILE_LIST_FILE
    ) == override.resolve()
    assert check_profiles_ultra._default_profiles_file() == override.resolve()
    assert dispatcher_mod._credentials_file_path() == override.resolve()


def test_missing_profile_list_raises_clear_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = (tmp_path / "missing_profiles.txt").resolve()
    monkeypatch.setenv("FLOW_PROFILE_LIST_FILE", str(missing))
    expected_message = (
        f"FLOW_PROFILE_LIST_FILE not found: {missing}.\n"
        "Set FLOW_PROFILE_LIST_FILE env to your credentials file "
        "(5-field format: profile|email|password|2fa_secret|recovery).\n"
        "See docs/PROJECT_SPINE.md Quickstart."
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        login_mod._load_credentials("alice")

    assert str(exc_info.value) == expected_message

    with pytest.raises(FileNotFoundError) as exc_info:
        check_profiles_ultra.build_report(missing, tmp_path / "chrome-profiles")

    assert str(exc_info.value) == expected_message
