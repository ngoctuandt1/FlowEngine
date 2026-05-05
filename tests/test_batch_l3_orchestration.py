"""PRD §5 — orchestration tests for batch_dispatch_l3_siblings.

Phase 3 reuses the Phase 2 orchestrator verbatim — the per-op submit /
wait / download primitives are level-agnostic. These tests verify the
delegation: an L3 batch produces the same shape of results as an L2
batch given the same Playwright stubs.
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


def _l3_job(jid: str, op: str, **extra: Any) -> dict:
    base = {
        "id": jid,
        "type": op,
        "job_level": 3,
        "profile": "ngoctuandt20",
        "parent_job_id": "L2",
        "media_id": "parent-mid",
        "edit_url": "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "project_url": "https://labs.google/fx/tools/flow/project/proj-x",
    }
    base.update(extra)
    return base


_captured_jobs: list[dict] = []


def _stub_submitters(monkeypatch, fake_submit):
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


@pytest.mark.asyncio
async def test_l3_batch_full_success_three_mixed_types(monkeypatch):
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
        _l3_job("aa", "extend-video", prompt="more action"),
        _l3_job("bb", "camera-move", direction="Dolly in"),
        _l3_job("cc", "insert-object", prompt="a hat",
                bbox={"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5}),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l2_gens",
                        _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l2_gen_at_tile", fake_dl)

    results = await batch_mod.batch_dispatch_l3_siblings(
        _FakeClient(),
        "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
        "parent-mid",
        jobs,
    )

    assert [r["job_id"] for r in results] == ["aa", "bb", "cc"]
    assert all(r["status"] == "completed" for r in results)
    assert {r["media_id"] for r in results} == {"mid-aa", "mid-bb", "mid-cc"}
    # parent_job_id propagates as the direct (L2) parent — L3 inheritance
    # walks one step.
    assert all(r.get("parent_job_id") == "L2" for r in results)
    assert len({r["output_files"][0] for r in results}) == 3


@pytest.mark.asyncio
async def test_l3_batch_recaptcha_propagates(monkeypatch):
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
        _l3_job("aa", "extend-video"),
        _l3_job("bb", "extend-video"),
    ]
    _captured_jobs.extend(jobs)

    _stub_submitters(monkeypatch, fake_submit)

    with pytest.raises(RecaptchaError):
        await batch_mod.batch_dispatch_l3_siblings(
            _FakeClient(),
            "https://labs.google/fx/tools/flow/project/proj-x/edit/parent-mid",
            "parent-mid",
            jobs,
        )


@pytest.mark.asyncio
async def test_l3_batch_empty_input_returns_empty_list():
    out = await batch_mod.batch_dispatch_l3_siblings(
        _FakeClient(), "edit-url", "parent-mid", [],
    )
    assert out == []


@pytest.mark.asyncio
async def test_l3_batch_delegates_to_l2_orchestrator(monkeypatch):
    """Phase 3 entry point must delegate to the Phase 2 orchestrator —
    duplicating orchestrator code would silently drift across phases.
    """
    seen: dict[str, Any] = {}

    async def fake_l2(client, parent_edit_url, parent_media_id, jobs):
        seen["parent_edit_url"] = parent_edit_url
        seen["parent_media_id"] = parent_media_id
        seen["jobs"] = jobs
        return [{"job_id": j["id"], "status": "completed"} for j in jobs]

    monkeypatch.setattr(batch_mod, "batch_dispatch_l2_siblings", fake_l2)

    jobs = [_l3_job("a", "extend-video"), _l3_job("b", "extend-video")]
    out = await batch_mod.batch_dispatch_l3_siblings(
        _FakeClient(), "edit-url", "parent-mid", jobs,
    )
    assert seen["parent_edit_url"] == "edit-url"
    assert seen["parent_media_id"] == "parent-mid"
    assert seen["jobs"] is jobs
    assert [r["job_id"] for r in out] == ["a", "b"]
