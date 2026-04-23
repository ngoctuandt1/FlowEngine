# Session — 2026-04-23: B39 editor-mount fallback (stress-test + fix + batch-mode design)

**Branch:** `claude/unruffled-chebyshev-177086`
**Profile:** `ngoctuandt20` (Ultra)
**Commits:** `28b8b1c` (branch), `1186768` (master squash via PR #32)
**Related parked work:** `docs/session-reports/2026-04-23_l2-media-id-fix-live-verified.md` (landed earlier same day)

## TL;DR

Fan-out stress test on a single project (1 L1 → 5 L2 siblings) exposed a second
failure mode of the Flow SPA that the existing `navigate_to_edit` recovery did
not handle: the URL stays on `/edit/{stale_media_id}` but the composer never
mounts (no `<video>`, no mode buttons). `click_action_button` then surfaced the
problem as a misleading `"Failed to find Insert button"`.

Fix: after `_activate_clip_tile`, do a bounded wait for the editor's `<video>`
element; on miss, fall back to first-tile click — the same recovery path the
URL-strip branch already uses. Live-verified on ngoctuandt20: **4/4 fan-out L2
jobs pass**, including two runs where the new fallback actually fired (log
evidence below).

Also captured in this report: the batch-mode architectural design that came
out of discovering Flow parallelizes generation server-side per project.

## Stress test that exposed B39

Goal was to validate "open profile once, create project once, then fan out N
L2 siblings" — i.e. whether the per-job Chrome-open cost is acceptable.

- L1 `text-to-video` on fresh project (seed).
- 5 × L2 siblings against the same `project_url` / same `parent_job_id`:
  `extend-video`, `camera-move`, `insert-object`, `remove-object`, `extend-video`.

Results pre-fix:

| Child op | Outcome | Symptom |
|---|---|---|
| extend-video #1 | ✅ | — |
| camera-move | ❌ (user error) | `"Failed to find camera preset: Pan Left"` — `Pan Left` is not in `CAMERA_MOTION_PRESETS` / `CAMERA_POSITION_PRESETS` |
| insert-object | ❌ (real bug) | `"Failed to find Insert button"` |
| remove-object | ❌ (cascaded) | same class as insert |
| extend-video #2 | ❌ (cascaded) | same class |

MCP Chrome probe against the failing state showed `buttons: []` on the supposed
`/edit/{media}` page — the URL looked right but the composer had not rendered.
This narrowed the failure to navigation, not the operation handlers.

## Root cause

Flow's SPA has two failure modes when you `page.goto` a stale
`/edit/{media_id}` (i.e. media that was valid at time of job-planning but has
since been consumed/replaced by a sibling op):

1. **URL-strip** — page bounces to `/project/{id}` without the `/edit/` suffix.
   Already handled by `navigate_to_edit`'s existing tile-click fallback.
2. **URL-kept** — page stays on `/edit/{stale_media_id}` but never mounts the
   editor: no `<video>`, no mode buttons. The existing `wait_for_video_loaded`
   soft-warns after 15s and returns; downstream `click_action_button` then
   fails with the misleading "Insert button" error.

The fan-out shape surfaced this because sibling ops consume the parent media
fast enough that the *second through Nth* child lands on a stale reference by
the time it hits `navigate_to_edit`.

## Fix (`28b8b1c`)

`flow/operations/_base.py:navigate_to_edit`:

```python
if not await _editor_mounted(page, timeout_ms=8000):
    logger.warning(
        "Editor did not mount after nav to %s — falling back to first-tile click",
        edit_url_val[:80],
    )
    recovered = await _click_video_tile(page, "")
    if not recovered or not await _editor_mounted(page, timeout_ms=8000):
        raise RuntimeError(
            f"Editor did not mount for {edit_url_val} and first-tile recovery "
            f"failed. Parent media may be stale (consumed by sibling op)."
        )
    if media_id:
        await _activate_clip_tile(page, media_id)


async def _editor_mounted(page, timeout_ms: int = 8000) -> bool:
    """Return True when the /edit/ composer has rendered its <video>."""
    try:
        await page.locator("video").first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False
```

Key points:

- **Bounded**: 8s ceiling on each `_editor_mounted` wait; total worst-case 16s
  (first check + recovery + second check) before hard failure.
- **Fallback reuses proven path**: first-tile click is the same recovery the
  URL-strip branch already uses, so we are not adding a new surface area.
- **Hard failure on second miss**: raises with a stale-parent diagnostic
  instead of letting the downstream `click_action_button` emit a confusing
  error. Callers now see the real reason.
- **Preserves clip targeting**: on successful recovery, re-runs
  `_activate_clip_tile(page, media_id)` so the op still operates on the
  intended clip.

## Tests (`28b8b1c`, `tests/test_base.py`)

Added 3 cases covering the new branches, plus a shared test fixture refactor:

- `_make_locator_mock(wait_for_ok=True)` — toggles the `<video>` wait behaviour
  so a test can simulate the editor never mounting.
- `_make_client(..., editor_mounts=True)` — wires the locator mock into the
  page.

New cases:

1. `test_navigate_recovers_when_editor_never_mounts` — first nav's wait fails,
   first-tile click lands on a live editor, operation succeeds.
2. `test_navigate_raises_when_editor_dead_and_recovery_fails` — both waits
   fail, expects `RuntimeError` with the stale-parent message.
3. `test_navigate_skips_fallback_when_editor_mounts_normally` — regression
   guard that the fallback path is *not* triggered on the happy path.

`python -m pytest -q` → **238/238 passed**.

## Live verification (2026-04-23, post-fix)

Same fan-out shape repeated on ngoctuandt20:

| Job | Op | Outcome | media_id (first 8) | Notes |
|---|---|---|---|---|
| J1 | L1 text-to-video | ✅ | seed mid | parent |
| J2 | L2 extend-video | ✅ | distinct | — |
| J3 | L2 camera-move | ✅ | distinct | preset from valid list |
| J4 | L2 insert-object | ✅ | distinct | **B39 fallback fired** |
| J5 | L2 remove-object | ✅ | distinct | **B39 fallback fired** |

Log evidence that the fallback ran in production (not just the happy path):

```
22:55:28 WARNING Editor did not mount after nav to
    https://labs.google/fx/tools/flow/project/.../edit/<stale_mid>... — falling back to first-tile click
```

4/4 post-fix; 0 regressions in the other L2 shapes. The camera-move run used a
valid preset this time, so the "Pan Left" issue is confirmed as a user-input
bug, not a code bug.

## PR / merge

- PR #32 opened on `claude/unruffled-chebyshev-177086` against `master`.
- `gh pr merge` blocked locally: `fatal: 'master' is already used by worktree
  at 'D:/AI/FlowEngine'`.
- Merged via GitHub REST API:
  `gh api repos/ngoctuandt1/FlowEngine/pulls/32/merge -X PUT -f merge_method=squash`
  → squash commit `1186768`.

## Master stash notes (preserved for next session)

Pulling master before the merge aborted on local WIP. Preserved in two scoped
stashes, **do not drop without review**:

- `stash@{0}: pre-B39-merge master wip: worker upload path hardening` —
  `_resolve_upload_path` path-traversal hardening in `worker/dispatcher.py`.
- `stash@{1}: pre-B39-merge master wip: resolve_final_media_id refactor` —
  `flow/operations/_base.py` + `tests/test_l2_media_id.py` edits for a
  `resolve_final_media_id` extraction.

Both are intentionally independent of B39 and should be re-homed onto a fresh
branch next session.

## Out-of-scope discovery — Flow parallelizes gen server-side

While the stress test ran, a UI screenshot (sent by the user) showed three
concurrent generations on the same project progressing at 7% / 36% / 47% with
one already done. Combined with the user's observation that navigating away
and back re-shows in-flight gens, this confirms: **Flow's generation is
server-side async per project**; the client does not need to hold the page
open for a job to complete.

Implication for batch throughput: submit-then-disconnect is safe, download can
poll later. This reshapes the next phase.

## Next-phase design (batch mode, user-approved 4-PR plan)

Current worker pattern opens a FlowClient per job. CDP reconnect to the base
profile is cheap (~1s), but composer-state churn and per-job nav cost still
dominate on a fan-out. Target architecture:

- Replace per-job `ProjectLock` with a `ProjectSession` that groups N pending
  jobs for the same `project_url` into one FlowClient session.
- **Serial submit** inside the session, ~3s spacing between submits, no wait
  for completion. Use a DOM signal ("submit re-enabled") rather than a fixed
  sleep to avoid composer-state races.
- **Parallel gen** — Flow handles this server-side; the session just holds the
  page open or disconnects and comes back.
- **Polling download** — iterate pending children, use the existing network /
  DOM / URL media_id resolution, download each as it completes.
- Per-job status in the batch so a single failure does not poison siblings.

Planned PR sequence:

1. **PR-1** — split `submit_only()` + `download_only()` in `_base.py` and the
   5 operation modules; keep current `run_*()` wrappers as the
   submit→download composition so the existing worker path is unchanged.
   Tests cover both paths.
2. **PR-2** — add `POST /api/batches` and a server-side batch claim endpoint.
   1-job worker flow still works.
3. **PR-3** — dispatcher "batch" mode: gather pending jobs sharing a
   `project_url`, run one FlowClient session. Feature-flag off by default.
4. **PR-4** — enable batch mode, live-verify on ngoctuandt20, measure
   throughput delta vs. current per-job path.

This starts next session after this report is committed.

## Spec follow-ups

- `CLAUDE.md §7 Common Gotchas` can grow a "Flow stale `/edit/` has two
  failure modes" entry pointing at B39.
- Camera preset validation — worth a server-side pre-flight check in
  `server/routes/jobs.py` so invalid presets fail at job creation instead of
  at runtime (the "Pan Left" case).
