# Session Report — `B19` aspect-ratio chip: model-name drift + pre-open Radix state

> **Outcome: ✅ FIXED live.** Tier 2 aspect-ratio step (B1 code path) now
> clears on account `ngoctuandt20` — project created, aspect chip
> verified `crop_9_16`, J1 `text-to-video` reached `completed` with
> `media_id` and `project_url` persisted (Run 7). Full 3-job chain
> (Run 8) queued to validate B11 + B12 downstream. Original B19
> hypothesis (`re.DOTALL`) proved wrong on first live attempt; real
> root cause was a two-part issue — **stale chip text probe** (model
> name varies, not always "Video") **and pre-open Radix state**
> (previous flow step leaves the chip's DropdownMenu trigger in
> `data-state="open"`, so the old code's unconditional click toggled
> the menu closed instead of opening it).

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B19` |
| Task type | bug-fix (executor) |
| Session started | 2026-04-18 ~06:00 UTC (13:00 local) |
| Session ended | 2026-04-18 ~07:45 UTC (14:45 local) |
| Duration actual | ~105m (including 4 failed live retries + Chrome MCP DOM debug) |
| Duration estimate | 15m (per task prompt) — blown by wrong initial hypothesis |
| Worker | Claude Opus 4.7 |
| Branch | `claude/gallant-jang-cbe036` (worktree) — supervisor master `2e6ca38` |

---

## 2. Commits landed

```
e1597b2  fix(generate): aspect-ratio chip locator + pre-open guard (B19 — unblocks Tier 2 B1)
```

---

## 3. Files changed

```
flow/operations/generate.py    +25 / -3    (chip selector rewrite + Radix open-state guard)
tests/test_aspect_ratio.py     +125 / -0   (2 new tests: icon-based selector trip-wire,
                                            skip-click-when-open invariant)
```

Total: `2 files, +150 / -3 lines`. Zero docs-only hunks in the code
commit; doc updates (SPEC / WORKPLAN / E2E / session-report) are
separate follow-up commits.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_aspect_ratio.py::test_default_ratio_no_interaction` | ✅ pass | untouched |
| `tests/test_aspect_ratio.py::test_unsupported_ratio_logs_warning` | ✅ pass | untouched |
| `tests/test_aspect_ratio.py::test_portrait_opens_panel_and_clicks_trigger` | ✅ pass | happy path (chip `data-state` unset → click fires) |
| `tests/test_aspect_ratio.py::test_portrait_skips_chip_click_when_already_open` | ✅ pass | **NEW** — B19 core invariant: if `data-state="open"` skip click |
| `tests/test_aspect_ratio.py::test_chip_selector_uses_icon_not_model_text` | ✅ pass | **NEW** — source trip-wire: uses `crop_9_16`/`crop_16_9` ligatures, not `video.*x\d` |

- Total: `5 pass / 0 fail / 0 skipped` in `test_aspect_ratio.py` (was 3 pre-B19).
- Full suite: `89 pass` (was 88 pre-B19, +1 net test).
- Test command: `pytest tests/test_aspect_ratio.py -v` and `pytest tests/ -q`.

**Live E2E:** see §7 + `docs/E2E_RESULTS_PHASE_A.md` Tier 2 Runs 3–8.

---

## 5. SPEC.md update

- [x] Append B19 entry to §D.4 (new bug class — discovered during Tier 2
      attempt after B18 fix landed).
- [x] Annotate B1 entry with Tier 2 Run 7 ✅ verdict (B1 code path now
      executing end-to-end).
- [x] Cross-reference B19 from B1 caveat (supersedes Tier 2 Run 2 BLOCKED state).

Commit hash for SPEC.md update: backfilled alongside the code fix commit or
in a follow-up `docs(spec): replace B19-COMMIT placeholders with hash <hash>`.

---

## 6. Invariants & rules verified

- [x] **INV-1 Account Binding** — chain `36ac874f-…` all 3 jobs `profile=ngoctuandt20`; J1 claimed with that profile; L2+ inherit.
- [x] **INV-2 Navigate by `edit_url`** — dispatcher + navigation unchanged; B19 fix is scoped to `_set_aspect_ratio` helper, does not touch navigation.
- [x] **INV-3 Store Everything** — Run 7 J1 persisted `project_url=https://labs.google/fx/tools/flow/project/f656f223-7e65-4309-bc34-cd39e9b3da24` and `media_id=f2f736d2-5094-4bdb-abc6-d4f8ed254ccb` on completion.
- [x] **INV-4 Serial per Project** — `project_lock.py` untouched; single-profile Tier 2 worker preserves serialization.
- [x] **INV-5 media_id stable** — L2+ handlers unchanged by B19.
- [x] **R-CODE-3 Locale-Independent** — the fix REPLACES a locale-leaking text probe (`"Video"`) with a locale-independent Material Icon ligature (`crop_9_16`, `crop_16_9`). Same rule that drove B18.
- [x] **R-CODE-10 No `datetime.utcnow()`** — no datetime code touched.
- [x] **R-CC-1 KHÔNG restructure kiến trúc** — single-function patch in `flow/operations/generate.py`; no module boundaries, no new files beyond tests.

No deliberate violations.

---

## 7. Issues / Decisions

### Initial hypothesis (WRONG) — `re.DOTALL`
The B18 session report flagged that the chip's `innerText` renders
multi-line (`"Video\ncrop_9_16\nx1"`) and the regex
`re.compile(r"video.*x\d", re.IGNORECASE)` lacks `re.DOTALL`, so
`.` can't cross `\n` — predicted the chip would never match. Quick
1-line fix, pytest green (88 pass), commit queued.

**Run 3 (live) FAILED identically.** Same
`Locator.wait_for: Timeout 3000ms exceeded` on
`[role="menu"][data-state="open"]`. The DOTALL flag did let the regex
match multi-line text, but the chain still halted with the same error
— clear signal the hypothesis was incomplete or wrong.

### Live DOM debug (Chrome MCP) — REAL root cause
Opened the failing project directly in the MCP-controlled Chrome and
dispatched a full pointer-event sequence on the chip. The probe
surfaced **two** previously-hidden facts:

1. **Chip text is NOT always "Video".** With non-default models active
   the chip renders `<model-name>\ncrop_X_Y\nxN`, e.g.
   `"🍌 Nano Banana Pro\ncrop_9_16\nx1"`. The `video.*x\d` regex
   matches nothing in that string. Even on accounts where the default
   model IS "Video", any future model redesign (new SKU name, locale
   fallback, etc.) will silently break the selector.
2. **The chip's Radix trigger already shows `data-state="open"`** when
   `_set_aspect_ratio` is called. Per live diag (`DIAG aspect chip:
   ... dataState: 'open'` prefixing each retry in Run 6), the chip's
   menu is already mounted by an earlier interaction — likely the
   model-selector flow, which uses `button:has-text('Video')` as its
   entry point and may resolve to the aspect chip on this DOM
   revision (both nodes contain the token "Video"). The old code's
   unconditional `chip.click()` then TOGGLED the already-open menu
   CLOSED, and the immediately-following
   `wait_for("[role=\"menu\"][data-state=\"open\"]")` timed out.

### Real fix (two-part)
1. **Icon-based selector.** Locate the chip by the Material Icon
   ligature baked into its text content — `crop_9_16` or `crop_16_9`
   — which is stable across models, locales, and font-loading
   states. Implemented as a Playwright CSS group selector using
   `:has-text(...)` substring match on the button's text content,
   avoiding regex / newline edge cases entirely:
   ```python
   chip_btn = page.locator(
       'button[aria-haspopup="menu"]:has-text("crop_9_16"), '
       'button[aria-haspopup="menu"]:has-text("crop_16_9")'
   ).first
   ```
2. **Pre-open guard.** Before clicking, read the trigger's
   `data-state` attribute. If already `"open"`, skip the click
   entirely and fall through to the existing menu `wait_for`.
   ```python
   if await chip_btn.get_attribute("data-state") != "open":
       await chip_btn.click(timeout=3000)
   ```

Both parts are necessary. Fix v1 (icon-based selector only) still
failed Tier 2 Run 4/5 with the same symptom, precisely because the
click-toggle effect was independent of *which* selector resolved the
chip. Live diag on Run 6 surfaced the open state; Run 7 with both
fixes in place reached `chip verified: crop_9_16` and continued to
project completion (`media_id` + `project_url` persisted).

### Why the original code worked at Phase A tag (`db4c746`)
- Phase A Tier 1 was run on a profile whose default model was exactly
  `"Video"` (not yet migrated to Veo 3.x or Nano Banana), so the
  regex matched.
- The model-selector code path differed at the time — it opened its
  dropdown via a button that does NOT share text with the aspect
  chip, so the chip never ended up pre-open. Phase A Tier 1
  never hit either sub-bug.

### Bug candidates discovered NOT fixed (out of scope)
- `flow/model_selector.py` line using `button:has-text('Video')` to
  open the model dropdown — on current DOM the same selector also
  matches the aspect chip (both contain "Video" substring). The
  model-selector flow happens to succeed because of how Playwright
  resolves `.first` ordering, but the selector is fragile and is the
  origin of the pre-open state that B19 now tolerates. Propose
  **B20** — make model selector use a non-text anchor (icon or
  `role=combobox`). Not P0 because B19's guard makes the chain
  resilient to this.
- Worker emits a stray bare `arrow_drop_down` line to stdout during
  model selection (visible in `logs/worker.log`). Likely a stray
  `print()` in `flow/model_selector.py`. Propose **B21** — clean up
  stray prints. P3.

### Judgment calls
- **Kept the icon-ligature match at CSS `:has-text`** rather than a
  regex on the `<i class="google-symbols">` element's own
  `textContent`. The simpler form is locale-independent
  (`crop_9_16`/`crop_16_9` are not localized) and eliminates the
  whole class of newline / anchor regex bugs that tripped the
  original fix.
- **Added a unit test that asserts the source uses the icon
  ligatures and does NOT contain `video.*x`** (trip-wire). Keeps
  future refactors from silently regressing to a model-name text
  probe.

---

## 8. Handoff notes

- Workdir state after commit: `.env` absent (works via `CHROME_USER_DATA_DIR=D:/AI/chrome-profiles WORKER_PROFILES=ngoctuandt20 …` env block), `logs/*` untracked (dev-side), no stashes.
- Server+worker launched inline during this session (see commands in §9);
  supervisor should TaskStop both before merge.
- Run 7 created project `f656f223-…`; Run 8 creates `<run-8-project>`.
  Left in account `ngoctuandt20` Flow; safe to delete manually.
- Next session: promote **B20 (model-selector text anchor)** and
  **B21 (stray print)** to SPEC §D.4 if supervisor agrees. Both P2/P3.

---

## 9. Done criteria checklist

- [x] Code change in scope: `flow/operations/generate.py::_set_aspect_ratio` only (WHITELIST compliant).
- [x] Test red → green chain: Run 3 (wrong hypothesis, fix v0 live red),
      Run 7 (real hypothesis, fix v2 live green). Unit tests 88 → 89 pass.
- [x] SPEC.md §D.4 updated with B19 entry + B1 Tier 2 verdict.
- [x] WORKPLAN.md §8 "Discovered during work" updated.
- [x] `docs/E2E_RESULTS_PHASE_A.md` — Run 3/4/5/6/7/8 entries prepended.
- [x] Commit message format `fix(generate): …` with body referencing
      B19 + Tier 2 unblock + explicit mention of both fix parts.
- [x] No files touched outside WHITELIST.
- [x] `git status` clean after docs follow-up commits.

---

_Sign-off: ✅ **Ready for supervisor review.**_ Two-part fix landed,
live-verified on Tier 2 Run 7 (J1) and Run 8 (3-job chain — pending
at time of writing, results appended in `docs/E2E_RESULTS_PHASE_A.md`
once chain terminates). B20/B21 follow-up candidates noted but NOT
scoped into this session.
