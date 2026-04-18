# Session Report — `B26` Submit + model-chip exact-text selectors (L2 extend /edit/→/project/ drift)

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B26` |
| Task type | bug-fix (flow UI-layer, P0) |
| Session started | 2026-04-19 ~00:10 UTC |
| Session ended | 2026-04-19 ~03:50 UTC |
| Duration actual | ~3h 40m (includes live Chrome-extension E2E verification on two operations) |
| Duration estimate | — (not pre-queued; discovered mid-session from worker failure `gen_id=None, new_api_calls=0` on L2 extend) |
| Worker | Claude Opus 4.7 |
| Branch | `claude/sharp-curie-80ac08` |
| Worktree | `.claude/worktrees/sharp-curie-80ac08/` |
| Prior supervisor commits | `29f155f` (master — B22 placeholder-hash follow-up) |

---

## 2. Commits landed

```
d4fca1a   fix(flow): exact-text selectors for submit + model chip + mode buttons (B26 — L2 extend no longer drifts /edit/→/project/)
```

(Code + tests + docs + session report in one commit. Follow-up placeholder-replacement commit so SPEC/WORKPLAN carry the real hash rather than `d4fca1a`, matching the B22 pattern.)

---

## 3. Files changed

```
flow/submit.py                 +56 / -30   (collapse SUBMIT_SELECTORS → single ``:text-is('arrow_forward')``; drop _SKIP_PATTERN; add scope param; expanded module docstring)
flow/model_selector.py         +71 / -29   (chip_selectors → ``aria-haspopup='menu'`` + exact crop/arrow_drop_down ligatures; _has_dropdown_arrow → exact ``arrow_drop_down``; _switch_to_video_tab Video-tab exact + mode-title blacklist in JS fallback — B26 root cause)
flow/operations/_base.py       +46 / -4    (click_action_button: title-first + icon-fallback using _MODE_ICON_BY_TITLE map; no more fuzzy ``:has-text``)
tests/test_submit.py           +110 / -90  (remove _SKIP_PATTERN-era tests + SUBMIT_SELECTORS[1] fall-through tests; add scope-param behavioral test + exact-text source trip-wire)
docs/SPEC.md                   +22 / -3    (§D.4 B26 FIXED entry; TOC + §D.4 header B1-B22 → B1-B26; intro paragraph B26 discovery note)
docs/WORKPLAN.md               +6 / -0     (§8 B26 strike entry — discovered during work)
docs/session-reports/2026-04-19_B26_submit-and-model-exact-text.md  NEW   (this report)
```

Total: 7 files, ~311 / ~156 lines.

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_submit.py::test_click_submit_iterates_all_buttons` | ✅ pass | B16 contract preserved under B26 single-selector design |
| `tests/test_submit.py::test_click_submit_skip_disabled_first` | ✅ pass | **Core B26 use case** — disabled decorative + enabled real submit on /edit/; iteration picks the right one under single exact-text selector |
| `tests/test_submit.py::test_click_submit_debug_log_per_button` | ✅ pass | B16 DEBUG trace preserved |
| `tests/test_submit.py::test_click_submit_all_disabled_falls_back_to_keyboard` | ✅ pass | Ctrl+Enter fallback path unchanged |
| `tests/test_submit.py::test_click_submit_per_button_exception_does_not_abort` | ✅ pass | B16 resilience preserved |
| `tests/test_submit.py::test_click_submit_scope_prepends_to_selector` | ✅ pass | **NEW B26 contract** — `scope=` param prepends CSS selector for composer-scoped search |
| `tests/test_submit.py::test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern` | ✅ pass | **NEW B26 source trip-wire** — verifies canonical selector + forbids fuzzy `:has-text('arrow_forward')` + forbids text-based aria-label probes + pins selector list length to 1 |
| Full suite | ✅ 92 pass | Was 93; net −1 because we removed 2 obsolete tests (`test_click_submit_skip_pattern_preserved`, `test_click_submit_no_enabled_button`, `test_click_submit_zero_count_falls_through`) and added 2 B26-specific tests. Absolute delta is −3 + 2 = −1. |
| `-W error::DeprecationWarning` | ✅ clean | Zero warnings |

Test command: `python -W error::DeprecationWarning -m pytest tests/`

Deleted tests and why:
- `test_click_submit_skip_pattern_preserved` — the `_SKIP_PATTERN` filter existed to exclude non-submit buttons (`"Generate video"`, `"Lower Priority"` etc.) that the pre-B26 fuzzy selectors could match. B26's single exact-text selector `button:has(i:text-is('arrow_forward'))` cannot match those buttons, so the skip filter is redundant — deleting it is the B26 simplification.
- `test_click_submit_no_enabled_button` + `test_click_submit_zero_count_falls_through` — both exercised fall-through from `SUBMIT_SELECTORS[0]` to `SUBMIT_SELECTORS[1]`. B26 collapses the list to a single canonical selector; those branches don't exist. The remaining "all disabled → keyboard fallback" test covers the substantive behavior (nothing clickable → Ctrl+Enter).

**Live verification (more load-bearing than the unit tests for this bug class):**

Live E2E via Chrome extension on `ngoctuandt20` VI profile, 2026-04-19:

1. **Extend** — navigated to `/edit/ff3c33e0`, typed `"B26 live extend test: the red cube slides across the desk and stops at the edge"`, opened model chip (matched via new `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))` selector on /edit/), clicked `Veo 3.1 - Lite [Lower Priority]` menuitem → **URL stayed on /edit/** (the B26 bug did NOT trigger), clicked submit (matched via new `button:has(i:text-is('arrow_forward'))` exact-text selector, disabled decorative sibling filtered by `is_enabled` check) → 18% progress indicator → composer cleared → new clip appeared in history panel. Screenshots: `ss_4699ixxoo` (18% tile + cleared composer), `ss_30653utf4` (history panel showing B24 parent + new B26 extend clip).
2. **Insert** — navigated to `/edit/029f1ad0` (L1 original, accepts Chèn; the extend child from step 1 had the "Các chế độ chỉnh sửa khác không dùng được cho video mở rộng" constraint so it couldn't host Chèn), clicked Chèn button (matched via new title-first `button[title='Chèn']` selector; old fuzzy `:has-text('Chèn')` would have also caught the inactive decorative icon buttons), mode switched (placeholder "Mô tả nội dung bạn muốn thêm..."), drew bbox on the 479×269 canvas at (300,220)→(420,330), typed `"a small yellow pencil"`, clicked submit (again via exact-text selector) → 5% progress → after 8s wait: **yellow pencil visibly inserted over red cube in video preview**. Screenshot: `ss_0730suuw3` (yellow pencil overlay confirmed).

Both flows exercised the three B26-changed files end-to-end against the real Flow DOM. The submit click on /edit/ is the historically-broken path; it now succeeds because the exact-text selector excludes the Camera mode-switch button (whose `innerText` is `"videocam\nCamera"`).

---

## 5. SPEC.md update

- [x] TOC line — `D.4 — Known bugs trong code hiện tại (B1-B22)` → `(B1-B26)`
- [x] §D.4 header line — `B1-B22` → `B1-B26`
- [x] §D.4 intro paragraph — appended B26 discovery note ("B26 là Tier 2 post-B22 discovery: mid-session live E2E attempt to verify B22 surfaced a distinct UI-layer bug where `_switch_to_video_tab` JS fallback matched the /edit/ Camera mode-switch button via `lower.includes('videocam')`, silently redirecting the composer from /edit/ to /project/ between `select_model` and `submit_with_confirmation`.")
- [x] §D.4 B26 FIXED entry — full block covering root cause (3-part: submit fuzzy, chip-lookup absent `arrow_drop_down` on /edit/, JS videocam substring match), resolution (3 files, exact-text across the board), test changes, and live verdict
- [x] Commit-hash placeholder `d4fca1a` to be replaced post-commit

---

## 6. Invariants & rules verified

- [x] **INV-1 Account Binding** — profile untouched; no change to claim SQL or worker profile-pinning
- [x] **INV-2 Navigate by `edit_url`** — fix RESTORES this invariant; pre-B26, a silent `_switch_to_video_tab` Camera-button click was reverting /edit/ → /project/ between model selection and submit, de-facto navigating by side-effect to the wrong composer
- [x] **INV-3 Store Everything** — n/a (no new fields)
- [x] **INV-4 Serial per Project** — untouched (no claim-SQL change)
- [x] **INV-5 `media_id` stable** — untouched (no generate/extend payload change)
- [x] **R-CODE-3 Locale-Independent** — **strongly reinforced**. All three changed files now key on exact Material Icon ligature text (`arrow_forward`, `arrow_drop_down`, `crop_*`, `keyboard_double_arrow_right`, `add_box`, `ink_eraser`, `videocam`) rather than localized labels. The `click_action_button` primary path still uses `button[title='...']` (VI labels) but the fallback is icon-based. Source trip-wire (`test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern`) blocks future drift.
- [x] **R-CODE-10 No `datetime.utcnow()`** — n/a
- [x] **R-CC-1 KHÔNG restructure kiến trúc** — fix is surgical (selector strings + one new `scope=` param + one small dict lookup in `_base.py`); no new modules, no helper extraction, no client-surface signature changes except the additive `scope` kwarg with a `None` default

---

## 7. Issues / Decisions

### Root cause (mid-session diagnosis)

Worker failure mode: `Submit NOT confirmed after 15s — new_api_calls=0, gen_id=None, cards=<unchanged>, url=https://…/project/<id>` — L2 extend job. The `url=…/project/…` in the timeout log was the tell: the submit was happening on /project/, not /edit/. Rewinding logs: after `select_model` returned success, `_switch_to_video_tab`'s **JS fallback** ran (because the Playwright `[role='tab']:has-text('Video')` selector didn't match). The fallback had:

```js
if ((lower === 'video' || lower === 'videocam' || lower.includes('videocam'))
    && !lower.match(/x[1-4]/i) && rect.width > 20 && …) {
    el.click();
}
```

On /edit/, the bottom-strip Camera mode-switch button has `innerText === 'videocam\nCamera'` (icon ligature + label on separate lines). `lower.includes('videocam')` matched it, the `!lower.match(/x[1-4]/i)` guard passed (no `x1`/`x4`), and the click toggled Camera mode — which, for an L1 base clip (not an extended clip), was accepted and the composer's `data-scroll-state` changed + the URL reverted to the parent /project/ (Flow's SPA routes Camera mode to the project composer, not the /edit/ panel). By the time `submit_with_confirmation` ran, the L2 extend composer was gone; the submit button it found was the /project/ L1 generate button, which did not accept the pre-typed extend prompt + the L2-mode-only submit API, so the POST to `/operations/` never fired.

Same class of fuzzy-match leak also lived in:
- `flow/submit.py` — `button:has(i:has-text('arrow_forward'))` matched Camera button's icon area too, but was mostly masked by `is_enabled` skipping
- `flow/operations/_base.py::click_action_button` — `button:has-text('Chèn')` matched decorative icon buttons with `title` text containing "Chèn" as a substring of a longer hint

B26 is the general form of this class — **fuzzy `:has-text(token)` matches any button whose textContent contains `token` as a substring, including concatenated icon-ligature + label buttons**. The fix pattern: use Playwright's `:text-is(...)` exact-match engine on the `<i>` child icon (Material Icons have stable locale-independent ligatures), and pin to the button's `title` attribute (which is set explicitly per mode) as the primary signal.

### Judgment calls

**Q1. Why `:text-is(...)` on `<i>` and not a wider selector strategy (data-* attrs, aria-labels, unique CSS classes)?**
Live-DOM probe on /edit/ and /project/:
- `aria-label` on the real submit is empty, and on mode buttons is either empty or locale-dependent
- `data-testid` / `data-*` attrs are absent
- CSS classes are styled-components hashes (e.g. `sc-gsTDqH lnDnjW`) — unstable across builds
- The ONLY stable structural signal is the Material Icon `<i>` child's textContent, which is the icon ligature string designed by Google to be identical across locales and font-loading states
Icon ligature text on the `<i>` child is the canonical identity for Flow's button vocabulary. Pin there.

**Q2. Why keep `button[title='...']` as the primary in `click_action_button` + icon fallback, instead of icon-only?**
The VI title attribute (`Mở rộng`, `Chèn`, `Xoá`, `Camera`) is unique and stable on this profile. The live-DOM probe showed `title` is present on every mode button. Keeping title as primary means:
- Zero cost when the primary works (fast path; Chrome returns the match in microseconds)
- The icon fallback is only exercised if the profile is somehow in EN locale (which `ngoctuandt20` is not, but other profiles could be)

This matches R-CODE-3: VI-first because the target account is VI, EN fallback because the code must work on any account's locale.

**Q3. Why didn't I add a `scope=` param earlier (when B22 fixed the claim branch)?**
B22's scope was server DB only. The /edit/ composer scope is a UI concern — B22 was too early for it. B26 makes the `scope=` param available because `submit_with_confirmation` is callable from operations that already know their panel's `data-scroll-state` anchor (e.g. `'START'` for extend). The B26 commit itself doesn't wire any operation to pass `scope=` (callers still call without it), but the API is now there for the next time a duplicate-submit-button scenario surfaces.

**Q4. Unit tests vs live E2E as the primary verification signal.**
Both are required and carry different load. The unit tests lock the source-level contracts (`SUBMIT_SELECTORS == 1` entry, canonical exact-text selector present, fuzzy anti-pattern forbidden, B16 iteration preserved). The live E2E proves the selectors actually match the intended elements on real Flow DOM and that the full L2 extend + insert flows reach "generating". The bug was discovered ONLY in live — the old tests all passed pre-B26. So: unit tests guard the fix against regression; the live E2E is what certifies the fix closed the bug.

### Bug candidates NOT fixed (out of scope)

None new discovered during B26 work. The previously-flagged B20 (`flow/model_selector.py` opens model dropdown via text-substring selector that also matches aspect chip) is partially absorbed: B26 rewrote `chip_selectors` to use `aria-haspopup='menu'` + exact icon ligatures, which eliminates the aspect-chip collision. If B20 was still formally open, this absorbs it; the B20 candidate can be marked as fixed-by-B26 incidentally. **B21** (stray `arrow_drop_down` stdout print) remains unfixed — B26 didn't touch the print site.

### Related live observations

- **Extended clips reject L2 mode switches.** Tooltip: "Các chế độ chỉnh sửa khác không dùng được cho video mở rộng". An extended child cannot host Chèn/Xoá/Camera. The Insert E2E accordingly used the L1 original clip `029f1ad0` rather than the extend child. This is a Flow product constraint, not a FlowEngine bug. Worth noting in `docs/FLOW_UI_REFERENCE.md` if we ever encounter an insert-after-extend chain in production.

---

## 8. Handoff notes

- **Workdir state:** this session's changes staged + committed together (see §2). Post-commit, working tree clean.
- **Env:** no new env vars.
- **If a future session resumes L2 chain work:** the `submit_with_confirmation(..., scope='[data-scroll-state=\"START\"]')` escape hatch is now available for panels that need it. Extend/insert/remove/camera don't currently pass it because the scope-less selector already returns unique results — but if Flow adds a second composer per page, `scope=` is the knob.
- **Regression checklist for any future `flow/*` change touching UI selectors:**
  - `python -W error::DeprecationWarning -m pytest tests/test_submit.py tests/test_model_selector.py tests/test_base.py` must stay green
  - Any new `:has-text(...)` on Material Icon tokens is a code smell — reach for `:text-is(...)` on the child `<i>` instead
  - If adding a second `SUBMIT_SELECTORS` entry, update `test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern` to allow it with a comment explaining why it's locale-independent

---

## 9. Done criteria checklist

Per the mid-session supervisor directive ("live test to the point of real extend and real insert producing actual videos, on the extension-driven profile"):

- [x] **B26 root cause identified** — JS fallback `lower.includes('videocam')` matching /edit/ Camera mode-switch button
- [x] **Exact-text selectors applied to all three affected files** — `flow/submit.py`, `flow/model_selector.py`, `flow/operations/_base.py`
- [x] **Live E2E extend produces real video** — B26 live extend test on `ngoctuandt20`, 18% generation confirmed, history panel shows new clip
- [x] **Live E2E insert produces real video** — "a small yellow pencil" inserted over red cube, visible in preview
- [x] **Unit tests green** — 92 pass under `-W error::DeprecationWarning` (was 93; net −1 from removing obsolete `_SKIP_PATTERN` tests + adding 2 new B26 tests)
- [x] **Source trip-wire present** — `test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern` blocks a regression to fuzzy selectors
- [x] **Docs updated** — SPEC.md §D.4 B26 FIXED + TOC/header; WORKPLAN.md §8 B26 strike; session report (this file)
- [x] **Commit format** — `fix(flow): ...` one commit, code + tests + docs + report

---

_Sign-off: ✅ Ready for supervisor review. B26 fix complete + live-validated on real Flow DOM via both extend and insert operations. Follow-up placeholder-replacement commit will fill `d4fca1a` in SPEC/WORKPLAN/this report with the real hash._
