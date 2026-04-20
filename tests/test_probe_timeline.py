"""Unit tests for scripts/probe_l2_media_id.py timeline polling and summary."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent
    / "scripts"
    / "probe_l2_media_id.py"
)


def _load_probe_module():
    spec = importlib.util.spec_from_file_location("probe_l2_media_id_timeline", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


probe = _load_probe_module()


class _FakeSession:
    def __init__(self, jobs):
        self._jobs = list(jobs)

    def request(self, method, url, timeout=30, **kwargs):
        if not self._jobs:
            raise AssertionError("Unexpected extra request")
        return _FakeResponse(self._jobs.pop(0))


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _job(status, edit_url, media_id):
    return {
        "id": "job-123",
        "status": status,
        "project_url": "https://labs.google/fx/tools/flow/project/project-1",
        "edit_url": edit_url,
        "media_id": media_id,
        "output_files": [],
    }


def test_poll_job_returns_terminal_job_and_checkpoints(monkeypatch):
    session = _FakeSession(
        [
            _job("queued", "https://labs.google/fx/tools/flow/project/p/edit/parent", "parent"),
            _job("running", "https://labs.google/fx/tools/flow/project/p/edit/child", "child"),
            _job("completed", "https://labs.google/fx/tools/flow/project/p/edit/child", "child"),
        ]
    )
    times = iter([0.0, 0.0, 1.0, 2.0])

    monkeypatch.setattr(probe.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(probe.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        probe,
        "build_checkpoint",
        lambda job: {
            "timestamp": f"t-{job['status']}",
            "status": job["status"],
            "edit_url": job["edit_url"],
            "media_id": job["media_id"],
        },
    )

    job, checkpoints = probe.poll_job(
        "job-123",
        server="http://server",
        timeout_seconds=30,
        session=session,
    )

    assert job["status"] == "completed"
    assert checkpoints == [
        {
            "timestamp": "t-queued",
            "status": "queued",
            "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent",
            "media_id": "parent",
        },
        {
            "timestamp": "t-running",
            "status": "running",
            "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/child",
            "media_id": "child",
        },
        {
            "timestamp": "t-completed",
            "status": "completed",
            "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/child",
            "media_id": "child",
        },
    ]


def test_build_report_includes_timeline_in_order():
    timeline = [
        probe.build_timeline_entry(
            "insert-object",
            "job-insert",
            [
                {
                    "timestamp": "2026-04-21T01:00:00",
                    "status": "running",
                    "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/a",
                    "media_id": "a",
                }
            ],
        ),
        probe.build_timeline_entry(
            "remove-object",
            "job-remove",
            [
                {
                    "timestamp": "2026-04-21T01:05:00",
                    "status": "completed",
                    "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/b",
                    "media_id": "b",
                }
            ],
        ),
    ]

    report = probe.build_report("p", "prompt", None, None, None, timeline)

    assert report["timeline"] == timeline


def test_summarize_timeline_flags_first_last_edit_url_flip():
    timeline = [
        probe.build_timeline_entry(
            "insert-object",
            "job-insert",
            [
                {
                    "timestamp": "t1",
                    "status": "running",
                    "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent",
                    "media_id": "parent",
                },
                {
                    "timestamp": "t2",
                    "status": "completed",
                    "edit_url": "https://labs.google/fx/tools/flow/project/p/edit/child",
                    "media_id": "child",
                },
            ],
        )
    ]

    assert probe.summarize_timeline(timeline) == [
        {
            "job_type": "insert-object",
            "job_id": "job-insert",
            "first_edit_url": "https://labs.google/fx/tools/flow/project/p/edit/parent",
            "last_edit_url": "https://labs.google/fx/tools/flow/project/p/edit/child",
            "flipped": True,
        }
    ]
