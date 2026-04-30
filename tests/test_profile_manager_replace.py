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
