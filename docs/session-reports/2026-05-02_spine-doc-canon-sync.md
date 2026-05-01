# Session - 2026-05-02: SPINE doc canon sync workstream

**Base commit before workstream:** `6a6b6a3`  
**Final synced master commit:** `cf991e0`  
**Final canon-sync tag:** `docs-canon-final-20260501-2352`  
**Previous SPINE checkpoint tag:** `spine-final-20260501-2323`

## TL;DR

On 2026-05-01 -> 2026-05-02, the SPINE workstream established
`docs/PROJECT_SPINE.md` as the canonical repo spine, synchronized the linked
canon docs (`SPEC`, `DESIGN`, `FLOW_UI`, `WORKPLAN`), and used repeated doc
review to surface 6 real runtime bugs that were then fixed on `master`. The
workstream closed with 4 review rounds, final doc sync tags pushed, and live
verification on the chain-create/profile-mutation paths.

## Goal

Establish one canonical project spine doc, sync all linked canon docs back to
current `master`, and use doc review as a deliberate bug-finding pass instead
of treating docs as a post-hoc write-up.

## Workstream

| Phase | Shape | Outcome |
|---|---|---|
| Bootstrap | `#109` + `#110` | Synced the 2026-05-01 cutover session report and introduced `PROJECT_SPINE.md` as the canonical spine doc |
| Round 1 | 10 reviewer Codex (`v1`) | Surfaced API/profile-route drift, WS frame-shape drift, and first-pass spine fixes -> `#111`, `#112`, `#113` |
| Round 2 | 10 reviewer Codex (`v2`) | Surfaced chain-builder root-type drift and stale-after-fix doc drift -> `#114`, `#115` |
| Round 3 | 5 reviewer Codex (`v3`) | Verified the post-r2 state and produced the final remaining fix list |
| Round 4 | 7 targeted fix Codex | Closed the remaining canon/runtime/doc gaps -> `#116`, `#119`, `#120`, `#121`, `#122`, `#123`, `#125` |

Total review/fix shape: 25 reviewer Codex across rounds 1-3, then 7 targeted
round-4 fix Codex to land the remaining changes.

## PRs merged

Note: the handoff text described this as a "15 PR" workstream across the
`#109-#125` span, but the enumerated merged set provided for this report totals
14 PRs because `#117`, `#118`, and `#124` were not part of the merged list.

| PR | Commit | Area | Outcome |
|---|---|---|---|
| #109 | `77c16bb` | docs sync | Synced the 2026-05-01 public cutover report into repo canon |
| #110 | `cfa16a4` | spine bootstrap | Added initial `docs/PROJECT_SPINE.md` as the canonical project index |
| #111 | `f73657b` | profiles/api | Fixed `list_jobs`, added `quarantine` / `activate`, and aligned profile update verb usage |
| #112 | `c5e0f58` | ws client | Aligned `frontend/js/ws.js` with server `{event,data}` frames |
| #113 | `779ef10` | docs/spine | Applied 25+ findings from 10 v1 reviewers |
| #114 | `408d598` | chain builder | Included `ingredients-to-video` in `L1_ONLY_TYPES` and validated placement |
| #115 | `b82f523` | docs/spine | Removed stale-after-fix drift entries and re-synced post-fix details |
| #116 | `a70dd83` | docs/workplan | Marked Phase A as historical and stopped presenting it as active planning |
| #119 | `c9a13ca` | docs/spine | Final spine sync for precedence, WS ping, quickstart, defaults, and single-profile deploy reality |
| #120 | `22f6342` | docs/flow-ui | Marked stale behavioral notes and renamed `camera-control` -> `camera-move` |
| #121 | `a999455` | docs/design | Synced INV-5, WS framing, auth, and public deploy topology |
| #122 | `b37c34a` | login/defaults | Fixed repo-relative `FLOW_PROFILE_LIST_FILE` default and fail-fast missing-file behavior |
| #123 | `cf991e0` | chain create | Fixed chain-level profile precedence and consistent step-profile fallback |
| #125 | `e3202fe` | docs/spec | Synced the INV-5 chain-claim narrative to current code behavior |

## Real bugs surfaced and fixed

| # | Bug | Fix |
|---|---|---|
| 1 | `GET /api/profiles/{name}/jobs` called `list_jobs` with a positional dict even though the function is keyword-only | `#111` switched the call to keyword args |
| 2 | `POST /api/profiles/{name}/quarantine` and `POST /api/profiles/{name}/activate` were used by the UI but missing on the server | `#111` added both routes |
| 3 | `frontend/js/ws.js` parsed `{type,payload}` while the server emits `{event,data}` | `#112` aligned the client parser with the server frame shape |
| 4 | `frontend/js/pages/chain-builder.js` omitted `ingredients-to-video` from the root-only type set | `#114` added it to `L1_ONLY_TYPES` and placement validation |
| 5 | `POST /api/chains` without `chain.profile` could create unpinned jobs | `#123` made chain-level `profile` win and added consistent-step fallback/erroring |
| 6 | `FLOW_PROFILE_LIST_FILE` still defaulted toward the legacy AI-Engine3 path assumptions | `#122` made the default repo-relative and fail-fast if the file is missing |

## Live verification

| Case | Input shape | Result | Outcome |
|---|---|---|---|
| A | `chain.profile` set, step profiles null | `201` | All jobs pinned to the chain profile |
| B | `chain.profile` null, step profiles present and consistent | `201` | Fallback profile resolution works |
| C | `chain.profile` null, step profiles mismatch | `422` | Clear validation error; no ambiguous partial pinning |
| D | `chain.profile` null, no step profile anywhere | `422` | Clear validation error instead of unpinned creation |
| Profiles | Quarantine -> activate end-to-end | `200` on both transitions | Status mutation persists correctly across the full route path |

## Tags pushed

| Tag | Purpose |
|---|---|
| `pre-spine-r2-20260501-2241` | Checkpoint before the round-2 review/fix pass |
| `pre-spine-r3-20260501-2310` | Checkpoint before the round-3 final-review pass |
| `pre-r4-spine-final-20260501-2335` | Checkpoint before the last round of final SPINE/doc fixes |
| `spine-final-20260501-2323` | SPINE workstream final-state checkpoint before the last canon sync pass |
| `docs-canon-final-20260501-2352` | Final all-canon-doc sync point pushed for the workstream |

## Open items / next steps

| Type | Item | Status | Notes |
|---|---|---|---|
| Capacity | Single-profile pool | open | Only `ngoctuandt20` is currently confirmed healthy; add more Flow-eligible accounts before depending on multi-profile throughput or re-running parked multi-profile verification |
| Model migration | LP -> Lite fallback deadline | open | `DEFAULT_MODEL` already flipped to `veo-3.1-lite-lp` in `#108`, but `veo-3.1-fast-lp` still intentionally appears in route-sentinel/fallback paths and older docs; audit remaining refs before the 2026-05-10 cutoff |
| Schema cleanup | Legacy `safety_filter` DDL column | open | Fresh `jobs` DDL still carries an unused `safety_filter TEXT` column even though the live job API guidance is "do not wire or persist"; remove or formally archive the schema remnant in a later cleanup pass |

## Lessons

- Doc review can surface real engine bugs, not just wording drift, if the review lens is allowed to challenge runtime assumptions.
- Ten narrow review lenses find more actionable drift than one generic review pass.
- When doc Codex and code Codex run in parallel, a follow-up sync round is required; round 2 found stale-after-fix entries that were technically correct when written but already outdated by merged fixes.
