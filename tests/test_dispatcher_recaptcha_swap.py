import importlib
import logging
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock


class _ProfileManagerStub:
    def __init__(self):
        self.busy = []
        self.available = []
        self.replace_profile = Mock()
        self.remove_profile = Mock()

    def mark_busy(self, profile, job_id):
        self.busy.append((profile, job_id))

    def mark_available(self, profile):
        self.available.append(profile)


class _ProjectLockStub:
    def __init__(self):
        self.acquired = []
        self.released = []

    def acquire(self, project_url, job_id):
        self.acquired.append((project_url, job_id))
        return True

    def release(self, project_url):
        self.released.append(project_url)


class _FakeRecaptchaError(Exception):
    def __init__(self, kind: str, url: str | None = None):
        self.kind = kind
        self.url = url
        super().__init__(f"reCAPTCHA detected ({kind})")


class _LegacyRecaptchaError(Exception):
    pass


def _reload_dispatcher():
    sys.modules.pop("worker.profile_swapper", None)
    from worker import dispatcher as dispatcher_module

    return importlib.reload(dispatcher_module)


def _install_fake_swapper(monkeypatch, *, new_profile):
    calls: list[str] = []
    init_args: list[tuple[Path, Path]] = []

    class FakeProfileSwapper:
        def __init__(self, profile_base_dir: Path, credentials_file: Path):
            init_args.append((profile_base_dir, credentials_file))

        def swap_burned(self, old_name: str):
            calls.append(old_name)
            return new_profile

    fake_module = types.ModuleType("worker.profile_swapper")
    fake_module.ProfileSwapper = FakeProfileSwapper
    monkeypatch.setitem(sys.modules, "worker.profile_swapper", fake_module)
    return calls, init_args


async def _run_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


async def test_dispatch_job_swaps_burned_profile_on_recaptcha(
    monkeypatch,
    caplog,
    tmp_path,
):
    dispatcher = _reload_dispatcher()
    assert "worker.profile_swapper" not in sys.modules

    monkeypatch.delenv("FLOW_AUTO_REPLACE_PROFILES", raising=False)
    monkeypatch.setenv("CHROME_USER_DATA_DIR", str(tmp_path / "chrome-profiles"))
    monkeypatch.setenv(
        "FLOW_PROFILE_LIST_FILE",
        str(tmp_path / "profiles_ultra.txt"),
    )
    monkeypatch.setattr(dispatcher.asyncio, "to_thread", _run_to_thread)

    swap_calls, init_args = _install_fake_swapper(
        monkeypatch,
        new_profile="newprofile",
    )
    handler = AsyncMock(
        side_effect=_FakeRecaptchaError(
            "v3_invisible",
            "https://labs.google/recaptcha-enterprise",
        )
    )
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "text-to-video", handler)
    monkeypatch.setattr(dispatcher, "RecaptchaError", _FakeRecaptchaError)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-rec-1",
        "type": "text-to-video",
        "profile": "oldprofile",
        "job_level": 1,
    }

    with caplog.at_level(logging.INFO, logger="worker.dispatcher"):
        result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    handler.assert_awaited_once_with(job)
    assert result["status"] == "failed"
    assert result["error_message"] == "recaptcha_v3_invisible_burned_oldprofile"
    assert swap_calls == ["oldprofile"]
    assert init_args == [(
        (tmp_path / "chrome-profiles").resolve(),
        (tmp_path / "profiles_ultra.txt").resolve(),
    )]
    profile_mgr.replace_profile.assert_called_once_with("oldprofile", "newprofile")
    profile_mgr.remove_profile.assert_not_called()
    assert profile_mgr.available == []


async def test_dispatch_job_handles_legacy_recaptcha_error_signature(
    monkeypatch,
    caplog,
    tmp_path,
):
    dispatcher = _reload_dispatcher()
    assert "worker.profile_swapper" not in sys.modules

    monkeypatch.delenv("FLOW_AUTO_REPLACE_PROFILES", raising=False)
    monkeypatch.setenv("CHROME_USER_DATA_DIR", str(tmp_path / "chrome-profiles"))
    monkeypatch.setenv(
        "FLOW_PROFILE_LIST_FILE",
        str(tmp_path / "profiles_ultra.txt"),
    )
    monkeypatch.setattr(dispatcher.asyncio, "to_thread", _run_to_thread)

    swap_calls, _ = _install_fake_swapper(
        monkeypatch,
        new_profile="newprofile",
    )
    handler = AsyncMock(side_effect=_LegacyRecaptchaError("legacy message"))
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "text-to-video", handler)
    monkeypatch.setattr(dispatcher, "RecaptchaError", _LegacyRecaptchaError)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-rec-legacy",
        "type": "text-to-video",
        "profile": "oldprofile",
        "job_level": 1,
    }

    with caplog.at_level(logging.INFO, logger="worker.dispatcher"):
        result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    assert result["status"] == "failed"
    assert result["error_message"] == "recaptcha_unknown_burned_oldprofile"
    assert swap_calls == ["oldprofile"]
    profile_mgr.replace_profile.assert_called_once_with("oldprofile", "newprofile")
    profile_mgr.remove_profile.assert_not_called()


async def test_dispatch_job_removes_burned_profile_when_auto_replace_disabled(
    monkeypatch,
    caplog,
):
    dispatcher = _reload_dispatcher()

    monkeypatch.setenv("FLOW_AUTO_REPLACE_PROFILES", "0")
    monkeypatch.setattr(dispatcher, "RecaptchaError", _FakeRecaptchaError)

    swap_calls, _ = _install_fake_swapper(
        monkeypatch,
        new_profile="newprofile",
    )
    handler = AsyncMock(
        side_effect=_FakeRecaptchaError(
            "v3_invisible",
            "https://labs.google/recaptcha-enterprise",
        )
    )
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "text-to-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-rec-2",
        "type": "text-to-video",
        "profile": "oldprofile",
        "job_level": 1,
    }

    with caplog.at_level(logging.WARNING, logger="worker.dispatcher"):
        result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    assert result["status"] == "failed"
    assert result["error_message"] == "recaptcha_v3_invisible_burned_oldprofile"
    assert swap_calls == []
    profile_mgr.replace_profile.assert_not_called()
    profile_mgr.remove_profile.assert_called_once_with("oldprofile")
    assert profile_mgr.available == []
    assert "manual recovery needed" in caplog.text


async def test_dispatch_job_removes_burned_profile_when_replacement_unavailable(
    monkeypatch,
    caplog,
):
    dispatcher = _reload_dispatcher()

    monkeypatch.delenv("FLOW_AUTO_REPLACE_PROFILES", raising=False)
    monkeypatch.setattr(dispatcher, "RecaptchaError", _FakeRecaptchaError)
    monkeypatch.setattr(dispatcher.asyncio, "to_thread", _run_to_thread)

    swap_calls, _ = _install_fake_swapper(monkeypatch, new_profile=None)
    handler = AsyncMock(
        side_effect=_FakeRecaptchaError(
            "v3_invisible",
            "https://labs.google/recaptcha-enterprise",
        )
    )
    monkeypatch.setitem(dispatcher.HANDLER_MAP, "text-to-video", handler)

    profile_mgr = _ProfileManagerStub()
    project_lock = _ProjectLockStub()
    job = {
        "id": "job-rec-3",
        "type": "text-to-video",
        "profile": "oldprofile",
        "job_level": 1,
    }

    with caplog.at_level(logging.ERROR, logger="worker.dispatcher"):
        result = await dispatcher.dispatch_job(job, profile_mgr, project_lock)

    assert result["status"] == "failed"
    assert result["error_message"] == "recaptcha_v3_invisible_burned_oldprofile"
    assert swap_calls == ["oldprofile"]
    profile_mgr.replace_profile.assert_not_called()
    profile_mgr.remove_profile.assert_called_once_with("oldprofile")
    assert profile_mgr.available == []
    assert "recovery failed" in caplog.text or "swap failed" in caplog.text
