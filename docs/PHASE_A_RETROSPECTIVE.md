# Phase A Retrospective — what we learned, what carries forward

> Distilled from 30+ session reports + 19 Tier 2 runs + 38 B-numbered fixes
> (2026-04-16 → 2026-04-20). One-page synthesis per user request: "k tổng hợp lại
> thành có giá trị à?". Actionable patterns, not another ledger.

---

## 1. What Phase A delivered

| Deliverable | Evidence |
|---|---|
| 5 video op types work end-to-end (t2v / extend / insert / remove / camera) | Run 10 (3-op chain), Run 12 (5-op chain-with-extend-middle) |
| Chain invariants (INV-1..5) hold across ops | Run 10 + Run 12 + Tests 5/6/7 infra |
| Cross-locale (EN-switched) verified on `ngoctuandt20` | Run 10.b |
| Profile pinning + project lock + stale recovery | DB-layer tests 5/6/7 |
| 720p download stable (API path, deterministic after B37) | Run 15 3/3 PASS |
| 1080p download via UI upscale (L1 baseline) | Run 17/18 (uncommitted) |
| 123 tests passing (+125 after PR#18 merge) | `pytest tests/ -q` |

Open at session close: L2 extend chain on a freshly-reset profile (Run 19
block — profile-auth, not engine logic).

---

## 2. Patterns that EARNED their place — carry forward

Each of these was not obvious on day one; each is now a reusable template.

### P1. Radix tab / menu selectors → attribute-ends-with, not hard ID
`[id$="-trigger-PORTRAIT"]` instead of `#radix-:r27:-trigger-PORTRAIT`. Radix
re-hashes the prefix on every render; the suffix is stable.

### P2. Icon-ligature text, not locale strings
`<button><i>download</i></button>` → `button:has(i:text-is("download"))`.
Works EN + VI identically. Applies to: `add_2` (new project), `crop_9_16`
(aspect), `arrow_forward` (submit), `videocam` (camera mode), `ink_eraser`
(remove), `add_box` (insert), `download` / `more_vert` (menus).

### P3. Pre-open state guard on Radix triggers
Before clicking a chip/menu trigger, check `data-state="open"` — clicking an
already-open trigger toggles it closed. Saved B19 (aspect), applied to B35
(quantity), will apply to anything Radix.

### P4. Source trip-wire tests via `inspect.getsource` / `Path.read_text`
When the bug is "someone silently reverts a one-liner" (B35 missing
`_set_output_count` call, B37 `evt["mid"]` key rename, `warm_profile.py`
ServiceLogin URL), the defense is an `assert "xxx" in source` test. Cheap,
blocks regression at CI time.

### P5. 2-layer lock for user-approved decisions
Memory (cross-session context) + trip-wire test (CI enforcement). Both or
nothing. Example index in memory `feedback_locked_items_require_user_approval.md`.

### P6. tile.click for SPA routing, not page.goto
Flow's SPA routing slug is NOT the `media_id` and NOT the `fe_id_{X}` tile
attribute — it's internal to the router. `page.goto(/edit/{...})` bounces.
Only clicking the tile (`[data-tile-id^="fe_id_"]`) lets the router resolve.

### P7. B22 claim-time inheritance + B30 walk-up is the chain pattern
Worker claims an L2+ job → server SELECT parent and propagates
`project_url` / `media_id` / `edit_url` into the child row in the same
UPDATE. `media_id` walks up past `extend-video` ancestors (max 16). `edit_url`
comes from the direct parent (B32 split). This lets `navigate_to_edit` just
do its job.

### P8. B32 tile-activation closes the chain-with-extend gap
When URL media ≠ target media after SPA bounce, dispatch a MouseEvent
sequence on `[data-tile-id="fe_id_{target}"]`. Re-enables the sidebar.
5-op chain (t2v → extend → insert → remove → camera) works because of this.

### P9. Supervisor ↔ parallel-sessions workflow
Main session produces self-contained prompts (with memories cross-checked);
parallel sessions execute heavy work (live Tier 2, refactors, MCP probes).
Main session reviews diffs, syncs docs, commits. User runs codex review on
every parallel-session diff.

### P10. FlowClient clone-to-temp per job
Each job gets a fresh `%TEMP%\flow_<profile>_<ts>` cloned from the base
`chrome-profiles/<profile>/`. Isolates state between jobs. Downside: cookies
may not survive the clone under some Chrome builds (suspected in Run 19).

---

## 3. Anti-patterns — things that broke and shouldn't come back

| # | Anti-pattern | What broke | Fix |
|---|---|---|---|
| A1 | `taskkill //F //IM chrome.exe` | Kills user's personal Chrome, not just engine's | `scripts/kill_engine_chrome.ps1` filters by `--user-data-dir` |
| A2 | Hard-coded auto-login URL without user approval | `ServiceLogin?service=googlefx` was rejected | Gmail entry URL, auto-login via `flow.login` |
| A3 | Text-based locale-dependent selectors | VI-locale account broke homepage (Run 1) | Icon-ligature (B18 `add_2`) |
| A4 | `page.goto(/edit/{media_id})` to enter editor | SPA strips `/edit/`, lands on project root | `tile.click` (B38, memory `feedback_flow_edit_nav_click.md`) |
| A5 | `git add -A` / wildcards when parallel sessions have uncommitted work | Would commit someone else's half-done files | Explicit file paths in `git add` |
| A6 | Relying on account defaults for critical settings | `ngoctuandt20` default output_count was x2 → 2× LP per submit | B35 force x1, always |
| A7 | Immediate `raise` in defensive guards on transient UI states | Run 11 J2 extend false-positive on healthy t2v | Probe-first pattern (B31) — check state, click only if needed |
| A8 | Speculative API window bumps (B34 → B34b) without verifying the endpoint works at all | `_upsampled` returns 404 permanent; retry bumps were dead code | Probe the endpoint first (B36 probe → B38 UI path) |
| A9 | "Cache-preserve bisect" on corrupted profile dirs | User called it "đồ ngu" — STATUS_BREAKPOINT is `Preferences` corruption, not cache | Full delete + re-warm (memory `feedback_profile_full_reset.md`) |
| A10 | Blanket session reports without synthesis | 30+ reports, no retrospective → user had to ask | This document |

---

## 4. Metrics

| Metric | Value |
|---|---|
| B-numbered bugs addressed | 38 (B25 skipped, B36 absorbed by B38) |
| Fixed + committed | 35 |
| In-flight (gated on L2) | B38 + `flow/wait.py` / `login.py` diagnostics |
| Tier 2 runs | 19 (most recent: Run 19 blocked) |
| Live-verified chain patterns | 5-op with extend middle; 3-op t2v→camera→insert; 3×parallel t2v |
| Tests passing | 123 (125 after PR#18 merge) |
| Memory files (cross-session rules) | 11 |
| Session reports | 30+ under `docs/session-reports/` |
| Locale-blockers resolved | 1 (B18 homepage) + EN-account policy (memory) |

---

## 5. Known risks carried into Phase B

1. **L2 chain live-unverified on a freshly-reset profile.** Run 19 blocked; not yet proved end-to-end after `scripts/warm_profile.py` auto-login lands.
2. **1080p upscale only validated at L1.** UI-driven flow assumed to work on L2+ edit views but not yet tested.
3. **`profiles_ultra.txt` is the single source of truth for credentials.** Not encrypted. Access control is filesystem-level. Phase B should replace with a proper secret store.
4. **FlowClient clone-to-temp cookie survival is fragile** under some Chromium builds. Fallback env `FLOW_USE_BASE_PROFILE=1` proposed but not wired.
5. **reCAPTCHA detection is reactive** (`flow/recaptcha.py`). If Flow tightens its bot detection, jobs will start failing with no recovery.
6. **Tag `v0.6.0-chain-complete` is stale** (at `fc31a54`, pre-B31). Tag bump blocked on B38 live validation.
7. **No cost / LP-budget controls.** A runaway chain could burn all credits before anyone notices. Phase B P1.
8. **Frontend is vanilla JS** — PR#18 adds structure (shared constants, WS incremental) but there's no type system, no build step, and no test framework. OK for MVP, will hurt when frontend grows.

---

## 6. Phase B candidates (high-value, low-risk first)

1. **L2 chain end-to-end verification** — finish Run 19 follow-up, commit B38, tag v0.7.0
2. **LP budget enforcement** — server rejects job if estimated cost > account's remaining LP (needs `flow/client.py::_account_info` capture which already exists)
3. **Metrics dashboard** — already-captured `client._calls` + `_video_urls` + `_media_id_events` are rich telemetry; Grafana + SQLite could ship in a day
4. **Credential vault** — replace `profiles_ultra.txt` with a signed / encrypted store
5. **Scheduled jobs** — cron-style repeat (generate N clips/day) without user intervention
6. **Parallel L2 siblings** (B29 fix) — currently blocked by SPA `/edit/` strip after sibling extend; needs a tile-catalog approach

---

## 7. If you're a fresh supervisor session reading this

Don't try to re-derive Phase A. The synthesis is here. Read:
- `docs/CLAUDE.md` (1 min)
- `docs/SUPERVISOR_HANDOFF.md` §2-§7 (5 min)
- This file §2 + §3 (2 min)
- Memory index at `~/.claude/.../memory/MEMORY.md` (1 min)

That's the 10-minute catch-up. Everything else (individual session reports,
full SPEC §D.4 ledger, E2E run log) is reference material — dive in when a
specific question surfaces, not upfront.
