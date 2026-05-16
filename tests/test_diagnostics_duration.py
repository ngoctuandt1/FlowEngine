from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from flow import diagnostics_duration


def test_ffprobe_duration_parses_valid_float(monkeypatch, tmp_path):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"mp4")

    def fake_run(args, **kwargs):
        assert args[:5] == [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
        ]
        assert args[-1] == str(video_path)
        assert kwargs["capture_output"] is True
        assert kwargs["text"] is True
        return SimpleNamespace(returncode=0, stdout="12.345000\n", stderr="")

    monkeypatch.setattr(diagnostics_duration.subprocess, "run", fake_run)

    assert diagnostics_duration.ffprobe_duration(video_path) == pytest.approx(12.345)


def test_ffprobe_duration_non_numeric_returns_zero_with_warning(
    monkeypatch,
    tmp_path,
    caplog,
):
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"mp4")
    monkeypatch.setattr(
        diagnostics_duration.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="N/A\n", stderr=""),
    )

    with caplog.at_level(logging.WARNING, logger="flow.diagnostics_duration"):
        duration = diagnostics_duration.ffprobe_duration(video_path)

    assert duration == 0.0
    assert "parse failed" in caplog.text


def test_ffprobe_duration_nonexistent_file_returns_zero_with_warning(
    monkeypatch,
    tmp_path,
    caplog,
):
    missing_path = tmp_path / "missing.mp4"
    run_called = False

    def fake_run(*args, **kwargs):
        nonlocal run_called
        run_called = True

    monkeypatch.setattr(diagnostics_duration.subprocess, "run", fake_run)

    with caplog.at_level(logging.WARNING, logger="flow.diagnostics_duration"):
        duration = diagnostics_duration.ffprobe_duration(missing_path)

    assert duration == 0.0
    assert run_called is False
    assert "file does not exist" in caplog.text


def test_assert_chain_duration_all_pass(monkeypatch):
    durations = {
        "l1.mp4": 8.1,
        "l2.mp4": 16.0,
        "l5.mp4": 39.8,
    }
    monkeypatch.setattr(
        diagnostics_duration,
        "ffprobe_duration",
        lambda path: durations[Path(path).name],
    )

    result = diagnostics_duration.assert_chain_duration([
        {"level": 1, "media_id": "media-l1", "path": Path("l1.mp4")},
        {"level": 2, "media_id": "media-l2", "path": Path("l2.mp4")},
        {"level": 5, "media_id": "media-l5", "path": Path("l5.mp4")},
    ])

    assert result["all_pass"] is True
    assert [row["pass"] for row in result["rows"]] == [True, True, True]
    assert result["rows"][2]["delta"] == pytest.approx(-0.2)


def test_assert_chain_duration_l5_fails(monkeypatch):
    durations = {
        "l1.mp4": 8.0,
        "l2.mp4": 16.0,
        "l3.mp4": 24.0,
        "l4.mp4": 32.0,
        "l5.mp4": 8.0,
    }
    monkeypatch.setattr(
        diagnostics_duration,
        "ffprobe_duration",
        lambda path: durations[Path(path).name],
    )

    result = diagnostics_duration.assert_chain_duration([
        {"level": level, "media_id": f"media-l{level}", "path": Path(f"l{level}.mp4")}
        for level in range(1, 6)
    ])

    assert result["all_pass"] is False
    assert result["rows"][4]["level"] == 5
    assert result["rows"][4]["expected"] == 40.0
    assert result["rows"][4]["actual"] == 8.0
    assert result["rows"][4]["delta"] == -32.0
    assert result["rows"][4]["pass"] is False


def test_assert_chain_duration_empty_downloads_passes(monkeypatch):
    monkeypatch.setattr(
        diagnostics_duration,
        "ffprobe_duration",
        lambda path: pytest.fail("ffprobe should not run"),
    )

    result = diagnostics_duration.assert_chain_duration([])

    assert result["all_pass"] is True
    assert result["rows"] == []


def test_assert_chain_duration_markdown_contains_columns_and_fail(monkeypatch):
    monkeypatch.setattr(diagnostics_duration, "ffprobe_duration", lambda path: 8.0)

    result = diagnostics_duration.assert_chain_duration([
        {"level": 5, "media_id": "abcdef1234567890", "path": Path("l5.mp4")},
    ])

    report = result["report_markdown"]
    assert "| Level | Expected | Actual | Δ | Pass | Media ID short |" in report
    assert "| L5 | 40.0s | 8.0s | -32.0s | FAIL | abcdef123456 |" in report


def test_write_duration_report_writes_and_overwrites(tmp_path):
    report_path = tmp_path / "nested" / "duration.md"

    diagnostics_duration.write_duration_report(
        {"report_markdown": "first\n"},
        report_path,
    )
    diagnostics_duration.write_duration_report(
        {"report_markdown": "second\n"},
        report_path,
    )

    assert report_path.read_text(encoding="utf-8") == "second\n"
