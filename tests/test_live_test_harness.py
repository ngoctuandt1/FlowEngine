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
    assert len(children) == 8
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
