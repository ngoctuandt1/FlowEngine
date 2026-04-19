# FLOW_ENGINEERING_NOTES — Master Reference (Supervisor's Own Notes)

> **Purpose.** Single-pass consolidation of what I (supervisor) have actually
> understood after reading the session reports, trilogy docs, and source
> code. Not a replacement for `SPEC.md` / `FLOW_BUTTON_EXACT.md` / session
> reports — those are authoritative for their scopes. This file is a
> **navigation aid**: when I forget something mid-session, I check here
> first before grep/read.
>
> **Written:** 2026-04-19 (after reading 30+ session reports).
> **Maintainer:** supervisor (Claude Opus 4.7 — 1M context).
> **Scope:** notes and cross-references only. No SPEC/decision-making
> authority. Update this file when my mental model shifts, not when the
> code changes (code already has SPEC for that).

---

## 1. What FlowEngine Is (30-second recap)

Playwright-driven browser automation for `labs.google/fx/tools/flow` (Google Flow / Veo 3.1).

```
frontend (JS SPA) ↔ server (FastAPI + SQLite) ↔ worker (claim loop + Playwright) ↔ Chrome profiles
```

**Worker shape (the key pattern):**
```python
while True:
    job = await remote_api.claim(profiles=available_profiles)
    async with ProjectLock(job.project_url):
        async with FlowClient(profile=job.profile) as client:  # fresh Playwright context
            await client.page.goto(edit_url(job.project_url, job.media_id))  # B27 direct goto
            result = await dispatcher.dispatch(job, client)
            await remote_api.update(job.id, result)
```

Each job = fresh browser context + DB-backed metadata recovery. Verified live: `discrete-2job-verify_en.md`.

---

## 2. The Two URLs

| URL | Shape | Meaning |
|---|---|---|
| `project_url` | `https://labs.google/fx/tools/flow/project/{project_id}` | Project library grid (parent of all ops on this project) |
| `edit_url` | `{project_url}/edit/{media_id}` | Editor view for ONE specific video (media_id) |

**Key distinction.** `project_id` (in URL) and `media_id` (in `/edit/{…}`) are different UUIDs. `project_id` is stable per project; `media_id` identifies a specific clip within the project.

**Locale caveat** (`feedback_english_locale.md`): Flow SPA rewrites `/fx/tools/flow/…` → `/fx/vi/tools/flow/…` on VI-locale Google accounts. Direct `page.goto(edit_url)` on VI profile lands on a Next.js catch-all. **All Flow accounts MUST be configured EN at myaccount.google.com/language before first engine run.** The engine uses canonical `/fx/tools/flow/…` URLs; selectors are locale-independent, URLs are not.

---

## 3. The 5 Invariants (latest state)

| # | Invariant | Status | Enforced where |
|---|---|---|---|
| INV-1 | Account Binding — 1 project = 1 Google account = 1 Chrome profile; chain stays on same profile | ✅ | `server/db/job_store.py::claim_next_job` — profile filter |
| INV-2 | Navigate by `edit_url` only — no DOM card counting, no `video_index` | ✅ | `flow/operations/_base.py::navigate_to_edit` (B27: direct goto primary) |
| INV-3 | Store Everything after op — `project_url` / `media_id` / `edit_url` / `profile` / `output_files` / `completed_at` | ✅ | `_base.py::finalize_operation` + B22 claim-time propagation |
| INV-4 | Serial per Project — 2 jobs same `project_url` never concurrent | ✅ | `worker/project_lock.py` + `claim_next_job` `NOT EXISTS active` clause |
| INV-5 | `media_id` re-extracted per op; **extend + camera mint NEW uuid**, insert/remove preserve (empirical 2026-04-19) | ✅ (revised 3×) | Chain inherits parent's FINAL `media_id` via B22; B30 walks up past extend ancestors |

**INV-5 revision history:**
1. Original: "stable across all L2 ops" (Phase A wording)
2. 2026-04-19 `3d7b884`: camera mints new; extend/insert/remove preserve
3. 2026-04-19 Tests 2/3/4 + B30: extend ALSO mints new (empirical J1 → J2 extend = new uuid); insert/remove still TBD (empirically unverified because B28 blocks L3-on-extend chain)

---

## 4. The ~12 Buttons (selector catalogue)

### Homepage
| Mục | Selector | Notes |
|---|---|---|
| New project | `button:has(i:text-is('add_2'))` | B18. Icon-first, locale-independent. VI label "Dự án mới", EN "New project", icon `add_2` same across locales. |

### T2V composer (project view, `/project/{id}`)
| Mục | Selector | Notes |
|---|---|---|
| Aspect chip (open panel) | `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down')):has(i:text-is('crop_16_9 \| crop_9_16 \| crop_1_1'))` | B19. Icon-ligature, NOT model-name regex (model name varies: "Video", "🍌 Nano Banana Pro", etc). |
| Aspect tab trigger | `[id$='-trigger-PORTRAIT \| LANDSCAPE \| VIDEO']` | B1. Radix per-render ID hash — use attribute-ends-with. |
| Aspect chip pre-open guard | check `data-state="open"` before clicking | B19 second fix. If chip already open (from model selector flow leak), skipping click avoids toggle-close. |
| Prompt editor (Slate) | `[data-slate-editor='true'][contenteditable='true']` | All modes. |
| Extend prompt editor (scroll-state) | `[data-scroll-state='START'] [data-slate-editor='true']` | B15 Method 1. More precise than "last slate editor" heuristic. |
| Model chip (open dropdown) | `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))` then `.filter(has_text=re.compile(r"^Veo", re.IGNORECASE))` | B20. Exact-text via icon + anchored regex. |
| Model LP item | Menu item with text matching `re.compile(r"Lower Priority", re.IGNORECASE)` | B17. Pre-check: if already visible, skip `_open_model_dropdown` (avoids toggle-close). |
| Close model panel | `page.locator("[data-slate-editor='true']").click()` (click outside), fallback Escape | B8. NEVER Escape as primary — closes entire composer. |

### Submit
| Mục | Selector | Notes |
|---|---|---|
| Submit (arrow_forward) | `button:has(i:text-is('arrow_forward'))` | B26. Canonical single-selector. On `/edit/` there are 2 matches: decorative disabled (nth 0) + real (nth 1). B16 iterate-and-skip-disabled handles this. |
| Submit with scope | `click_submit(page, scope="[data-scroll-state='START']")` | B26 scope param. Narrows to composer panel when ambiguity. |

### Edit mode (/edit/{media_id}) — 4 action buttons
| Mode | VI title | EN title | Icon | Default |
|---|---|---|---|---|
| Extend | `Mở rộng` | `Extend` | `keyboard_double_arrow_right` | **DEFAULT ACTIVE when entering /edit/** (discrete-2job §4.2 step 14) |
| Insert | `Chèn` | `Insert` | `add_box` | — |
| Remove | `Xoá` / `Xóa` | `Remove` / `Delete` | `ink_eraser` | — |
| Camera | `Camera` | `Camera` | `videocam` | — |

**2-pass selector** (`_base.py::click_action_button`):
1. `button[title='{localized title}']` (primary)
2. `button:has(i:text-is('{icon}'))` (locale-independent fallback)

**B31 (2026-04-19, this session):** extend Step 3 now probes `_verify_extend_panel` FIRST — if panel already open (default case), skip click. Avoids the "click active mode = toggle-close" trap.

### Camera presets (after Camera click)
| Mục | Selector | Notes |
|---|---|---|
| Preset button | `page.get_by_text(direction, exact=True)` | B12. Only strategy that works — no aria-label, no explicit role=button. |
| Verify selected | `getComputedStyle(labelDiv).color` — selected `rgb(48,48,48)` sum 144; unselected `rgb(255,255,255)` sum 765; threshold R+G+B < 400 | B12. Class names are styled-components hashes (unstable); color is the only semantic signal. |

### Bbox (Insert/Remove) — canvas drag
| Mục | Selector | Notes |
|---|---|---|
| Canvas target | largest visible `<canvas>` with `width ≥ 300 && height ≥ 200` | B11. `<video>` tag is a 105×60 thumbnail — wrong element. Preview is a 598×336 canvas. |
| Bbox verify | **none — pointer-trust** | B11 Option B. Flow paints bbox onto canvas 2D bitmap (no DOM overlay to detect). Pixel-sampling rejected due to video-frame noise + CORS/WebGL risk. |
| Input validation | 0-1 range + overflow clamp (`x+w>1 → w=1-x`) | B2 preserved. Also Pydantic `Field(ge=0, le=1)` at API boundary → 422 before job enters. |

---

## 5. The 5 Operations — What Each Needs

### L1 — text-to-video
1. Homepage → `+ New project` (B18 icon selector)
2. Aspect chip (B19) → pick ratio (B1 Radix tab) → close panel (click outside at `(10, 10)`, NOT Escape)
3. Focus Slate editor → type prompt
4. Open model chip → pick LP (B17 pre-check if items already visible)
5. Submit (B26 canonical) → URL pushes to `/edit/{new_media_id}`
6. Extract `media_id` from URL or network response (`flow/media_id.py`)

### L2 — extend-video
**Entry state:** `/edit/{parent.media_id}` — Extend mode DEFAULT ACTIVE.
1. `navigate_to_edit(edit_url)` (B27 direct goto)
2. Wait video loaded
3. **Probe panel state (B31)** — `_verify_extend_panel` returns True? skip click; False? click Extend.
4. Type extend prompt (B15 scroll-state selector)
5. Select LP model (B17)
6. Submit (B26) → **mints NEW `media_id`** (INV-5 revised)
7. Download + return new metadata

### L2 — camera-move
1. Navigate to `/edit/{parent.media_id}`
2. Click Camera mode button (icon `videocam`)
3. Grid of presets renders → click preset by exact text (B12)
4. Verify via `getComputedStyle(labelDiv).color` (B12)
5. Submit → **mints NEW `media_id`** (INV-5 revised, camera)
6. Download

### L2 — insert-object
1. Navigate to `/edit/{parent.media_id}`
2. Click Insert mode (icon `add_box`)
3. Type prompt (describes what to insert)
4. `draw_bbox_on_video` (B11 canvas target + pointer-trust)
5. Submit → preserves `media_id` (INV-5 revised — TBD empirical, not yet verified post-B28)
6. Download

### L2 — remove-object
Same as insert, different icon (`ink_eraser`), different placeholder.

---

## 6. Chain Routing (B22 + B30)

### B22 — L2+ claim inheritance
When worker claims an L2+ job, `claim_next_job` fetches parent row and propagates into child:
- `profile` (from direct parent)
- `project_url` (from direct parent)
- `media_id` (see B30 walk-up below)
- `edit_url` (see B30 walk-up below)

### B30 — extend-ancestor walk-up
For `media_id` + `edit_url`, walks up past `extend-video` ancestors until a non-extend ancestor (or root) is reached. Max 16 iterations.

**Why:** extend-output `/edit/{new_media}` has Insert/Remove/Camera **disabled** (B28 "extend-child lockout"). Chain needs to navigate to a clip where those modes are enabled.

**Example chain:**
```
L1 t2v → L2 extend → L3 insert
         ↑ B22 inherits from L1 t2v (extend's new uuid)
                     ↑ B30 walks up past L2 extend → inherits L1 t2v's media_id
```

**But:** B30 alone doesn't solve chain-with-extend. Navigating `/edit/{L1.media}` post-extend hits B29 (SPA strips `/edit/` segment → lands on project library grid). Unsolved in engine; the FLOW_BUTTON_EXACT §5.1 workaround (navigate to project grid, click L1 timeline thumbnail to re-enter edit mode with sidebar re-enabled) is not implemented.

---

## 7. Known Unsolved Gaps

### Chain pattern matrix

| Pattern | Status | Note |
|---|---|---|
| t2v → camera | ✅ works (Run 10) | — |
| t2v → insert | ✅ works (Run 10) | — |
| t2v → remove | ⚠️ code ready, live-untested | Same fix as insert; likely works |
| t2v → extend | ✅ works (Run 10 J2, discrete-2job) | — |
| t2v → extend → extend → … | ✅ likely works | Extend button stays enabled on extend-output |
| **t2v → extend → insert / remove / camera** | ❌ **BLOCKED** | B28 extend-child lockout (sidebar disabled) + B29 L1 stale URL. B30 walk-up + guards ≠ solved; workaround not implemented. |
| **Parallel L2 siblings on L1** | ❌ **BLOCKED** | B29 — L1 /edit/ stale after sibling extend. Serial chains work; parallel L2 forks don't. |

### Defensive guards (raise loud instead of silent-fail)
- **B28 guard** (`click_action_button`): visible + disabled → raise "extend-child lockout (FLOW_BUTTON_EXACT §5.1). Check B22 inheritance."
- **B29 guard** (`navigate_to_edit`): post-goto `"/edit/" not in page.url` → raise "SPA stripped /edit/ — stale media_id post-sibling-extend."

**Warning:** B28 guard was initially implemented as immediate raise (fc31a54 session). That caused Run 11 J2 extend to fail false-positive on healthy t2v-output (Extend button briefly disabled during Flow progressive render). B31 inverted the logic — `extend_video` now probes panel FIRST and only clicks Extend if not default-active. Pattern to match existing wait-for-ready logic (B15 `_verify_extend_panel`, B19 Radix `data-state` wait).

---

## 8. Bug Ledger (B1 … B31, quick lookup)

| # | File/Scope | Commit | One-liner |
|---|---|---|---|
| B1 | aspect ratio | `b359c84` | Radix chip `[id$='-trigger-PORTRAIT \| LANDSCAPE']` |
| B2 → B11 | bbox | `a165105` → `ce6683a` | Canvas target ≥300px, pointer-trust verify |
| B3 → B12 | camera verify | `58937d4` → `78d3e40` | `getComputedStyle(labelDiv).color` R+G+B<400 |
| B4 | chains table | `4dcf50f` | INSERT chain row on POST, GET derives status from jobs |
| B5 | completed_at | `4d24c10` | Auto-stamp on terminal status |
| B6 | profile.current_job_id | `0118e6d` | Set on claim, clear on complete |
| B7 | port | `a95c9b5` | Server default 8000 → 8080 |
| B8 | datetime.utcnow | `573cffd` | Migrate 7 callsites → `datetime.now(UTC)` |
| B9 | test foundation | `adca116` | pytest + fixtures + temp DB |
| B10 | pydantic default_factory utcnow | `fe13870` | `default_factory=lambda: datetime.now(UTC)` |
| B13 | docs cleanup | inline | Resolved inline with Tier1 retest |
| B14 | nav verify + tile click media_id-aware | `72e056b` | Stash cherry-pick KEEP-2 + KEEP-3 |
| B15 | extend panel verify + submit diag + Slate selector | `caef3e9` | Stash KEEP-4 + KEEP-5 + KEEP-6 |
| B16 | click_submit iterate + skip disabled | `004d8fb` | Stash KEEP-7 |
| B17 | LP pre-check | `f5dab42` | Stash KEEP-1 |
| B18 | homepage locale | `8dc357c` | Icon-first `add_2` + bilingual text fallbacks |
| B19 | aspect chip | `e1597b2` | Icon-ligature `crop_9_16`/`crop_16_9` + Radix open guard |
| B20 | model_selector fuzzy Veo | `0aa01b8` | 3 sites → icon-anchor + `^Veo` regex |
| B21 | stray print | — | Self-resolved (not present at master 83f621f) |
| B22 | L2+ claim inheritance | `0637c92` | SELECT parent + propagate project_url/media_id/edit_url |
| B23 | _click_video_tile rewrite | `78f7994` | Match media_id via `<video \| img> src` |
| B24 | locale re-detect | `f3313de` | Re-detect locale from page.url at write-time |
| B25 | (skipped) | — | — |
| B26 | submit + model chip exact-text | `d4fca1a` | 3 files → `:text-is()` + MODE_TITLES blacklist |
| B27 | direct goto primary | `9519c06` | `navigate_to_edit` → `goto(edit_url)` primary + tile fallback |
| B28 | disabled-button diagnostic guard | `fc31a54` | `is_enabled()` check → raise "extend-child lockout" |
| B29 | URL-strip guard | `fc31a54` | Post-goto `"/edit/" in page.url` check |
| B30 | extend-ancestor walk-up | `fc31a54` | Skip extend ancestors in claim inheritance |
| B31 | extend panel probe-first | `6aace7f` | Probe `_verify_extend_panel` before click (Extend default active) |

**Current tag:** `v0.6.0-chain-complete` @ `fc31a54` (pre-B31). B31 lives on master but tag not bumped yet.

**Total: 29 bugs fixed** (B25 skipped in numbering).

---

## 9. Testing Layout

| File | Scope |
|---|---|
| `tests/conftest.py` | temp DB + api_client fixtures (B9) |
| `tests/test_smoke.py` | fixture smoke tests |
| `tests/test_config.py` | port (B7) |
| `tests/test_datetime_migration.py` | no utcnow in source + tz-aware timestamps (B8) |
| `tests/test_job_store.py` | claim/update/completed_at (B5) |
| `tests/test_profile_store.py` | profile tracking (B6) |
| `tests/test_claim_algorithm.py` | B22 inheritance + B30 walk-up + priority |
| `tests/test_chains.py` | B4 chain CRUD |
| `tests/test_aspect_ratio.py` | B1 + B19 |
| `tests/test_bbox.py` | B11 canvas-target + trip-wires |
| `tests/test_camera.py` | B12 color-verify + trip-wires |
| `tests/test_base.py` | B14 nav verify + B28/B29 guards + B27 direct goto |
| `tests/test_extend.py` | B15 panel verify + B31 probe-first |
| `tests/test_submit.py` | B16 iterate + B26 canonical |
| `tests/test_model_selector.py` | B17 LP pre-check + B20 no-fuzzy-Veo |
| `tests/test_generate.py` | B18 homepage selectors |
| `tests/test_e2e_invariants.py` | INV-1/4/stale-recovery infra tests (no Flow) |

**Count at master `fc31a54`: 107 tests pass.** CI: `.github/workflows/tests.yml` — pytest on PR + push to master.

**Source-level trip-wires** (prevent silent regression):
- `test_bbox_evaluate_script_targets_canvas` — bbox JS uses `canvas` + `300`, not `querySelector('video')`
- `test_bbox_returns_true_after_drag_no_post_verify` — no post-drag verify (B11 Option B)
- `test_verify_script_uses_computed_color_signal` — camera verify uses `getComputedStyle` + `color` + `rgb`
- `test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern` — SUBMIT_SELECTORS len=1, canonical selector, no fuzzy
- `test_chip_selector_uses_icon_not_model_text` — aspect chip uses icon ligature, not `video.*x\d`
- `test_no_fuzzy_veo_selector` — no `:has-text('Veo')` or `filter(has_text="Veo")`
- `test_icon_selector_comes_first` — B18 top-3 uses `add_2`
- `test_navigate_uses_edit_url_as_primary_goto` — B27 first goto carries `/edit/`

---

## 10. Validation Tier Ladder

| Tier | What | Cost | Purpose |
|---|---|---|---|
| 0 | Unit tests (pytest) | 0 | Source contracts + mock Playwright |
| 1 | DOM probe (Chrome MCP, read-only) | 0 | Verify selectors match live DOM |
| 1.5 | DB-layer live (claim snapshot against real DB) | 0 | B22-style — verify DB propagation against real rows |
| 2 | Full chain submit (worker + Playwright) | LP credits (~1 per job) | End-to-end production verification |

**Tier 1 history:**
- Round 1 (2026-04-17 `9facbe3`): B1 ✅, B2/B3 ❌ flipped → created B11/B12
- Round 2 (2026-04-17 `db4c746`): B11/B12 ✅ post-fix

**Tier 2 history:**
- Run 1 (`e618731`): BLOCKED at homepage (VI locale) → B18 opened
- Run 2 (B18 retry): PASS homepage, BLOCKED at aspect chip → B19
- Run 3-8: B19 iterations
- Run 9 (B22 DB-layer, `gallant-jang-cbe036`): DB PASS; full-browser deferred
- Run 10 (VI `9519c06`, post-language-switch): ✅ chain t2v → camera → insert
- Tests 2/3/4 (`eb20092`): chain 5-op blocked at J3 extend output → B28 + B29 discovered
- Run 11 (this session, post-fc31a54): J2 extend fail → B31 (extend default active) discovered and fixed

---

## 11. Session Chronology (selected)

| Date | Commits | Milestone |
|---|---|---|
| 2026-04-16 | legacy #2-#8 (flow-bugs epic) | Pre-rebuild: store media_id, project_url, profile pinning, etc. |
| 2026-04-17 | B7, B9, B8, B5, B6, B1, B2, B3 | Phase A core bugs |
| 2026-04-17 | Tier 1 R1 + R2 | B1 ✅; B2/B3 flipped; B11/B12 fixed |
| 2026-04-17 | Stash triage | 10 KEEP + 1 OBSOLETE + 3 CONFLICT hunks |
| 2026-04-17 | B14, B15 | Stash cherry-pick |
| 2026-04-18 | B16, B17, B10, B4 | Stash cherry-pick + residuals |
| 2026-04-18 | Tier 2 Run 1-8, B18, B19, B22 | Tier 2 iterations |
| 2026-04-19 | B26, B27, discrete-2job, B28/B29 probe | Mid-session discoveries |
| 2026-04-19 | B20/B21 cleanup, CI setup, Tests 5/6/7 infra | Housekeeping |
| 2026-04-19 | Run 10 VI post-language-switch, INV-5 revision | Cross-locale verification |
| 2026-04-19 | Tests 2/3/4 (chain 5-op) → B28/B29 probe | 5-op chain surfaces extend-child lockout |
| 2026-04-19 | B30 + B28/B29 guards + B31 | Inheritance walk-up + defensive guards + extend probe-first |

---

## 12. What I Got Wrong This Session (honest log)

For my future-self:

1. **Session B28 guard prompt (fc31a54):** I wrote "raise on disabled" instead of the probe report's recommended `logger.warning + fall through`. The executor session applied raise. Result: Run 11 J2 extend fail — healthy t2v-output Extend button briefly disabled during progressive render triggered the guard. **Lesson:** follow the probe report's fix direction verbatim unless I have a documented reason to deviate.

2. **Run 11 post-mortem:** I immediately speculated B28 guard false-positive. User pointed out the actual fail was upstream — `click_action_button` never matched because Extend is default-active on /edit/ and the old code assumed the button must be clicked to open the panel. **Lesson:** trace the exact RuntimeError message to its `raise` site before theorizing.

3. **Docs-reading discipline:** User stated 3 times "phần này đã debug rất kĩ rồi" before I bothered to grep for "default active" in session reports. The fact was in `discrete-2job-verify_en.md:119` the whole time. **Lesson:** when user references prior debug, grep first, theorize second.

4. **Spawned too many sessions:** For a 5-line extend.py fix, I kept reaching for session spawning. User overrode explicitly ("mày bị đần à, mở mcp để làm gì?"). **Lesson:** task scope < ~20 lines + selector/logic edit → self-edit with read/edit tools, not spawn. Spawn is for (a) code .py changes that need TDD loop + commit message + report, (b) research sessions with clear scope.

5. **Live probe via Chrome MCP was available from day one.** I kept asking user to "open a tab" when I could have used `mcp__Claude_in_Chrome__*` directly once loaded. User made this explicit after the fact.

**Meta:** Codex (session con) has outperformed me this session on most landed commits — B18/B19/B22/B23/B24/B26/B27/B30 were all executor sessions, I only committed B31 directly + some docs housekeeping. If supervisor authority is questioned again, the honest answer is: I am useful for chronology/merges/tracking/user-facing updates, not for debug/fix work at this codebase's current complexity.

---

## 13. When Something Breaks — Where to Look First

| Symptom | First place to look |
|---|---|
| Job fails immediately, worker logs "Failed to find X button" | `click_action_button` — check if X is default-active on /edit/ (B31 pattern) |
| Submit times out with `gen_id=None, new_api_calls=0, url=/project/…` | URL drifted to /project/ mid-op. Check `_switch_to_video_tab` didn't click a mode button (B26 pattern — MODE_TITLES blacklist should prevent this). |
| L2+ job with `project_url=NULL` | B22 inheritance broken. Check `claim_next_job` SELECT includes all 4 fields. |
| L3 op on extend output fails "Failed to find Insert button" | B28 — sidebar disabled. Chain must walk up past extend via B30. |
| `navigate_to_edit` warns "Video element not found after 15s" | B29 — SPA stripped `/edit/`. `page.url` now on `/project/{id}` library grid. Check B30 walk-up didn't hand us a stale L1 media. |
| Camera job raises "Failed to find camera preset" | B12 — `_verify_preset_selected` using wrong signal. Should be `getComputedStyle(labelDiv).color` R+G+B<400. |
| Insert/Remove bbox lands in wrong place | B11 — canvas target. Verify `document.querySelectorAll('canvas')` filtered `width≥300 && height≥200` picks the preview, not a thumbnail. |
| Aspect ratio set but chip text doesn't change | B19 — chip pre-open guard missed. Check `data-state !== "open"` before click. |
| Unicode/mojibake in worker log on Windows | stdout `cp1252` — set `PYTHONIOENCODING=utf-8` (B-candidate, never filed formally). |
| "Failed to find '+ New project' button" | B18 — homepage locale. Check account is EN at myaccount.google.com/language. Icon selector `add_2` should work regardless. |

---

## 14. Cross-References (the real docs)

| Topic | Authoritative doc |
|---|---|
| Rules / invariants / bug ledger | `docs/SPEC.md` |
| Tactical plan / bug queue | `docs/WORKPLAN.md` |
| Architecture / design | `docs/DESIGN.md` |
| Chronological UI walkthrough | `docs/FLOW_BUTTON_EXACT.md` |
| UI reference tables | `docs/FLOW_UI_REFERENCE.md` |
| Multi-level job history | `docs/FLOW_MULTILEVEL_JOBS.md` |
| Pipeline knowledge | `docs/FLOW_PIPELINE_KNOWLEDGE.md` |
| Phase A E2E results | `docs/E2E_RESULTS_PHASE_A.md` |
| Per-task evidence | `docs/session-reports/YYYY-MM-DD_<task>_*.md` |
| Project/user context | `CLAUDE.md` |
| Supervisor's notes (this file) | `docs/FLOW_ENGINEERING_NOTES.md` |
| Memory (user-level, cross-session) | `~/.claude/projects/D--AI-FlowEngine/memory/` |

---

_End of supervisor notes. Update this when mental model shifts, not code changes._
