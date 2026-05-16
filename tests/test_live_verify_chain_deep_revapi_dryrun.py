import json
from pathlib import Path

import scripts.live_verify_chain_deep_revapi as script


def test_cli_parses_all_modes():
    parser = script.build_parser()

    for mode in ("ui", "hybrid", "revapi"):
        args = parser.parse_args(["--profile", "dummy", "--mode", mode])
        assert args.profile == "dummy"
        assert args.mode == mode
        assert args.assert_duration is True


def test_depth_n_produces_n_level_plan():
    levels = script.build_level_plan("dummy", 7, "hybrid")

    assert len(levels) == 7
    assert [entry["level"] for entry in levels] == list(range(1, 8))
    assert [entry["expected_dur"] for entry in levels] == [8.0, 16.0, 24.0, 32.0, 40.0, 48.0, 56.0]

def test_plan_submit_modes():
    expected = {
        "ui": ["t2v-ui", "extend-ui", "extend-ui"],
        "hybrid": ["t2v-ui", "extend-ui-capture", "extend-replay-api-fallback-ui"],
        "revapi": ["t2v-ui", "extend-ui-capture", "extend-replay-api-fallback-ui"],
    }

    for mode, submits in expected.items():
        levels = script.build_level_plan("dummy", 3, mode)
        assert [entry["submit"] for entry in levels] == submits


async def test_dry_run_emits_plan_and_summary(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script.time, "time", lambda: 1234567890)
    args = script.build_parser().parse_args(
        ["--profile", "dummy", "--depth", "3", "--mode", "hybrid", "--dry-run"]
    )

    code, summary = await script.run(args)
    stdout = capsys.readouterr().out

    assert code == 0
    assert summary["all_pass"] is True
    assert summary["levels"][1]["expected_dur"] == 16.0
    assert "DRY-RUN: profile=dummy depth=3 mode=hybrid" in stdout
    assert "L3 | 24.0s | completed | extend-replay-api-fallback-ui" in stdout
    summary_path = tmp_path / "tests" / "live_runs" / "1234567890_chain_deep_revapi.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["depth"] == 3
    assert len(data["levels"]) == 3


async def test_dry_run_multiple_profiles_parallel(tmp_path, monkeypatch):
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script.time, "time", lambda: 1234567890)
    args = script.build_parser().parse_args(
        [
            "--profile",
            "ignored",
            "--profiles",
            "p1,p2",
            "--depth",
            "2",
            "--mode",
            "ui",
            "--dry-run",
            "--no-assert-duration",
        ]
    )

    assert script.selected_profiles(args) == ["p1", "p2"]
    code, summary = await script.run(args)

    assert code == 0
    assert summary["profiles"] == ["p1", "p2"]
    assert summary["credit_estimate"] == 24
    assert [len(result["levels"]) for result in summary["results"]] == [2, 2]
    assert Path(summary["results"][0]["duration"]["report_path"]).exists()
