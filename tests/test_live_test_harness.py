"""Topology tests for scripts/live_test_full_cate.py."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "live_test_full_cate.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("live_test_full_cate", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


live_test = _load_module()


class _RecordingApi:
    def __init__(self) -> None:
        self.posts: list[dict] = []

    def post(self, path: str, payload: dict):
        self.posts.append({"path": path, "payload": dict(payload)})
        return {
            "id": f"job-{len(self.posts)}",
            "type": payload["type"],
            "chain_id": payload.get("chain_id"),
        }


def _steps_by_ref(steps: list[dict]) -> dict[str, dict]:
    return {step["ref"]: step for step in steps}


def _steps_by_type(steps: list[dict], job_type: str) -> list[dict]:
    return [step for step in steps if step["payload"]["type"] == job_type]


def _ancestor_types(step: dict, by_ref: dict[str, dict]) -> list[str]:
    types: list[str] = []
    parent_ref = step["parent_job_id"]
    while parent_ref is not None:
        parent = by_ref[parent_ref]
        types.append(parent["payload"]["type"])
        parent_ref = parent["parent_job_id"]
    return types


def test_l2_chain_payload_has_four_new_l1_heads():
    steps = live_test._l2_chain_payload()

    heads = [step for step in steps if step["job_level"] == 1]
    children = [step for step in steps if step["job_level"] > 1]

    assert len(heads) == 4
    assert len(children) == 10  # 4 extend (L2-L5) + 2 camera + 2 insert + 2 remove
    assert {step["chain_name"] for step in heads} == {"extend", "camera", "insert", "remove"}
    assert all(step["payload"]["type"] == "text-to-video" for step in heads)
    assert all(step["parent_job_id"] is None for step in heads)


def test_l2_chain_payload_keeps_parentage_within_each_category():
    steps = live_test._l2_chain_payload()
    by_ref = _steps_by_ref(steps)

    for step in steps:
        parent_ref = step["parent_job_id"]
        if parent_ref is None:
            continue
        parent = by_ref[parent_ref]
        assert parent["chain_name"] == step["chain_name"]


def test_camera_chain_has_no_extend_ancestor():
    steps = live_test._l2_chain_payload()
    by_ref = _steps_by_ref(steps)
    camera_steps = [step for step in steps if step["payload"]["type"] == "camera-move"]

    assert len(camera_steps) == 2
    for step in camera_steps:
        assert "extend-video" not in _ancestor_types(step, by_ref)


def test_submit_all_queues_four_chain_heads_before_any_l2():
    api = _RecordingApi()

    submitted = live_test.submit_all(
        api,
        skip_frames=True,
        skip_ingredients=True,
    )

    first_l2_index = next(i for i, job in enumerate(submitted) if job.level > 1)
    assert first_l2_index == 7

    head_jobs = [
        job for job in submitted[:first_l2_index]
        if job.chain_name in {"extend", "camera", "insert", "remove"}
    ]
    assert {job.chain_name for job in head_jobs} == {"extend", "camera", "insert", "remove"}
    assert all(job.level == 1 and job.parent_job_id is None for job in head_jobs)

    by_id = {job.job_id: job for job in submitted}
    for job in submitted:
        if job.parent_job_id is None:
            continue
        parent = by_id[job.parent_job_id]
        assert parent.chain_name == job.chain_name

    for job in submitted:
        if job.chain_name != "camera" or job.level == 1:
            continue
        ancestor = by_id[job.parent_job_id]
        while True:
            assert ancestor.job_type != "extend-video"
            if ancestor.parent_job_id is None:
                break
            ancestor = by_id[ancestor.parent_job_id]


def test_optional_asset_jobs_are_skipped_when_env_vars_are_unset(monkeypatch):
    monkeypatch.delenv("FLOW_TEST_FRAMES_DIR", raising=False)
    monkeypatch.delenv("FLOW_TEST_INGREDIENTS_DIR", raising=False)

    plan = live_test.build_submission_plan(
        skip_frames=False,
        skip_ingredients=False,
    )

    assert _steps_by_type(plan.jobs, "frames-to-video") == []
    assert _steps_by_type(plan.jobs, "ingredients-to-video") == []
    assert any("FLOW_TEST_FRAMES_DIR not set" in warning for warning in plan.warnings)
    assert any("FLOW_TEST_INGREDIENTS_DIR not set" in warning for warning in plan.warnings)


def test_asset_jobs_stage_images_into_flow_upload_dir(monkeypatch, tmp_path):
    upload_dir = tmp_path / "uploads"
    frames_dir = tmp_path / "frames-src"
    ingredients_dir = tmp_path / "ingredients-src"
    frames_dir.mkdir()
    ingredients_dir.mkdir()

    frame_a = frames_dir / "frame_a.jpg"
    frame_b = frames_dir / "frame_b.png"
    ingredient_a = ingredients_dir / "ingredient_a.jpg"
    ingredient_b = ingredients_dir / "ingredient_b.png"
    ingredient_c = ingredients_dir / "ingredient_c.webp"
    frame_a.write_bytes(b"frame-a")
    frame_b.write_bytes(b"frame-b")
    ingredient_a.write_bytes(b"ingredient-a")
    ingredient_b.write_bytes(b"ingredient-b")
    ingredient_c.write_bytes(b"ingredient-c")

    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("FLOW_TEST_FRAMES_DIR", str(frames_dir))
    monkeypatch.setenv("FLOW_TEST_INGREDIENTS_DIR", str(ingredients_dir))
    monkeypatch.setattr(live_test, "_new_stage_run_id", lambda: "20260430T120000_000000Z")

    plan = live_test.build_submission_plan(
        skip_frames=False,
        skip_ingredients=False,
    )

    staging_dir = upload_dir / "livetest_20260430T120000_000000Z"
    assert staging_dir.is_dir()
    assert (staging_dir / "frame_a.jpg").read_bytes() == b"frame-a"
    assert (staging_dir / "frame_b.png").read_bytes() == b"frame-b"
    assert (staging_dir / "ingredient_a.jpg").read_bytes() == b"ingredient-a"
    assert (staging_dir / "ingredient_b.png").read_bytes() == b"ingredient-b"
    assert (staging_dir / "ingredient_c.webp").read_bytes() == b"ingredient-c"

    frames_steps = _steps_by_type(plan.jobs, "frames-to-video")
    ingredients_steps = _steps_by_type(plan.jobs, "ingredients-to-video")
    assert len(frames_steps) == 2
    assert len(ingredients_steps) == 2

    expected_frame_start = "livetest_20260430T120000_000000Z/frame_a.jpg"
    expected_frame_end = "livetest_20260430T120000_000000Z/frame_b.png"
    expected_ingredients = [
        "livetest_20260430T120000_000000Z/ingredient_a.jpg",
        "livetest_20260430T120000_000000Z/ingredient_b.png",
        "livetest_20260430T120000_000000Z/ingredient_c.webp",
    ]

    assert frames_steps[0]["payload"]["start_image_path"] == expected_frame_start
    assert frames_steps[0]["payload"]["end_image_path"] == expected_frame_end
    assert frames_steps[1]["payload"]["start_image_path"] == expected_frame_start
    assert ingredients_steps[0]["payload"]["ingredient_image_paths"] == expected_ingredients
    assert ingredients_steps[1]["payload"]["ingredient_image_paths"] == expected_ingredients

    assert not Path(expected_frame_start).is_absolute()
    assert not Path(expected_frame_end).is_absolute()
    assert all(not Path(path).is_absolute() for path in expected_ingredients)

    assert len(plan.staging_reports) == 2
    assert all(report.staging_abs_dir == staging_dir for report in plan.staging_reports)


def test_dry_run_prints_staging_plan_without_copying(monkeypatch, tmp_path, capsys, caplog):
    upload_dir = tmp_path / "uploads"
    frames_dir = tmp_path / "frames-src"
    frames_dir.mkdir()
    (frames_dir / "frame_a.jpg").write_bytes(b"frame-a")
    (frames_dir / "frame_b.png").write_bytes(b"frame-b")

    monkeypatch.setenv("FLOW_UPLOAD_DIR", str(upload_dir))
    monkeypatch.setenv("FLOW_TEST_FRAMES_DIR", str(frames_dir))
    monkeypatch.delenv("FLOW_TEST_INGREDIENTS_DIR", raising=False)
    monkeypatch.setattr(live_test, "_new_stage_run_id", lambda: "20260430T120000_000000Z")
    caplog.set_level("INFO", logger="live_test_full_cate")

    exit_code = live_test.main(
        ["--dry-run", "--skip-ingredients", "--server", "http://example.test"]
    )
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Dry run: would stage 2 test images to" in caplog.text
    assert "Dry-run staging plan:" in captured.out
    assert "frame_a.jpg -> livetest_20260430T120000_000000Z/frame_a.jpg" in captured.out
    assert "frame_b.png -> livetest_20260430T120000_000000Z/frame_b.png" in captured.out
    assert not (upload_dir / "livetest_20260430T120000_000000Z").exists()
