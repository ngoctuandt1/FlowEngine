# Session Report — Wave 1 Flow 2026-05 feature update (`fix-live-test-bugs-2026-05`)

## 1. Metadata

| Field | Value |
|---|---|
| Session date | 2026-05-24 |
| Branch | `claude/fix-live-test-bugs-2026-05` |
| PR | #287 (base master) |
| Profile (live) | `ngoctuandt20` (free tier) |
| MCP probe browser | user's personal Chrome `ngoctuandt1` (ULTRA tier) |
| Codex 9router | OFFLINE today — 401 on every dispatch |
| Stop reason | Architectural-stop — 3 same-symptom live-verify failures on Bug B |

## 2. What landed (Wave 1)

8 commits on `claude/fix-live-test-bugs-2026-05` (PR #287):

```
22bed6d  fix(warm): switch warm entry from Gmail to Flow
1cfd356  fix(composer): dismiss pointer-intercepting overlays + omni model support
8a5aa3c  fix(livetest): bug B ingredients — broader picker upload selectors
3c063b2  docs(livetest): probe artifacts + 2026-05-24 plan + Bug B-F session report
d908181  fix(livetest): bug B picker tile-commit + diagnostic capture
<HEAD>   fix(livetest): bug B diagnostic restructure — always log candidates
```

- Bug A (frames-to-video) — already PASS live 2026-05-21, no change this session.
- Bug B (ingredients-to-video) — picker selector + tile-commit logic added; **NOT live PASS** (see §4).
- Bug C/D/E/F (extend / insert / remove / camera) — no op-file changes needed; `flow/operations/_base.py::_assert_l2_available` is wired through `navigate_to_edit` and detects the free-tier `paid_tier_required` banner. **Live-blocked** because L1 must PASS first for the L2 chain to fire.
- Folded-in hardening: warm-entry Gmail→Flow switch; composer-overlay dismissal; Omni-Flash model registry; landing/canvas recovery.

Unit tests: 1415 passed, 14 skipped on the full suite; 179 passed on the targeted ingredient / i2v / chain / composer subset.

## 3. MCP probe findings (2026-05-24)

Full DOM evidence at [docs/livetest-2026-05-24/probe_findings.md](../livetest-2026-05-24/probe_findings.md).
**New Flow surfaces** beyond the 6-bug scope:

| Surface | Status |
|---|---|
| Agent mode + Agent Instructions | New op type, not in engine |
| View Scenes | New left-rail surface |
| Tools marketplace (10+ mini-apps: Mockup / Image Editor / Shot Explorer / Mask Magic / Converge / Grid Architect / Shader Effects / Type Overlays / pixelBento / Poster Designer) | New op family |
| Inline image edit: `crop` / `select` / `draw` | New L2 op family on image media |
| Composer chip schema | Already covered |
| Omni-Flash model | Already wired |
| `add_2` icon ligature | Already wired |

All deferred to Waves 2-4 of [plans/20260524-1430-flow-feature-update/plan.md](../../plans/20260524-1430-flow-feature-update/plan.md).

## 4. Live-verify attempts

3 attempts on `ngoctuandt20`, all failed at the L1 ingredients upload step. Credit tally:

| Attempt | Job id | Symptom | Credit (est.) |
|---|---|---|---:|
| 1 | `adbb908a` | `Ingredient upload action not found after clicking the + button` | ~5 (1 L1 burn before fix) |
| 2 | `38465478` | `Ingredient attach mismatch after retry: expected 1, found 0` | ~5 |
| 3 (diag) | `24ea5632` | Same — `Ingredient attach mismatch`; expected diagnostic log lines did NOT appear because inner try/except muted them | ~5 |

Total ~15 credits. `ngoctuandt20` is still alive (no reCAPTCHA hit).

### Root cause (best current theory)

The 2026-05 picker schema (live-probed via MCP on `ngoctuandt1`): dialog has
sidebar tabs (`All / Images / Videos / Voices / Characters / Avatar /
Uploads`) + a single `upload\nUpload media` action. There is NO bottom
Add/Insert/Done button visible. Commit affordance after upload is presumed
to be a tile-click in the picker grid.

Current `_commit_uploaded_tile_in_picker` clicks SOMETHING that matches
`[role='dialog'] button:has(img):not(:has-text('Upload media'))` — but the
ingredient chip never appears in the composer. Two possibilities:

1. The tile-selector accidentally matches a non-tile element (e.g., the
   account avatar in the picker header / date-dropdown), not the uploaded
   asset. Click does nothing meaningful.
2. The clicked tile is correct but the picker needs a second commit step
   (Enter key? scroll? secondary click?), OR `_count_uploaded_ingredients`
   selectors don't match the 2026-05 chip markup so a present chip is
   silently invisible to the post-upload counter.

Both hypotheses require forensic DOM evidence captured DURING the click.

### Diagnostic capture (HEAD commit)

`_commit_uploaded_tile_in_picker` was restructured so the candidate-tile
enumeration runs FIRST (via `page.evaluate`) and ALWAYS logs the list of
candidate tiles, regardless of whether the click step throws. Next session
will see lines like:

```
Picker tile candidates (N): [{tag: ..., role: ..., imgSrc: ..., rect: ...}, ...]
Clicked newly-uploaded tile to attach ingredient
Post-tile-click state: {dialogOpen: ..., imgCount: ..., composerImgs: ...}
```

`composerImgs` counts img elements sized 40-200 px, a rough proxy for
ingredient chips. If `composerImgs >= 1` while
`_count_uploaded_ingredients` returns 0, the bug is in the chip-counting
selector list, not the tile-click. If `composerImgs == 0` and
`dialogOpen == true`, the click did nothing — need to look at other
commit affordances (Enter / Esc / secondary button).

## 5. Codex 9router status (NEW)

Every dispatch returned `401 Unauthorized: API key required for remote API
access` even with the env-var key. Last successful codex run was
2026-05-21. Memory updated: `project_codex_status.md` flipped to
UNAVAILABLE. Hybrid review degraded to Claude-in-session only this session.

## 6. Done criteria

- [x] PR #287 opened with all Wave 1 commits
- [x] Unit tests pass (1415/1429)
- [x] MCP probe findings recorded
- [x] Multi-wave plan written (`plans/20260524-1430-flow-feature-update/`)
- [x] Claude in-session code review (single-reviewer; codex offline)
- [ ] **L1 ingredients live PASS** — blocked by tile/chip mismatch
- [ ] L2 C/D/E/F live verify (`paid_tier_required` on free tier) — blocked on L1
- [x] Forensic diagnostic capture instrumented for next session

## 7. Handoff notes

- Worker process `13370` exited after the 3rd failure; server `13156` still up on `:8080`.
- Profile pool: `ngoctuandt20` alive. `s17524h173.burned-1779369566` archived.
- Next session: re-run a single L1 ingredients job with the new diagnostic
  instrumentation. The `Picker tile candidates` log line tells you which
  element the current selector hits — choose a tighter selector based on
  `imgW`/`imgH` (real uploads should be >=200 px); choose a chip-count
  selector based on `composerImgs` shape.
- Codex review still required for the final commit; user must verify
  9router key rotation status before relying on `/codex` again.
- Memory `feedback_warm_profile_manual_gmail.md` is superseded by commit
  `22bed6d`; retire it after PR #287 merges.

**Status:** BLOCKED — Wave 1 implementation complete, Bug B live verify needs forensic capture data from the new diagnostic instrumentation (next session).
