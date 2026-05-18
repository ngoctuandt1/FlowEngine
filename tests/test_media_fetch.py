from __future__ import annotations

import asyncio
import json
import platform
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _client_with_data_dir(monkeypatch, tmp_path, *, media_fetch_enabled: bool = True):
    if media_fetch_enabled:
        monkeypatch.setenv("FLOW_MEDIA_FETCH_ENABLED", "1")
    else:
        monkeypatch.delenv("FLOW_MEDIA_FETCH_ENABLED", raising=False)
    download_dir = (tmp_path / "downloads").resolve()
    download_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FLOW_DOWNLOAD_DIR", str(download_dir))
    monkeypatch.setenv("FLOW_YTDLP_BIN", "yt-dlp")  # bypass shutil.which lookup

    import server.routes.media_fetch
    import server.app

    monkeypatch.setattr(
        server.routes.media_fetch,
        "_ENABLED",
        media_fetch_enabled,
        raising=False,
    )
    return TestClient(server.app.app), download_dir, server.routes.media_fetch


def _install_public_dns(monkeypatch, media_fetch):
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (media_fetch.socket.AF_INET, None, None, "", ("93.184.216.34", 0))
        ],
    )


class _FakeProc:
    """Async mock of ``asyncio.subprocess.Process``."""

    def __init__(self, stdout: bytes, stderr: bytes, returncode: int, pid: int = 4242):
        self._stdout = stdout
        self._stderr = stderr
        self._returncode = returncode
        self.pid = pid
        self.killed = False
        self.terminated = False

    @property
    def returncode(self):
        return self._returncode

    async def communicate(self):
        return self._stdout, self._stderr

    async def wait(self):
        return self._returncode

    def kill(self):
        self.killed = True

    def terminate(self):
        self.terminated = True


def _patch_subprocess(monkeypatch, media_fetch, *, probe_info, download_writes_to: Path | None,
                     probe_rc: int = 0, download_rc: int = 0):
    """Wire up a fake ``asyncio.create_subprocess_exec`` for the two passes."""

    calls = {"args": [], "popen_kwargs": []}

    async def fake_create(*args, **kwargs):
        calls["args"].append(args)
        calls["popen_kwargs"].append(kwargs)
        binary, *cli_args = args
        if "--dump-single-json" in cli_args:
            return _FakeProc(json.dumps(probe_info).encode("utf-8"), b"", probe_rc)
        # Download pass: simulate output file creation.
        if download_writes_to is not None and download_rc == 0:
            download_writes_to.parent.mkdir(parents=True, exist_ok=True)
            download_writes_to.write_bytes(b"fake-video-bytes")
        return _FakeProc(b"", b"download-stderr" if download_rc else b"", download_rc)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)
    return calls


def _find_download_output_path(calls) -> Path:
    """Pull the ``-o <path>`` argument out of the download invocation."""
    for args in calls["args"]:
        cli = list(args[1:])
        if "-o" in cli:
            return Path(cli[cli.index("-o") + 1])
    raise AssertionError("download call never issued")


# ---------------------------------------------------------------------------
# URL validation tests (do not require subprocess mocking)
# ---------------------------------------------------------------------------


def test_fetch_url_disabled_by_default(temp_db_path, monkeypatch, tmp_path):
    client, _, _ = _client_with_data_dir(
        monkeypatch,
        tmp_path,
        media_fetch_enabled=False,
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 410
    assert response.json()["detail"] == (
        "endpoint disabled; set FLOW_MEDIA_FETCH_ENABLED=1 to enable with caveats"
    )


@pytest.mark.parametrize("url", ["ftp://example.com/video.mp4", "file:///tmp/video.mp4"])
def test_fetch_url_rejects_non_http_urls(temp_db_path, monkeypatch, tmp_path, url):
    client, _, _ = _client_with_data_dir(monkeypatch, tmp_path)

    response = client.post("/api/media/fetch-url", json={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url must use http or https"


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/video.mp4",
        "http://127.0.0.1/video.mp4",
        "http://10.0.0.25/video.mp4",
        "http://172.16.5.4/video.mp4",
        "http://192.168.1.10/video.mp4",
        "http://169.254.10.20/video.mp4",
        "http://[::1]/video.mp4",
        "http://[fe80::1]/video.mp4",
        "http://224.0.0.1/video.mp4",
        "http://service.internal/video.mp4",
    ],
)
def test_fetch_url_rejects_denied_hosts(temp_db_path, monkeypatch, tmp_path, url):
    client, _, _ = _client_with_data_dir(monkeypatch, tmp_path)

    response = client.post("/api/media/fetch-url", json={"url": url})

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url host is not allowed"


def test_fetch_url_rejects_invalid_max_height(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4", "max_height": 999},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, max_height must be one of 360, 480, 720, 1080"


def test_fetch_url_rejects_dns_rebinding_target(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (media_fetch.socket.AF_INET, None, None, "", ("10.0.0.8", 0))
        ],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url host is not allowed"


def test_fetch_url_rejects_cgnat_host(temp_db_path, monkeypatch, tmp_path):
    """100.64.0.0/10 (RFC 6598 CGNAT) — not covered by ``is_private``."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(
        media_fetch.socket,
        "getaddrinfo",
        lambda *a, **kw: [
            (media_fetch.socket.AF_INET, None, None, "", ("100.64.0.5", 0))
        ],
    )

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://cgnat.example.com/video.mp4"},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Value error, url host is not allowed"


# ---------------------------------------------------------------------------
# Subprocess-driven flow
# ---------------------------------------------------------------------------

def test_fetch_url_happy_path(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    download_dest_holder: dict[str, Path] = {}

    real_create = media_fetch.asyncio.create_subprocess_exec

    async def fake_create(*args, **kwargs):
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            info = {
                "title": "Example video",
                "duration": 42,
                "webpage_url": "https://www.youtube.com/watch?v=demo",
                "url": "https://cdn.example.com/stream.m3u8",
            }
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        # download pass: write the output mp4 the route checks for.
        dest = Path(cli[cli.index("-o") + 1])
        download_dest_holder["path"] = dest
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-video-bytes")
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://www.youtube.com/watch?v=demo", "max_height": 720},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    output_path = download_dir / body["output_path"]
    assert output_path.is_file()
    assert output_path.parent == download_dir / "fetched"
    assert body["title"] == "Example video"
    assert body["duration_seconds"] == 42
    assert body["source_url"] == "https://www.youtube.com/watch?v=demo"
    # No global monkeypatch leak — keep this assertion as a regression guard.
    assert media_fetch.asyncio.create_subprocess_exec is fake_create
    # Restore (test client teardown handles this, but verify the symbol is the
    # one we set, not a leftover from a previous patch).
    _ = real_create  # silence linter


def test_fetch_url_maps_download_error_to_502(temp_db_path, monkeypatch, tmp_path):
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    info = {"title": "ok", "duration": 1, "webpage_url": "https://example.com/", "url": "https://cdn.example.com/x"}

    async def fake_create(*args, **kwargs):
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        return _FakeProc(b"", b"boom C:\\secret\\path", 1)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 502
    assert response.json()["detail"] == "Failed to fetch media from source URL"
    # Sanitized — no leak of stderr details into the surfaced response body.
    assert "secret" not in response.text


def test_fetch_url_output_path_stays_under_download_dir(temp_db_path, monkeypatch, tmp_path):
    client, download_dir, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    info = {"title": "ok", "duration": 1, "webpage_url": "https://cdn.example.com/video.mp4"}

    async def fake_create(*args, **kwargs):
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        dest = Path(cli[cli.index("-o") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake-video-bytes")
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://cdn.example.com/video.mp4"},
    )

    assert response.status_code == 200, response.text
    output_path = download_dir / response.json()["output_path"]
    assert output_path.is_relative_to(download_dir.resolve())
    assert output_path.name.startswith("fetch_")
    assert output_path.suffix == ".mp4"


def test_fetch_url_probe_rejects_manifest_with_private_ip(temp_db_path, monkeypatch, tmp_path):
    """Probe pass-1 returns an info_dict pointing at AWS metadata; pass-2 must NOT run."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    download_called = {"yes": False}
    info = {
        "url": "https://attacker.example.com/page",
        "manifest_url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "formats": [
            {"url": "http://10.0.0.5/segment.ts"},
        ],
    }

    async def fake_create(*args, **kwargs):
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        download_called["yes"] = True
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://attacker.example.com/page"},
    )

    assert response.status_code in (400, 502)
    assert response.json()["detail"] != "Example video"
    assert download_called["yes"] is False, "download pass must not run when probe finds a bad URL"


def test_fetch_url_probe_timeout_kills_process_group(temp_db_path, monkeypatch, tmp_path):
    """Probe hangs → asyncio.wait_for raises TimeoutError → process group is killed."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    proc = _FakeProc(b"", b"", returncode=None, pid=99999)  # type: ignore[arg-type]
    # ``returncode`` of None signals the proc is still alive when killed.
    proc._returncode = None  # type: ignore[attr-defined]

    async def slow_communicate():
        await asyncio.sleep(10)
        return b"", b""

    proc.communicate = slow_communicate  # type: ignore[assignment]

    async def fake_create(*args, **kwargs):
        return proc

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)
    # Replace wait_for to fire instantly on the communicate() future.
    real_wait_for = media_fetch.asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        # Force a timeout on communicate(); allow proc.wait() to short-circuit.
        if timeout and timeout > 1:
            # Cancel the awaitable cleanly to avoid pending-task warnings.
            task = asyncio.ensure_future(awaitable)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, BaseException):
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(media_fetch.asyncio, "wait_for", fast_wait_for)

    # Record kill behaviour.
    killpg_calls = []

    def fake_killpg(pgid, sig):
        killpg_calls.append((pgid, sig))
        # Mark proc as dead so subsequent kills are no-ops.
        proc._returncode = -9  # type: ignore[attr-defined]

    monkeypatch.setattr(media_fetch.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(media_fetch.os, "getpgid", lambda pid: pid, raising=False)
    monkeypatch.setattr(media_fetch.platform, "system", lambda: "Linux")

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 502
    assert "timed out" in response.json()["detail"].lower()
    # POSIX path → killpg called exactly on our proc, not on PID 1 or others.
    assert killpg_calls, "killpg must fire on probe timeout"
    assert killpg_calls[0][0] == proc.pid  # signal value depends on platform stubs


def test_fetch_url_timeout_on_windows_uses_terminate_then_kill(temp_db_path, monkeypatch, tmp_path):
    """Windows path: no killpg, escalate via proc.terminate()/kill()."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    proc = _FakeProc(b"", b"", returncode=None, pid=12345)  # type: ignore[arg-type]
    proc._returncode = None  # type: ignore[attr-defined]

    async def slow_communicate():
        await asyncio.sleep(10)
        return b"", b""

    proc.communicate = slow_communicate  # type: ignore[assignment]

    async def fake_create(*args, **kwargs):
        return proc

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(media_fetch.platform, "system", lambda: "Windows")

    real_wait_for = media_fetch.asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        if timeout and timeout > 1:
            task = asyncio.ensure_future(awaitable)
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(media_fetch.asyncio, "wait_for", fast_wait_for)

    # killpg should NOT be called on Windows path; trap it to assert.
    def boom_killpg(*a, **kw):
        raise AssertionError("killpg must not run on Windows")

    monkeypatch.setattr(media_fetch.os, "killpg", boom_killpg, raising=False)

    # Override terminate/kill to track + mark process as exited.
    def fake_terminate():
        proc.terminated = True

    def fake_kill():
        proc.killed = True
        proc._returncode = -9  # type: ignore[attr-defined]

    proc.terminate = fake_terminate  # type: ignore[assignment]
    proc.kill = fake_kill  # type: ignore[assignment]

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 502
    assert proc.terminated is True
    assert proc.killed is True


def test_fetch_url_windows_timeout_kills_descendants(temp_db_path, monkeypatch, tmp_path):
    """Windows timeout: signal parent before snapshotting/killing descendants."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    proc = _FakeProc(b"", b"", returncode=None, pid=12345)  # type: ignore[arg-type]
    proc._returncode = None  # type: ignore[attr-defined]
    events: list[str] = []

    def terminate_parent():
        events.append("parent.terminate")
        proc.terminated = True

    def kill_parent():
        events.append("parent.kill")
        proc.killed = True

    proc.terminate = terminate_parent  # type: ignore[assignment]
    proc.kill = kill_parent  # type: ignore[assignment]

    async def slow_communicate():
        await asyncio.sleep(10)
        return b"", b""

    proc.communicate = slow_communicate  # type: ignore[assignment]

    async def fake_create(*args, **kwargs):
        return proc

    class FakeChild:
        def __init__(self):
            self.terminated = False
            self.killed = False

        def terminate(self):
            events.append("child.terminate")
            self.terminated = True

        def kill(self):
            events.append("child.kill")
            self.killed = True

    child = FakeChild()

    class FakeParent:
        def __init__(self, pid):
            self.pid = pid

        def children(self, recursive=False):
            assert self.pid == proc.pid
            assert recursive is True
            events.append("snapshot")
            assert events == ["parent.terminate", "snapshot"]
            return [child]

    def fake_wait_procs(procs, timeout):
        events.append("wait_descendants")
        return [], procs

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(media_fetch.platform, "system", lambda: "Windows")
    monkeypatch.setattr(media_fetch.psutil, "Process", FakeParent)
    monkeypatch.setattr(media_fetch.psutil, "wait_procs", fake_wait_procs)

    real_wait_for = media_fetch.asyncio.wait_for

    async def fast_wait_for(awaitable, timeout):
        if timeout and timeout > 1:
            task = asyncio.ensure_future(awaitable)
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise asyncio.TimeoutError()
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(media_fetch.asyncio, "wait_for", fast_wait_for)

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )

    assert response.status_code == 502
    assert child.terminated is True
    assert child.killed is True
    assert proc.terminated is True
    assert proc.killed is True
    assert events == [
        "parent.terminate",
        "snapshot",
        "child.terminate",
        "wait_descendants",
        "child.kill",
        "parent.kill",
    ]


def test_fetch_url_no_global_socket_monkeypatch(temp_db_path, monkeypatch, tmp_path):
    """Regression: round-2 patched ``socket.create_connection`` globally and
    leaked on timeout. Round-3 must never reassign that symbol."""
    import socket as real_socket

    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    info = {"title": "x", "duration": 1, "webpage_url": "https://example.com/", "url": "https://cdn.example.com/x"}

    async def fake_create(*args, **kwargs):
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        dest = Path(cli[cli.index("-o") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake")
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)

    original_create_connection = real_socket.create_connection
    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )
    assert response.status_code == 200, response.text
    # The symbol must be untouched — no leftover monkeypatch from the route.
    assert real_socket.create_connection is original_create_connection


def test_fetch_url_subprocess_uses_isolated_process_group(temp_db_path, monkeypatch, tmp_path):
    """Verify pass-1 and pass-2 each request a fresh process group."""
    client, _, media_fetch = _client_with_data_dir(monkeypatch, tmp_path)
    _install_public_dns(monkeypatch, media_fetch)

    seen_kwargs: list[dict] = []
    info = {"title": "x", "duration": 1, "webpage_url": "https://example.com/", "url": "https://cdn.example.com/x"}

    async def fake_create(*args, **kwargs):
        seen_kwargs.append(kwargs)
        cli = list(args[1:])
        if "--dump-single-json" in cli:
            return _FakeProc(json.dumps(info).encode("utf-8"), b"", 0)
        dest = Path(cli[cli.index("-o") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"fake")
        return _FakeProc(b"", b"", 0)

    monkeypatch.setattr(media_fetch.asyncio, "create_subprocess_exec", fake_create)
    monkeypatch.setattr(media_fetch.platform, "system", lambda: "Linux")

    response = client.post(
        "/api/media/fetch-url",
        json={"url": "https://example.com/video.mp4"},
    )
    assert response.status_code == 200
    assert len(seen_kwargs) == 2
    for kw in seen_kwargs:
        assert kw.get("start_new_session") is True, (
            "POSIX subprocess must set start_new_session=True so killpg "
            "only nukes yt-dlp + its descendants"
        )


def test_fetch_url_openapi_uses_typed_request_and_response(temp_db_path, monkeypatch, tmp_path):
    client, _, _ = _client_with_data_dir(monkeypatch, tmp_path)

    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/media/fetch-url"]["post"]
    request_body_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    response_schema = operation["responses"]["200"]["content"]["application/json"]["schema"]

    assert request_body_schema == {"$ref": "#/components/schemas/FetchUrlRequest"}
    assert response_schema == {"$ref": "#/components/schemas/FetchUrlResponse"}
    assert (
        "additionalProperties"
        not in schema["components"]["schemas"]["FetchUrlRequest"]
    )
