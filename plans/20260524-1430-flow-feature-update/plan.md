---
title: "Flow 2026-05 feature update — full feature parity"
description: "Land all new Google Flow surfaces (6 livetest bugs + Agent + Scenes + Tools + inline image edit) into FlowEngine"
status: in-progress
priority: P1
effort: "Multi-wave (~3-5 sessions)"
branch: "claude/fix-live-test-bugs-2026-05"
tags: [flow-2026-05, feature-update, autopilot]
created: 2026-05-24
---

# Flow 2026-05 feature update — multi-wave plan

## Context

User invoked autopilot to "đẩy các feature mới trên gg flow về full code". MCP probe on
2026-05-24 (`docs/livetest-2026-05-24/probe_findings.md`) discovered the scope is much
larger than the branch name suggests: beyond the 6 known livetest bugs, Flow has shipped
**Agent mode**, **Scenes**, a **Tools mini-app marketplace (10+ apps)**, and **inline
image editing (crop / select / draw)** since the last sync.

Wrapping every new surface = multi-week epic. User authorized the full-epic option; this
plan decomposes it into ordered waves so each wave ships a coherent, live-verified slice.

## Wave structure (single autopilot session = Wave 1 only)

### Wave 1 — finalize 6 livetest bugs (THIS SESSION)

Goal: clear the branch named `claude/fix-live-test-bugs-2026-05` so the existing scope is
done before adding new surfaces. Bugs A and Omni-Flash already landed. Remaining:

- **B**: ingredients picker — finalize "Uploads" tab + "Add to Prompt" handling (WIP commit
  `2e49830` reached submit but not live PASS).
- **C**: extend-video L2 against 2026-05 edit view selectors.
- **D**: insert-object L2.
- **E**: remove-object L2.
- **F**: camera-move L2.

Acceptance per bug: live worker job on `ngoctuandt20` completes (free tier: L2 ops C/D/E/F
may return canonical `error_kind=paid_tier_required` per spine §9 — that counts as PASS
because the UI detection is the actual feature work). Capture credit tally + screenshot.

Codex parallel dispatch: 4 file-disjoint units (B / C / D&E shared / F).

### Wave 2 — Scenes + Uploads gallery + image inline editing

- New `View Scenes` left-rail surface backing API + UI.
- New `Uploads` browse view (the Bug B picker tab elevated to first-class page).
- Inline `crop / select / draw` for L2 image edit (extends `flow/operations/image.py` +
  new ops `crop-image`, `mask-image`, `draw-image`).

Dispatch when Wave 1 lands.

### Wave 3 — Agent mode

- New L0 job type: `agent-mode` (takes instructions + initial prompt, runs Flow's Agent
  loop until completion, captures all intermediate generations as child jobs).
- New frontend page for Agent Instructions.
- Live-verify needs a paid profile.

### Wave 4 — Tools marketplace (10+ mini-apps)

One operation handler per tool (Mockup / Image Editor / Shot Explorer / Mask Magic /
Converge / Grid Architect / Shader Effects / Type Overlays / pixelBento / Poster
Designer). Each tool has its own submit endpoint and output shape — must probe each
individually.

Decompose into 3 sub-waves of file-disjoint codex (`Image tools` / `Video tools` /
`Community tools`).

## Wave 1 — codex unit decomposition

OWNS contracts so 4 parallel codex don't conflict:

- **Unit 1** (Bug B finalize): `flow/operations/ingredients.py` only. Use probe data in
  `docs/livetest-2026-05-21/l1_ingredients_upload_probe.json` plus today's
  `docs/livetest-2026-05-24/probe_findings.md`. READS: `flow/operations/frames_to_video.py`
  for media-picker pattern. Acceptance: live ingredients PASS on `ngoctuandt20` OR
  documented `recaptcha_burned` reproduction.
- **Unit 2** (Bug C extend): `flow/operations/extend.py` only. READS: `flow/operations/_base.py`.
- **Unit 3** (Bug D + E — insert/remove share bbox tooling): `flow/operations/insert.py`,
  `flow/operations/remove.py`. READS: `flow/operations/_base.py`.
- **Unit 4** (Bug F camera-move): `flow/operations/camera.py` only.

FORBIDDEN for all 4: do not touch `flow/operations/_base.py`, `flow/client.py`,
`flow/landing.py`, `flow/model_selector.py`, server/worker layers.

Each unit must:
1. Probe live DOM via MCP / inspect script if 2026-05 selectors differ from existing code.
2. Update selectors to match 2026-05.
3. Detect `paid_tier_required` banner and set `error_kind="paid_tier_required"` cleanly.
4. Add/update unit tests under `tests/`.
5. Commit as `fix(livetest): bug <X> — 2026-05 selectors`.

## Hard caps (autopilot)

- ≥3 round-2 fix attempts on same unit → architectural stop.
- ≥2 live-verify failures with same symptom → root-cause stop.
- Profile pool exhaustion (reCAPTCHA on last live profile) → stop, report to user.
- Credit cap: report tally per round but no hard limit (per user 2026-05-02).

## Reports + memory

- Session reports go to `docs/session-reports/2026-05-24_*.md` per wave.
- Probe artifacts to `docs/livetest-2026-05-24/`.
- This plan's `status` updates as waves complete.
