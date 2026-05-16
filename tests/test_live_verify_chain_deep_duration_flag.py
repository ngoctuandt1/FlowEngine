from __future__ import annotations

import logging
from pathlib import Path

import pytest

from scripts import live_verify_chain_deep


def _chain_results() -> list[dict]:
    return [
        {
            "status": "completed",
            "media_id": f"media-l{level}",
            "job_level": level,
            "output_files": [str(Path(f"l{level}.mp4"))],
        }
        for level in range(2, 6)
    ]


def test_parse_args_assert_duration_defaults_true():
    args = live_verify_chain_deep.parse_args(["ngoctuandt20"])

    assert args.assert_duration is True
    assert args.depth == 5
    assert args.duration_tolerance_sec == 2.0


def test_parse_args_no_assert_duration_sets_false():
    args = live_verify_chain_deep.parse_args([
        "ngoctuandt20",
        "--no-assert-duration",
    ])

    assert args.assert_duration is False


def test_duration_check_invoked_when_flag_on(monkeypatch):
    calls = []

    def fake_run_duration_assertion(downloads, *, profile, ts, tolerance_sec):
        calls.append({
            "downloads": downloads,
            "profile": profile,
            "ts": ts,
            "tolerance_sec": tolerance_sec,
        })
        return {"rows": downloads, "all_pass": True, "report_markdown": ""}

    monkeypatch.setattr(
        live_verify_chain_deep,
        "run_duration_assertion",
        fake_run_duration_assertion,
    )

    exit_code = live_verify_chain_deep.verify_chain_outputs(
        {"media_id": "media-l1", "file": str(Path("l1.mp4"))},
        _chain_results(),
        live_verify_chain_deep.build_chain_ops_spec(5),
        assert_duration=True,
        profile="profile-a",
        ts=123,
        duration_tolerance_sec=1.5,
        log=logging.getLogger("test-duration-on"),
    )

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["profile"] == "profile-a"
    assert calls[0]["ts"] == 123
    assert calls[0]["tolerance_sec"] == 1.5
    assert [entry["level"] for entry in calls[0]["downloads"]] == [1, 2, 3, 4, 5]


def test_duration_check_not_invoked_when_flag_off(monkeypatch):
    monkeypatch.setattr(
        live_verify_chain_deep,
        "run_duration_assertion",
        lambda *args, **kwargs: pytest.fail("duration assertion should not run"),
    )

    exit_code = live_verify_chain_deep.verify_chain_outputs(
        {"media_id": "media-l1", "file": str(Path("l1.mp4"))},
        _chain_results(),
        live_verify_chain_deep.build_chain_ops_spec(5),
        assert_duration=False,
        profile="profile-a",
        ts=123,
        duration_tolerance_sec=1.5,
        log=logging.getLogger("test-duration-off"),
    )

    assert exit_code == 0
