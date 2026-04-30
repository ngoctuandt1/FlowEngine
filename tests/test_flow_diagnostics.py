import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import flow.diagnostics as diagnostics


def _make_page(*, html: str = "<html><body>ok</body></html>", screenshot_error: Exception | None = None):
    page = MagicMock()
    page.is_closed = MagicMock(return_value=False)

    async def _screenshot(*, path, full_page, timeout):
        assert full_page is False
        assert timeout == 5000
        if screenshot_error is not None:
            raise screenshot_error
        Path(path).write_bytes(b"png-bytes")

    page.screenshot = AsyncMock(side_effect=_screenshot)
    page.content = AsyncMock(return_value=html)
    return page


def _make_client(*, calls, page):
    client = MagicMock()
    client._calls = calls
    client.page = page
    return client


def _sample_calls():
    return [
        {
            "url": "https://example.com/one",
            "status": 200,
            "method": "GET",
            "ts": 101.0,
            "body": {"result": "ok"},
        },
        {
            "url": "https://example.com/two",
            "status": 403,
            "method": "POST",
            "ts": 102.0,
            "body": "blocked",
        },
        {
            "url": "https://example.com/three",
            "status": 500,
            "method": "PUT",
            "ts": 103.0,
            "body": None,
        },
    ]


async def test_capture_failure_writes_png_network_and_html(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.delenv("FLOW_ERROR_CAPTURE", raising=False)
    monkeypatch.setattr(diagnostics.time, "time", lambda: 1700000000)

    page = _make_page(
        html='<html><body><img src="data:image/png;base64,AAAA">hello</body></html>',
    )
    client = _make_client(calls=_sample_calls(), page=page)

    result = await diagnostics.capture_failure(
        client,
        "abc12345-dead-beef-cafe-feedface0000",
        "blocked_403",
    )

    expected = tmp_path / "1700000000_abc12345_blocked_403.png"
    assert result == expected
    assert expected.exists()

    network_path = expected.with_suffix(".network.json")
    network_data = json.loads(network_path.read_text(encoding="utf-8"))
    assert len(network_data) == 3
    assert network_data[0] == {
        "url": "https://example.com/one",
        "status": 200,
        "method": "GET",
        "ts": 101.0,
        "body_preview": '{"result": "ok"}',
    }

    html_path = expected.with_suffix(".html")
    html = html_path.read_text(encoding="utf-8")
    assert html.startswith("<html")
    assert "data:image" not in html


async def test_capture_failure_disabled_by_env_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.setenv("FLOW_ERROR_CAPTURE", "0")

    page = _make_page()
    client = _make_client(calls=_sample_calls(), page=page)

    result = await diagnostics.capture_failure(client, "abc12345-dead", "blocked_403")

    assert result is None
    assert list(tmp_path.iterdir()) == []


async def test_capture_failure_screenshot_error_still_writes_other_artifacts(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.delenv("FLOW_ERROR_CAPTURE", raising=False)
    monkeypatch.setattr(diagnostics.time, "time", lambda: 1700000000)

    page = _make_page(screenshot_error=RuntimeError("page crashed"))
    client = _make_client(calls=_sample_calls(), page=page)

    result = await diagnostics.capture_failure(client, "abc12345-dead", "timeout")

    expected = tmp_path / "1700000000_abc12345_timeout.png"
    assert result == expected
    assert not expected.exists()
    assert expected.with_suffix(".network.json").exists()
    assert expected.with_suffix(".html").exists()


async def test_capture_failure_writes_empty_network_dump_for_no_calls(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.delenv("FLOW_ERROR_CAPTURE", raising=False)
    monkeypatch.setattr(diagnostics.time, "time", lambda: 1700000000)

    page = _make_page()
    client = _make_client(calls=[], page=page)

    result = await diagnostics.capture_failure(client, "abc12345-dead", "blocked_403")

    assert result == tmp_path / "1700000000_abc12345_blocked_403.png"
    network_data = json.loads(result.with_suffix(".network.json").read_text(encoding="utf-8"))
    assert network_data == []


async def test_capture_failure_redacts_obvious_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.delenv("FLOW_ERROR_CAPTURE", raising=False)
    monkeypatch.setattr(diagnostics.time, "time", lambda: 1700000000)

    page = _make_page()
    client = _make_client(
        calls=[
            {
                "url": "https://example.com/auth",
                "status": 401,
                "method": "POST",
                "ts": 104.0,
                "body": 'password=secret Authorization: Bearer abc123 {"password":"hidden"}',
            }
        ],
        page=page,
    )

    result = await diagnostics.capture_failure(client, "abc12345-dead", "blocked_403")

    preview = json.loads(result.with_suffix(".network.json").read_text(encoding="utf-8"))[0]["body_preview"]
    assert "secret" not in preview
    assert "abc123" not in preview
    assert "hidden" not in preview
    assert preview.count("***REDACTED***") >= 2


async def test_capture_failure_sanitizes_kind_in_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("FLOW_ERROR_CAPTURE_DIR", str(tmp_path))
    monkeypatch.delenv("FLOW_ERROR_CAPTURE", raising=False)
    monkeypatch.setattr(diagnostics.time, "time", lambda: 1700000000)

    page = _make_page()
    client = _make_client(calls=_sample_calls(), page=page)

    result = await diagnostics.capture_failure(
        client,
        "abc12345-dead",
        "Bad/Kind:Stuff",
    )

    assert result is not None
    assert "bad_kind_stuff" in result.name
    assert "/" not in result.name
    assert ":" not in result.name
