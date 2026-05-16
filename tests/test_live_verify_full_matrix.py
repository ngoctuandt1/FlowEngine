import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import scripts.live_verify_full_matrix as script


class DummyFlowClient:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.profile_name = kwargs["profile_name"]
        self._job_id = ""
        DummyFlowClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_cli_defaults_cover_full_matrix():
    args = script.build_parser().parse_args(["--profile", "dummy"])

    assert script.parse_csv(args.cates, script.ALL_CATES, label="cates") == list(script.ALL_CATES)
    assert script.parse_csv(args.modes, script.ALL_MODES, label="modes") == list(script.ALL_MODES)
    assert args.depth == 5
    assert args.cooldown_sec == 30.0


def test_build_op_plan_l1_only_vs_l2_chain():
    assert [op["cate"] for op in script.build_op_plan("t2v", 5)] == ["t2v"]
    assert [op["cate"] for op in script.build_op_plan("extend", 4)] == [
        "t2v",
        "extend",
        "extend",
        "extend",
    ]


@pytest.mark.parametrize(
    ("cate", "mode", "via", "force"),
    [
        ("t2v", "ui", "0", "0"),
        ("extend", "hybrid", "1", "0"),
        ("camera", "revapi", "1", "1"),
    ],
)
def test_configure_mode_env_sets_target_gate_and_clears_others(monkeypatch, cate, mode, via, force):
    for known_cate in script.ALL_CATES:
        monkeypatch.setenv(script.env_names(known_cate)[0], "stale")
        monkeypatch.setenv(script.env_names(known_cate)[1], "stale")

    env = script.configure_mode_env(cate, mode)
    via_key, force_key = script.env_names(cate)

    assert env == {
        "via_key": via_key,
        "via_value": via,
        "force_key": force_key,
        "force_value": force,
    }
    assert script.os.environ[via_key] == via
    assert script.os.environ[force_key] == force
    for other_cate in set(script.ALL_CATES) - {cate}:
        other_via, other_force = script.env_names(other_cate)
        assert script.os.environ[other_via] == "0"
        assert script.os.environ[other_force] == "0"


async def test_dry_run_emits_matrix_and_json(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script.time, "time", lambda: 1234567890)
    args = script.build_parser().parse_args(
        [
            "--profile",
            "dummy",
            "--depth",
            "2",
            "--cates",
            "t2v,extend",
            "--modes",
            "ui,revapi",
            "--dry-run",
        ]
    )

    code, summary = await script.run(args)
    stdout = capsys.readouterr().out

    assert code == 0
    assert summary["all_pass"] is True
    assert summary["matrix"]["t2v"]["modes"]["ui"]["ok_label"] == "plan1/1"
    assert summary["matrix"]["extend"]["modes"]["revapi"]["ok_label"] == "plan2/2"
    assert "| Cate" in stdout
    assert "| t2v" in stdout
    assert "| extend" in stdout

    summary_path = tmp_path / "tests" / "live_runs" / "1234567890_matrix.json"
    assert summary_path.exists()
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    assert data["profile"] == "dummy"
    assert data["cates"] == ["t2v", "extend"]
    assert data["modes"] == ["ui", "revapi"]
    assert len(data["chains"]) == 4
    assert data["chains"][0]["ops"][0]["status"] == "planned"


@pytest.mark.parametrize(
    ("cate", "mode", "expected_calls", "via", "force"),
    [
        ("t2v", "ui", ["t2v"], "0", "0"),
        ("i2i", "hybrid", ["i2i"], "1", "0"),
        ("remove", "revapi", ["t2v", "remove"], "1", "1"),
    ],
)
async def test_live_chain_dispatches_ops_and_records_env(
    tmp_path,
    monkeypatch,
    cate,
    mode,
    expected_calls,
    via,
    force,
):
    calls = []
    output_file = tmp_path / "source.mp4"
    output_file.write_bytes(b"mp4")

    async def fake_operation(client, **kwargs):
        op_cate = client.current_cate
        calls.append({"cate": op_cate, "kwargs": kwargs})
        return {
            "project_url": f"https://example.test/{op_cate}",
            "edit_url": f"https://example.test/{op_cate}/edit",
            "media_id": f"mid-{len(calls)}",
            "generation_id": f"gen-{len(calls)}",
            "output_files": [str(output_file)],
        }

    def fake_load_operation(op_cate):
        async def wrapped(client, **kwargs):
            client.current_cate = op_cate
            return await fake_operation(client, **kwargs)

        return wrapped

    DummyFlowClient.instances = []
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "get_flow_client_class", lambda: DummyFlowClient)
    monkeypatch.setattr(script, "load_operation", fake_load_operation)

    chain = await script.run_live_chain(
        profile="dummy",
        cate=cate,
        mode=mode,
        depth=2,
        ts=123,
    )

    assert [call["cate"] for call in calls] == expected_calls
    assert chain["success_count"] == len(expected_calls)
    assert chain["total_count"] == len(expected_calls)
    assert chain["env"]["via_value"] == via
    assert chain["env"]["force_value"] == force
    download_dir = DummyFlowClient.instances[0].kwargs["download_dir"]
    expected_suffix = str(Path(f"matrix_123") / cate / mode)
    assert download_dir.endswith(expected_suffix)
    assert Path(chain["ops"][0]["path"]).exists()

    if cate == "i2i":
        assert Path(calls[0]["kwargs"]["ref_image_path"]).exists()
    if cate == "remove":
        assert calls[1]["kwargs"]["job"]["media_id"] == "mid-1"
        assert calls[1]["kwargs"]["bbox"] == script.DEFAULT_BBOX


async def test_live_chain_stops_on_first_error(tmp_path, monkeypatch):
    first = AsyncMock(
        return_value={
            "project_url": "project",
            "edit_url": "edit",
            "media_id": "mid-1",
            "output_files": [],
        }
    )
    second = AsyncMock(side_effect=RuntimeError("boom"))

    def fake_load_operation(cate):
        return first if cate == "t2v" else second

    DummyFlowClient.instances = []
    monkeypatch.setattr(script, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(script, "get_flow_client_class", lambda: DummyFlowClient)
    monkeypatch.setattr(script, "load_operation", fake_load_operation)

    chain = await script.run_live_chain(
        profile="dummy",
        cate="extend",
        mode="hybrid",
        depth=3,
        ts=123,
    )

    assert chain["success_count"] == 1
    assert chain["total_count"] == 3
    assert chain["first_error"] == "boom"
    assert chain["ops"][1]["status"] == "failed"
    assert chain["ops"][2]["status"] == "planned"
