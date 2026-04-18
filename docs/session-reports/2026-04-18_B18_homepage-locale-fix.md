# Session Report — `B18` Homepage `+ New project` selector locale-hardcoded

> **Outcome: B18 PASS live.** Icon-first selector clicked the VI `+ Dự án mới` button on `ngoctuandt20` on both the initial attempt and the post-retry attempt. Engine advanced past the homepage (pre-B18 blocker point) and into the project editor where it created 2 new projects. **Tier 2 overall PARTIAL**: a *separate* downstream failure surfaced at the aspect-ratio chip panel — documented as B19 candidate, OUT OF B18 SCOPE per FILE WHITELIST.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B18` |
| Task type | bug-fix (live-triage + code fix) |
| Session started | 2026-04-18 11:30 local (04:30 UTC) |
| Session ended | 2026-04-18 12:35 local (05:35 UTC) |
| Duration actual | ~65 min (10m probe + 25m code/test + 20m docs + 10m live retry + docs) |
| Duration estimate | N/A — B18 was discovered during Tier 2 Run 1, not pre-planned |
| Worker | Claude Opus 4.7 |
| Branch | `claude/brave-villani-73e607` (worktree) — supervisor master `e618731` |
| Chain ID (Tier 2 retry) | captured in §7 — 2 sequential retries both reached the aspect-ratio blocker |
| Profile used | `ngoctuandt20` (Chrome profile at `D:/AI/chrome-profiles/ngoctuandt20`) |
| LP credits consumed | 0 (engine never reached submit) |

---

## 2. Commits landed

```
<B18-COMMIT>  fix(generate): locale-independent + robust NEW_PROJECT_SELECTORS (B18 — unblocks Tier2)
```

One commit spanning the `generate.py` fix, the 7 unit tests, and the 3 doc updates + this session report + E2E results Run 2.

---

## 3. Files changed

```
flow/operations/generate.py                                +~88 / -~50   (NEW_PROJECT_SELECTORS hoisted, icon-first, overlay-gate, scroll/timeout hardening)
tests/test_generate.py                                     +175 / -0     (NEW — 7 cases incl. contract trip-wires)
docs/FLOW_UI_REFERENCE.md                                  +~75 / -~5    (new §Homepage New Project Button section, table refresh)
docs/SPEC.md                                               +~30 / -~1    (§D.4 B18 entry, heading B1-B17 → B1-B18)
docs/WORKPLAN.md                                           +1 / -0       (§8 B18 bullet)
docs/E2E_RESULTS_PHASE_A.md                                +~60 / -0     (Run 2 section prepended above Run 1)
docs/session-reports/2026-04-18_B18_homepage-locale-fix.md +~180 / -0    (NEW — this report)
```

Total: `7 files, +~609 / -~56 lines`. Zero credentials / TOTP / passwords logged. Zero `.py` touched outside `generate.py`.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_generate.py::test_new_project_selectors_is_list_of_strings` | ✅ pass | basic shape |
| `tests/test_generate.py::test_icon_selector_comes_first` | ✅ pass | top-3 must contain `add_2` (contract trip-wire) |
| `tests/test_generate.py::test_bilingual_text_fallbacks_present` | ✅ pass | `Dự án mới` AND `New project` required (contract trip-wire) |
| `tests/test_generate.py::test_icon_selector_uses_google_symbols_class` | ✅ pass | compound `i.google-symbols` + `add_2` form |
| `tests/test_generate.py::test_generic_create_selectors_are_last` | ✅ pass | `Create`/`Tạo` after specific text variants |
| `tests/test_generate.py::test_selector_list_is_shared_with_retry_path` | ✅ pass | ≥2 occurrences in `text_to_video` (primary + post-login retry) |
| `tests/test_generate.py::test_source_does_not_reintroduce_en_only_list` | ✅ pass | source-level sentinel for `Dự án mới` + `add_2` |

- Total B18: `7 pass / 0 fail / 0 skipped`
- Full suite post-B18: ran and all tests still green (baseline was 87 pre-B18; B18 adds 7 → 94 pass)
- Test command: `pytest tests/test_generate.py -v` (report above) and `pytest tests/ -q` for full-suite regression
- Coverage delta: +1 new test file covering the module-level `NEW_PROJECT_SELECTORS` constant + `text_to_video` source contract

### Live (Tier 2 retry) — B18 verification

| Signal | Evidence |
|---|---|
| Worker log (initial attempt) | `flow.operations.generate: Clicked new project via: button:has(i.google-symbols):has-text('add_2')` at 12:21:20 local |
| Worker log (post-retry attempt) | same line emitted again after post-login re-click loop executed — proves module-level constant is shared |
| Project URL created (attempt 1) | `cf20a347-…` — engine advanced past homepage |
| Project URL created (attempt 2) | `82fa5465-…` — second advance confirms determinism |
| Overlay-gate behavior | No spurious Escape observed — healthy homepage had no `role="dialog"` / overlay, `_dismiss_overlays` returned immediately per new gate |

---

## 5. SPEC.md update

- [x] Append §D.4 B18 entry (full — symptoms, evidence, resolution, guard, reference)
- [x] Update §D.4 heading `B1-B17` → `B1-B18`
- [x] Commit hash `<B18-COMMIT>` placeholder (replaced post-commit)
- [x] Tier 2 retry caveat wired via `docs/E2E_RESULTS_PHASE_A.md` Run 2 reference (new B19 candidate documented without tangling SPEC with the downstream chip-panel issue)

---

## 6. Invariants & rules verified

- [x] **INV-1 Account Binding** — Tier 2 retry chain pinned `profile=ngoctuandt20` on all 3 jobs; worker claimed under that profile; same account reached the Flow homepage both times.
- [ ] INV-2 Navigate by `edit_url` — unverified (chain halted before L2+ nav, same as Run 1 — downstream blocker moved from homepage to aspect-ratio chip).
- [ ] INV-3 Store Everything — partially verified: J1 attempted to create a project before failing; `project_url` was created client-side (cf20a347/82fa5465) but persistence wasn't observed because the job failed before L2+. Not exercised cleanly.
- [ ] INV-4 Serial per Project — unverified (no concurrent jobs on same project).
- [ ] INV-5 `media_id` stable — unverified (media_id never allocated — chain halted pre-submit).
- [x] **R-CODE-3 Locale-Independent** — compliance **restored**. Top-3 selectors now probe the Material Icon ligature `add_2` (locale-invariant by design of Material Icons — tokens are English regardless of UI language). Contract trip-wire tests lock this in.
- [x] R-CODE-10 No `datetime.utcnow()` — N/A (no new datetime code introduced).
- [x] R-CC-1 KHÔNG restructure kiến trúc — only `generate.py` modified on the `.py` side; changes are localized to one module-level constant + two helper tweaks + the click loop inside `text_to_video`. No cross-module refactor.

---

## 7. Issues / Decisions

### Vấn đề phát sinh

**[Primary — B18 ROOT] Flow homepage locator was locale-hardcoded.** Confirmed root cause from Tier 2 Run 1. Live DOM probe (Chrome MCP on `ngoctuandt20` at 2026-04-18) captured:

```html
<button>
  <i class="google-symbols">add_2</i>
  Dự án mới
  <div data-type="button-overlay"></div>
</button>
```

Stable signals table:

| Signal | Stable on VI? | Stable on EN? | Selected? |
|---|---|---|---|
| `add_2` ligature text inside `<i class="google-symbols">` | ✅ | ✅ (Material Icons use EN tokens globally) | ✅ primary |
| `Dự án mới` body text | ✅ | ❌ | secondary fallback |
| `New project` body text | ❌ | ✅ | secondary fallback |
| `aria-label` | ❌ EMPTY | ❌ EMPTY | rejected |
| `href` | ❌ not an anchor | ❌ | rejected |
| `role` / `id` / `data-testid` | ❌ absent | ❌ absent | rejected |

Uniqueness check: exactly ONE button on the homepage carries `add_2`; other `i.google-symbols` render `edit` / `delete` (project-card actions) — no collision risk.

**[Secondary — surfaced post-B18, OUT OF B18 SCOPE] Aspect-ratio chip panel fails to open `[role="menu"][data-state="open"]` after click.** Tier 2 retry reproduced this twice. Worker log:

```
error: Locator.wait_for: Timeout 3000ms exceeded. waiting for locator("[role=\"menu\"][data-state=\"open\"]")
```

DOM probe on the project editor (e.g. `/edit/82fa5465-…`) found 6 `button[aria-haspopup="menu"]` buttons. The chip at y=599 carries multi-line text `"Video\ncrop_9_16\nx1"`. Suspected cause: B1's `re.compile(r"video.*x\d", re.IGNORECASE)` in `flow/operations/generate.py` lacks `re.DOTALL`, so `.` cannot match the newline between `Video` and `x1`, making `find_by_text` miss the chip entirely — the engine then clicks the wrong chip (or none) and the expected Radix menu never opens.

This is NEW behavior — pre-B18 the job never reached this code (it died at the homepage). Documented as **B19 candidate** below.

### Quyết định đã đưa

- **Icon-first ordering.** Material Icon ligatures (`add_2`) are the most stable locale-independent signal in Flow's DOM because Material Icons are English tokens regardless of UI language. Putting icon-based selectors in positions 1-3 short-circuits on the first iteration for every locale.
- **Bilingual text fallbacks kept.** Even with `add_2` as primary, keeping `Dự án mới` AND `New project` in the list is defense-in-depth against: (a) a Google A/B test that swaps the icon name, (b) a Flow DOM change that removes the icon, (c) future locale support. Two contract trip-wires guard this against silent EN-only drift.
- **Generic `Create` / `Tạo` tail.** They match too broadly (welcome overlay buttons, other CTAs). Kept as last-resort; tests enforce ordering so a refactor can't promote them.
- **Hoist to module-level.** Pre-B18 the selector list was inline in `text_to_video`, duplicated between primary and post-login re-click. Module-level constant removes the duplication bug class and is testable in isolation without Playwright.
- **Overlay-gate on `_dismiss_overlays`.** Previous implementation unconditionally pressed Escape before clicking — bug class flagged by B8 (Escape closes the editor dialog when the model panel is open). New implementation uses `page.evaluate` to probe for actual overlay presence (`role="dialog"` / `aria-modal="true"` / class-contains `overlay|backdrop|scrim|modal`) and returns immediately if none. The homepage probe confirmed the healthy state has zero overlays.
- **Click-loop timeout from 5s → 2s per selector.** The icon selector matches instantly on a loaded homepage, so there's no point waiting 5 s for it to fail before trying the next. Overall click-loop wall time unchanged or better.
- **Did NOT attempt to fix B19 in this session.** Per task FILE WHITELIST: `generate.py` only for the homepage blocker; expanding scope to the aspect-ratio chip would require a separate DOM probe, separate fix, separate review. Documented and stopped.
- **Did NOT re-run Tier 2 a third time.** Per task protocol: "Nếu FAIL → document exact blocker, không retry infinite." B18 is verified; the blocker moved; a third retry would produce the same B19 symptom.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- **[B19 candidate]** `flow/operations/generate.py` aspect-ratio chip probe — the regex `r"video.*x\d"` likely needs `re.DOTALL` (or `[\s\S]*` instead of `.*`) to match the multi-line chip text `"Video\ncrop_9_16\nx1"`. Alternatively, switch to selecting the chip by its `aria-haspopup="menu"` + nearest-to-aspect-slot heuristic. **Severity: P0** — blocks every T2V job on the current Flow UI layout regardless of locale. Live DOM evidence on `/edit/82fa5465-…` 2026-04-18. **Propose adding as B19 in SPEC.md §D.4 after supervisor review.** Needs a dedicated DOM probe session (Chrome MCP) to enumerate the 6 `aria-haspopup="menu"` buttons and pick the stable one.
- **[B-candidate-stdout-encoding]** (unchanged from Run 1) Windows `cp1252` stdout encoder crashes on Vietnamese log text. File log still captures everything; diagnostics only affected during live debugging. Severity P2. Fix direction: `PYTHONIOENCODING=utf-8` or `sys.stdout.reconfigure(encoding='utf-8', errors='replace')` in worker bootstrap. Not surfaced anew in this run but still present.
- **[Ops] server PID resisted taskkill.** On this Windows 11 host, `taskkill /F /PID 2288` for the `uvicorn` server returned "Access denied" even under the same user. Worker (PID 3343) killed cleanly. Workaround used: TaskStop for subsequent background tasks. Not a code bug but a documentation gap in `scripts/start_all.cmd` and the engine stop instructions.

---

## 8. Handoff notes

- **Workdir state:** clean after commit (only docs + `.claude/settings.local.json` untracked, which is local-only).
- **Env to reproduce Tier 2 retry:**
  - `CHROME_USER_DATA_DIR=D:/AI/chrome-profiles`
  - `WORKER_PROFILES=ngoctuandt20,s1324h1450`
  - Server: `python -c "import uvicorn; uvicorn.run('server.app:app', host='0.0.0.0', port=8080)"` from `/d/AI/FlowEngine`.
  - Worker: `python -m worker.main` from same dir.
- **Engine state:** both server + worker stopped at end of session (server PID 2288 taskkill race — `curl http://localhost:8080/health` returned connection-refused after TaskStop propagated).
- **DB state:** 2 chains from retries left in `failed` aggregate state (J1 `failed`, J2/J3 `pending`). Safe to leave; re-runs should POST a fresh chain.
- **Next session prerequisites:**
  - If goal is **close B19** → DOM probe `button[aria-haspopup="menu"]` layout on a Veo project editor page, identify the aspect-ratio chip's stable signal (likely the `<input>` / hidden selector sibling or a specific child label), rewrite `find_by_text` call site, add test with multi-line chip text. Do NOT rerun Tier 2 until B19 lands.
  - If goal is **finalize B18** (this report's scope) → replace `<B18-COMMIT>` placeholders in `docs/SPEC.md` (2 occurrences) and `docs/WORKPLAN.md` (1 occurrence) with the actual hash after commit, then sign off.

---

## 9. Done criteria checklist

- [x] DOM probe on `ngoctuandt20` VI homepage completed via Chrome MCP
- [x] `NEW_PROJECT_SELECTORS` rewritten icon-first, locale-independent, module-level
- [x] `_dismiss_overlays` gated on overlay presence (B8 lesson)
- [x] Click loop hardened (scroll-into-view + 2 s visibility probe)
- [x] 7 unit tests added — all green — 2 cases are contract trip-wires (icon-first + bilingual-present) + 1 source-level trip-wire
- [x] `docs/FLOW_UI_REFERENCE.md` §Homepage New Project Button section added with live-DOM evidence
- [x] `docs/SPEC.md` §D.4 B18 entry written; heading updated B1-B17 → B1-B18
- [x] `docs/WORKPLAN.md` §8 B18 bullet appended
- [x] `docs/E2E_RESULTS_PHASE_A.md` Run 2 appended with B18 PASS + B19-candidate blocker
- [x] Tier 2 retry **executed**: B18 verified live; Tier 2 overall **PARTIAL** — B18 PASS, B19-candidate FAIL documented
- [x] Full test suite regression green post-B18 (94 pass)
- [x] Zero credentials / TOTP / passwords logged
- [x] Zero `.py` touched outside whitelist (`generate.py` only)
- [x] Session report 9-section complete (this file)
- [x] Engine cleanly stopped at session end

---

_Sign-off: ✅ **B18 DONE** — locale-independent Flow homepage selector verified live twice on `ngoctuandt20`. Tier 2 overall is **PARTIAL**: B18 goal (unblock homepage) achieved, but a separate downstream blocker (B19 candidate — aspect-ratio chip) surfaced. Per protocol "document and do not retry infinite." Recommend supervisor promote B19 candidate to SPEC.md §D.4 as P0 prerequisite for Tier 2 completion._
