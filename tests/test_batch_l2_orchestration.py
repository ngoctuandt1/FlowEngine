"""PRD §4 — orchestration tests for batch_dispatch_l2_siblings.

Mirrors `tests/test_batch_l1_orchestration.py`. All Playwright /
network calls are stubbed so no Chrome is touched.
"""

from __future__ import annotations

from typing import Any

import pytest

import flow.operations._batch as batch_mod
from flow.recaptcha import RecaptchaError


class _FakePage:
    url = "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid"


class _FakeClient:
    profile_name = "ngoctuandt20"
    page = _FakePage()


def _make_collective_wait(per_gen_fake):
    async def _collective(client, submits, *, parent_media_id=None, **kw):
        out = []
        for sub in submits:
            res = await per_gen_fake(client, sub["gen_id"])
            out.append(res)
        return out
    return _collective


def _l2_job(jid: str, op: str, **extra: Any) -> dict:
    base = {
        "id": jid,
        "type": op,
        "job_level": 2,
        "profile": "ngoctuandt20",
        "parent_job_id": "L1",
        "media_id": "parent-mid",
        "edit_url": "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "project_url": "https://labs.google/fx/tools/flow/project/proj-x",
    }
    base.update(extra)
    return base


def _stub_submitters(monkeypatch, fake_submit):
    """Route every submit_X to a single fake_submit(client, job, ...)."""
    async def _ext(client, parent, prompt="", *, panel_already_open=False, **kw):
        return await fake_submit(client, _captured_jobs.pop(0), op="extend-video")

    async def _cam(client, parent, direction, *, panel_already_open=False, **kw):
        return await fake_submit(client, _captured_jobs.pop(0), op="camera-move",
                                 direction=direction)

    async def _ins(client, parent, prompt="", bbox=None, *, panel_already_open=False, **kw):
        return await fake_submit(client, _captured_jobs.pop(0), op="insert-object")

    async def _rm(client, parent, bbox=None, *, panel_already_open=False, **kw):
        return await fake_submit(client, _captured_jobs.pop(0), op="remove-object")

    monkeypatch.setattr(batch_mod, "submit_extend", _ext)
    monkeypatch.setattr(batch_mod, "submit_camera", _cam)
    monkeypatch.setattr(batch_mod, "submit_insert", _ins)
    monkeypatch.setattr(batch_mod, "submit_remove", _rm)


# Module-level scratch list used by stubs above to recover the original
# job dict (since the submit_X signatures don't carry it through).
_captured_jobs: list[dict] = []


@pytest.mark.asyncio
async def test_l2_batch_full_success_three_mixed_types(monkeypatch):
    submit_log: list[str] = []

    async def fake_submit(client, job, *, op, **kw):
        submit_log.append(job["id"])
        return {
            "gen_id": f"operations/G_{job['id']}",
            "calls_before": 0,
            "batch_resp_before": 0,
            "submit_ts": float(len(submit_log)),
            "op_type": op,
        }

    async def fake_wait(client, gen_id):
        return {
            "status": "completed",
            "media_id": f"mid-{gen_id[-2:]}",
            "media_ids": [f"mid-{gen_id[-2:]}"],
            "error": None,
        }

    async def fake_dl(client, *, tile_index, media_id, edit_url, **kw):
        return [f"downloads/{media_id}.mp4"]

    _captured_jobs[:] = []
    jobs = [
        _l2_job("aa", "extend-video", prompt="more action"),
        _l2_job("bb", "camera-move", direction="Dolly in"),
        _l2_job("cc", "insert-object", prompt="a hat",
                bbox={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l2_gens",
                        _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l2_gen_at_tile", fake_dl)

    results = await batch_mod.batch_dispatch_l2_siblings(
        _FakeClient(),
        "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "parent-mid",
        jobs,
    )

    assert [r["job_id"] for r in results] == ["aa", "bb", "cc"]
    assert all(r["status"] == "completed" for r in results)
    assert {r["media_id"] for r in results} == {"mid-aa", "mid-bb", "mid-cc"}
    assert {r["generation_id"] for r in results} == {
        "operations/G_aa", "operations/G_bb", "operations/G_cc",
    }
    # parent_job_id propagates through into result for FK preservation.
    assert all(r.get("parent_job_id") == "L1" for r in results)
    assert len({r["output_files"][0] for r in results}) == 3


@pytest.mark.asyncio
async def test_l2_batch_mid_submit_failure_does_not_abort(monkeypatch):
    async def fake_submit(client, job, *, op, **kw):
        if job["id"] == "bb":
            raise RuntimeError("camera preset not found")
        return {
            "gen_id": f"operations/G_{job['id']}",
            "calls_before": 0, "batch_resp_before": 0, "submit_ts": 0.0,
            "op_type": op,
        }

    async def fake_wait(client, gen_id):
        return {"status": "completed", "media_id": f"mid-{gen_id[-2:]}",
                "media_ids": [f"mid-{gen_id[-2:]}"], "error": None}

    async def fake_dl(client, *, tile_index, media_id, edit_url, **kw):
        return [f"downloads/{media_id}.mp4"]

    _captured_jobs[:] = []
    jobs = [
        _l2_job("aa", "extend-video"),
        _l2_job("bb", "camera-move", direction="Dolly in"),
        _l2_job("cc", "extend-video"),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l2_gens",
                        _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l2_gen_at_tile", fake_dl)

    results = await batch_mod.batch_dispatch_l2_siblings(
        _FakeClient(),
        "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "parent-mid",
        jobs,
    )
    by_id = {r["job_id"]: r for r in results}
    assert by_id["aa"]["status"] == "completed"
    assert by_id["bb"]["status"] == "failed"
    assert "preset" in by_id["bb"]["error"]
    assert by_id["cc"]["status"] == "completed"


@pytest.mark.asyncio
async def test_l2_batch_recaptcha_propagates(monkeypatch):
    async def fake_submit(client, job, *, op, **kw):
        if job["id"] == "bb":
            raise RecaptchaError(kind="v3_score", url="https://x")
        return {
            "gen_id": f"operations/G_{job['id']}",
            "calls_before": 0, "batch_resp_before": 0, "submit_ts": 0.0,
            "op_type": op,
        }

    _captured_jobs[:] = []
    jobs = [
        _l2_job("aa", "extend-video"),
        _l2_job("bb", "extend-video"),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)

    with pytest.raises(RecaptchaError):
        await batch_mod.batch_dispatch_l2_siblings(
            _FakeClient(),
            "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
            "parent-mid",
            jobs,
        )


@pytest.mark.asyncio
async def test_l2_batch_results_in_input_order(monkeypatch):
    """Even if waits return out-of-order media_ids in collective, the
    orchestrator must preserve input order in the result list.
    """
    async def fake_submit(client, job, *, op, **kw):
        return {
            "gen_id": f"operations/G_{job['id']}",
            "calls_before": 0, "batch_resp_before": 0,
            "submit_ts": float(ord(job["id"][0])),
            "op_type": op,
        }

    async def fake_wait_collective(client, submits, *, parent_media_id=None, **kw):
        # Pretend completion order = submission order, but verify result
        # order is the orchestrator's responsibility.
        return [
            {"status": "completed",
             "media_id": f"mid-{i}",
             "media_ids": [f"mid-{i}"], "error": None}
            for i, _ in enumerate(submits)
        ]

    async def fake_dl(client, *, tile_index, media_id, edit_url, **kw):
        return [f"downloads/{media_id}.mp4"]

    _captured_jobs[:] = []
    jobs = [
        _l2_job("aa", "extend-video"),
        _l2_job("bb", "camera-move", direction="Orbit left"),
        _l2_job("cc", "remove-object",
                bbox={"x": 0.1, "y": 0.1, "w": 0.4, "h": 0.4}),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l2_gens", fake_wait_collective)
    monkeypatch.setattr(batch_mod, "download_l2_gen_at_tile", fake_dl)

    results = await batch_mod.batch_dispatch_l2_siblings(
        _FakeClient(),
        "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "parent-mid",
        jobs,
    )
    assert [r["job_id"] for r in results] == ["aa", "bb", "cc"]


@pytest.mark.asyncio
async def test_l2_batch_empty_input_returns_empty_list():
    out = await batch_mod.batch_dispatch_l2_siblings(
        _FakeClient(), "edit-url", "parent-mid", [],
    )
    assert out == []
