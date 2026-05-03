"""PRD §3.3 — orchestration tests for batch_dispatch_l1_same_project.

Covers:
* full-success batch of 3 → 3 distinct results in input order
* mid-batch submit failure on job index 1 → that result fails, others ok
* first submit fails → entire batch aborts with all-failed results
* RecaptchaError mid-batch → propagates out (dispatcher recovers profile)

All tests stub `submit_generate_l1`, `wait_for_l1_gen`, `download_l1_gen`
so no real Chrome / network is touched.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import flow.operations._batch as batch_mod
from flow.recaptcha import RecaptchaError


class _FakeClient:
    profile_name = "ngoctuandt20"


def _make_collective_wait(per_gen_fake):
    """Adapt a per-gen fake (used in tests) into a collective wait stub.

    The orchestrator calls ``wait_for_all_l1_gens(client, submits)`` and
    expects a list of N result dicts in submission order. Tests still find
    it more readable to define one ``fake_wait(client, gen_id, ...)`` and
    have this adapter dispatch the calls.
    """
    async def _collective(client, submits, **kw):
        out = []
        for sub in submits:
            res = await per_gen_fake(
                client, sub["gen_id"],
                calls_before=sub.get("calls_before", 0),
                submit_ts=sub.get("submit_ts", 0.0),
            )
            out.append(res)
        return out
    return _collective


def _job(jid: str, prompt: str = "x") -> dict:
    return {"id": jid, "type": "text-to-video", "prompt": prompt,
            "profile": "ngoctuandt20", "job_level": 1}


@pytest.mark.asyncio
async def test_batch_full_success_returns_three_distinct_results(monkeypatch):
    submit_log: list[str] = []

    async def fake_submit(client, job, *, project_already_open, **kw):
        submit_log.append(job["id"])
        idx = len(submit_log) - 1
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "https://labs.google/fx/tools/flow/project/proj-1",
            "project_id": "proj-1",
            "locale": "",
            "calls_before": idx * 5,
            "submit_ts": 1000.0 + idx,
            "prompt": job["prompt"],
        }

    async def fake_wait(client, gen_id, *, calls_before, submit_ts,
                        parent_media_id=None, **kw):
        return {
            "status": "completed",
            "media_id": f"mid-{gen_id[-1]}",
            "media_ids": [f"mid-{gen_id[-1]}"],
            "error": None,
        }

    async def fake_download(client, media_id, **kw):
        return [f"downloads/t2v_{media_id}.mp4"]

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l1_gens", _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l1_gen", fake_download)

    jobs = [_job("aa", "red cat"), _job("bb", "blue dog"), _job("cc", "yellow bird")]
    results = await batch_mod.batch_dispatch_l1_same_project(_FakeClient(), jobs)

    assert [r["job_id"] for r in results] == ["aa", "bb", "cc"]
    assert all(r["status"] == "completed" for r in results)
    assert {r["media_id"] for r in results} == {"mid-a", "mid-b", "mid-c"}
    assert {r["generation_id"] for r in results} == {
        "operations/G_aa", "operations/G_bb", "operations/G_cc",
    }
    # All 3 share the same project_url (PRD §3.1 invariant).
    assert {r["project_url"] for r in results} == {
        "https://labs.google/fx/tools/flow/project/proj-1"
    }
    # Output files distinct.
    assert len({r["output_files"][0] for r in results}) == 3


@pytest.mark.asyncio
async def test_batch_mid_submit_failure_does_not_abort(monkeypatch):
    async def fake_submit(client, job, *, project_already_open, **kw):
        if job["id"] == "bb":
            raise RuntimeError("type prompt failed")
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "https://x/y/project/p", "project_id": "p", "locale": "",
            "calls_before": 0, "submit_ts": 0.0, "prompt": job["prompt"],
        }

    async def fake_wait(client, gen_id, **kw):
        return {"status": "completed", "media_id": f"mid-{gen_id[-1]}",
                "media_ids": [], "error": None}

    async def fake_download(client, media_id, **kw):
        return [f"downloads/{media_id}.mp4"]

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l1_gens", _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l1_gen", fake_download)

    results = await batch_mod.batch_dispatch_l1_same_project(
        _FakeClient(),
        [_job("aa"), _job("bb"), _job("cc")],
    )

    by_id = {r["job_id"]: r for r in results}
    assert by_id["aa"]["status"] == "completed"
    assert by_id["bb"]["status"] == "failed"
    assert "type prompt failed" in by_id["bb"]["error"]
    assert by_id["cc"]["status"] == "completed"


@pytest.mark.asyncio
async def test_batch_first_submit_failure_aborts_remaining(monkeypatch):
    async def fake_submit(client, job, *, project_already_open, **kw):
        raise RuntimeError("homepage navigation failed")

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)

    results = await batch_mod.batch_dispatch_l1_same_project(
        _FakeClient(), [_job("aa"), _job("bb"), _job("cc")],
    )
    assert len(results) == 3
    assert all(r["status"] == "failed" for r in results)
    # bb / cc carry the "skipped" sentinel; aa carries the original error.
    assert "homepage" in results[0]["error"]
    assert "skipped" in results[1]["error"]
    assert "skipped" in results[2]["error"]


@pytest.mark.asyncio
async def test_batch_wait_failure_does_not_block_other_completions(monkeypatch):
    async def fake_submit(client, job, *, project_already_open, **kw):
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "p", "project_id": "p", "locale": "",
            "calls_before": 0, "submit_ts": 0.0, "prompt": job["prompt"],
        }

    async def fake_wait(client, gen_id, **kw):
        if gen_id.endswith("bb"):
            return {"status": "failed", "media_id": None, "media_ids": [],
                    "error": "no_signal_timeout"}
        return {"status": "completed", "media_id": f"mid-{gen_id[-1]}",
                "media_ids": [], "error": None}

    async def fake_download(client, media_id, **kw):
        return [f"downloads/{media_id}.mp4"]

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l1_gens", _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l1_gen", fake_download)

    results = await batch_mod.batch_dispatch_l1_same_project(
        _FakeClient(), [_job("aa"), _job("bb"), _job("cc")],
    )
    by_id = {r["job_id"]: r for r in results}
    assert by_id["aa"]["status"] == "completed"
    assert by_id["bb"]["status"] == "failed"
    assert by_id["bb"]["error"] == "no_signal_timeout"
    assert by_id["cc"]["status"] == "completed"


@pytest.mark.asyncio
async def test_batch_recaptcha_propagates_to_dispatcher(monkeypatch):
    async def fake_submit(client, job, *, project_already_open, **kw):
        if job["id"] == "bb":
            raise RecaptchaError(kind="v3_score", url="https://x")
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "p", "project_id": "p", "locale": "",
            "calls_before": 0, "submit_ts": 0.0, "prompt": job["prompt"],
        }

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)

    with pytest.raises(RecaptchaError):
        await batch_mod.batch_dispatch_l1_same_project(
            _FakeClient(), [_job("aa"), _job("bb")],
        )


@pytest.mark.asyncio
async def test_batch_empty_input_returns_empty_list():
    out = await batch_mod.batch_dispatch_l1_same_project(_FakeClient(), [])
    assert out == []


@pytest.mark.asyncio
async def test_batch_results_in_input_order_under_concurrent_waits(monkeypatch):
    """Even if waits finish out-of-order, results stay aligned to input order."""
    finish_order = ["bb", "aa", "cc"]
    finishers: dict[str, asyncio.Event] = {jid: asyncio.Event() for jid in ("aa", "bb", "cc")}

    async def fake_submit(client, job, *, project_already_open, **kw):
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "p", "project_id": "p", "locale": "",
            "calls_before": 0, "submit_ts": 0.0, "prompt": job["prompt"],
        }

    async def fake_wait(client, gen_id, **kw):
        # Block until our finishers signal in non-input order.
        jid = gen_id[-2:]
        await finishers[jid].wait()
        return {"status": "completed", "media_id": f"mid-{jid}",
                "media_ids": [], "error": None}

    async def fake_download(client, media_id, **kw):
        return [f"downloads/{media_id}.mp4"]

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit)
    monkeypatch.setattr(batch_mod, "wait_for_all_l1_gens", _make_collective_wait(fake_wait))
    monkeypatch.setattr(batch_mod, "download_l1_gen", fake_download)

    async def signaller():
        for jid in finish_order:
            await asyncio.sleep(0.01)
            finishers[jid].set()

    sig = asyncio.create_task(signaller())
    results = await batch_mod.batch_dispatch_l1_same_project(
        _FakeClient(), [_job("aa"), _job("bb"), _job("cc")],
    )
    await sig

    assert [r["job_id"] for r in results] == ["aa", "bb", "cc"]
    assert [r["media_id"] for r in results] == ["mid-aa", "mid-bb", "mid-cc"]
