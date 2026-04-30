from pathlib import PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import flow.diagnostics as diagnostics
from flow import wait
from flow.operations import _base
from worker import dispatcher


class _ProfileManagerStub:
    def mark_busy(self, profile, job_id):
        return None

    def mark_available(self, profile):
        return None


class _ProjectLockStub:
    def acquire(self, project_url, job_id):
        return True

    def release(self, project_url):
        return None


def _install_capture_mock(monkeypatch) -> AsyncMock:
    capture_mock = AsyncMock(return_value=PurePosixPath("/tmp/fake.png"))
    monkeypatch.setattr(diagnostics, "capture_failure", capture_mock)
    return capture_mock


def _make_wait_client(*, calls=None):
    page = MagicMock()
    page.url = "https://labs.google/fx/tools/flow/project/proj/edit/media"
    return SimpleNamespace(
        page=page,
        _calls=calls or [],
        _video_urls=[],
        _media_id_events=[],
        _job_id="job-wait-1",
    )


def _make_disabled_button_page():
    button = MagicMock()
    button.is_visible = AsyncMock(return_value=True)
    button.is_enabled = AsyncMock(return_value=False)
    button.click = AsyncMock()

    locator = MagicMock()
    locator.first = button

    page = MagicMock()
    page.locator = MagicMock(return_value=locator)
    return page, button


async def test_wait_blocked_403_captures_failure(monkeypatch):
    capture_mock = _install_capture_mock(monkeypatch)
    monkeypatch.setattr(wait, "_inject_observer", AsyncMock())
    monkeypatch.setattr(wait, "detect_recaptcha_in_network", AsyncMock(return_value=None))

    client = _make_wait_client(
        calls=[{"url": "https://labs.google/fx/tools/flow/operations/123", "status": 403}]
    )

    result = await wait.wait_for_completion(client, job_type="extend-video", timeout=1)

    assert result["error"] == "blocked_403 [cap=/tmp/fake.png]"
    assert capture_mock.await_args.args[2] == "blocked_403"


async def test_wait_timeout_captures_failure(monkeypatch):
    capture_mock = _install_capture_mock(monkeypatch)
    monkeypatch.setattr(wait, "_inject_observer", AsyncMock())

    client = _make_wait_client()

    result = await wait.wait_for_completion(client, job_type="extend-video", timeout=-1)

    assert result["error"] == "timeout [cap=/tmp/fake.png]"
    assert capture_mock.await_args.args[2] == "timeout"


async def test_wait_recaptcha_raises_after_capture(monkeypatch):
    capture_mock = _install_capture_mock(monkeypatch)
    monkeypatch.setattr(wait, "_inject_observer", AsyncMock())
    monkeypatch.setattr(wait, "detect_recaptcha_in_network", AsyncMock(return_value="v3_invisible"))
    monkeypatch.setattr(
        wait,
        "first_recaptcha_call",
        lambda client: {"url": "https://labs.google/recaptcha-enterprise"},
    )

    client = _make_wait_client()

    with pytest.raises(wait.RecaptchaError, match="v3_invisible") as exc_info:
        await wait.wait_for_completion(client, job_type="extend-video", timeout=1)

    assert exc_info.value.kind == "v3_invisible"
    assert capture_mock.await_args.args[2].startswith("recaptcha_")


async def test_click_action_button_lockout_captures_failure(monkeypatch):
    capture_mock = _install_capture_mock(monkeypatch)
    page, button = _make_disabled_button_page()
    client = SimpleNamespace(_job_id="job-lockout-1")

    with pytest.raises(RuntimeError, match=r"extend-child lockout.*\[cap=/tmp/fake\.png\]"):
        await _base.click_action_button(page, ["Camera"], client=client)

    button.click.assert_not_called()
    assert capture_mock.await_args.args[2] == "extend_child_lockout"


async def test_failed_job_error_surfaces_capture_path(monkeypatch):
    _install_capture_mock(monkeypatch)

    async def failing_handler(job):
        page, _button = _make_disabled_button_page()
        client = SimpleNamespace(_job_id=job["id"])
        await _base.click_action_button(page, ["Camera"], client=client)

    monkeypatch.setitem(dispatcher.HANDLER_MAP, "camera-move", failing_handler)

    result = await dispatcher.dispatch_job(
        {
            "id": "job-cap-1",
            "type": "camera-move",
            "job_level": 1,
        },
        _ProfileManagerStub(),
        _ProjectLockStub(),
        manage_profile=False,
    )

    error_message = result.get("error_message", result["error"])
    assert result["status"] == "failed"
    assert "[cap=/tmp/fake.png]" in error_message
