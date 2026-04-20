# Session Handoff — 2026-04-20

**Supervisor:** Claude (tech lead) + Codex (junior dev)
**Profile:** `ngoctuandt20` (Ultra, English locale)
**Final master commit:** `ef09a13`

## TL;DR

- **Image 2K/4K UI-upscale path: DONE v1** — env-gated, live-verified both qualities, composer-mode persistence handled.
- **Multi-image iteration fix:** image UI branch now iterates all `media_ids` instead of dropping extras.
- **Test suite: 153 → 192 passed** (+39 new unit tests covering the image upscale state machine).
- **L2 insert/remove live-verified** (both produce files), but a media_id extraction bug was discovered and parked.

---

## 1. PRs merged (in order)

| # | Commit | One-liner |
|---|---|---|
| 24 | `18a1e74` | Image 2K/4K UI path + async busy/done state machine (reverts video→2K regression from `7990707`) |
| 25 | `f930739` | Composer-chip fallback for `_switch_to_image_output` (persisted Video mode) |
| 26 | `429dad6` | Unified `COMPOSER_MENU_SELECTORS` across 5 chip-icon variants |
| 27 | `849c39d` | +36 unit tests for image upscale path (closes PR #24 parked test debt) |
| 28 | `ef09a13` | Iterate all `media_ids` in image UI upscale branch (don't drop extras) |

Plus docs-only commit `548632d` between #26 and #27.

---

## 2. How the image 2K/4K path works

**Env gate:** `FLOW_IMAGE_QUALITY=original|2k|4k` (default `original`).
Dispatch in `flow/download.py:_requested_image_quality()`: explicit `"2k"`/`"4k"` wins over env; explicit `"original"` forces original; empty explicit consults env; unrecognized explicit falls to `original`.

**Flow image `/edit/` menu** (newline-separated labels, different from video):
```
1K\nOriginal size
2K\nUpscaled
4K\nUpscaled
```

**Regexes** (`flow/upscale.py`):
- 2K: `^2K\s*Upscaled$`, `^2KUpscaled$`, `\b2K\b`
- 4K: `^4K\s*Upscaled$`, `^4KUpscaled$`, `\b4K\b`
- `\s` matches the `\n` between size token and descriptor.

**Async state machine in `upscale_and_download_image()`:**
1. Open composer chip → click 4K/2K menu item.
2. Race `expect_download` vs `_popup_state` polling for ~15s:
   - Immediate download → save + return path.
   - `busy` state → `_wait_upscale()` up to `UPSCALE_TIMEOUT_SEC` → `done` → close toast → re-click → `expect_download` for real file.
   - `done` state immediately (cached) → re-click + download.
   - `failed` state → close toast → retry outer loop.
3. After 2 exhausted attempts → return `None` → caller falls back to API original download.

**File naming:** `{prefix}_{quality}_{ts}.{ext}` where ext preserves `.jpg`/`.png`/`.webp` from suggested filename or falls back to magic-byte detection via `_content_type_from_bytes()`.

**Video menu stays unchanged:** `1080pUpscaled` primary, `1080p` fallback, save as `{prefix}_1080p_{ts}.mp4`. Do NOT confuse with image menu — see memory `feedback_image_upscale_2k_4k.md`.

---

## 3. Composer-chip robustness

**Problem:** Flow persists the last-used composer mode (Image/Video/Frames/Ingredients) per account. After a video job, the chip icon is `crop_9_16`/`crop_16_9`; after an image job, it's `crop_square`/`crop_landscape`/`crop_portrait`. An image t2i run on a worker that just finished video would hit an unrecognized chip icon and raise.

**Fix (PR #26):** `COMPOSER_MENU_SELECTORS` in `flow/operations/frames_to_video.py` now lists all 5 icon variants. `flow/operations/image.py:_locate_image_chip` imports and iterates the shared list. `_open_composer_menu` logs a diagnostic dump of visible `button[aria-haspopup='menu']` texts when all selectors miss.

**Entry path (`_switch_to_image_output` in `image.py`):**
1. Try inline Image tab (`[role='tab']:text-is('Image')` or `[role='tab']:has(i:text-is('image'))`).
2. If not visible, open composer chip via unified selectors, then find Image tab inside `[role='menu'][data-state='open']` by text or icon.

---

## 4. Live-test results

**2K t2i (earlier session, pre-PR #24 merge):** 1 file, `t2i_2k_1776693136.jpg` (3.99 MB).

**4K t2i (post PR #26 merge):**

| Job ID | File | Size | Path taken |
|---|---|---|---|
| `5966f88f` | `t2i_4k_1776697296.jpg` | 12.0 MB | inline Image tab fast path |
| `db13913f` | `t2i_4k_1776697408.jpg` | 9.4 MB | chip-menu fallback (✅ PR #26 fix exercised) |
| `db171e0b` | `t2i_4k_1776697519.jpg` | 12.0 MB | chip-menu fallback |

All 3 logged: `menu items: ['1K\nOriginal size', '2K\nUpscaled', '4K\nUpscaled']` + `Clicked 4k item with regex ^4K\s*Upscaled$`.

**L2 chain (same profile, same project):**

| Job ID | Type | Input media_id | Output media_id | File | Size |
|---|---|---|---|---|---|
| `7299b989` | L1 t2v | — | `d406e882` | `t2v_1080p_1776702733.mp4` | 3.4 MB |
| `1e22cf62` | L2 insert | `d406e882` | `632c087f` | `ins_1080p_1776702887.mp4` | 5.4 MB |
| `532c1d64` | L2 remove | `d406e882` | `632c087f` ⚠️ | `rm_1080p_1776703025.mp4` | 3.4 MB |

Both L2 jobs navigated correctly to `/edit/d406e882` (L1's edit_url) as input. Project_url preserved across all 3 jobs. Project-lock serialization worked (insert completed before remove claimed).

---

## 5. 🐛 Known bug (parked): L2 media_id extraction

**Symptom:** L2 insert-object and L2 remove-object both reported the SAME output media_id `632c087f-6777-42ac-84db-34fcc3621d9b`, despite producing different output files (5.4 MB vs 3.4 MB — so genuinely different videos).

**Leading theory:** `extract_media_id(page.url)` called after the op is picking up Flow SPA's "last-viewed media" state rather than the actual new media that was just generated. Since remove ran directly after insert on the same project, the SPA may have left insert's media as the "visible" one when remove finished, and remove's extraction grabbed that instead of its own fresh media.

**Evidence:**
- Insert output file size 5.4 MB ≠ L1's 3.4 MB → real new video was generated.
- Remove output file size 3.4 MB (same coincidence as L1) → real new video.
- But both stored `media_id=632c087f`, breaking any potential L3 chain.

**Severity:** MEDIUM — chain-breaking for L3+ built on insert/remove results, but L2 itself works (files saved, single-level chains usable).

**Spec mismatch:** `CLAUDE.md` §4 says "Insert/remove preserve in-place" — but live behavior shows both mint new media_ids AND the extraction is unreliable. The spec wording may also need updating once the extraction is fixed.

**Parked for:** dedicated live-probe session — inspect page.url trajectory during insert+remove, find the correct URL segment to extract from, probably need to capture the new media_id from network events (like the generate path does) instead of the URL.

---

## 6. Test coverage (tests/test_image_upscale.py — 39 tests)

- `_requested_image_quality` — 9 parametrized cases (explicit × env interactions).
- `_save_image_download` — 6 cases (.jpg/.jpeg/.png/.webp + magic-byte fallback).
  - **Finding:** `.jpeg` is rewritten to `.jpg` (not preserved). Test asserts current behavior.
- `_content_type_from_bytes` — 4 cases (PNG/JPEG/WebP/unknown).
- `_DONE_RE` / `_BUSY_RE` regex — matches for video + image phrases, non-match for empty + error.
  - **Finding:** `_BUSY_RE` does NOT cover generic `"in progress"` — only `upscaling`, `processing 2k`, `processing 4k`. Broaden if Flow copy changes.
- `upscale_and_download_image` async state machine — 7 cases (immediate / busy→done / done-toast / failed-retry / exhausted / menu-miss / timeout).
- Multi-image iteration (PR #28) — 3 cases (all-success / partial-success / all-fail→fallback).

All mocked (no real filesystem, no network, no Playwright).

---

## 7. Memory updates

- **`feedback_image_upscale_2k_4k.md`** — added 4K live-verification section with PR #26 note.
- No new memory files created; composer-chip behavior documented in session reports + inline code comments.

---

## 8. Parked items (next sessions)

### HIGH priority
- **L2 media_id extraction bug** (§5 above) — blocks any L3 chain on top of insert/remove.

### LOW priority
- **`.jpeg` preservation** — currently rewritten to `.jpg`; harmless but surprising.
- **`_BUSY_RE` broadening** — if Flow changes busy-toast copy beyond `upscaling`/`processing`.
- **Async busy/done toast fallback live-exercise** — implemented and unit-tested, but Flow always served 4K immediately in live runs; no live proof the `busy → _wait_upscale → re-click` path works end-to-end.
- **Camera-move L2 chain** — not tested this session; SPEC says "mints NEW on early-chain, preserves on deep-chain" — may have same extraction issue as insert/remove.
- **Extend-video multi-generation** — not tested this session.

---

## 9. Worker / state at session end

- **Master HEAD:** `ef09a13`
- **Worker:** PID 51960, running `python -m worker.main` with `FLOW_USE_BASE_PROFILE=1 WORKER_PROFILES=ngoctuandt20` (no `FLOW_IMAGE_QUALITY` — default `original`)
- **Server:** up on `http://127.0.0.1:8080`
- **Active profile:** `ngoctuandt20` available, not locked to any job
- **Downloads dir:** `D:\AI\FlowEngine\downloads\` — contains 7 output files from this session (4 images + 3 videos)

---

## 10. Directives observed

From user this session:
- "video thì 1080 thôi, 2k 4k là đang nói ảnh" — video stays 1080p; 2K/4K is images only.
- "1 2 3 4 giao cho codex để tiết kiệm token" — aggressive delegation, claude-tech-lead/codex-junior-dev model.
- "cho codex nó debug nhiều test case vào" — codex writes comprehensive tests, not just one happy-path smoke.
- "xong hết rồi thì làm 1 file tổng hợp cho đỡ mất context" — this file.

Long-standing (from memory):
- Senior-reviewer code quality bar.
- Mode A Chrome launch (no stealth flags).
- Never blanket-kill chrome.exe (profile-filtered only).
- Cross-check memory before pasting playbook prompts.
