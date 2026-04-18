# Session Report — `B16` click_submit iterate all matching buttons, skip disabled

Cherry-picks 1 hunk from `stash@{0}` ("flow refinements") onto
`flow/submit.py::click_submit` per the stash triage
(`docs/session-reports/2026-04-17_stash-triage_flow-refinements.md` §7.4).
Replaces per-selector `.first` probe with `range(count) / .nth(i)`
iteration that skips disabled buttons. No behavioural change on the
happy path where the first visible match is also enabled (master already
clicked it; post-fix still clicks it — at index 0). The fix only kicks
in when `.first` is disabled and a sibling match on the same selector is
enabled — a scenario master could not recover from.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B16` |
| Task type | bug-fix (stash cherry-pick, TDD) |
| Session started | 2026-04-18 |
| Session ended | 2026-04-18 |
| Duration actual | ~40m |
| Duration estimate | ~40m (supervisor prompt) |
| Worker | Claude Opus 4.7 (executor session) |
| Branch | `claude/brave-gates-0152ca` (worktree) |
| Master @ | `d11500f` |

---

## 2. Commits landed

```
<B16-COMMIT>  feat(submit): iterate all matching buttons, skip disabled (B16 cherry-pick)
```

1 commit: 1 prod file + 1 test file + SPEC + WORKPLAN + this report.
Optional follow-up commit (per B14/B15 precedent) replaces the
`<B16-COMMIT>` placeholder with the actual hash in `docs/SPEC.md` §D.4 B16
and `docs/WORKPLAN.md` §8 B16.

---

## 3. Files changed

```
flow/submit.py                                               +19 / -8    (KEEP-7 inner loop body)
tests/test_submit.py                                         NEW         (8 cases)
docs/SPEC.md                                                 +24 / -2    (§D.4 B16 entry + header bump B1-B15 → B1-B16)
docs/WORKPLAN.md                                             +1  / -0    (§8 B16 resolved entry)
docs/session-reports/2026-04-18_B16_submit-iterate.md        NEW         (this report)
```

Tổng: **3 modified + 2 new**.

**Files NOT touched** (blacklist enforced):
- `flow/submit.py::submit_with_confirmation` — Phase A commit `5c7d625`
  scope (timeout-returns-False + NEW-api-calls delta snapshot). `git diff
  flow/submit.py` confirms every changed line is inside the `click_submit`
  `for selector in SUBMIT_SELECTORS:` body; `submit_with_confirmation`,
  `_count_cards`, `_has_progress`, `SUBMIT_SELECTORS` list, `_SKIP_PATTERN`
  regex, and the module docstring are byte-identical with master.
- `flow/submit.py::_SKIP_PATTERN` — regex body unchanged (still
  `image|video|frames|ingredients|reference|9:16|16:9|x1-4|veo|lower priority`).
  The cherry-pick uses it *inside* the new per-button loop rather than
  once per selector — same filter semantics, applied at finer granularity.
- `flow/operations/_base.py` (B14 scope — already merged `72e056b`;
  `navigate_to_edit` + `_click_video_tile` untouched; `tests/test_base.py`
  still 7/7).
- `flow/operations/extend.py` (B15 scope — already merged `caef3e9`;
  `_verify_extend_panel` + `extend_video` + `_type_extend_prompt` untouched;
  `tests/test_extend.py` still 12/12).
- `flow/model_selector.py` (B17 scope — LP items pre-check is a separate
  branch; KEEP-1 from stash §7.1).
- `flow/operations/_base.py::draw_bbox_on_video` (B11 preserved —
  `tests/test_bbox.py` 6/6 pass).
- `stash@{0}` (read via `git stash show -p` only; `git stash list` confirms
  intact post-session).
- `server/*`, `worker/*`, `docs/DESIGN.md`, `.claude/*`.

---

## 4. Tests

| Test | Pre-apply | Post-apply | Notes |
|---|---|---|---|
| `test_click_submit_iterates_all_buttons` | ❌ `btn_ok.click` not awaited | ✅ pass | KEEP-7 core: 3 buttons (disabled+invisible+enabled) → click only enabled |
| `test_click_submit_skip_disabled_first` | ❌ master clicked `.first` disabled | ✅ pass | Core contract trip-wire: `.nth(0)` disabled → continue to `.nth(1)`, no fall-through |
| `test_click_submit_skip_pattern_preserved` | ❌ master clicked `.first` "Generate video" (in skip pattern) | ✅ pass | `_SKIP_PATTERN` filter still runs inside loop |
| `test_click_submit_no_enabled_button` | ❌ master clicked disabled btn A1 | ✅ pass | Selector fall-through still works on top of iteration |
| `test_click_submit_debug_log_per_button` | ❌ no per-btn debug log in master | ✅ pass | `btn[0]` / `btn[1]` / `count=` DEBUG records present |
| `test_click_submit_all_disabled_falls_back_to_keyboard` | ❌ master clicked disabled `.first` of first selector | ✅ pass | Ctrl+Enter fallback reached only when iteration exhausts every selector |
| `test_click_submit_zero_count_falls_through` | ✅ pass | ✅ pass | Invariant: 0 matches → skip iteration → probe next selector. Master also passes via `.first.is_visible=False`. |
| `test_click_submit_per_button_exception_does_not_abort` | ❌ master aborted selector on `.first.is_visible` raise | ✅ pass | Per-button try/except isolates each `btn[i]` — loop survives one broken button |

RED baseline (pre-apply, current master `d11500f`): **7 fail / 1 pass**.
The one pass (`test_click_submit_zero_count_falls_through`) is an
invariant both master and KEEP-7 satisfy — included as a regression
guard rather than a behavioral-divergence proof. All 7 failing tests
exercise the exact master-vs-KEEP-7 divergence: disabled button
handling, per-button iteration, per-button debug logging, and per-button
exception isolation.

GREEN post-apply: **8 pass / 8 total**.

Full suite: `56 passed in 6.19s` (baseline was 48; +8 from
`test_submit.py`). No regression in `test_base.py` (B14, 7/7),
`test_extend.py` (B15, 12/12), `test_bbox.py` (B11, 6/6),
`test_camera.py` (B12, 7/7), `test_aspect_ratio.py` (B1, 3/3),
`test_config.py` (B7, 2/2), `test_datetime_migration.py` (B8, 2/2),
`test_job_store.py` (B5, 4/4), `test_profile_store.py` (B6, 3/3), or
`test_smoke.py` (B9 fixtures, 2/2).

DeprecationWarning-strict: `pytest tests/ -W error::DeprecationWarning`
→ **56 passed in 6.25s** (clean).

Command: `pytest tests/ -v`.

Pytest mode: `asyncio_mode=auto` (existing). No sleep-stubbing fixture
needed in `test_submit.py` — `click_submit` has no `asyncio.sleep`
calls; the `is_visible(timeout=500)` / `is_enabled(timeout=300)` calls
are mocked (return immediately) so tests run at ~10-20ms each (~170ms
for the module).

---

## 5. SPEC.md update

- [x] §D.4 header bumped from B1-B15 → B1-B16 + descriptor updated
  ("B14-B15 là stash-triage cherry-picks" → "B14-B16 là stash-triage
  cherry-picks (2026-04-17 triage, landed 2026-04-17/18)")
- [x] MỤC LỤC link updated (`b1-b15` → `b1-b16` anchor)
- [x] §D.4 B16 entry added (struck-through, `<B16-COMMIT>` placeholder)
- [x] WORKPLAN §8 B16 resolved entry added (struck-through,
  `<B16-COMMIT>` placeholder)

Placeholder `<B16-COMMIT>` will be replaced with the actual hash by a
follow-up docs commit (same pattern as B14/B15 used pre-hash, see
commits `a4e9092` / `a2293bf` / `d11500f` replacing
`<B14-COMMIT>`/`<B15-COMMIT>` placeholders).

No strike-through on an existing bug row — B16 is a NEW discovery from
the stash triage, not a previously-documented gap.

---

## 6. Invariants & rules verified

- [x] **INV-1 Account Binding** — no profile handling touched;
  `client.profile_name` unchanged; `click_submit` takes `page` only.
- [x] **INV-2 Navigate by `edit_url`** — untouched (B14 territory).
- [x] **INV-3 Store Everything** — no change to `finalize_operation`;
  `project_url` / `media_id` still stored downstream.
- [x] **INV-4 Serial per Project** — `project_lock` untouched.
- [x] **INV-5 `media_id` stable** — not involved (click_submit is
  DOM-level, has no identity concept).
- [x] **R-CODE-3 Locale-Independent** — no new selectors added;
  `SUBMIT_SELECTORS` list unchanged. The 6 existing selectors mix
  icon-text (`arrow_forward`) and English `aria-label` fragments
  (`'Create' i`, `'Generate' i`, `'Send' i`); no Vietnamese branches
  introduced or removed. The noise filter `_SKIP_PATTERN` keeps its
  existing English/numeric tokens.
- [x] **R-CODE-10 No `datetime.utcnow()`** — no datetime usage in the
  touched code.
- [x] **R-CC-1 KHÔNG restructure** — 1 localized hunk inside the
  existing `click_submit` function body. No public signature change:
  `async def click_submit(page, timeout_ms: int = 3000) -> bool`
  preserved. `SUBMIT_SELECTORS` / `_SKIP_PATTERN` / keyboard fallback
  all preserved.

---

## 7. Issues / Decisions

### Judgment calls during execution

**1. Inner-loop `_SKIP_PATTERN` placement.**
Stash applies the `_SKIP_PATTERN` check at the same indentation level
as `is_visible` / `is_enabled` — evaluated per button, not per selector.
Master evaluates it once per selector (after `.first.is_visible`).
KEEP-7 retains semantic equivalence on happy paths but filters *each*
match individually, which matters when a selector returns both a
"submit" and a noise button (e.g. both `arrow_forward` submit and a
`Generate video | Image` chooser). Applied verbatim per stash.

**2. Did NOT move `_SKIP_PATTERN` out of the loop for "efficiency".**
Tempting micro-optimization: hoist the regex check to skip buttons
without reading `inner_text` for buttons that are already invisible or
disabled. Rejected — keeps the cherry-pick minimal and preserves the
exact log ordering (a `btn[i]: vis=X ena=Y skip=Z` line logs every
probed button, even filtered ones; operators can trace why each button
was rejected).

**3. `is_enabled(timeout=300)` vs `is_enabled(timeout=500)`.**
Stash uses 300ms for `is_enabled` vs 500ms for `is_visible`. Kept
verbatim. Rationale (my interpretation, not in stash): visibility
reflects layout which can lag during panel mount; enabled state is
synchronous after visibility is confirmed. Shorter `is_enabled` timeout
reduces stuck-button wait on the rare `is_visible=True but enabled
state racing` path.

**4. Short-circuit on `vis=False`.**
Stash writes `ena = await btn.is_enabled(timeout=300) if vis else False`
— only probes `is_enabled` when `vis` is True. Saves ~300ms per
invisible button. Applied verbatim.

**5. Per-button DEBUG log wording.**
Used stash verbatim: `"  btn[%d]: vis=%s ena=%s skip=%s text=%s"` (note
the two leading spaces for tree-style indentation under the selector
log line). Tests assert `"btn[0]" in m` (substring) rather than exact
format, so a future cosmetic tweak doesn't break the suite.

**6. Success log wording includes index + text.**
Stash: `"Submit clicked via: %s [%d] text=%s"`. Master: `"Submit clicked
via: %s"`. The added `[%d] text=%s` fields are operationally valuable
— a `.nth(3)` win in prod tells you the cherry-pick was load-bearing at
least once. Applied verbatim.

**7. Preserved all master log levels.**
Master uses INFO for success and has no per-selector DEBUG. Stash adds
DEBUG logs (selector-entry, per-button, per-button-error, selector-error)
and keeps INFO for success / Ctrl+Enter fallback. No master logs were
demoted or removed.

**8. Test mock `.first` aliases `buttons[0]`.**
Initially set `loc.first.is_visible = AsyncMock(return_value=False)` as
a safety default, which made master *falsely* pass two tests (the
`.first` probe returned False so master fell through). Revised to
`loc.first = buttons[0] if buttons else <sentinel>` — now master sees
the actual first button's state and fails the regression tests as
expected. RED count jumped from 6 → 7 after this fix, confirming the
mock accurately models Playwright's `.first` semantic.

### Rejected hunks — none

Supervisor prompt scoped this session to KEEP-7 only (single hunk,
single file). Stash §7.4 has no other hunks; H5-style "also removes
master fallbacks" is not applicable — the cherry-pick is additive
(iterate instead of .first) without removing the `_SKIP_PATTERN` filter
or the keyboard fallback.

### Bug candidates discovered during this session — none

Execution was a focused cherry-pick; no new issues observed in
`flow/submit.py`. Adjacent file B17 (`model_selector.py` KEEP-1 LP
items pre-check) is explicitly the next branch per supervisor plan and
was not opened.

---

## 8. Handoff notes

**Workdir state:** clean (modulo the B16 commit itself). `git stash
list` still shows `stash@{0}` intact — supervisor may re-run triage or
eventually `git stash drop stash@{0}` after B17 also lands (B17 is the
last KEEP hunk in the stash; once merged, the stash has only the 3
CONFLICT hunks the triage §7 flagged for user review, and supervisor
can decide stash disposition then).

**Env:** no new env vars required.

**Next session:** **B17** — `flow/model_selector.py` LP pre-check.
Cherry-picks KEEP-1 from stash §7.1 H2 — before calling
`_open_model_dropdown` in extend mode, probe whether LP items are
already visible and skip the dropdown open if so (clicking it would
toggle-close the panel and hide the LP items). Standalone hunk, no
dependency on the stash's H1/H3 chip-handle threading (those were
flagged as dependencies of H4 which is a CONFLICT — user review
deferred). Supervisor soạn prompt sau. Separate branch, separate PR.

**Reference docs the next session needs to read:**
- `docs/session-reports/2026-04-17_stash-triage_flow-refinements.md`
  §7.1 + KEEP-1 code block (stash lines 124-138 region)
- This report + B14/B15 reports for the cherry-pick pattern
  (B14 → B15 → B16 lineage: same shape, TDD RED→GREEN, preserve master
  fallbacks by default, trip-wire test per behavioral contract,
  placeholder-then-backfill commit pair)
- `flow/model_selector.py` current state — Phase A commit `7245ae8`
  introduced the click-outside-via-Slate + Escape-fallback close
  pattern (triage §7.1 H4, not in B17 scope). KEEP-1 adds a pre-check
  *before* the dropdown-open call; it does NOT modify the close path.

**After B17 lands → stash drop review.** Stash will have only the 3
CONFLICT hunks (model_selector.py H4 toggle-close, _base.py H1 nav
strategy reversal, extend.py H5 placeholder removal) which are all
philosophy decisions that require user input, not blind cherry-picks.

**Known follow-ups deferred to post-Phase-A:**
- B10 (Pydantic `default_factory=datetime.utcnow`) — still pending per
  SPEC §D.4.
- B4 (chains table) — still deferred.

---

## 9. Done criteria checklist

From supervisor prompt's `[DONE CRITERIA]`:

- [x] KEEP-7 applied (iterate + skip disabled + debug log)
- [x] `_SKIP_PATTERN` filter still active in loop
- [x] `submit_with_confirmation` KHÔNG bị chạm (`git diff flow/submit.py`
      shows zero lines outside `click_submit`'s inner `for selector`
      body)
- [x] Tests GREEN (8/8 in `test_submit.py`), full suite pass (56/56),
      no regression (B11 bbox 6/6, B12 camera 7/7, B14 base 7/7,
      B15 extend 12/12)
- [x] `-W error::DeprecationWarning` clean (56/56)
- [x] SPEC §D.4 B16 + WORKPLAN §8 B16 added (both with `<B16-COMMIT>`
      placeholder)
- [x] Stash@{0} còn (`git stash list` post-session confirms)
- [x] Zero diff ngoài whitelist (only `flow/submit.py`,
      `tests/test_submit.py`, `docs/SPEC.md`, `docs/WORKPLAN.md`, this
      report)
- [x] Report has 9 sections per `_TEMPLATE.md`
- [x] (Optional) placeholder backfill commit theo precedent B15 — will
      land as a second commit after supervisor reviews the main commit

---

_Sign-off: ✅ Ready for supervisor review._
