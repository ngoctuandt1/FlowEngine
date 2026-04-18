# Session Report — `B17` LP items pre-check before opening model dropdown

Cherry-picks 1 hunk from `stash@{0}` ("flow refinements") onto
`flow/model_selector.py::select_model` per the stash triage
(`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7.1
KEEP-1). Adds an LP-items visibility pre-check so the Step-2.7
dropdown-open call is skipped when the panel already shows LP options
(extend-mode scenario) — avoiding the toggle-close side-effect that would
hide those items. No behavioural change on the happy path where the
dropdown was already closed before entering Step 2.7.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B17` |
| Task type | bug-fix (stash cherry-pick, TDD) |
| Session started | 2026-04-18 |
| Session ended | 2026-04-18 |
| Duration actual | ~45m |
| Duration estimate | ~40m (supervisor prompt) |
| Worker | Claude Opus 4.7 (executor session) |
| Branch | `claude/laughing-euler-850876` (worktree) |
| Master @ | `d11500f` |

---

## 2. Commits landed

```
f5dab42  feat(model_selector): LP pre-check to avoid toggle-close in extend mode (B17 cherry-pick)
```

1 commit: 1 prod file + 1 new test file + SPEC + WORKPLAN + this report.
Optional placeholder backfill commit (replacing `f5dab42` → actual
hash) follows the B14/B15 precedent (commits `a2293bf`/`a4e9092` for B14,
`d11500f` for B15) and will be added after the feat commit is pushed.

---

## 3. Files changed

```
flow/model_selector.py                                    +24 / -9    (KEEP-1 Step 2.7 rewrite)
tests/test_model_selector.py                              NEW         (7 cases)
docs/SPEC.md                                              +26 / -2    (§D.4 B17 entry + TOC/header/intro bump B1-B15 → B1-B17)
docs/WORKPLAN.md                                          +1  / -0    (§8 B17 resolved entry)
docs/session-reports/2026-04-18_B17_lp-precheck.md        NEW         (this report)
```

Tổng: **3 modified + 2 new**. No `stash.patch.tmp` artefact (the stash
peek was a single `git stash show -p stash@{0}` redirected to the
console, not a tempfile).

**Files NOT touched** (blacklist enforced):

- `flow/model_selector.py::_close_model_panel` — signature
  `(page, dropdown_was_opened)` UNCHANGED (stash H3 rejected). Body
  unchanged too: master's click-outside on `[data-slate-editor='true']`
  + single Escape fallback from B8 commit `7245ae8` is preserved
  (stash H4 rejected).
- `flow/model_selector.py` H1 chip_handle capture block — NOT applied
  (dep of H4).
- `flow/operations/_base.py` (B14 scope — already merged `72e056b`;
  `navigate_to_edit` + `_click_video_tile` untouched; `tests/test_base.py`
  still 7/7).
- `flow/operations/extend.py` (B15 scope — already merged `caef3e9`;
  `_verify_extend_panel` + `_type_extend_prompt` untouched; the 12
  test_extend.py cases still 12/12).
- `flow/submit.py` (B16 scope — running in a parallel worktree;
  `click_submit` iterate-enabled-buttons fix is a separate branch and
  the KEEP-7 hunk belongs to B16, not B17).
- `stash@{0}` (read via `git stash show -p stash@{0}` only; `git stash
  list` confirms intact post-session).
- `server/*`, `worker/*`, `docs/DESIGN.md`, `.claude/*`.

---

## 4. Tests

| Test | Pre-apply | Post-apply | Notes |
|---|---|---|---|
| `test_lp_precheck_skips_open_when_items_already_visible` | ❌ fail | ✅ pass | RED→GREEN core: master calls `_open_model_dropdown` unconditionally → `assert_not_called` fails pre-apply |
| `test_lp_precheck_opens_when_items_not_visible` | ✅ pass | ✅ pass | Regression guard: common-case open still called when items absent |
| `test_non_lp_model_skips_precheck_and_opens_directly` | ✅ pass | ✅ pass | Regression guard: non-LP target → direct open (master behavior) |
| `test_precheck_exception_falls_back_to_open` | ✅ pass | ✅ pass | Resilience contract: pre-check locator raising does not block the flow |
| `test_precheck_source_uses_lp_regex_and_skip_message` | ❌ fail | ✅ pass | RED→GREEN source-level trip-wire: looks for "already visible" + "skipping dropdown open" log strings + ≥2 `Lower Priority` regex occurrences |
| `test_close_model_panel_signature_unchanged` | ✅ pass | ✅ pass | H3 REJECTED static contract: `inspect.signature` proves the 2-arg signature |
| `test_close_model_panel_preserves_click_outside_approach` | ✅ pass | ✅ pass | H4 REJECTED static contract: body has `[data-slate-editor='true']` click, no `chip_handle`/`chip_tagged_js`/`data-flow-chip` |

RED baseline (pre-apply): **2 failed / 5 passed**. The two failures are
the two strongest distinguishing contracts (behavioral happy path +
source-level log string). The 5 passing tests are "don't regress" guards
— three of them (opens-when-absent, non-LP-skips, exception-fallback)
happen to be structurally compatible with master's "always open"
behavior, and two are rejected-hunk static contracts that hold both pre
and post because H1/H3/H4 were never applied.

GREEN post-apply: **7 / 7 pass**.

Full suite: `55 passed in 6.36s` (baseline was 48; +7 from
`test_model_selector.py`). No regression in `test_base.py` (B14, 7/7),
`test_bbox.py` (B11, 6/6), `test_camera.py` (B12, 7/7),
`test_extend.py` (B15, 12/12), `test_aspect_ratio.py` (B1, 3/3),
`test_config.py` (B7, 2/2), `test_datetime_migration.py` (B8, 2/2),
`test_job_store.py` (B5, 4/4), `test_profile_store.py` (B6, 3/3), or
`test_smoke.py` (B9 fixtures, 2/2).

DeprecationWarning-strict: `pytest tests/ -W error::DeprecationWarning`
→ **55 passed in 6.17s** (clean).

Command: `pytest tests/test_model_selector.py -v`, then `pytest tests/ -v`.

Pytest mode: `asyncio_mode=auto` (existing), plus an autouse
`_no_sleep` fixture in `test_model_selector.py` that stubs
`asyncio.sleep` so Step-2 (0.5s), Step-2.5 (1.5s inside
`_switch_to_video_tab`, monkeypatched), and the per-attempt 1.5s retry
waits don't inflate runtime. All `test_model_selector.py` cases finish
in under 50ms each.

---

## 5. SPEC.md update

- [x] §D.4 header bumped from B1-B15 → B1-B17 (line 1225; mirrored in
  TOC line 38)
- [x] §D.4 intro paragraph "B14-B15 là stash-triage cherry-picks
  (2026-04-17)" updated to "B14-B17 là stash-triage cherry-picks
  (2026-04-17 / 2026-04-18)" — reflects B17's 2026-04-18 date
- [x] §D.4 B17 entry added (struck-through, `f5dab42` placeholder)
- [x] WORKPLAN §8 B17 resolved entry added (struck-through,
  `f5dab42` placeholder)

Placeholder `f5dab42` will be replaced with the actual hash by a
follow-up backfill commit (same pattern as B11/B12/B14/B15 used pre-hash
— see commits `6612215`, `85e2f45`, `a2293bf`, `a4e9092`, `d11500f`).

No strike-through on an existing bug row — B17 is a NEW discovery from
the stash triage, not a previously-documented gap.

**Note on parallel B16:** if the B16 session (running in a separate
worktree, touching `flow/submit.py` + its own SPEC §D.4 entry) lands on
master first, the B1-B15 → B1-B17 bump here will conflict with B16's
B1-B15 → B1-B16 bump. Supervisor merge-resolution: keep the broader
range B1-B17 and both entries (B16, B17). This session did not
coordinate with B16 per the supervisor prompt's explicit "chạy
independent" instruction.

---

## 6. Invariants & rules verified

- [x] **INV-1 Account Binding** — no profile handling touched.
  `select_model` still receives a `page` whose context is already bound
  to a single profile via `FlowClient`.
- [x] **INV-2 Navigate by `edit_url`** — untouched (B14 territory).
- [x] **INV-3 Store Everything** — no change to `finalize_operation`;
  `project_url` / `media_id` still stored downstream.
- [x] **INV-4 Serial per Project** — `project_lock` untouched.
- [x] **INV-5 `media_id` stable** — not involved (pre-check looks at
  model list items by text, not at `/edit/{media_id}` navigation).
- [x] **R-CODE-3 Locale-Independent** — the pre-check filter uses
  `re.compile(r"Lower Priority", re.IGNORECASE)`, which matches the
  EXACT SAME regex master's retry-loop filter already uses. "Lower
  Priority" is a Flow UI convention that appears on LP items regardless
  of browser locale (not a translated label — see B8 commit `7245ae8`
  which also hardcodes this string for LP detection). No new locale
  coupling introduced.
- [x] **R-CODE-10 No `datetime.utcnow()`** — no datetime usage in the
  touched code.
- [x] **R-CC-1 KHÔNG restructure** — single localized hunk inside
  `select_model`. No public signature change: `select_model(page, model,
  free_mode)` preserved; `_close_model_panel(page, dropdown_was_opened)`
  explicitly preserved (H3 rejected). `MODEL_ITEM_SELECTORS` + `is_lp` +
  `base_name` are simply hoisted up from their original position inside
  the retry loop to before the pre-check — no semantic change for the
  retry loop which still reads the same three locals.

---

## 7. Issues / Decisions

### Judgment calls during execution

**1. Test harness: how to isolate KEEP-1's effect in a mock.**
`select_model` runs ~200 lines of orchestration (chip selector loop,
video-tab switch, pre-check, retry loop, JS fallback, close) and the
KEEP-1 zone is 24 lines in the middle. To make the behavioral tests
track only the pre-check decision, I:

- Monkeypatched `_open_model_dropdown`, `_switch_to_video_tab`,
  `_verify_credits` on `model_selector_mod` (the import-level module
  reference) so `select_model` calls the spy/stubs instead of the real
  helpers.
- Left `_close_model_panel` un-monkeypatched so its click-outside
  behavior runs through naturally against the mock page's
  `[data-slate-editor='true']` locator. This validates H4's rejection
  in the same run (the happy-path test exercises master's
  click-outside code).
- Kept `page.locator(MODEL_ITEM_SELECTORS).filter(...)` routing wired to
  a shared `filtered` mock that both the pre-check call and the
  retry-loop call reuse — so lp_count=N is observed consistently in
  both zones. For the exception test, the first `.count()` raises and
  subsequent calls return 2; the retry loop therefore finds the item
  after the pre-check has fallen back to the open call.

**2. "Lower Priority" string is English-only — is this a locale issue?**
No. B8 commit `7245ae8` already established "Lower Priority" as a
locale-invariant Flow UI convention: LP items ship that literal
English substring even when the rest of the Flow UI is in Vietnamese.
The pre-check reuses the SAME regex as the retry-loop filter — if one
goes stale due to a Flow UI rename, both break together and the fix
applies to one constant. `test_precheck_source_uses_lp_regex_and_skip_message`
explicitly asserts `≥ 2` occurrences of the regex in `select_model` to
prevent silent drift where one is updated but not the other.

**3. `dropdown_opened=False` when the pre-check skips the open call.**
Stash KEEP-1 initialises `dropdown_opened = False` before the
conditional, and when items are already visible the only branch that
sets it to True (`dropdown_opened = await _open_model_dropdown(page)`)
is skipped. The 4 `_close_model_panel(page, dropdown_opened)` call
sites in the retry/fallback/exit paths therefore receive `False` in the
skip case. Master's `_close_model_panel` body does not currently
BRANCH on `dropdown_was_opened` (the parameter name suggests it was
reserved for a hypothetical "only close if we opened it" optimization,
but the current body just tries Slate click → Escape fallback
regardless). So the skip case `_close_model_panel(page, False)` is
functionally equivalent to `_close_model_panel(page, True)` on the
current master body. Behavior is preserved; only the TRUTH of
`dropdown_opened` matches reality (we did NOT open the dropdown, so
passing False is more honest). This is consistent with the stash's
KEEP-1 intent.

**4. Placeholder `f5dab42` vs waiting for the hash.**
Followed the B15 precedent (`caef3e9` commit had `<B15-COMMIT>` in
SPEC/WORKPLAN; `d11500f` backfilled). Initial feat commit ships with
the placeholder; a separate `docs(spec): replace B17-COMMIT placeholders
with hash <hash>` commit lands after to backfill. Lets the feat commit
have a known message ahead of knowing its own hash.

**5. Decided not to refactor the "Step 3" comment.**
Master had a two-line comment block above `MODEL_ITEM_SELECTORS`:

```python
# Step 3: Find and click the target model
# Use broad selectors: menuitem, button, [role] — and retry up to 3 times.
```

Stash KEEP-1 drops both lines and re-adds just `# Step 3: Find and click
the target model` right before `for attempt in range(3)`. The "Use broad
selectors..." comment is lost. Applied verbatim since KEEP-1 is a
verbatim cherry-pick — keeping the refactor minimal. (The behavior it
described is still observable from the `MODEL_ITEM_SELECTORS` constant
literal a few lines up, so no information loss.)

### Rejected hunks — actions documented

- **model_selector.py H1** (capture `chip_handle` before click) — NOT
  applied. Dep of H4 toggle-close rewrite. No standalone value (would
  add an unused ElementHandle field).
- **model_selector.py H3** (thread `chip_handle` + `chip_tagged_js`
  through 4 call sites of `_close_model_panel`) — NOT applied.
  Signature change required only for H4. `_close_model_panel(page,
  dropdown_was_opened)` master signature is guarded by
  `test_close_model_panel_signature_unchanged`.
- **model_selector.py H4** (rewrite `_close_model_panel` to re-click
  the captured chip in 3 fallback methods) — NOT applied. User's
  decision (per supervisor prompt and triage §7.1 CONFLICT row) is to
  preserve master's click-outside (Slate editor click) + single Escape
  fallback from B8 commit `7245ae8`, which passed Phase A validation
  (LP credit leak fix) and has not exhibited the "accidentally closed
  extend panel" bug the stash docstring warned about.
  `test_close_model_panel_preserves_click_outside_approach` is the
  contract trip-wire against silent H4 drift (e.g. via a future
  cherry-pick that brings `chip_handle` / `data-flow-chip` back).

### Bug candidates discovered during this session — none

Execution was a focused cherry-pick of a standalone hunk. No new
issues observed in `flow/model_selector.py`. Adjacent files (B14
`_base.py`, B15 `extend.py`, B16 `submit.py`) are explicitly separate
scopes per supervisor plan and were not opened.

---

## 8. Handoff notes

**Workdir state:** clean (modulo the B17 commit itself). `git stash list`
still shows `stash@{0}` intact — the cherry-pick process used only
`git stash show -p stash@{0}` (read-only peek).

**Env:** no new env vars required.

**Next session:** **stash drop review.** With B14 (`72e056b`), B15
(`caef3e9`), B16 (parallel, landing on separate branch), and B17 (this
commit) now covering the 4 supervisor-sanctioned KEEP hunks from the
stash triage, the next supervisor action is to verify all KEEP hunks
landed (walk the `stash@{0}` diff against current master) and then `git
stash drop stash@{0}`. The stash's rejected hunks (model_selector H1/H3/H4,
_base.py H1, _base.py H4 dead code, extend.py H5) have explicit guards
in the post-Phase-A test suite so dropping the stash does not lose any
remaining useful signal.

**If B16 landed first:** this session's SPEC/WORKPLAN range-bumps
(B1-B15 → B1-B17) will conflict with B16's bump (B1-B15 → B1-B16).
Supervisor-resolved merge: keep both B16 and B17 entries; final range
= B1-B17. This is the expected outcome per the supervisor prompt's
"chạy independent" instruction.

**Reference docs the next session needs to read:**

- `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7
  (all verdicts) + §8 (handoff notes about stash disposition).
- Current state of `git stash list` — confirms `stash@{0}` still intact.
- This report + `2026-04-17_B14_base-nav-verify.md` +
  `2026-04-17_B15_extend-panel-verify.md` + B16 report (when landed)
  for the four cherry-pick post-mortems.

**Known follow-ups deferred to post-Phase-A:**

- B10 (Pydantic `default_factory=datetime.utcnow`) — still pending per
  SPEC §D.4.
- B4 (chains table) — still deferred.

---

## 9. Done criteria checklist

From supervisor prompt's `[DONE CRITERIA]`:

- [x] KEEP-1 applied (LP pre-check replaces Step 2.7 unconditional open)
- [x] `_close_model_panel` signature UNCHANGED (H1/H3 rejected —
  `test_close_model_panel_signature_unchanged` is the guard)
- [x] Master click-outside + Escape fallback PRESERVED (H4 rejected —
  `test_close_model_panel_preserves_click_outside_approach` is the
  guard)
- [x] Tests GREEN (7/7 in `test_model_selector.py`), full suite pass
  (55/55), no regression (B1 3/3, B5 4/4, B6 3/3, B7 2/2, B8 2/2, B9
  2/2, B11 6/6, B12 7/7, B14 7/7, B15 12/12)
- [x] DeprecationWarning-strict: clean (55/55 with `-W
  error::DeprecationWarning`)
- [x] SPEC §D.4 B17 + WORKPLAN §8 B17 added (both with `f5dab42`
  placeholder)
- [x] Stash@{0} còn (`git stash list` post-session confirms intact)
- [x] Zero diff ngoài whitelist (only `flow/model_selector.py`,
  `tests/test_model_selector.py`, `docs/SPEC.md`, `docs/WORKPLAN.md`,
  this report)
- [x] Report has 9 sections per `_TEMPLATE.md`

---

_Sign-off: ✅ Ready for supervisor review._
