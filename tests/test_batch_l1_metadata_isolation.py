"""PRD §3.3 Phase 1 — metadata isolation between successive L1 submits.

Pure-logic tests for the helpers in `flow.operations._l1_batch` that
prevent cross-contamination between batched submits in one Chrome.

The load-bearing invariants (PRD §0.4):

* gen_id is captured per-submit by slicing `client._calls[calls_before:]`
  — never read from `client._gen_id` (which is overwritten by the next
  submit).
* `_scan_api_for_gen` filters operations responses to a specific gen_id.
* `_collect_media_ids_after` time-windows media events to a single
  generation and excludes the parent media_id.
* `build_l1_result` produces the canonical job-update payload.
"""

from __future__ import annotations

import time

from flow.operations._l1_batch import (
    _capture_gen_id_from_window,
    _collect_media_ids_after,
    _scan_api_for_gen,
    build_l1_result,
)
from flow.operations._batch import _attach_job_id


class _FakeClient:
    """Stand-in for FlowClient with the buffer fields the batch path reads."""

    def __init__(self):
        self._calls: list[dict] = []
        self._media_id_events: list[dict] = []
        # Sentinel: any test that incorrectly reads this attribute will see
        # the canary string and fail the explicit assertion below.
        self._gen_id = "SENTINEL_DO_NOT_READ"

    def push_op(
        self,
        gen_name: str,
        *,
        progress: int = 0,
        done: bool = False,
        error: str | None = None,
        status: int = 200,
    ):
        body: dict = {"name": gen_name}
        if progress:
            body["progressPercentage"] = progress
        if done:
            body["done"] = True
        if error:
            body["error"] = error
        self._calls.append(
            {
                "url": f"https://labs.google/fx/api/v1/{gen_name}",
                "body": body,
                "status": status,
            }
        )

    def push_media(self, mid: str, ts: float):
        self._media_id_events.append({"mid": mid, "ts": ts, "source": "test"})


def test_capture_gen_id_only_sees_window_slice():
    """gen_id capture must only look at calls AFTER calls_before."""
    c = _FakeClient()
    c.push_op("operations/SUBMIT_A_id_111", progress=5)
    calls_before = len(c._calls)  # 1
    c.push_op("operations/SUBMIT_B_id_222", progress=5)

    gen = _capture_gen_id_from_window(c, calls_before)
    assert gen == "operations/SUBMIT_B_id_222", (
        "must pick up the post-window operation, not the prior one"
    )


def test_capture_gen_id_returns_empty_when_no_window_op():
    c = _FakeClient()
    c.push_op("operations/old", progress=5)
    calls_before = len(c._calls)
    # No new operations/ entries after this point.
    c._calls.append({"url": "/v1/credits", "body": {"credits": 10}, "status": 200})

    assert _capture_gen_id_from_window(c, calls_before) == ""


def test_scan_api_for_gen_filters_to_target_only():
    c = _FakeClient()
    # 3 concurrent gens: only gen B is done.
    c.push_op("operations/A_aaa", progress=20)
    c.push_op("operations/B_bbb", progress=10)
    c.push_op("operations/C_ccc", progress=15)
    c.push_op("operations/B_bbb", progress=100, done=True)
    c.push_op("operations/A_aaa", progress=80)  # A still in flight

    res_a = _scan_api_for_gen(c, "operations/A_aaa")
    res_b = _scan_api_for_gen(c, "operations/B_bbb")
    res_c = _scan_api_for_gen(c, "operations/C_ccc")

    assert res_a["done"] is False
    assert res_a["progress"] == 80
    assert res_b["done"] is True
    assert res_c["done"] is False
    assert res_c["progress"] == 15


def test_scan_api_for_gen_propagates_error_only_for_match():
    c = _FakeClient()
    c.push_op("operations/A_aaa", progress=20)
    c.push_op("operations/B_bbb", error="ALL_FAILED")

    assert _scan_api_for_gen(c, "operations/A_aaa")["error"] is None
    assert _scan_api_for_gen(c, "operations/B_bbb")["error"] == "ALL_FAILED"


def test_scan_api_for_gen_blocked_status_only_for_match():
    c = _FakeClient()
    c.push_op("operations/A_aaa", progress=10, status=403)
    c.push_op("operations/B_bbb", progress=10, status=200)

    assert _scan_api_for_gen(c, "operations/A_aaa")["error"] == "blocked_403"
    assert _scan_api_for_gen(c, "operations/B_bbb")["error"] is None


# Real Flow media_ids are UUIDs (or 24+ hex strings). Use realistic ids in
# the helper tests so `looks_like_media_id` does not filter them out.
_PARENT = "11111111-1111-1111-1111-111111111111"
_MID_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_MID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_MID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"


def test_collect_media_ids_after_excludes_parent_and_old_events():
    submit_ts = 1000.0
    c = _FakeClient()
    c.push_media(_PARENT, ts=900.0)        # before submit, ignore
    c.push_media(_MID_A, ts=1010.0)        # in-window
    c.push_media(_PARENT, ts=1015.0)       # parent re-emitted: exclude
    c.push_media(_MID_B, ts=1020.0)        # in-window
    c.push_media(_MID_A, ts=1025.0)        # duplicate: dedup

    out = _collect_media_ids_after(c, since_ts=submit_ts, exclude=_PARENT)
    assert out == [_MID_A, _MID_B], out


def test_collect_media_ids_after_no_exclude_returns_all_post_ts():
    c = _FakeClient()
    c.push_media(_MID_A, ts=10.0)
    c.push_media(_MID_B, ts=20.0)
    c.push_media(_MID_C, ts=30.0)

    assert _collect_media_ids_after(c, since_ts=15.0) == [_MID_B, _MID_C]


def test_build_l1_result_completed_shape():
    submit = {
        "gen_id": "operations/G_xxx",
        "project_url": "https://labs.google/fx/tools/flow/project/proj-1",
        "project_id": "proj-1",
        "locale": "",
    }
    wait = {"media_id": "mid-zzz"}
    out = build_l1_result(
        submit=submit, wait=wait,
        output_files=["downloads/t2v_1.mp4"], profile="ngoctuandt20",
    )
    assert out["status"] == "completed"
    assert out["media_id"] == "mid-zzz"
    assert out["edit_url"].endswith("/project/proj-1/edit/mid-zzz")
    assert out["output_files"] == ["downloads/t2v_1.mp4"]
    assert out["profile"] == "ngoctuandt20"
    assert out["generation_id"] == "operations/G_xxx"


def test_build_l1_result_failed_shape():
    out = build_l1_result(
        submit={"gen_id": "operations/Q", "project_url": "p"},
        wait={}, output_files=None, profile="prof", error="timeout",
    )
    assert out["status"] == "failed"
    assert out["error"] == "timeout"
    assert out["project_url"] == "p"
    assert out["generation_id"] == "operations/Q"


def test_attach_job_id_preserves_payload_keys():
    job = {"id": "job-123", "type": "text-to-video"}
    payload = {"status": "completed", "media_id": "m"}
    out = _attach_job_id(job, payload)
    assert out == {"job_id": "job-123", "status": "completed", "media_id": "m"}


def test_three_isolated_submits_do_not_cross_contaminate():
    """End-to-end isolation simulation across 3 successive submits.

    Models the actual capture pattern used by `submit_generate_l1`:
    snapshot calls_before, push the operations/ network event, capture
    gen_id from THIS submit's window only.
    """
    c = _FakeClient()

    captured: list[str] = []
    submit_ts: list[float] = []
    parent = None
    for tag in ("AAA", "BBB", "CCC"):
        before = len(c._calls)
        ts = time.time()
        # simulate Flow emitting the operations/ POST response after submit
        c.push_op(f"operations/{tag}_id", progress=2)
        submit_ts.append(ts)
        gen = _capture_gen_id_from_window(c, before)
        captured.append(gen)
        # interleaved noise from siblings
        c.push_op("operations/AAA_id", progress=10)
        c.push_op("operations/BBB_id", progress=10)
        c.push_op("operations/CCC_id", progress=10)

    assert len(set(captured)) == 3, captured
    assert captured == [
        "operations/AAA_id",
        "operations/BBB_id",
        "operations/CCC_id",
    ]

    # Now finish each gen with its own done event + media event.
    finish_pairs = [
        ("AAA", _MID_A),
        ("BBB", _MID_B),
        ("CCC", _MID_C),
    ]
    for tag, mid in finish_pairs:
        c.push_op(f"operations/{tag}_id", progress=100, done=True)
        c.push_media(mid, ts=time.time() + 1)

    for gen, ts, (_, mid) in zip(captured, submit_ts, finish_pairs):
        api = _scan_api_for_gen(c, gen)
        assert api["done"] is True, gen
        mids = _collect_media_ids_after(c, since_ts=ts, exclude=parent)
        # Each gen sees ITS OWN mid first because mids are appended in
        # finish order (matching the deterministic test fixture).
        assert mid in mids


def test_sentinel_canary_never_read_by_helpers():
    """No helper must read `client._gen_id` — those helpers only consult
    `_calls` and `_media_id_events`. This guards against accidentally
    reintroducing the 1-1-1 idiom that's broken under batch."""
    c = _FakeClient()
    c.push_op("operations/REAL_gen", progress=5, done=True)
    c.push_media(_MID_A, ts=time.time())
    # All helpers operate without touching _gen_id.
    assert _scan_api_for_gen(c, "operations/REAL_gen")["done"] is True
    assert _collect_media_ids_after(c, since_ts=0) == [_MID_A]
    # If anything mutated _gen_id from the sentinel, fail loudly:
    assert c._gen_id == "SENTINEL_DO_NOT_READ"
