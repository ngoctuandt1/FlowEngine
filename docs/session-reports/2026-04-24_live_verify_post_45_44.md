# Live-verify — post #45 / #44 fixes (2026-04-24)

## Context

Master commit tested: **`b62ac73`** (not `221d380` as original ticket — fetch
confirmed `b62ac73 refactor(flow): extract resolve_final_media_id helper (#37)`
is the actual `origin/master` HEAD as of run-time, with PR #46 (`2dacd96`)
and PR #44 (`3417448`) underneath).

Baseline `pytest tests/` → **308 passed, 1 skipped, 3 xfailed** (ticket
mentioned 305; diff +3 comes from the PR #37 refactor test additions —
not a regression).

Scope reduced from original ticket on user direction: single profile
`ngoctuandt20`, 3 L1 `text-to-video` + optional L2 `insert-object` +
L2 `remove-object` on J2's project. Cross-profile parallelism not
exercised.

Worker env: `WORKER_PROFILES=ngoctuandt20 MAX_CONCURRENT_JOBS=1`.
Warm-profile auto-login completed cleanly (Gmail → TOTP → inbox) —
session cookies fresh before the batch.

## Batch results

| Job | Type | Status | Project suffix | `media_id` | Output | Note |
|---|---|---|---|---|---|---|
| J1 `556f2136` | text-to-video | ❌ FAILED | — | — | — | Marketing landing bypass incomplete (see finding 1) |
| J2 `4142ef3d` | text-to-video | ✅ completed | `…3cecf52a` | `225c2222-…` | `t2v_1080p_1777019417.mp4` | Clean happy path, no marketing landing |
| J3 `63261fa9` | text-to-video | ⚠️ completed (suspicious) | `…3cecf52a` | `ea418494-…` | `t2v_720p_1777020043.mp4` | Landed in **J2's** project instead of creating new (see finding 3) |
| INS `0ee7d43c` | insert-object | ✅ completed | `…3cecf52a` | `68ad2734-…` | `ins_1080p_1777019974.mp4` | Parent media=`225c2222`, new `68ad2734` — media_id helper works |
| REM `f35490d8` | remove-object | ❌ FAILED | `…3cecf52a` | (parent `225c2222`) | — | "Failed to find Remove button" |

## PR #44 verdict — **INCOMPLETE FIX**

**J1 failure repro.** Marketing landing **detection** fired correctly
(`flow.operations.generate: Flow marketing landing detected — clicking
'button:has-text('Create with Flow')'` at `15:22:51`), but the click did
not navigate into the app:

- page URL at failure: `https://labs.google/fx/tools/flow#capabilities`
  (an in-page anchor, not the Flow editor)
- "New-project button did not attach within 15s"
- `RuntimeError: Failed to find '+ New project' button on Flow homepage`
- failure screenshot: `debug_screens/new_project_btn_missing_20260424_152313.png`

Hypothesis: the `button:has-text('Create with Flow')` selector matched
a scroll-anchor nav link ("Create with Flow" appears as both a hero CTA
and a nav shortcut to `#capabilities`) instead of the hero CTA that
actually mounts the app. The A/B marketing variant served this run
differs from the variant PR #44 was written against.

On J2 (a few minutes later) the marketing landing was **not** served —
normal homepage with `+ New project` button was available directly. So
the landing is indeed A/B per memory `feedback_flow_marketing_landing_bypass.md`.

**Action:** needs follow-up issue — harden the selector (e.g. prefer the
button whose parent is the hero section, or follow up with a post-click
URL assertion and click-next-candidate if still on `/flow#…`).

## PR #46 verdict — **NOT EXERCISED**

No evidence in the worker logs that the new cold-start fallback
(`DOM media-id scrape recovered N id(s)` / `Completion via DOM`) fired
on any job. J2 took the clean download-pipeline path. The original
repro (first L1 after cold Chrome launch across ≥4 profiles) was not
reproducible with 1 profile, so this run neither confirms nor refutes
the fix. Retest scope should be multi-profile.

## L2 media_id (parked bug #4) — **partial evidence**

- **INS** inherited parent's `media_id=225c2222` but ended with
  `media_id=68ad2734` — distinct from parent, output file populated,
  resolver worked end-to-end. PR #37's `resolve_final_media_id` path
  is live-green for insert-object.
- **REM** never reached the submit stage (fail before UI draw) so
  there's no new media_id evidence for remove-object. Parked bug #4
  review doc not updated — no new signal to add.

Collateral observation: on INS, direct-nav to the stored edit URL
(`.../edit/225c2222-…`) did not mount the editor within 15s; the
first-tile-click fallback picked media `ea418494` and proceeded. That
fallback is existing behavior (logged `falling back to first-tile
click`), not a regression.

## Finding 3 — L1 "reusing existing project" (NEW suspicious behavior)

J3 (`text-to-video`, `parent_job_id=null`, `chain_id=null`) completed
with `project_url=…3cecf52a` — **the same** project J2 created, not a
fresh project as an L1 should. Its `media_id=ea418494` is the same id
the INS first-tile fallback landed on earlier.

The J3 worker trace isn't in the logs I own (handled by a pre-existing
worker process on the box, not the one this session started — see log
coverage note below). Cannot diagnose from evidence at hand whether:
(a) `+ New project` click reopened an existing project for some UI
reason, or (b) temp-profile clone carried residual editor state, or
(c) J3 raced with INS and observed the post-INS edit URL as its result.

Also J3's output is `t2v_720p_…` not `t2v_1080p_…` — different quality
path than J2, unexplained.

**Action:** needs a dedicated repro run (fresh profile, sequential L1s,
full log ownership) before filing — current evidence is circumstantial.
Flagged here for a future session.

## REM failure

`flow.operations.remove: Failed to find Remove button` after retry
(`attempt 1/3 failed (remove-object failed: no_signal_timeout)`).
Log shows first-tile recovery DID mount the editor (video element
loaded) but the Remove mode button selector missed. Could be:

- DOM changed after INS completed in the same project (mode bar shifted)
- UI glitch — single sample, not enough signal to file

**Action:** single occurrence, low confidence. Re-run later; file only
if it repeats.

## Log coverage note

Two worker processes appear to have been active concurrently during the
run (J3 was claimed ~1s before INS by `worker_id=worker-1` but J3's
execution trace is absent from this session's log files). A stale
worker from a prior session was likely still attached to the same
server. `MAX_CONCURRENT_JOBS=1` is enforced per-process, not globally,
so two workers + one profile ⇒ both can hold BUSY state on different
jobs at once. No code bug, but noted as operational hygiene: kill all
old workers before a new verify run.

## Issues to open / reopen

1. **Marketing-landing bypass incomplete** (new) — PR #44 scope cancelled
   the earlier attempt but current fix doesn't cover this A/B variant.
   Evidence: this report + screenshot.
2. **Cold-start race (#45/#46)** — remains unverified; retest scope
   multi-profile. Do not close #45 yet.
3. Parked L2 `media_id` bug #4 — partial positive evidence (INS). No
   update needed to `reviews/4_media_id_bug.md` from this run.

Per session contract ("KHÔNG tự sửa"): no code was touched for any of
the above.

## Commits tested

```
b62ac73 refactor(flow): extract resolve_final_media_id helper + fail-fast config (#37)
2dacd96 fix(#45): scrape media_ids from DOM on cold-start download race (#46)
3417448 fix(l1): bypass Flow marketing landing + harden new-project selector (#44)
```
