---
date: 2026-04-20
epic: Phase B — multi-level chains (Tier 2 live)
run: Run 20 (L2 extend chain — H1/H2 discriminator)
status: ✅ PASS — H1 confirmed; L2 extend completed end-to-end with 1080p output
author: Claude (Opus 4.7) supervisor + parallel executor session
---

# Tier 2 Run 20 — L2 extend PASS; H1 confirmed

## 1. Goal

Unblock Run 19's L2 chain failure (worker landed on Flow marketing page
instead of editor SPA). Two competing hypotheses from
`docs/CHROME_LAUNCH_SECURITY.md` §5:

- **H1:** temp-clone user-data-dir drops cookies → marketing landing.
- **H2:** Google fingerprints CDP-attached Chrome → serves marketing
  variant regardless of cookies.

Discriminator: run worker with `FLOW_USE_BASE_PROFILE=1` (skip temp-clone,
read base `chrome-profiles/ngoctuandt20/` directly).

## 2. Chain

- L1 `4a032d83-8a31-42b7-be58-c937485736ca` text-to-video — DONE (Run 18)
- project: `fb9728e5-a5f4-4bb5-8579-df258dd8969f`
- parent media: `df78a409-9a01-4594-a47c-948e2bef71d3`
- chain: `1871a218-d9f0-41cb-bc20-ccb4135d9671`

## 3. Execution — all 8 plan steps PASS

| Step | Result |
|---|---|
| 1. Selective Chrome kill (`kill_engine_chrome.ps1`) | PASS (no leftover) |
| 2. Selective Python kill (port 8080 / `run_worker.py`) | PASS (no leftover) |
| 3. Stuck-job reset (`7c41a24a`) | N/A — already `failed` in DB |
| 4. `warm_profile.py ngoctuandt20` | PASS — CDP connect, inbox loaded, cookies persisted |
| 5. `run_server.py` start | PASS — 0.0.0.0:8080 |
| 6. Worker with `FLOW_USE_BASE_PROFILE=1 WORKER_PROFILES=ngoctuandt20` | PASS — claim loop on correct profile |
| 7. POST L2 extend | PASS — job `44d928bd-ba79-4e9c-8d8c-aca8b3992c7c` created |
| 8. Monitor 300s | PASS — completed 2026-04-20T04:09:48Z |

## 4. Output artifact

| Field | Value |
|---|---|
| File | `ext_1080p_1776658187.mp4` |
| Size | 20,490,052 B (~19.5 MB) |
| Resolution (ffprobe) | 1920×1080 |
| Duration | 8.0 s |
| Codec | H.264 |
| New media_id | `293d3ab0-93fc-4d7d-8062-b5b99f60a2d8` |

## 5. Diagnosis — H1 confirmed (with caveat)

`FLOW_USE_BASE_PROFILE=1` eliminated the temp-clone signal. Worker
launched Chrome against the same dir `warm_profile.py` wrote cookies to.
Editor SPA loaded; extend completed; 1080p UI upscale produced the
20 MB file.

**Caveat:** worker log also shows
`Flow landing detected → Landing recovery complete` firing mid-run.
Landing still renders intermittently even with base profile, but the
`flow/landing.py` recovery hook (committed in `d856bf6`) clicks the
CTA to bridge into the editor. Pure `FLOW_USE_BASE_PROFILE=1` is
necessary but not sufficient — landing recovery is the second
component of the working configuration.

H2 (CDP fingerprinting forcing marketing) is not the dominant failure
mode observed in Run 19. Option 2 (pipe) and Option 3 (stealth) from
`docs/CHROME_LAUNCH_SECURITY.md` §4 remain unnecessary for now.

## 6. Commits landed after Run 20

| Hash | Scope |
|---|---|
| `a5009af` | `feat(upscale): UI-driven 1080p primary path (B38, P0)` |
| `d856bf6` | `fix(editor-landing): recover worker when Flow serves marketing variant (Run 20, P0)` |
| `1154ffc` | `fix(wait+login): tighten POLICY regex, reload stuck login, dump screenshot on error` |
| `a4c4888` | `fix(warm_profile): bridge Workspace marketing → sign-in; fail loud on unknown landing` |
| `849834e` | `fix(claim): child inherits direct-parent media_id + edit_url together (supersedes B30/B32)` |

Tests: 144 passed (was 123 pre-Run 20; +21 new trip-wires/state tests).

## 7. Follow-ups

- Update `CLAUDE.md §4` + `docs/SPEC.md §A.1 INV-5`: child of `extend-video`
  now inherits direct-parent media+edit_url together (B30/B32 superseded).
- Document `FLOW_USE_BASE_PROFILE=1` as the recommended default worker
  env for L2+ chains. Cross-project parallelism on the same profile
  would need a policy decision — `project_lock.py` already serializes
  same-project.
- Backfill B38 Run 17/18 session report (docs-only, open from handoff).

## 8. Artifacts

- Warm log: `warm_profile_run20_current.log`
- Server log: `server_run20_current.log`
- Worker log: `worker_run20_current.log`
- Video: `downloads/ext_1080p_1776658187.mp4`
- No `logs/error_*` generated during Run 20 window.
