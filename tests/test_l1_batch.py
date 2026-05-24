from __future__ import annotations

from typing import Any

import flow.operations._batch as batch_mod
import flow.operations._l1_batch as l1_batch_mod
import flow.operations._multitab as multitab_mod
from flow.model_selector import DEFAULT_MODEL


class _FakeClient:
    profile_name = "profile-a"


def _l1_job(job_id: str, **extra: Any) -> dict:
    return {
        "id": job_id,
        "type": "text-to-video",
        "prompt": f"prompt {job_id}",
        "profile": "profile-a",
        "job_level": 1,
        **extra,
    }


def _l2_job(job_id: str, **extra: Any) -> dict:
    return {
        "id": job_id,
        "type": "extend-video",
        "prompt": f"extend {job_id}",
        "profile": "profile-a",
        "job_level": 2,
        "parent_job_id": "parent-l1",
        **extra,
    }


def _submit_body(workflow_id: str, media_id: str) -> dict[str, Any]:
    return {
        "media": [{"name": media_id, "workflowId": workflow_id}],
        "workflows": [{"name": workflow_id}],
    }


def test_capture_submit_metadata_ignores_sibling_status_response():
    client = type("FakeClient", (), {})()
    client._batch_responses = [
        {
            "url": "https://example.test/v1/video:batchCheckAsyncVideoGenerationStatus",
            "body": _submit_body("workflow-A", "media-A"),
        },
        {
            "url": "https://example.test/v1/video:batchAsyncGenerateVideoText",
            "body": _submit_body("workflow-B", "media-B"),
        },
    ]
    client._calls = []

    meta = l1_batch_mod._capture_submit_metadata_from_window(
        client, calls_before=0, batch_resp_before=0,
    )

    assert meta["workflow_id"] == "workflow-B"
    assert meta["media_id"] == "media-B"
    assert meta["gen_id"] == "workflow-B"


async def test_l1_batch_passes_paid_and_default_free_modes(monkeypatch):
    calls: list[dict] = []

    async def fake_submit_generate_l1(
        client,
        job,
        *,
        project_already_open,
        model,
        free_mode,
    ):
        calls.append({
            "id": job["id"],
            "project_already_open": project_already_open,
            "model": model,
            "free_mode": free_mode,
        })
        return {
            "gen_id": f"operations/G_{job['id']}",
            "project_url": "https://labs.google/fx/tools/flow/project/p-l1",
            "project_id": "p-l1",
            "locale": "",
            "calls_before": 0,
            "submit_ts": 0.0,
            "prompt": job["prompt"],
        }

    async def fake_wait_for_all_l1_gens(client, submits, **kw):
        return [
            {"status": "failed", "error": "stop after submit", "media_id": None,
             "media_ids": []}
            for _ in submits
        ]

    monkeypatch.setattr(batch_mod, "submit_generate_l1", fake_submit_generate_l1)
    monkeypatch.setattr(batch_mod, "wait_for_all_l1_gens", fake_wait_for_all_l1_gens)

    await batch_mod.batch_dispatch_l1_same_project(
        _FakeClient(),
        [_l1_job("paid", model="omni-flash"), _l1_job("default")],
    )

    assert calls == [
        {"id": "paid", "project_already_open": False,
         "model": "omni-flash", "free_mode": False},
        {"id": "default", "project_already_open": True,
         "model": DEFAULT_MODEL, "free_mode": True},
    ]


async def test_l2_extend_batch_passes_paid_and_default_free_modes(monkeypatch):
    calls: list[dict] = []

    async def fake_submit_extend(
        client,
        parent_job,
        prompt="",
        *,
        panel_already_open=False,
        model,
        free_mode,
    ):
        calls.append({"prompt": prompt, "model": model, "free_mode": free_mode})
        return {
            "gen_id": "operations/G_extend",
            "calls_before": 0,
            "batch_resp_before": 0,
            "submit_ts": 0.0,
            "op_type": "extend-video",
        }

    monkeypatch.setattr(batch_mod, "submit_extend", fake_submit_extend)

    await batch_mod._dispatch_l2_submit(
        _FakeClient(), _l2_job("paid", model="omni-flash"), {}, first=True,
    )
    await batch_mod._dispatch_l2_submit(
        _FakeClient(), _l2_job("default"), {}, first=False,
    )

    assert calls == [
        {"prompt": "extend paid", "model": "omni-flash", "free_mode": False},
        {"prompt": "extend default", "model": DEFAULT_MODEL, "free_mode": True},
    ]


class _FakeTab:
    def __init__(self) -> None:
        self.url = ""
        self.handlers: list[tuple[str, Any]] = []
        self.closed = False

    def on(self, event: str, handler) -> None:
        self.handlers.append((event, handler))

    async def bring_to_front(self) -> None:
        return None

    async def goto(self, url: str, **kw) -> None:
        self.url = url

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    def __init__(self) -> None:
        self.tabs: list[_FakeTab] = []

    async def new_page(self) -> _FakeTab:
        tab = _FakeTab()
        self.tabs.append(tab)
        return tab


class _FakeRealClient:
    profile_name = "profile-a"

    def __init__(self) -> None:
        self.context = _FakeContext()


async def test_multitab_extend_passes_paid_and_default_free_modes(monkeypatch):
    calls: list[dict] = []

    async def no_sleep(delay):
        return None

    async def fake_extend_video(client, job, *, prompt, model, free_mode):
        calls.append({"id": job["id"], "model": model, "free_mode": free_mode})
        return {"status": "completed", "media_id": job["media_id"]}

    monkeypatch.setattr(multitab_mod.asyncio, "sleep", no_sleep)
    monkeypatch.setattr("flow.operations.extend.extend_video", fake_extend_video)

    client = _FakeRealClient()
    base = {
        "type": "extend-video",
        "parent_edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent-mid",
        "parent_media_id": "parent-mid",
        "parent_project_url": "https://labs.google/fx/tools/flow/project/p",
    }

    await multitab_mod.dispatch_op_in_new_tab(
        client, {**base, "id": "paid", "model": "omni-flash"},
    )
    await multitab_mod.dispatch_op_in_new_tab(client, {**base, "id": "default"})

    assert calls == [
        {"id": "paid", "model": "omni-flash", "free_mode": False},
        {"id": "default", "model": DEFAULT_MODEL, "free_mode": True},
    ]
