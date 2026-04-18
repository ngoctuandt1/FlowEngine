# FLOW_BUTTON_EXACT — Step-by-step Button Guide

> **What this doc is.** A chronological, button-by-button walkthrough of a full
> FlowEngine job chain on `labs.google/fx/tools/flow` — what you click, what
> you expect to happen, what the DOM looks like underneath, and the selector
> FlowEngine actually uses for that click.
>
> **What this doc is NOT.** A reference table. For side-by-side VI/EN label
> tables and selector catalogs, see [FLOW_UI_REFERENCE.md](FLOW_UI_REFERENCE.md).
> This doc is the "happy-path narration" a human (or a bot author) would want
> open on a second monitor while watching the automation run.
>
> **Source data.** Walkthroughs verified live on two Chrome profiles:
>   * **VI profile** — 2026-04-18 (B22/B23/B24/B25 session)
>   * **EN profile** — 2026-04-19 (B26 EN-regression session)
> Both sessions: DPR=1.25, Windows 11, Chrome via MCP extension, logged in
> with Gemini Advanced account, `labs.google/fx/tools/flow`.

---

## 0. Device-pixel-ratio (DPR) warning — read this first

The test setup used `window.devicePixelRatio = 1.25`. That means:

```
screen_x = css_x * 1.25
screen_y = css_y * 1.25
```

Every CSS coordinate quoted in this doc maps to a **larger** physical-pixel
coordinate. FlowEngine's Playwright code always works in CSS coords
(`page.locator(...).click()` — Playwright handles DPR internally). The
Chrome-MCP extension used for manual verification reports CSS coords in its
snapshots but accepts **screen** coords for `computer.click`. Mixing them up
is the #1 cause of "the click landed 8px below the tab" mistakes.

**Rule:** when FlowEngine does it programmatically, use the selector. When
you're driving Chrome manually (MCP, Playwright Inspector, DevTools),
multiply CSS by 1.25 before passing to a screen-coord tool.

---

## 1. L1 — Create a new project (text-to-video)

### 1.1 Navigate to homepage

* **URL:** `https://labs.google/fx/tools/flow`
* **Expect:** grid of past projects. Top-left corner has a big **New Project** tile.

### 1.2 Click "New Project"

* **Selector FlowEngine uses:** `button:has(i:text-is('add_2'))`
  — the `add_2` Material Icon ligature is locale-independent; the visible
  label is "New Project" (EN) / "Dự án mới" (VI) but we never match on text.
* **Expect:** URL changes to `/project/{uuid}` — this is the **L1 composer**.
  URL pattern:
  ```
  /project/f5148b9f-0e85-4b40-8873-5007490015bd
  ```
  The UUID here is the **project_id**, NOT the media_id. Media IDs
  ("slugs") only show up later on `/edit/{slug}` URLs.

### 1.3 Pick aspect ratio — 9:16 (Portrait)

The aspect chip is a Radix Tabs trigger. B1 (Phase A) fixed this.

* **Look for:** a rounded chip near the top of the composer showing
  the current aspect + an `arrow_drop_down` icon. Default is 16:9.
* **Click 1 (open the dropdown):**
  ```
  button:has(i:text-is('arrow_drop_down')):has(i:text-is('crop_16_9')),
  button:has(i:text-is('arrow_drop_down')):has(i:text-is('crop_9_16')),
  button:has(i:text-is('arrow_drop_down')):has(i:text-is('crop_1_1'))
  ```
  (FlowEngine tries all three — whichever one matches the current state is
  the one present in the DOM.)
* **Expect:** a Radix dropdown panel appears with three tabs: `16:9`, `9:16`, `1:1`.
* **Click 2 (pick 9:16):**
  ```
  [id$='-trigger-PORTRAIT']
  ```
  Similar `-trigger-LANDSCAPE` / `-trigger-SQUARE` for the other two.
  These Radix IDs are stable across locales.
* **Gotcha — manual MCP click.** The 9:16 tab's screen Y-range was
  `538-580` on this DPR. A first-attempt click at `y=588` missed by 8px.
  Click the tab's **vertical center**, not its bottom edge.
* **Expect after pick:** chip collapses, now shows `crop_9_16` + `arrow_drop_down`.
  The composer's preview frame swaps to 9:16.

### 1.4 (Optional) Set output count — "x1"

* **Look for:** a small counter button (same row as aspect chip).
  Default is `x1`. Change only if you want multiple variants per submit.
* **Left alone** for the B26 walkthroughs — every test used x1 so that
  `media_id` extraction was unambiguous.

### 1.5 Focus the prompt editor

The prompt editor is a Slate.js contenteditable.

* **Selector:** `[data-slate-editor='true'][contenteditable='true']`
* **Click it once.** Cursor should blink inside.
* **Gotcha — the model panel is still open.** If you opened the LP model
  selector (see §1.6) and then tried to click the editor while the panel was
  covering it, your click dismisses the panel but does NOT focus the editor.
  **Fix:** click far from the editor first (e.g. page background at
  `(200, 300)` CSS) to close the panel, **then** click the editor again.
  This is what the live session had to do on attempt #1.

### 1.6 (Optional) Pick a model — LP panel

* **Button:** bottom of the composer, shows current model name + chevron.
* **Click → panel opens.** The panel has tabs (`Video`, `Image`, etc.) and
  a list of model rows with credit costs.
* **Pick "Veo 3.1 — Fast"** (or whichever). Click the row.
* **Dismiss the panel by clicking OUTSIDE it** — NOT Escape.
  * Escape closes the whole editor and you lose everything.
  * See `flow/model_selector.py` — this was bug #8.
* **B26-specific regression check:** `flow/model_selector.py._switch_to_video_tab`
  has a `MODE_TITLES` blacklist (`{'Camera', 'Extend', 'Insert', 'Remove',
  'Delete', 'Mở rộng', 'Chèn', 'Xoá', 'Xóa'}`) that prevents the fallback
  `button:has(i:text-is('videocam'))` selector from ever clicking the
  /edit/ Camera mode button. This is the fix for B26 — the fuzzy
  `has-text('videocam')` would otherwise hit the Camera button whose
  innerText is `"videocam\nCamera"`.

### 1.7 Type the prompt

Pure keystrokes into the focused Slate editor. No button press.

Example prompts used this session:
* **L1 #1 (EN):** `a red vintage car driving through a misty forest at dawn, cinematic`
* **L1 #2 (EN):** `a calm zen garden with a stone fountain and green moss`
* **L1 (VI session):** — see prior report.

### 1.8 Submit — the `arrow_forward` button

This is the button B26 is about. On `/project/{uuid}` there is usually
exactly one match, but the selector is locale-independent and exact-text.

* **Selector (canonical, post-B26):**
  ```
  button:has(i:text-is('arrow_forward'))
  ```
* **Why `:text-is`, not `:has-text`.** Material Icons ligatures like
  `arrow_forward_ios` would be substring-matched by `:has-text('arrow_forward')`.
  Exact-match pins us to the real submit button.
* **Why not aria-label.** Locale-dependent AND the live submit button has
  EMPTY aria-label. Don't use `aria-label*='Create' i` etc — the `tests/test_submit.py`
  source trip-wire forbids it.
* **Expect:** URL pushes to `/edit/{slug}` after the generation API call fires.
  FlowEngine captures `media_id` from the API response and stores it with
  `project_url`. See `flow/media_id.py`.

### 1.9 Wait — video materializes

Polling loop, not a button. `flow/wait.py` watches for the generation to
complete (spinner disappears + thumbnail/video element stable).

---

## 2. L2 — The `/edit/{slug}` composer

### 2.1 URL shape

```
/edit/0ca02dbb-17a2-4719-beb5-f38bd96822f3
```

The slug in the `/edit/` URL is **not** the same as media_id — it's a routing
slug. Media_id is the UUID FlowEngine extracted from the generation response
and persisted on the job record (see `flow/media_id.py`). Navigate between
L2 operations using FlowEngine's `edit_url(project_url, media_id)` helper
(`flow/navigation.py`) — **never** by scrolling the DOM card list.

### 2.2 The four mode buttons

Down the left (or bottom) of `/edit/`, four icon buttons pick the L2 mode.
Each has a `title` attribute (localized) AND a Material Icon ligature (global).

| Mode | `<i>` ligature | VI title | EN title |
|---|---|---|---|
| Extend | `keyboard_double_arrow_right` | `Mở rộng` | `Extend` |
| Insert | `add_box` | `Chèn` | `Insert` |
| Remove | `ink_eraser` | `Xoá` / `Xóa` | `Remove` / `Delete` |
| Camera | `videocam` | `Camera` | `Camera` |

**FlowEngine two-pass selector** (see `flow/operations/_base.py
click_action_button`):
1. Try `button[title='{localized title}']` — primary, keyed off
   `_MODE_ICON_BY_TITLE` which contains both VI and EN entries.
2. Fallback to `button:has(i:text-is('{ligature}'))`.

The two-pass matters because when a user has never hovered a button, its
`title` attribute may or may not be in the rendered DOM depending on React
hydration state. The icon fallback is always present.

### 2.3 Active vs inactive visual state

The active mode button has background `rgba(218, 220, 224, 0.25)`; inactive
is `rgba(218, 220, 224, 0.1)`. FlowEngine doesn't probe this directly — it
just clicks the right selector and trusts the URL and placeholder change —
but it's a good visual sanity check when debugging live.

### 2.4 The placeholder trick

Each mode swaps the prompt editor's placeholder text. This is a reliable
"did the click register?" signal:

| Mode | VI placeholder | EN placeholder |
|---|---|---|
| Extend | "Mô tả đoạn tiếp theo" (approx.) | "Describe the next shot" (approx.) |
| Insert | "Mô tả vật thể cần thêm" | "Describe the object to add" |
| Remove | "Mô tả vật thể cần xoá" | "Describe the object to remove" |
| Camera | (no placeholder — preset picker replaces editor) | (same) |

If the placeholder doesn't change after clicking a mode button, the click
missed. Re-probe the selector.

---

## 3. L2-specific walkthroughs

### 3.1 Extend

* **Click:** `button[title='Mở rộng']` (VI) / `button[title='Extend']` (EN)
  — fallback `button:has(i:text-is('keyboard_double_arrow_right'))`.
* **Type into prompt editor:** the shot continuation. E.g.
  `"the car emerges into bright sunlight and the forest thins out"`.
* **Submit:** same `arrow_forward` selector as L1 — but now **two matches exist.**
  The /edit/ page has a disabled decorative `arrow_forward` icon somewhere
  (probably a carousel arrow) in addition to the real submit button.
* **B16 KEEP-7 behavior.** `flow/submit.py click_submit` iterates
  `.nth(0)` → `.nth(1)` → … and skips any match whose `is_enabled()` is
  false. On `/edit/` this means it skips the decorative disabled one at
  `.nth(0)` and clicks the real submit at `.nth(1)`. See
  `tests/test_submit.py::test_click_submit_skip_disabled_first` — this is
  the exact shape of the /edit/ DOM.
* **Scope parameter (B26).** `click_submit(page, scope='[data-scroll-state=\'START\']')`
  prepends the scope to the selector so the match only considers buttons
  inside the composer panel. See `tests/test_submit.py::test_click_submit_scope_prepends_to_selector`.
* **Expect:** new child clip appears in the video strip. URL stays
  `/edit/{same_slug}`. `media_id` is STABLE across extend.

### 3.2 Camera — **this is what B26 is for**

* **Click:** `button[title='Camera']` — same label in both VI and EN —
  fallback `button:has(i:text-is('videocam'))`.
* **B26 relevance.** Before B26, the fallback `button:has(i:has-text('videocam'))`
  (fuzzy) would ALSO match any `button` whose innerText contained the word
  "videocam" — which included the /edit/ Camera mode button itself when the
  tab was already active. Worse, when trying to pick the "Video" tab inside
  the model selector panel, the fuzzy selector's fallback-to-icon probe
  could bleed through to this Camera mode button on /edit/ and cause a
  destructive misclick. The B26 fix pins to `:text-is('videocam')` AND adds
  the `MODE_TITLES` blacklist in `_switch_to_video_tab`.
* **Preset picker.** Instead of a prompt editor, Camera shows a grid of
  preset buttons:

  | Preset (EN) | Preset (VI) |
  |---|---|
  | Dolly in | Di chuyển ra trước |
  | Dolly out | Di chuyển ra sau |
  | Pan left | Quét trái |
  | Pan right | Quét phải |
  | Tilt up | Nghiêng lên |
  | Tilt down | Nghiêng xuống |
  | Pedestal up | ... |
  | Pedestal down | ... |

* **Selector for preset:** `button:has-text('Dolly in')` — text-based here
  is tolerable because presets have no icon and no ambiguity. FlowEngine's
  B3→B12 fix verifies the selected state with
  `getComputedStyle(labelDiv).color → R+G+B<400` (the active preset's
  label color goes dark).
* **Submit:** same `arrow_forward` + B16 iterate.
* **Expect:** URL stable, new clip with the camera move generated.

### 3.3 Insert

* **Click:** `button[title='Chèn']` (VI) / `button[title='Insert']` (EN)
  — fallback `button:has(i:text-is('add_box'))`.
* **Draw bounding box.** Unique to Insert + Remove: the video canvas becomes
  interactive; you must drag-draw a rectangle showing WHERE to insert.
* **Canvas selector (B2→B11 fix).** `canvas` elements ≥ 300×200 — the
  *largest* qualifying canvas wins. Background thumbnail `<canvas>` elements
  are smaller than 300px and correctly skipped.
  ```python
  # flow/operations/_base.py draw_bbox_on_video
  canvases = [c for c in page.locator('canvas') if w >= 300 and h >= 200]
  target = max(canvases, key=lambda c: c.width * c.height)
  ```
* **Pointer-trust verify.** B11 added a verification step that records
  where the pointer actually landed vs where we asked it to — rejects the
  draw if drift > threshold. This caught the canvas-vs-video mismatch that
  B2 couldn't.
* **Type prompt:** e.g. `"a small bronze statue of a frog"`.
* **Submit.** Expect the object to appear inside the drawn bbox region.

### 3.4 Remove

* **Click:** `button[title='Xoá']` or `'Xóa'` (VI) / `button[title='Remove']`
  or `'Delete'` (EN) — fallback `button:has(i:text-is('ink_eraser'))`.
* **Same bbox workflow as Insert.** Draw box around the thing to erase.
* **Type prompt** (optional — Remove often works with just bbox).
* **Submit.**

---

## 4. Chain invariants (NON-negotiable)

These aren't buttons — they're the rules FlowEngine enforces at the worker
layer. Relevant here because a manual walkthrough can violate them and
you'll think it's an automation bug when it's actually your own mistake.

1. **Same profile on every job.** If L1 ran on profile `foo`, L2 MUST run on
   `foo`. Different account → 404 on project_url because the project is
   private to the creating account. Worker's claim loop filters by `profile`.
2. **Navigate by `edit_url`.** Never by "click the 3rd video card". See
   `flow/navigation.py`.
3. **Store everything on completion.** `project_url`, `media_id`, `profile`,
   `generation_id`. Worker's PATCH does this.
4. **Serial per project.** `worker/project_lock.py` — one job per
   `project_url` at a time. Two extends on the same project would race.
5. **`media_id` is re-extracted per op.** Extend / Insert / Remove preserve
   the UUID (Flow updates in-place). **Camera-move mints a NEW `media_id`**
   — confirmed Tier 2 Run 10 2026-04-19 J1→J2 (see SPEC §A.1 INV-5 and
   `docs/E2E_RESULTS_PHASE_A.md`). Engine re-extracts post-op via
   `finalize_operation` and stores the FINAL value; the next job in the
   chain inherits that stored value via B22 claim-time propagation. A NEW
   `media_id` outside of camera-move means you accidentally started a new
   project (wrong-tile click or SPA bounce).

---

## 5. Known gotchas (things that look like bugs but aren't)

### 5.1 Extend-child clip lockout

After an L2 Extend, the UI disables Insert/Remove/Camera on the **extended
child clip** until the user scrolls back to the original parent clip.
Reproduced on both VI and EN sessions. Not a FlowEngine bug — Flow-side
UX. FlowEngine works around this by navigating back to the parent clip's
scroll position before attempting the next L2 on the base media_id.

### 5.2 Two `arrow_forward` buttons on /edit/

Already covered in §3.1 / §1.8. The decorative disabled one at `.nth(0)`
and the real submit at `.nth(1)`. Any "stop at `.first`" or
`click(force=True)` strategy will click the wrong one — see B16 KEEP-7
tests in `tests/test_submit.py`.

### 5.3 Panel-covers-editor focus steal

Opening the model panel and then clicking the prompt editor without first
dismissing the panel: the click dismisses the panel but does not focus the
editor. Click far away first, then click the editor.

### 5.4 Aspect chip: click the tab CENTER

The 9:16 tab's clickable region is shorter than it looks. Click the
vertical middle, not the bottom edge, when driving manually.

### 5.5 Don't use `Escape` to close the LP model panel

Escape closes the whole editor dialog. Click outside the panel instead.
This was bug #8. See `flow/model_selector.py`.

### 5.6 `datetime.utcnow()` is banned

Code-level rule, not a UI rule — but it's a common oversight. Use
`datetime.now(UTC)`. B8 migrated 7 callsites. See R-CODE-10 in SPEC.md.

---

## 6. What to check if a click "does nothing"

Ranked by likelihood from live observation:

1. **The selector matched a disabled/invisible sibling.** Run
   `page.locator(sel).count()` — if >1, you're probably clicking `.first`.
   Use B16 iterate pattern.
2. **The mode panel is open and stealing clicks.** See §5.3.
3. **Wrong locale title.** Check `_MODE_ICON_BY_TITLE` in
   `flow/operations/_base.py` — did the user's Chrome switch profile mid-run
   and end up with a locale that's not in the map?
4. **Manual MCP coord math off by DPR.** Multiply CSS × 1.25 before passing
   screen coords.
5. **React not yet hydrated.** Wait for the specific data attribute (e.g.
   `[data-slate-editor='true']`) rather than a fixed sleep.

---

## 7. Session report cross-reference

For the commit-level and test-level record of the B26 fix and its
regression coverage, see:

* `docs/session-reports/2026-04-19_B26_*.md` — EN-profile live regression
* `docs/session-reports/2026-04-18_B25_*.md` — VI-profile live regression
* `tests/test_submit.py` — 5× B16 KEEP-7 + 2× B26 (scope + source trip-wire)
* `docs/SPEC.md §D.4 B26` — spec line (struck through on fix landing)
* `flow/submit.py` — canonical selector and `click_submit` impl
* `flow/model_selector.py` — `_switch_to_video_tab` blacklist

---

_End of walkthrough. If you're extending this doc, add new sections
chronologically (i.e. where the user would hit the new button in the flow),
not topically. The "how would a human reading along see this happen in
order?" structure is the whole point — if you catch yourself writing a
table, move it to `FLOW_UI_REFERENCE.md` instead._
