import logging
from unittest.mock import Mock

from worker.profile_manager import ProfileManager


def test_replace_profile_swaps_out_old_name():
    manager = ProfileManager("./chrome-profiles", ["a", "b", "c"])

    manager.replace_profile("b", "z")

    assert set(manager.profiles) == {"a", "c", "z"}
    assert "b" not in manager.profiles


def test_replace_profile_appends_when_old_name_missing():
    manager = ProfileManager("./chrome-profiles", ["a", "b", "c"])

    manager.replace_profile("missing", "x")

    assert set(manager.profiles) == {"a", "b", "c", "x"}


def test_remove_profile_removes_name_from_pool(caplog):
    manager = ProfileManager("./chrome-profiles", ["a", "b"])

    with caplog.at_level(logging.INFO, logger="worker.profile_manager"):
        manager.remove_profile("a")

    assert list(manager.profiles) == ["b"]
    assert "Profile removed from pool: a" in caplog.text


def test_remove_profile_missing_name_is_noop(caplog):
    manager = ProfileManager("./chrome-profiles", ["a"])

    with caplog.at_level(logging.WARNING, logger="worker.profile_manager"):
        manager.remove_profile("missing")

    assert list(manager.profiles) == ["a"]
    assert "remove_profile: profile 'missing' not in pool" in caplog.text


def test_replace_profile_delegates_to_remove_profile(monkeypatch):
    manager = ProfileManager("./chrome-profiles", ["a", "b", "c"])
    remove_profile = Mock(wraps=manager.remove_profile)
    monkeypatch.setattr(manager, "remove_profile", remove_profile)

    manager.replace_profile("b", "z")

    remove_profile.assert_called_once_with("b")
    assert set(manager.profiles) == {"a", "c", "z"}
