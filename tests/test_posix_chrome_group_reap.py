from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

from flow import client as client_module


def _new_client() -> client_module.FlowClient:
    client = client_module.FlowClient.__new__(client_module.FlowClient)
    client.profile_name = "posix-reap"
    client.profile_path = Path("/tmp/chrome-profiles/posix-reap")
    client.headless = True
    client.real_chrome = True
    client.debug_port = 19300
    client.action_delay_ms = 0
    client.download_dir = Path("/tmp/posix-reap-downloads")
    client.page = None
    client.context = None
    client.browser = None
    client._pw = None
    client._chrome_proc = None
    client._temp_profile = Path("/tmp/flow_posix_reap")
    client._video_urls = []
    client._calls = []
    client._media_id_events = []
    client._gen_id = None
    client._account_info = None
    client._hooks_bound = False
    return client


def _fake_browser_stack():
    page = SimpleNamespace(url="about:blank", on=MagicMock(), route=AsyncMock())
    context = SimpleNamespace(pages=[page])
    browser = SimpleNamespace(contexts=[context])
    return page, context, browser


async def test_start_cdp_uses_start_new_session_on_posix(monkeypatch):
    client = _new_client()
    page, context, browser = _fake_browser_stack()
    connect = AsyncMock(return_value=browser)
    popen = MagicMock(return_value=SimpleNamespace(pid=4321))

    client._pw = SimpleNamespace(chromium=SimpleNamespace(connect_over_cdp=connect))
    client._wait_for_port = AsyncMock(return_value=True)

    monkeypatch.setattr(client, "_prepare_profile", lambda: None)
    monkeypatch.setattr(
        client_module,
        "_find_chrome_executable",
        lambda: "/usr/bin/google-chrome",
    )
    monkeypatch.setattr(client_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(client_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(client_module.subprocess, "Popen", popen)

    await client._start_cdp()

    assert popen.call_args.kwargs["start_new_session"] is True
    assert "creationflags" not in popen.call_args.kwargs
    assert client.context is context
    assert client.browser is browser
    assert client.page is page


def test_terminate_chrome_proc_kills_owned_posix_group(monkeypatch):
    client = _new_client()
    proc = MagicMock()
    proc.pid = 4321
    proc.poll.return_value = None
    proc.wait.return_value = None
    client._chrome_proc = proc

    killpg = MagicMock()
    sigterm = getattr(client_module.signal, "SIGTERM", 15)
    monkeypatch.setattr(client_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(client_module.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(client_module.os, "getpgid", lambda pid: 4321, raising=False)
    monkeypatch.setattr(client_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(client_module, "_read_proc_comm", lambda pid: "chrome")
    monkeypatch.setattr(
        client_module,
        "_read_proc_cmdline",
        lambda pid: [
            f"--user-data-dir={client._temp_profile}",
            "--remote-debugging-port=19300",
        ],
    )

    client._terminate_chrome_proc()

    killpg.assert_called_once_with(4321, sigterm)
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()
    assert client._chrome_proc is None


def test_terminate_chrome_proc_escalates_to_sigkill_on_timeout(monkeypatch):
    client = _new_client()
    proc = MagicMock()
    proc.pid = 4321
    proc.poll.return_value = None
    proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="chrome", timeout=5),
        None,
    ]
    client._chrome_proc = proc

    killpg = MagicMock()
    sigterm = getattr(client_module.signal, "SIGTERM", 15)
    sigkill = getattr(client_module.signal, "SIGKILL", 9)
    monkeypatch.setattr(client_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(client_module.signal, "SIGTERM", sigterm, raising=False)
    monkeypatch.setattr(client_module.signal, "SIGKILL", sigkill, raising=False)
    monkeypatch.setattr(client_module.os, "getpgid", lambda pid: 4321, raising=False)
    monkeypatch.setattr(client_module.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(client_module, "_read_proc_comm", lambda pid: "chrome")
    monkeypatch.setattr(
        client_module,
        "_read_proc_cmdline",
        lambda pid: [
            f"--user-data-dir={client._temp_profile}",
            "--remote-debugging-port=19300",
        ],
    )

    client._terminate_chrome_proc()

    assert killpg.call_args_list == [
        call(4321, sigterm),
        call(4321, sigkill),
    ]
    proc.kill.assert_not_called()
    assert proc.wait.call_count == 2
