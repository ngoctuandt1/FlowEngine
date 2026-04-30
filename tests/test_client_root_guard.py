import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from flow import client as client_module


def _require_linux() -> None:
    if client_module._IS_WINDOWS or client_module.platform.system() != "Linux":
        pytest.skip("Linux-only root guard tests")


def _new_client():
    client = client_module.FlowClient.__new__(client_module.FlowClient)
    client.profile_name = "root-guard"
    client.profile_path = Path("/tmp/root-guard-profile")
    client.headless = True
    client.real_chrome = True
    client.debug_port = 19300
    client.action_delay_ms = 0
    client.download_dir = Path("/tmp/root-guard-downloads")
    client.page = None
    client.context = None
    client.browser = None
    client._pw = None
    client._chrome_proc = None
    client._temp_profile = Path("/tmp/root-guard-temp")
    client._video_urls = []
    client._calls = []
    client._media_id_events = []
    client._gen_id = None
    client._account_info = None
    client._hooks_bound = False
    return client


def _fake_browser_stack():
    page = SimpleNamespace(url="about:blank", on=MagicMock())
    context = SimpleNamespace(pages=[page])
    browser = SimpleNamespace(contexts=[context])
    return page, context, browser


def _force_non_docker(monkeypatch) -> None:
    real_exists = client_module.Path.exists

    def fake_exists(path):
        if str(path) == "/.dockerenv":
            return False
        return real_exists(path)

    monkeypatch.setattr(client_module.Path, "exists", fake_exists)
    monkeypatch.delenv("IS_DOCKER", raising=False)


def _force_docker(monkeypatch) -> None:
    monkeypatch.setenv("IS_DOCKER", "1")


@pytest.mark.parametrize("args", [[], ["--no-sandbox"]], ids=["no-flag", "preexisting-flag"])
def test_apply_root_sandbox_guard_refuses_root_without_opt_in(monkeypatch, args):
    _require_linux()

    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)

    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root without --no-sandbox"):
        client_module._apply_root_sandbox_guard(list(args))


def test_apply_root_sandbox_guard_logs_opt_in_warning(monkeypatch, caplog):
    _require_linux()

    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.setenv("FLOW_ALLOW_ROOT_NO_SANDBOX", "1")

    with caplog.at_level(logging.WARNING):
        args = client_module._apply_root_sandbox_guard(["--no-first-run"])

    assert "--no-sandbox" in args
    assert "FLOW_ALLOW_ROOT_NO_SANDBOX=1" in caplog.text


async def test_start_cdp_refuses_root_without_opt_in(monkeypatch):
    _require_linux()

    client = _new_client()
    popen = MagicMock()

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module, "_find_chrome_executable", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)
    monkeypatch.setattr(client_module.subprocess, "Popen", popen)

    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root without --no-sandbox"):
        await client._start_cdp()

    popen.assert_not_called()


async def test_start_cdp_appends_no_sandbox_for_root_opt_in(monkeypatch):
    _require_linux()

    client = _new_client()
    page, context, browser = _fake_browser_stack()
    connect = AsyncMock(return_value=browser)
    popen = MagicMock(return_value=SimpleNamespace(pid=1234))

    client._pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    client._wait_for_port = AsyncMock(return_value=True)

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module, "_find_chrome_executable", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.setenv("FLOW_ALLOW_ROOT_NO_SANDBOX", "1")
    monkeypatch.setattr(client_module.subprocess, "Popen", popen)

    await client._start_cdp()

    cmd = popen.call_args.args[0]
    assert cmd[-1] == "--no-sandbox"
    assert "--no-sandbox" in cmd
    assert client.context is context
    assert client.browser is browser
    assert client.page is page


async def test_start_cdp_non_root_does_not_add_no_sandbox(monkeypatch):
    _require_linux()

    client = _new_client()
    page, context, browser = _fake_browser_stack()
    connect = AsyncMock(return_value=browser)
    popen = MagicMock(return_value=SimpleNamespace(pid=1234))

    client._pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    client._wait_for_port = AsyncMock(return_value=True)

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module, "_find_chrome_executable", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)
    monkeypatch.setattr(client_module.subprocess, "Popen", popen)

    await client._start_cdp()

    cmd = popen.call_args.args[0]
    assert "--no-sandbox" not in cmd


async def test_start_persistent_refuses_root_without_opt_in(monkeypatch):
    _require_linux()

    client = _new_client()
    launch = AsyncMock()
    client.real_chrome = False
    client._pw = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch)
    )

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)
    _force_non_docker(monkeypatch)

    with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root without --no-sandbox"):
        await client._start_persistent()

    launch.assert_not_called()


async def test_start_persistent_appends_no_sandbox_for_root_opt_in(monkeypatch):
    _require_linux()

    client = _new_client()
    client.real_chrome = False
    page = SimpleNamespace(url="about:blank", on=MagicMock())
    context = SimpleNamespace(pages=[page])
    launch = AsyncMock(return_value=context)
    client._pw = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch)
    )

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    monkeypatch.setenv("FLOW_ALLOW_ROOT_NO_SANDBOX", "1")
    _force_non_docker(monkeypatch)

    await client._start_persistent()

    args = launch.call_args.kwargs["args"]
    assert args[-1] == "--no-sandbox"
    assert "--no-sandbox" in args
    assert client.context is context
    assert client.page is page


@pytest.mark.parametrize(
    ("allow_root_opt_in", "should_raise"),
    [(False, True), (True, False)],
    ids=["docker-root-no-opt-in", "docker-root-opt-in"],
)
async def test_start_persistent_root_in_docker_requires_explicit_opt_in(
    monkeypatch, allow_root_opt_in, should_raise
):
    _require_linux()

    client = _new_client()
    client.real_chrome = False
    page = SimpleNamespace(url="about:blank", on=MagicMock())
    context = SimpleNamespace(pages=[page])
    launch = AsyncMock(return_value=context)
    client._pw = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch)
    )

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 0)
    _force_docker(monkeypatch)
    if allow_root_opt_in:
        monkeypatch.setenv("FLOW_ALLOW_ROOT_NO_SANDBOX", "1")
    else:
        monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)

    if should_raise:
        with pytest.raises(RuntimeError, match="Refusing to launch Chrome as root without --no-sandbox"):
            await client._start_persistent()
        launch.assert_not_called()
        return

    await client._start_persistent()

    args = launch.call_args.kwargs["args"]
    assert args.count("--no-sandbox") == 1
    assert "--disable-setuid-sandbox" in args
    assert "--disable-dev-shm-usage" in args
    assert client.context is context
    assert client.page is page


async def test_start_persistent_non_root_does_not_add_no_sandbox(monkeypatch):
    _require_linux()

    client = _new_client()
    client.real_chrome = False
    page = SimpleNamespace(url="about:blank", on=MagicMock())
    context = SimpleNamespace(pages=[page])
    launch = AsyncMock(return_value=context)
    client._pw = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch)
    )

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.os, "geteuid", lambda: 1000)
    monkeypatch.delenv("FLOW_ALLOW_ROOT_NO_SANDBOX", raising=False)
    _force_non_docker(monkeypatch)

    await client._start_persistent()

    args = launch.call_args.kwargs["args"]
    assert "--no-sandbox" not in args
