"""Unit tests for worker.main.preflight_profiles.

The preflight catches the most common DX trap: running the worker from a
fresh git worktree where ./chrome-profiles/<name>/ exists but contains no
cookies (auto-mkdir'd by some tooling). Without this guard the worker
would march all the way to Flow and fail with a misleading
"+ New project button missing" error — see PR #61 live-verify session.
"""

from __future__ import annotations

import sqlite3

import pytest

from worker.main import _profile_looks_warm, preflight_profiles


# ---- _profile_looks_warm -------------------------------------------------


def test_profile_looks_warm_missing_dir(tmp_path):
    assert _profile_looks_warm(tmp_path / "nope") is False


def test_profile_looks_warm_empty_dir(tmp_path):
    profile = tmp_path / "ngoctuandt20"
    profile.mkdir()
    assert _profile_looks_warm(profile) is False


def test_profile_looks_warm_default_dir_no_cookies(tmp_path):
    profile = tmp_path / "ngoctuandt20"
    (profile / "Default").mkdir(parents=True)
    assert _profile_looks_warm(profile) is False


@pytest.mark.parametrize("rel_path", ["Default/Cookies", "Default/Network/Cookies"])
def test_profile_looks_warm_with_cookies(tmp_path, rel_path):
    profile = tmp_path / "ngoctuandt20"
    cookies = profile / rel_path
    cookies.parent.mkdir(parents=True)
    # A real Chrome Cookies file is a SQLite DB, but we only check size > 0.
    cookies.write_bytes(b"SQLite format 3\x00fake-cookies-payload")
    assert _profile_looks_warm(profile) is True


def test_profile_looks_warm_zero_byte_cookies(tmp_path):
    """An empty Cookies file (0 bytes) shouldn't pass."""
    profile = tmp_path / "ngoctuandt20"
    cookies = profile / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.touch()
    assert _profile_looks_warm(profile) is False


def test_profile_looks_warm_real_sqlite(tmp_path):
    """A real (empty schema) SQLite file should pass — header alone is non-zero."""
    profile = tmp_path / "ngoctuandt20"
    cookies = profile / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    conn = sqlite3.connect(str(cookies))
    conn.execute("CREATE TABLE meta (k TEXT)")
    conn.commit()
    conn.close()
    assert cookies.stat().st_size > 0
    assert _profile_looks_warm(profile) is True


# ---- preflight_profiles --------------------------------------------------


def test_preflight_missing_base_dir(tmp_path):
    problems = preflight_profiles(str(tmp_path / "no-such-dir"), ["alice"])
    assert len(problems) == 1
    assert "does not exist" in problems[0]


def test_preflight_missing_profile(tmp_path):
    problems = preflight_profiles(str(tmp_path), ["alice"])
    assert len(problems) == 1
    assert "missing" in problems[0].lower()
    assert "alice" in problems[0]


def test_preflight_unwarmed_profile(tmp_path):
    """Empty profile dir (the worktree gotcha) must be flagged."""
    (tmp_path / "alice").mkdir()
    problems = preflight_profiles(str(tmp_path), ["alice"])
    assert len(problems) == 1
    assert "no cookies" in problems[0]
    assert "warm_profile.py alice" in problems[0]


def test_preflight_all_warm(tmp_path):
    for name in ("alice", "bob"):
        cookies = tmp_path / name / "Default" / "Cookies"
        cookies.parent.mkdir(parents=True)
        cookies.write_bytes(b"SQLite format 3\x00")
    problems = preflight_profiles(str(tmp_path), ["alice", "bob"])
    assert problems == []


def test_profile_looks_warm_swallows_oserror(tmp_path, monkeypatch):
    """Symlink loops, permission denied, or offline network drives must
    degrade to False, not crash preflight. (Codex review #8.)"""
    profile = tmp_path / "alice"
    profile.mkdir()

    real_is_file = type(profile).is_file

    def boom(self):
        if "Cookies" in self.name:
            raise PermissionError(f"simulated EACCES on {self}")
        return real_is_file(self)

    monkeypatch.setattr(type(profile), "is_file", boom)
    # Should return False, not raise.
    assert _profile_looks_warm(profile) is False


def test_preflight_mixed_one_warm_one_cold(tmp_path):
    cookies = tmp_path / "alice" / "Default" / "Cookies"
    cookies.parent.mkdir(parents=True)
    cookies.write_bytes(b"SQLite format 3\x00")
    (tmp_path / "bob").mkdir()  # empty / cold
    problems = preflight_profiles(str(tmp_path), ["alice", "bob"])
    assert len(problems) == 1
    assert "bob" in problems[0]
    assert "alice" not in problems[0]
