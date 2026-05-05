from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow import login as login_mod
from worker.profile_swapper import ProfileSwapper

_FILESYSTEM_UNSAFE_CHARS = re.compile(r'[\/\\:*?"<>|]')


def _make_swapper(tmp_path: Path, credentials_file: Path | None = None) -> ProfileSwapper:
    profile_base_dir = tmp_path / "chrome-profiles"
    return ProfileSwapper(
        profile_base_dir=profile_base_dir,
        credentials_file=credentials_file or (tmp_path / "profiles_ultra.txt"),
    )


@pytest.mark.parametrize(
    ("email", "expected"),
    [
        ("Jane.Doe@gmail.com", "Jane.Doe"),
        ("Foo.Bar+test@x.sbs", "Foo.Bar+test"),
        ("UPPER_case@x.sbs", "UPPER_case"),
        ("name-with-dash@example.com", "name-with-dash"),
        ("j\u00f6hn.doe+tag@example.com", "j\u00f6hn.doe+tag"),
        ("\u7528\u6237@example.com", "\u7528\u6237"),
        ("foo:bar@x.sbs", "foo_bar"),
        ("abcdefghijklmnopqrstuvwxyz1234567890@example.com", "abcdefghijklmnopqrstuvwxyz1234567890"),
    ],
)
def test_derive_profile_name(tmp_path: Path, email: str, expected: str) -> None:
    swapper = _make_swapper(tmp_path)
    assert swapper.derive_profile_name(email) == expected


def test_derive_profile_name_is_filesystem_safe(tmp_path: Path) -> None:
    swapper = _make_swapper(tmp_path)

    profile_name = swapper.derive_profile_name("foo.bar+test@x.sbs")

    assert profile_name == "foo.bar+test"
    assert _FILESYSTEM_UNSAFE_CHARS.search(profile_name) is None


def test_derive_profile_name_matches_login_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_file = tmp_path / "profiles_ultra.txt"
    credentials_file.write_text(
        "C:/profiles/fallback|foo.bar+test@x.sbs|pw|totp|recovery",
        encoding="utf-8",
    )
    monkeypatch.delenv("FLOW_PROFILE_LIST_FILE", raising=False)
    monkeypatch.setattr(login_mod, "PROFILE_LIST_FILE", str(credentials_file))
    swapper = _make_swapper(tmp_path, credentials_file)

    creds = login_mod._load_credentials(
        swapper.derive_profile_name("foo.bar+test@x.sbs")
    )

    assert creds == {
        "email": "foo.bar+test@x.sbs",
        "password": "pw",
        "totp_secret": "totp",
        "recovery": "recovery",
    }


def test_mark_burned_moves_profile_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    swapper = _make_swapper(tmp_path)
    profile_dir = tmp_path / "chrome-profiles" / "alice"
    profile_dir.mkdir(parents=True)
    (profile_dir / "sentinel.txt").write_text("ok", encoding="utf-8")
    monkeypatch.setattr("worker.profile_swapper.time.time", lambda: 1_714_000_000)

    burned_path = swapper.mark_burned("alice")

    assert profile_dir.exists() is False
    assert burned_path.exists() is True
    assert re.fullmatch(r"alice\.burned-\d+", burned_path.name)
    assert (burned_path / "sentinel.txt").read_text(encoding="utf-8") == "ok"


def test_mark_burned_missing_profile_is_noop(tmp_path: Path) -> None:
    swapper = _make_swapper(tmp_path)

    burned_path = swapper.mark_burned("ghost")

    assert burned_path == tmp_path / "chrome-profiles" / "ghost"
    assert list((tmp_path / "chrome-profiles").glob("ghost.burned-*")) == []


def test_available_credentials_excludes_burned_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_file = tmp_path / "profiles_ultra.txt"
    credentials_file.write_text(
        "\n".join(
            [
                "C:/profiles/alice|alice@example.com|pw1|totp1|recovery1",
                "C:/profiles/bob|foo.bar+test@x.sbs|pw2|totp2|recovery2",
                "C:/profiles/carol|j\u00f6hn.doe@example.com|pw3||",
            ]
        ),
        encoding="utf-8",
    )
    profile_base_dir = tmp_path / "chrome-profiles"
    profile_base_dir.mkdir()
    (profile_base_dir / "alice.burned-1714000000").mkdir()
    monkeypatch.setenv("FLOW_PROFILE_LIST_FILE", str(credentials_file))
    swapper = _make_swapper(tmp_path, tmp_path / "unused.txt")

    entries = swapper.available_credentials()

    assert [entry.profile_name for entry in entries] == ["foo.bar+test", "j\u00f6hn.doe"]
    assert [entry.email for entry in entries] == [
        "foo.bar+test@x.sbs",
        "j\u00f6hn.doe@example.com",
    ]


def test_pick_next_fresh_returns_first_unwarmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_file = tmp_path / "profiles_ultra.txt"
    credentials_file.write_text(
        "\n".join(
            [
                "C:/profiles/alice|alice@example.com|pw1",
                "C:/profiles/bob|bob@example.com|pw2",
                "C:/profiles/carol|carol@example.com|pw3",
            ]
        ),
        encoding="utf-8",
    )
    profile_base_dir = tmp_path / "chrome-profiles"
    (profile_base_dir / "alice").mkdir(parents=True)
    monkeypatch.setenv("FLOW_PROFILE_LIST_FILE", str(credentials_file))
    swapper = _make_swapper(tmp_path)

    assert swapper.pick_next_fresh() == "bob"


def test_pick_next_fresh_returns_none_when_all_warmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    credentials_file = tmp_path / "profiles_ultra.txt"
    credentials_file.write_text(
        "\n".join(
            [
                "C:/profiles/alice|alice@example.com|pw1",
                "C:/profiles/bob|bob@example.com|pw2",
            ]
        ),
        encoding="utf-8",
    )
    profile_base_dir = tmp_path / "chrome-profiles"
    (profile_base_dir / "alice").mkdir(parents=True)
    (profile_base_dir / "bob").mkdir(parents=True)
    monkeypatch.setenv("FLOW_PROFILE_LIST_FILE", str(credentials_file))
    swapper = _make_swapper(tmp_path)

    assert swapper.pick_next_fresh() is None


def test_warm_new_profile_returns_true_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = _make_swapper(tmp_path)

    def fake_run(cmd, cwd, env, timeout, check):
        assert cmd == [sys.executable, "scripts/warm_profile.py", "fresh"]
        assert cwd == swapper.repo_root
        assert timeout == 45
        cookies = swapper.profile_base_dir / "fresh" / "Default" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"cookies")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_run)

    assert swapper.warm_new_profile("fresh", timeout=45) is True


def test_warm_new_profile_passes_expected_env_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = _make_swapper(tmp_path)
    monkeypatch.delenv("CHROME_USER_DATA_DIR", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("FLOW_REAL_CHROME", raising=False)
    monkeypatch.delenv("FLOW_PROFILE_LIST_FILE", raising=False)
    monkeypatch.delenv("FLOW_USE_BASE_PROFILE", raising=False)

    captured_env: dict[str, str] = {}

    def fake_run(cmd, cwd, env, timeout, check):
        captured_env.update(env)
        cookies = swapper.profile_base_dir / "fresh" / "Default" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"cookies")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_run)

    assert swapper.warm_new_profile("fresh") is True
    assert captured_env["CHROME_USER_DATA_DIR"] == str(swapper.profile_base_dir)
    assert captured_env["DISPLAY"] == ":99"
    assert captured_env["FLOW_REAL_CHROME"] == "1"
    assert captured_env["FLOW_PROFILE_LIST_FILE"] == str(swapper.credentials_file)
    assert captured_env["FLOW_USE_BASE_PROFILE"] == "1"


def test_warm_new_profile_cookie_check_uses_profile_base_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile_base_dir = tmp_path / "custom-profiles"
    credentials_file = tmp_path / "profiles_ultra.txt"
    swapper = ProfileSwapper(
        profile_base_dir=profile_base_dir,
        credentials_file=credentials_file,
    )

    def fake_run(cmd, cwd, env, timeout, check):
        cookies = profile_base_dir / "fresh" / "Default" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"cookies")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_run)

    assert swapper.warm_new_profile("fresh") is True


def test_warm_new_profile_returns_false_on_nonzero_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = _make_swapper(tmp_path)

    def fake_run(cmd, cwd, env, timeout, check):
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_run)

    assert swapper.warm_new_profile("fresh") is False


def test_warm_new_profile_returns_false_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = _make_swapper(tmp_path)

    def fake_run(cmd, cwd, env, timeout, check):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=timeout)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_run)

    assert swapper.warm_new_profile("fresh") is False


def test_swap_burned_full_flow(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    swapper = _make_swapper(tmp_path)
    calls: list[tuple[str, str]] = []

    def fake_mark_burned(profile_name: str) -> Path:
        calls.append(("mark_burned", profile_name))
        return tmp_path / "chrome-profiles" / f"{profile_name}.burned-1714000000"

    def fake_pick_next_fresh() -> str | None:
        calls.append(("pick_next_fresh", ""))
        return "fresh"

    def fake_warm_new_profile(profile_name: str, timeout: int = 180) -> bool:
        calls.append(("warm_new_profile", profile_name))
        assert timeout == 180
        return True

    monkeypatch.setattr(swapper, "mark_burned", fake_mark_burned)
    monkeypatch.setattr(swapper, "pick_next_fresh", fake_pick_next_fresh)
    monkeypatch.setattr(swapper, "warm_new_profile", fake_warm_new_profile)

    assert swapper.swap_burned("burned") == "fresh"
    assert calls == [
        ("mark_burned", "burned"),
        ("pick_next_fresh", ""),
        ("warm_new_profile", "fresh"),
    ]


def test_swap_burned_returns_none_when_pool_exhausted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    swapper = _make_swapper(tmp_path)

    monkeypatch.setattr(swapper, "mark_burned", lambda profile_name: tmp_path / profile_name)
    monkeypatch.setattr(swapper, "pick_next_fresh", lambda: None)
    monkeypatch.setattr(
        swapper,
        "warm_new_profile",
        lambda profile_name, timeout=180: pytest.fail("warm_new_profile should not run"),
    )

    assert swapper.swap_burned("burned") is None


# ---------------------------------------------------------------------------
# wipe_profile — PermissionError fallback to privileged helper
# ---------------------------------------------------------------------------


def test_wipe_profile_falls_back_on_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shutil.rmtree raises PermissionError → sudo helper called, returns True."""
    swapper = _make_swapper(tmp_path)
    profile_dir = swapper.profile_base_dir / "ngoctuandt20"
    profile_dir.mkdir(parents=True)

    subprocess_calls: list[list[str]] = []

    def fake_rmtree(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    def fake_subprocess_run(cmd, *, check, timeout):
        subprocess_calls.append(list(cmd))
        # Simulate successful removal by deleting the dir ourselves.
        import shutil as _shutil
        if profile_dir.exists():
            _shutil.rmtree.__wrapped__(profile_dir) if hasattr(_shutil.rmtree, "__wrapped__") else None
        profile_dir.rmdir() if profile_dir.exists() else None
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.shutil.rmtree", fake_rmtree)
    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_subprocess_run)

    result = swapper.wipe_profile("ngoctuandt20")

    assert result is True
    assert subprocess_calls == [
        ["sudo", "-n", swapper._PURGE_HELPER, "ngoctuandt20"],
    ]


def test_wipe_profile_returns_false_if_fallback_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """rmtree raises PermissionError AND helper fails → returns False."""
    swapper = _make_swapper(tmp_path)
    profile_dir = swapper.profile_base_dir / "ngoctuandt20"
    profile_dir.mkdir(parents=True)

    def fake_rmtree(path, *args, **kwargs):
        raise PermissionError(13, "Permission denied", str(path))

    def fake_subprocess_run(cmd, *, check, timeout):
        # Helper returns non-zero; dir still exists.
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr("worker.profile_swapper.shutil.rmtree", fake_rmtree)
    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_subprocess_run)

    result = swapper.wipe_profile("ngoctuandt20")

    assert result is False
    # Profile dir was NOT removed (helper didn't clean it up).
    assert profile_dir.exists()


def test_wipe_profile_no_subprocess_when_rmtree_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Happy path: rmtree succeeds → subprocess.run is never called."""
    swapper = _make_swapper(tmp_path)
    profile_dir = swapper.profile_base_dir / "ngoctuandt20"
    profile_dir.mkdir(parents=True)

    subprocess_calls: list[list[str]] = []

    def fake_subprocess_run(cmd, **kwargs):
        subprocess_calls.append(list(cmd))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_subprocess_run)

    result = swapper.wipe_profile("ngoctuandt20")

    assert result is True
    assert subprocess_calls == [], "subprocess.run must not be called when rmtree succeeds"


# ---------------------------------------------------------------------------
# wipe_profile — burned-archive sudo fallback verification
# ---------------------------------------------------------------------------


def test_wipe_profile_archive_sudo_fallback_logged_but_does_not_block(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Archive PermissionError + sudo helper invoked but archive persists
    → error is logged but wipe_profile still returns True (non-blocking).

    The main profile dir is gone (critical path succeeds).  The leftover
    archive is logged as an error so operators can investigate, but it must
    NOT cause wipe_profile to return False — doing so would prevent re-warm.
    """
    import logging

    swapper = _make_swapper(tmp_path)
    # Main profile dir — will be removed by real rmtree (only archive is broken).
    profile_dir = swapper.profile_base_dir / "ngoctuandt20"
    profile_dir.mkdir(parents=True)
    # Burned archive that will survive the sudo helper call.
    archive_dir = swapper.profile_base_dir / "ngoctuandt20.burned-1714000000"
    archive_dir.mkdir(parents=True)

    real_rmtree = __import__("shutil").rmtree

    def selective_rmtree(path, *args, **kwargs):
        # Allow removal of the main profile dir; raise for the archive.
        if Path(path) == archive_dir:
            raise PermissionError(13, "Permission denied", str(path))
        real_rmtree(path, *args, **kwargs)

    def fake_subprocess_run(cmd, *, check, timeout):
        # Helper exits 0 but does NOT actually remove the archive.
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.shutil.rmtree", selective_rmtree)
    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_subprocess_run)

    with caplog.at_level(logging.ERROR, logger="worker.profile_swapper"):
        result = swapper.wipe_profile("ngoctuandt20")

    assert result is True, "archive cleanup failure must not block wipe_profile"
    assert archive_dir.exists(), "archive was intentionally left in place by fake helper"
    assert any(
        "Sudo fallback failed to remove burned archive" in record.message
        for record in caplog.records
        if record.levelno == logging.ERROR
    ), "must log an ERROR when the sudo fallback leaves the archive in place"


def test_wipe_profile_archive_sudo_fallback_success_no_error_logged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Archive PermissionError + sudo helper actually removes the archive
    → no 'Sudo fallback failed' error is logged and wipe_profile returns True.
    """
    import logging

    swapper = _make_swapper(tmp_path)
    profile_dir = swapper.profile_base_dir / "ngoctuandt20"
    profile_dir.mkdir(parents=True)
    archive_dir = swapper.profile_base_dir / "ngoctuandt20.burned-1714000000"
    archive_dir.mkdir(parents=True)

    real_rmtree = __import__("shutil").rmtree

    def selective_rmtree(path, *args, **kwargs):
        if Path(path) == archive_dir:
            raise PermissionError(13, "Permission denied", str(path))
        real_rmtree(path, *args, **kwargs)

    def fake_subprocess_run(cmd, *, check, timeout):
        # Helper exits 0 AND removes the archive.
        real_rmtree(str(archive_dir))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("worker.profile_swapper.shutil.rmtree", selective_rmtree)
    monkeypatch.setattr("worker.profile_swapper.subprocess.run", fake_subprocess_run)

    with caplog.at_level(logging.ERROR, logger="worker.profile_swapper"):
        result = swapper.wipe_profile("ngoctuandt20")

    assert result is True
    assert not archive_dir.exists()
    assert not any(
        "Sudo fallback failed to remove burned archive" in record.message
        for record in caplog.records
        if record.levelno == logging.ERROR
    ), "must NOT log an error when the sudo fallback successfully removes the archive"
