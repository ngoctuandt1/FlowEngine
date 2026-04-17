# Google Flow UI Reference (VI + EN) — VERIFIED on Both Locales

> Last updated: 2026-04-17 (v5 — Aspect Ratio UI added via B1a research)
> Source: hands-on testing on BOTH Vietnamese + English Chrome profiles
> IMPORTANT: All labels marked ✅ are verified in browser. DOM selectors included for engine use.

## URL Structure

```
Homepage:     /fx/{locale}/tools/flow
Project:      /fx/{locale}/tools/flow/project/{project_uuid}
Media edit:   /fx/{locale}/tools/flow/project/{project_uuid}/edit/{media_uuid}
```

- `locale` = `vi` (Vietnamese profile) or empty (English profile)
- English profile → `labs.google/fx/tools/flow` (no locale in path)
- Vietnamese profile → `labs.google/fx/vi/tools/flow`
- Locale is determined by Google account language — NO language switcher in Flow UI

## Data Model

```
Flow Account (Google login, ULTRA tier)
  └── Project (UUID, timestamp, thumbnail)
        ├── Media Item 1 (video, UUID = media_id)
        ├── Media Item 2 (image, UUID = media_id)  
        └── ...
```

- **Project** = canvas/board containing multiple generated media
- **Media Item** = 1 video or image, has own UUID (`media_id`)
- `media_id` visible ONLY in URL `/edit/{media_uuid}` — NOT in UI info panel
- **Operations (extend/insert/remove/camera) update video IN-PLACE — same media_id**

## Homepage

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Page title | "Flow" | "Flow" | — |
| New project button | "+ Dự án mới" | "+ New project" | — |
| Tier badge | "ULTRA" | "ULTRA" | `generic "ULTRA"` |
| Flow TV button | "Flow TV" | "Flow TV" | — |

## Project Editor (Grid View)

URL: `/fx/{locale}/tools/flow/project/{project_uuid}`

### Composer Bar

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Placeholder | "Bạn muốn tạo gì?" | "What do you want to create?" | `generic "What do you want to create?"` |
| Model chip | "🍌 Nano Banana Pro 📱 x1" | "Video 🖥️ x1" | `button "Videox1"` |
| Submit button | → (arrow) | → (arrow) | `generic "Create"` / `generic "arrow_forward"` |
| Start frame | "Bắt đầu" | "Start" | `generic "Start"` |
| End frame | "Kết thúc" | "End" | `generic "End"` |
| Swap frames | — | — | `generic "Swap first and last frames"` |
| Footer | "Flow có thể mắc sai sót nên bạn hãy xác minh nội dung do công cụ này tạo" | "Flow can make mistakes, so double check it" | `generic "Flow can make mistakes, so double check it"` |
| Empty state | — | "Start creating or drop media" | `generic "Start creating or drop media"` |

### Top Bar

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Back button | ← | ← | `generic "Go Back"` / `generic "arrow_back"` |
| More options | ⋮ | ⋮ | `generic "More options"` / `generic "more_vert"` |
| Search | 🔍 | 🔍 | `generic "Search"` |
| Sort/Filter | ≡ | ≡ | `generic "Sort & Filter"` / `generic "filter_list"` |
| Add Media | + | + | `generic "Add Media"` / `generic "add"` |
| Scenebuilder | 🎬 | 🎬 | `generic "Scenebuilder"` / `generic "play_movies"` |
| Settings | ⚙ | ⚙ | `generic "View Tile Grid Settings"` / `generic "settings_2"` |
| Help | ? | ? | `generic "Product Help"` / `generic "help"` |

### Left Sidebar (View Filters)

| Icon | DOM Selector |
|---|---|
| 田 Grid (all media) | `generic "nav_rail_all_media"` / `generic "dashboard"` |
| 🖥️ Video only | (icon filter) |

### Media Cards
- Videos: thumbnail + ▶ orange play icon + "Veo" watermark
- Each card links to `/project/{project_uuid}/edit/{media_uuid}`
- Cards have `[data-tile-id]` attribute in DOM

## Media Edit View

URL: `/fx/{locale}/tools/flow/project/{project_uuid}/edit/{media_uuid}`

### Top Bar

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Back | ← | ← | `generic "Back"` / `generic "arrow_back"` |
| Info | ⓘ | ⓘ | `generic "Get more info about this media"` / `generic "info"` |
| Download | "📥 Tải xuống" | "📥 Download" | `generic "Download"` / `generic "download"` |
| Show history | "Hiện nhật ký" | "Show history" | `generic "Show history"` / `generic "history"` |
| Hide history | "Ẩn nhật ký" | "Hide history" | `generic "Hide history"` |
| Done | "Xong" | "Done" | `generic "Done"` / `generic "check"` |

### ⓘ Info Panel (media metadata)

| Field | VI ✅ | EN ✅ |
|---|---|---|
| Title | prompt text | prompt text |
| Created date | "Ngày tạo 16 thg 4, 2026" | "Created Apr 16, 2026" |
| Edited date | — | "Edited Apr 16, 2026" |
| Model | "Veo 3.1 - Fast" | "Veo 3.1 - Lite [Lower Priority]" |
| Aspect ratio | 📱 9:16 | 🖥️ 16:9 |
| Duration | "Thời lượng video: 8s" | "Video length: 8s" |
| media_id | NOT shown — only in URL | NOT shown — only in URL |

### Action Buttons (Bottom Toolbar)

| VI ✅ | EN ✅ | DOM Selector | Icon |
|---|---|---|---|
| **>> Mở rộng** | **Extend** | `button "Extend"` | `keyboard_double_arrow_right` |
| **⊞ Chèn** | **Insert** | `button "Insert"` | `add_box` |
| **✏ Xoá** | **Remove** | `button "Remove"` | `ink_eraser` |
| **🎥 Camera** | **Camera** | `button "Camera"` | `videocam` |

### Composer Placeholders Per Mode

| Mode | VI ✅ | EN ✅ |
|---|---|---|
| Extend (default) | "Tiếp theo là gì?" | "What happens next?" |
| Insert | "Mô tả nội dung bạn muốn thêm, không bắt buộc: nhấp và kéo ở trên để chỉ định vị trí" | "Describe what you'd like to add, optional: click-and-drag above to specify location" |
| Remove | "Nhấp và kéo để chọn toàn bộ nội dung bạn muốn xoá khỏi video." | "Click-and-drag to fully select what you want to remove from the video." |
| Camera | (no composer — preset picker replaces it) | (same) |
| Project grid | "Bạn muốn tạo gì?" | "What do you want to create?" |

### Action Button Details

| EN Button | Job Type | Has Model Selector | Has Prompt | Has Bbox |
|---|---|---|---|---|
| **Extend** | extend-video | ✅ Yes | ✅ Yes ("What happens next?") | ❌ No |
| **Insert** | insert-object | ❌ No | ⚠️ Optional | ✅ Yes (click-drag) |
| **Remove** | remove-object | ❌ No | ❌ No (bbox only) | ✅ Yes (click-drag) |
| **Camera** | camera-control | ❌ No | ❌ No (preset only) | ❌ No |

## Camera Mode (2 tabs)

Clicking "Camera" replaces composer with visual preset picker.

### Tab 1: Camera motion

| DOM Selector | VI ✅ | EN ✅ |
|---|---|---|
| `tab "Camera motion"` | "Chuyển động của camera" | "Camera motion" |
| `generic "Dolly in"` | "Di chuyển ra trước" | "Dolly in" |
| `generic "Dolly out"` | "Di chuyển lùi ra xa" | "Dolly out" |
| `generic "Orbit left"` | "Xoay quanh từ phải sang trái" | "Orbit left" |
| `generic "Orbit right"` | "Quay phải" | "Orbit right" |
| `generic "Orbit up"` | "Xoay quanh lên" | "Orbit up" |
| `generic "Orbit low"` | "Xoay quanh thấp" | "Orbit low" |
| `generic "Dolly in zoom out"` | "Đưa camera vào gần và thu nhỏ" | "Dolly in zoom out" |
| `generic "Dolly out zoom in"` | "Đưa camera ra xa và phóng to" | "Dolly out zoom in" |

### Tab 2: Camera position

| DOM Selector | VI ✅ | EN ✅ |
|---|---|---|
| `tab "Camera position"` | "Vị trí camera" | "Camera position" |
| `generic "Center"` | "Giữa" | "Center" |
| `generic "Left"` | "Trái" | "Left" |
| `generic "Right"` | "Phải" | "Right" |
| `generic "High"` | "Cao" | "High" |
| `generic "Low"` | "Thấp" | "Low" |
| `generic "Closer"` | "Gần hơn" | "Closer" |
| `generic "Further"` | "Xa hơn" | "Further" |

- Each preset = animated thumbnail preview
- Submit: ⓘ info + → arrow button
- Camera submit DOM: `generic "See how many credits this generation will use"` + `generic "Create"`

### Camera Preset Selection & Active State

The engine clicks a preset by name (`direction: str`, EN label — see `flow/operations/camera.py::CAMERA_MOTION_PRESETS` / `CAMERA_POSITION_PRESETS`) and must verify it became **active** before submit, otherwise the submit will run with the default preset and the user's direction is silently ignored.

#### Entry point + tab
- Camera button: `button "Camera"` (or icon fallback `button:has(span:has-text('videocam'))`).
- After click, composer is replaced by a 2-tab preset picker.
- Tab switch: `[role='tab']:has-text('Camera motion')` or `'Camera position')`.

#### Preset element (from ARIA tree inspection)
- Rendered as `generic "Dolly in"` (ARIA role `generic` with accessible name = direction label). In DOM this is typically a `<button>` or `<div role="button">` with an `aria-label` matching the EN direction + an animated thumbnail child.
- EN profile: `aria-label` and visible text both = EN direction (`"Dolly in"`, `"Center"`, …).
- VI profile: visible text is translated ("Di chuyển ra trước"); **`aria-label` is assumed to retain the EN label** (consistent with how Radix/Material components usually set stable aria-labels), but this is **not yet confirmed on live DOM** (see "Known unknowns" below).

#### Click selector strategy (locale-independent first, text-match last)
Implemented in `_click_preset` (`flow/operations/camera.py`) — three strategies, ordered most-reliable → most-fragile. All three require `_verify_preset_selected` to pass before returning True.

1. **`[aria-label='<direction>']`** — exact attribute match. Most reliable because aria-label is typically set from a stable i18n key, not the display string. Works across locales if Flow follows common Radix/Material conventions.
2. **`[role='button']` filtered by exact-text regex** `^<direction>$` (anchored). Prevents partial match (e.g. direction="Low" no longer matches button "Lower" / "Lowering").
3. **`page.get_by_text(direction, exact=True)`** — Playwright exact-text match (case-sensitive, whole node). Last resort because it is locale-dependent (only works on EN profile). Still safer than the previous `*:visible + regex` which ignored case and matched anywhere in the subtree.

#### Active state signal (post-click)

After clicking a preset, Flow highlights the selected thumbnail. Exact indicator not confirmed on live DOM — the verify helper checks the union of common SPA conventions:

| Signal | Selector / attribute |
|---|---|
| Pressed button (Material / Radix) | `aria-pressed="true"` on the preset element |
| Selected option (tablist / listbox) | `aria-selected="true"` |
| Class-based (CSS Modules / Tailwind) | `className` matches `/active\|selected\|pressed/i` |
| Parent wrapper marker | Parent `className` matches `/active\|selected/i` |

**Verify JS snippet** (used by `_verify_preset_selected`):
```javascript
(direction) => {
    const els = document.querySelectorAll('[aria-label], [role="button"], button');
    for (const el of els) {
        const text = el.textContent?.trim() || '';
        const label = el.getAttribute('aria-label') || '';
        if (text === direction || label === direction) {
            if (el.getAttribute('aria-pressed') === 'true') return true;
            if (el.getAttribute('aria-selected') === 'true') return true;
            const cls = el.className || '';
            if (/active|selected|pressed/i.test(cls)) return true;
            const parent = el.parentElement;
            if (parent && /active|selected/i.test(parent.className || '')) return true;
        }
    }
    return false;
}
```

Returns `true` if ANY signal matches. Returns `false` if no match — caller treats as unverified and either falls through to the next strategy or returns False (caller logs ERROR and the operation raises `"Failed to find camera preset"` in the outer `camera_move` handler).

#### Pitfalls

- **Partial-text matching** — the prior `*:visible` + `re.compile(re.escape(direction), re.IGNORECASE)` regex matched ANY subtree containing the direction as a substring. For direction="Low" this would match buttons "Lower", "Slow motion", "Follow", as well as footer text like "Follow on Low Priority". The current strategies all require full-text equivalence (aria-label exact OR `^<direction>$` anchored regex OR Playwright `exact=True`). No substring match anywhere in the selector chain.
- **Case sensitivity** — all three strategies are now case-sensitive. Flow preset labels are Title Case; callers must pass the exact canonical label (see `ALL_PRESETS` constant).
- **Click-without-verify** — previously a successful click returned True immediately. Now strategies 1 and 2 can "click something that matched my selector but isn't actually a preset button" (e.g. if aria-label collides with another UI element) — the verify step catches this and the function falls through to the next strategy.
- **Dark-theme class names** — Flow's class names may be hashed (`_abc123_active`). The class keyword regex (`/active|selected|pressed/i`) matches even hashed names as long as the keyword appears anywhere in the className string. If Flow uses purely hashed names (no keyword), verification fails; see Known unknowns.

#### Locale notes (EN vs VI)

- Preset visible text is translated (EN "Dolly in" ↔ VI "Di chuyển ra trước"). Engine callers pass the EN canonical label.
- Strategy 1 (aria-label) is **expected** to work on both locales (aria-label stable); Strategy 2 is text-based but uses the direction string as-is — works on EN, fails on VI unless aria-label also covers (via Strategy 1 fallback). Strategy 3 (`get_by_text`) works only on EN.
- Recommendation for L2+ camera jobs: ensure profile locale is EN (matches `flow/navigation.py::detect_locale` expectation). A VI-only profile would require a direction map (out of scope for B3).

#### Ground truth (live DOM, Tier1 retest 2026-04-17 on L1 project `785d2255-…`)

The "Known unknowns" from phase-1 research were confirmed or refuted against live DOM. **The current `_click_preset` + `_verify_preset_selected` implementation is broken against real Flow UI.** See SPEC.md §D.4 B12 and `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B3 for the full evidence trail.

**What's actually on the page:**

| Item | Ground truth |
|---|---|
| Preset tag | `<button>` element — implicit role `button`, NO explicit `role="button"` attribute |
| `aria-label` | **Absent on all 15 presets** (8 motion + 7 position). No `data-preset-name` either. |
| Class tokens | Styled-components hashes only, e.g. `sc-16c4830a-1 hxjMEo ... byyZkY` — NO `active\|selected\|pressed` keyword anywhere |
| `aria-pressed` / `aria-selected` | Not set, in any state |
| Parent className | Also styled-components hash (`sc-2384ceab-7 jrdoRH`) — no keyword |
| Real state signal | **`getComputedStyle(labelDivInsideButton).color`** — selected = `rgb(48, 48, 48)` (dim grey, because the thumbnail is highlighted), unselected = `rgb(255, 255, 255)` (bright white). Label styled-components hash also differs (`jYmHac` selected vs `hkGUbO` unselected) but hashes may rotate per Flow release; color is the stable signal. |

**Impact on the three click strategies (live-DOM behavior):**

1. **`[aria-label='<direction>']`** — finds **0 elements**. aria-label is absent.
2. **`[role='button']` + anchored regex** — finds **0 elements**. Playwright's CSS `[role='button']` requires the attribute literally; it does not match implicit button roles. (This is different from `page.get_by_role('button')`, which would match.)
3. **`page.get_by_text(direction, exact=True)`** — **works**: finds and clicks the preset, Flow accepts (preview animates, submit enables).

**Impact on `_verify_preset_selected`:** all four signals return false on a correctly-selected preset. The helper returns False on every call, strategy #3 falls through, `_click_preset` exhausts all strategies, and `camera_move` raises `RuntimeError("Failed to find camera preset: {direction}")`. **Every camera-move job currently fails hard.**

**Fix direction (see SPEC.md §D.4 B12):** swap the union verify for a single `getComputedStyle(label).color` check (threshold: R+G+B < 150 ⇒ selected); keep the 3-strategy click chain (partial-match defense remains sound even though strategies 1+2 find 0 elements today — cheap insurance against future Flow refactors that might add aria or explicit roles).

**Locale notes (unchanged from phase-1):** visible text is translated (EN "Dolly in" ↔ VI "Di chuyển ra trước"); engine passes EN. Strategy #3 is EN-only. Without aria-label available, VI profile camera-move requires a direction map — out of scope for B12.

## Model Selector (Dropdown)

Appears in **Extend** mode and **project-level** composer only.

### Models (verified on EN profile ✅)

| DOM Text | Credits | Audio |
|---|---|---|
| `"Veo 3.1 - Lite"` | 5 credits | 🔊 `volume_up` |
| `"Veo 3.1 - Fast"` | 5 credits | 🔊 `volume_up` |
| `"Veo 3.1 - Quality"` | costs credits | 🔊 `volume_up` |
| **`"Veo 3.1 - Lite [Lower Priority]"`** | **0 credits** | 🔊 `volume_up` |
| **`"Veo 3.1 - Fast [Lower Priority] (leaving 5/10)"`** | **0 credits** | 🔊 `volume_up` |

### Credit Footer Text

| State | VI ✅ | EN ✅ | DOM |
|---|---|---|---|
| Normal model | "Quá trình tạo sẽ tốn 5 tín dụng" | "Generating will use 5 credits" | `generic "Generating will use"` + `link "5 credits"` |
| LP model | "Quá trình tạo sẽ tốn 0 tín dụng" | "Generating will use 0 credits" | same pattern, link text = "0 credits" |

### Model Selector DOM Structure
```
menu
  menuitem → button → generic "volume_up" + generic "Veo 3.1 - Lite"
  menuitem → button → generic "volume_up" + generic "Veo 3.1 - Fast"
  menuitem → button → generic "volume_up" + generic "Veo 3.1 - Quality"
  menuitem → button → generic "volume_up" + generic "Veo 3.1 - Lite [Lower Priority]"
  menuitem → button → generic "volume_up" + generic "Veo 3.1 - Fast [Lower Priority] (leaving 5/10)"
  generic "Generating will use" → link "5 credits"
```

## Prompt Input (Composer editor)

> Verified 2026-04-17 on EN profile — Slate.js editor. See B1a session report §Bonus.

### Primary selector

```
[data-slate-editor="true"][contenteditable="true"]
```

On a fresh T2V project page, there is **exactly one** Slate editor in the viewport (composer at bottom). Extend/Insert modes have their own editor with the same selector but different placeholder text.

### DOM attributes

| Attr | Value |
|---|---|
| `role` | `"textbox"` |
| `contenteditable` | `"true"` |
| `data-slate-editor` | `"true"` |
| `aria-multiline` | `"true"` |
| `placeholder` | — (NOT a placeholder attribute) |
| `aria-label` / `aria-placeholder` | — (NONE) |

### Editor content structure

When user types `"hello world"` the inner DOM becomes:

```
<p data-slate-node="element">
  <span data-slate-node="text">
    <span data-slate-leaf="true">
      <span data-slate-string="true">hello world</span>
    </span>
  </span>
</p>
```

One `<p data-slate-node="element">` per paragraph (Shift+Enter creates new paragraph).

### Empty-state detection (canonical)

**Do NOT rely on `innerText`** — when the editor is empty, `innerText` is `"What do you want to create?\n﻿\n"` (placeholder label + zero-width BOM + newlines). This is the PLACEHOLDER rendered inline, not real content.

**Canonical empty check:**
```python
empty = await page.locator('[data-slate-string="true"]').count() == 0
# Equivalent: await page.locator('[data-slate-placeholder="true"]').count() > 0
```

**Reading real prompt text:**
```python
nodes = await page.locator('[data-slate-string="true"]').all()
prompt_text = "\n".join([await n.text_content() for n in nodes])
```

### Placeholder text by mode

| Mode | EN placeholder | VI placeholder |
|---|---|---|
| Project-level T2V | "What do you want to create?" | "Bạn muốn tạo gì?" |
| Extend | "What happens next?" | "Tiếp theo là gì?" |
| Insert | "Describe what you'd like to add, optional: click-and-drag above to specify location" | (Vietnamese equivalent) |
| Remove | "Click-and-drag to fully select what you want to remove from the video." | (Vietnamese equivalent) |

The placeholder lives in `[data-slate-placeholder="true"]` child node of the editor. DO NOT use it to identify the mode — use the active toolbar button (Extend/Insert/Remove) or composer-level markers.

### Typing interaction

**Golden path (works for all Slate editors):**
```python
editor = page.locator('[data-slate-editor="true"][contenteditable="true"]').first
await editor.click()              # focus editor
await page.keyboard.press("Control+a")  # select-all (clears placeholder)
await page.keyboard.press("Delete")     # delete selection → truly empty
await page.keyboard.type(prompt, delay=15)  # type character-by-character
```

- **`page.fill()` does NOT work reliably** — Slate needs real key events to trigger its controller; `fill()` sets innerText but the model doesn't update.
- **`page.type()` / `keyboard.type()` works** — per-character input events trigger Slate's onChange.
- `delay=15` (15ms/char) avoids races on fast machines.

### Critical gotchas

1. **Enter submits, does NOT insert newline.** Plain Enter on the composer triggers the same handler as the ➜ submit button (starts generation). **Never include raw `\n` in prompts.** If a multi-line prompt is required, use **Shift+Enter** (sends `Shift` modifier + Enter to insert paragraph break).

   Observed regression: `keyboard.type("line1\nline2")` caused Enter handling — line1 vanished (submitted/cleared), line2 remained as fresh text.

2. **Ctrl+A selects the Slate document**, including the placeholder's zero-width BOM. Pair with `Delete` to get a truly empty editor.

3. **Ctrl+A + Delete MUST be two separate `press()` calls.** Playwright `page.keyboard.press("Control+a")` followed by `page.keyboard.press("Delete")` works (verified). If a harness ever batches the two keys into a single call, the `Ctrl` modifier may not persist — observed failure mode: only the character at cursor position gets deleted (1 char loss, placeholder NOT restored, slate still reports non-empty).

   Quick self-check after clear: `await page.locator('[data-slate-string="true"]').count() == 0`. If non-zero, re-press.

4. **Fastest clean-clear is the in-UI button.** When the editor is non-empty, a `button` with innerText `"close\nClear prompt"` (a 32×32 icon) appears next to the composer. Clicking it resets the editor atomically (no keyboard state to manage). Selector: `button:has-text("Clear prompt")`. Safe fallback when the keyboard-clear approach fails.

5. **Placeholder inside `innerText`** — the zero-width BOM `﻿` (U+FEFF) appears in `innerText` when empty. Strip/ignore it. Better: use `[data-slate-string="true"]` node presence.

6. **Fresh Slate uses `data-slate-zero-width`** attribute variants for empty blocks — do not confuse with content.

7. **Character fidelity verified for single-line typing.** `keyboard.type("hello world flow test prompt", delay=15)` → 28 chars in, 28 chars out, verified via `[data-slate-string="true"]` nodeText. No IME / autocomplete drops observed on EN profile.

### Auxiliary composer buttons (around the editor)

All sit within the composer container (bottom ~30% of viewport).

| Button | Icon text | aria-label / Selector pattern | When visible | Purpose |
|---|---|---|---|---|
| Swap first/last frame | `swap_horiz` | `button` with innerText starting `"swap_horiz"` | Always in T2V | Swap Start/End frame inputs |
| Clear prompt | `close` | `button` with innerText `"close\nClear prompt"` | Only when editor NOT empty | Click to clear editor (faster than Ctrl+A+Delete) |
| Submit / Create | `arrow_forward` | `button` with innerText `"arrow_forward\nCreate"` | Always; visually disabled when empty | Submit job |
| Start frame upload | "Start" | `generic "Start"` | Always in T2V | Drop an image to use as first frame |
| End frame upload | "End" | `generic "End"` | Always in T2V | Drop an image to use as last frame |

### Engine-ready prompt-entry helper

```python
async def _type_prompt(page, prompt: str, timeout_sec: float = 15.0) -> None:
    """Focus composer, clear any existing text, type prompt. Raises RuntimeError on failure."""
    # Guard: strip raw newlines — Enter submits, causing silent drop of lines
    if "\n" in prompt:
        logger.warning("Prompt contains newlines — replacing with spaces to avoid Enter=submit")
        prompt = prompt.replace("\n", " ")

    editor = page.locator('[data-slate-editor="true"][contenteditable="true"]').first
    await editor.wait_for(state="visible", timeout=int(timeout_sec * 1000))
    await editor.click()
    await asyncio.sleep(0.2)

    # Clear any placeholder-state zero-width or leftover text.
    # Two SEPARATE press() calls — some harnesses drop the Ctrl modifier when
    # batching "Ctrl+a" and "Delete" together, yielding a 1-char deletion bug.
    await page.keyboard.press("Control+a")
    await page.keyboard.press("Delete")
    await asyncio.sleep(0.1)

    # Self-check: if clear failed, fall back to the in-UI "Clear prompt" button.
    if await page.locator('[data-slate-string="true"]').count() > 0:
        clear_btn = page.locator('button:has-text("Clear prompt")').first
        if await clear_btn.is_visible(timeout=500):
            await clear_btn.click()
            await asyncio.sleep(0.1)

    await page.keyboard.type(prompt, delay=15)

    # Verify: at least one slate-string node should now exist
    count = await page.locator('[data-slate-string="true"]').count()
    if count == 0:
        raise RuntimeError(f"Prompt did not land in editor (empty after type) — len={len(prompt)}")
    logger.info("Prompt typed OK (%d slate-string nodes)", count)
```

## Model Chip Panel (Composer — project-level T2V)

> Verified 2026-04-17 on EN profile. See B1a session report.

Clicking the model chip at the bottom-right of the composer opens a **single Radix `DropdownMenuContent`** panel that bundles ALL generation options: media type, source type, aspect ratio, quantity, model, credits.

### Chip button (trigger)

| Attr | Value |
|---|---|
| Tag | `button` |
| `aria-haspopup` | `"menu"` |
| `aria-expanded` | `"true"` when panel open, `"false"` when closed |
| `data-state` | `"open"` / `"closed"` |
| `id` | `radix-:rXX:` (dynamic hash — NEVER hardcode full id) |
| Text pattern | `"<MediaType> <aspect-icon> x<qty>"` e.g. `"Video crop_16_9 x1"` |

**Engine MUST read chip text AFTER closing panel** to verify aspect ratio was set. The icon substring (`crop_9_16` vs `crop_16_9`) is the canonical confirmation.

### Panel container (opened)

| Attr | Value |
|---|---|
| Selector | `div[role="menu"][data-state="open"].DropdownMenuContent` |
| `aria-labelledby` | references chip id |

Panel contains 4 Radix `tablist` rows + 1 model dropdown button + 1 credits link, in this exact order:

1. Media Type tablist (`IMAGE` / `VIDEO`)
2. Source Type tablist (`VIDEO_FRAMES` / `VIDEO_REFERENCES`) — only in Video mode
3. **Aspect Ratio tablist** — contents differ by media type (see §Aspect Ratio UI)
4. Quantity tablist (`1` / `2` / `3` / `4`)
5. Model dropdown (`button[aria-haspopup="menu"]` with text like `"Veo 3.1 - Lite [Lower Priority] arrow_drop_down"`)
6. Credits link (`a[href*="googleone"]` with text `"N credits"`)

All tablists share class `flow_tab_slider_trigger` on tab buttons and `sc-eb68e7f-1 boDrty` on the tablist wrapper. Distinguish by `id` suffix only.

## Aspect Ratio UI

> Verified 2026-04-17 on EN profile. DOM-level B1a research. See `docs/session-reports/2026-04-17_B1a_aspect-ratio-research.md`.

### Where it lives

Aspect ratio is a Radix `tablist` INSIDE the Model Chip Panel (see above). There is **no standalone aspect-ratio button on the main toolbar** — the engine must open the model chip panel first.

Only present in **project-level T2V composer** (homepage → New project). **NOT available in extend/insert/remove/camera modes** (those inherit aspect ratio from the source media — ratio is fixed once video generated).

### DOM structure (open panel)

```
div[role="menu"][data-state="open"].DropdownMenuContent
└── … (other rows)
    └── div[role="tablist"].sc-eb68e7f-1.boDrty
        ├── button[role="tab"][id$="-trigger-PORTRAIT"]
        │     data-state="active"|"inactive"  aria-selected="true"|"false"
        │     innerText: "crop_9_16\n9:16"
        └── button[role="tab"][id$="-trigger-LANDSCAPE"]
              data-state="active"|"inactive"
              innerText: "crop_16_9\n16:9"
```

### Video mode — 2 options

| Ratio | `id` suffix | Icon name | Default in Video |
|---|---|---|---|
| 9:16 (portrait) | `PORTRAIT` | `crop_9_16` | ❌ |
| 16:9 (landscape) | `LANDSCAPE` | `crop_16_9` | ✅ **default** |

### Image mode — 5 options

Switching the Media Type tab to `IMAGE` replaces the aspect tablist with a 5-option variant:

| Ratio | `id` suffix | Icon name |
|---|---|---|
| 16:9 | `LANDSCAPE` | `crop_16_9` |
| 4:3 | `LANDSCAPE_4_3` | `crop_landscape` |
| 1:1 | `SQUARE` | `crop_square` |
| 3:4 | `PORTRAIT_3_4` | `crop_portrait` |
| 9:16 | `PORTRAIT` | `crop_9_16` |

### Active state — verify signal

For any ratio tab:

| Attribute | Active | Inactive |
|---|---|---|
| `data-state` | `"active"` | `"inactive"` |
| `aria-selected` | `"true"` | `"false"` |

**Locale-independent:** icon names and `id` suffixes are English-only but stable code tokens — they do NOT change on VI profile.

### Interaction flow (T2V only)

```
1. Open composer chip (click element with class flow_video_chip / aria-haspopup="menu" at bottom-right)
   → wait for [role="menu"][data-state="open"]
2. Ensure Video tab is active: check [id$="trigger-VIDEO"][data-state="active"]
   If not, click [id$="trigger-VIDEO"] and wait 100ms
3. Click target ratio tab:
   - "9:16" → click [id$="trigger-PORTRAIT"]
   - "16:9" → click [id$="trigger-LANDSCAPE"]
   - "1:1" → **NOT SUPPORTED IN VIDEO** — log warning, keep default 16:9
4. Verify: re-read target tab, assert data-state="active"
5. Close panel: click outside (e.g. page.click on composer area, NOT Escape)
6. Post-close verify: read chip innerText, assert substring "crop_9_16" (9:16) or "crop_16_9" (16:9)
```

### Pitfalls / Gotchas

1. **Playwright `.click()` via `page.evaluate(el => el.click())` does NOT trigger Radix state change** — Radix uses pointerdown events. Use `Locator.click()` (real mouse event) only.
2. **Radix id prefix `radix-:rXX:` is per-render hash** — NEVER hardcode `radix-:r2f:-trigger-PORTRAIT`. Always use attribute-ends-with selector: `[id$="-trigger-PORTRAIT"]` or contains: `[id*="trigger-PORTRAIT"]`.
3. **State resets on Media Type switch** — switching IMAGE→VIDEO reverts aspect ratio to 16:9 default. Set ratio AFTER ensuring Video tab is active.
4. **State persists across panel close/reopen** — same page, same media type → ratio stays.
5. **Escape closes too much** — pressing Escape dismisses the composer itself in some modes. Use click-outside (click on empty canvas area) to dismiss the chip panel.
6. **No 1:1 for video** — Flow Video does NOT offer square aspect. Engine should reject `aspect_ratio="1:1"` or log warning and default to 16:9.
7. **Chip icon is the ground truth post-close** — the chip innerText updates to reflect current ratio (`crop_9_16` / `crop_16_9`). Use it as the single source of truth for verification.
8. **No aspect ratio in L2+ modes** — extend/insert/remove/camera inherit from source video. Do not attempt `_set_aspect_ratio` on L2+ jobs.

### Recommended engine selectors (locale-independent)

```python
# Open chip panel
chip = page.locator('button[aria-haspopup="menu"][data-state="closed"]').filter(
    has_text=re.compile(r"video.*x\d", re.IGNORECASE)
).first
await chip.click()
await page.locator('[role="menu"][data-state="open"]').wait_for(timeout=3000)

# Ensure Video tab is active (avoid setting image ratios on video)
video_tab = page.locator('[id$="-trigger-VIDEO"]').first
if await video_tab.get_attribute("data-state") != "active":
    await video_tab.click()

# Click target ratio (video mode)
RATIO_IDS = {"9:16": "PORTRAIT", "16:9": "LANDSCAPE"}
suffix = RATIO_IDS.get(ratio)
if not suffix:
    logger.warning("Aspect ratio %r not supported for video — using default", ratio)
    return
tab = page.locator(f'[id$="-trigger-{suffix}"]').first
await tab.click()

# Verify active state
await page.wait_for_function(
    f"""() => document.querySelector('[id$="-trigger-{suffix}"]')?.getAttribute("data-state") === "active" """,
    timeout=2000,
)

# Close panel (click-outside)
await page.locator("body").click(position={"x": 100, "y": 100})

# Post-close verify via chip text
chip_text = await page.locator('button[aria-haspopup="menu"]').first.inner_text()
expected_icon = "crop_9_16" if ratio == "9:16" else "crop_16_9"
assert expected_icon in chip_text, f"Chip did not reflect ratio: {chip_text!r}"
```

## History Panel

| State | VI ✅ | EN ✅ | DOM |
|---|---|---|---|
| Button (closed) | "Hiện nhật ký" | "Show history" | `generic "Show history"` |
| Button (open) | "Ẩn nhật ký" | "Hide history" | `generic "Hide history"` |

- Opens right sidebar with version timeline
- Each entry: thumbnail + "Veo" label + prompt text + "Reuse text prompt" button
- Chronological order: oldest at top, newest at bottom
- Active version: white border highlight
- Entry count = total operations performed on this media

## Bbox Tool (Insert/Remove modes)
- Icon: dashed-square (⊡) on left side of canvas
- Click-drag on video to define region
- Normalized coordinates (0-1) → engine sends as `bbox: {x, y, w, h}`

### Bbox Overlay UI

After the user drags on the video canvas, Flow renders a **selection rectangle** over
the video preview. The engine needs this as the verification signal that the drag
landed on the canvas — no detection means the drag missed.

#### Ground truth (live DOM, Tier1 retest 2026-04-17 on L1 project `785d2255-…`)

The phase-1 "overlay rendering patterns" table below turned out not to match Flow's actual UI. **The bbox overlay is not a DOM element at all — it is painted onto the preview canvas bitmap.** Two independent problems with the current `draw_bbox_on_video` helper (`flow/operations/_base.py`):

**Problem 1 — wrong target element.**
- The helper targets `document.querySelector('video')`. On an L1 project this returns a **105×60 card-strip thumbnail**, not the main preview.
- The main preview is a `<canvas width=598 height=336>` element, CSS-sized ~479×269, positioned center-screen.
- Proof: `document.querySelectorAll('canvas').length` = 1 visible canvas matching preview bounds; `elementFromPoint(x, y)` for any coordinate inside the visible preview returns `<CANVAS>`.
- Effect: the drag `start`/`end` are computed against the thumbnail rect, so Playwright drags on the wrong element — the main canvas never receives pointer events.

**Problem 2 — overlay is canvas-painted, not DOM.**
- After a successful drag, Flow draws the bbox rectangle onto the canvas 2D bitmap via `CanvasRenderingContext2D` calls. There is no `<svg rect>`, no keyword-class div, no `role="region"` element.
- Proof (post-drag, with a visible bbox on screen): `document.querySelectorAll('svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]').length` = **0**. `elementFromPoint` on three points inside the visible bbox — `[[350,280], [420,300], [480,340]]` — all return `CANVAS`.
- Effect: the current union-selector verify step always returns false, regardless of whether the drag actually landed. The "Bbox drawing failed or unverified" WARNING is emitted on every bbox-using job.

Runtime consequence: every `insert-object` / `remove-object` job silently uses Flow's default region.

#### Fix direction (see SPEC.md §D.4 B11)

- **Target the canvas, not the video tag.** Replace `document.querySelector('video')` with the largest visible `<canvas>` (filter by rect dimensions; the card-strip uses its own `<video>` so the preview canvas is unambiguous once you switch target types).
- **Verify via pixel sampling**, not via DOM query. Capture `getContext('2d').getImageData(sampleRect)` before drag and after `mouseup`; compare mean RGBA. A non-trivial delta (threshold TBD during implementation) confirms bbox was painted. Sample-rect should be inside the expected bbox coordinates.
- **Alternative / complement**: intercept the network request Flow fires when a region is committed — B11 implementer should capture one real request first to decide which approach is more reliable. The network hook in `FlowClient` already has the plumbing for this.

The union-selector verify should be removed, not extended — adding more DOM selectors cannot detect a canvas-painted shape.

#### Phase-1 reference (for historical context; do not use as implementation guide)

The original phase-1 design assumed one of three DOM patterns (SVG `<rect>`, keyword-class div, `role="region"`) and used the union selector `'svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]'`. **None of these match Flow.** Tier1 retest disproved the assumption on a live L1 project. See `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2 for the full evidence.

### Bbox Coordinate System (engine-side)

- Input: normalized `{x, y, w, h}` in `[0, 1]` relative to the video's
  `getBoundingClientRect()`.
- Validation: any value outside `[0, 1]` → reject (return `False`, log `ERROR`).
- Clamping: if `x + w > 1` → `w = 1 - x` (same for y/h). Flow's canvas coordinates
  do not extend past the video rect, so we clip before dragging.
- Pixel conversion: `start = (rect.left + x*rect.width, rect.top + y*rect.height)`;
  `end = (rect.left + (x+w)*rect.width, rect.top + (y+h)*rect.height)`.
- Minimum video size: reject if `width < 50` or `height < 50` (video not loaded or
  collapsed).

## Engine Selector Mapping (DOM selectors for automation)

### Button detection (works for BOTH EN and VI)
```
# Extend — use button name
button "Extend"          (EN)
button text "Mở rộng"   (VI)
# Fallback: aria-label containing "extend", icon "keyboard_double_arrow_right"

# Insert
button "Insert"          (EN)
button text "Chèn"      (VI)
# Icon: "add_box"

# Remove
button "Remove"          (EN)
button text "Xoá"       (VI)
# Icon: "ink_eraser"

# Camera
button "Camera"          (EN + VI same text)
# Icon: "videocam"

# Submit/Create
generic "Create"         (EN + VI) — aria text
generic "arrow_forward"  (icon)

# Done
generic "Done"           (EN)
generic "check"          (icon)

# Download
generic "Download"       (EN)
generic "download"       (icon)
```

### Recommended selector strategy (locale-independent)
```python
# BEST: Use icon names (same in both locales)
page.locator('[class*="keyboard_double_arrow_right"]')  # Extend icon
page.locator('[class*="add_box"]')                       # Insert icon
page.locator('[class*="ink_eraser"]')                    # Remove icon
page.locator('[class*="videocam"]')                      # Camera icon
page.locator('[class*="arrow_forward"]')                 # Submit icon

# GOOD: Use button text with both locales as fallback
for text in ("Extend", "Mở rộng"):
    btn = page.locator("button").filter(has_text=text)

# ALSO GOOD: Use ARIA generic text (EN only but stable)
page.locator('generic:has-text("Create")')
```

## Flow Operations (Step-by-Step)

### text-to-video (Level 1)
```
1. Homepage → Click "+ New project" / "+ Dự án mới"
2. New project opens with empty canvas
3. Type prompt in composer
4. Select model (Video tab for video prompts)
5. Click → submit (generic "Create")
6. Wait for generation (blurry gradient + % progress)
7. Result: new media_id in the project
```

### extend-video (Level 2)
```
1. Navigate to project URL → see grid of media cards
2. Click target video card → opens edit view (/edit/{media_uuid})
3. Click "Extend" / "Mở rộng" button
4. Type prompt in "What happens next?" / "Tiếp theo là gì?" textarea
5. Select LP model (0 credits)
6. Click → submit
7. Wait for generation (blurry gradient + % progress)
8. Result: SAME media_id — video updated in-place, new version in history
```

### insert-object (Level 2)
```
1. Navigate to /edit/{media_uuid}
2. Click "Insert" / "Chèn" button
3. (Optional) Click-drag bbox on video
4. Type description
5. Click → submit
6. Wait for generation
7. Result: SAME media_id — updated in-place
```

### remove-object (Level 2)
```
1. Navigate to /edit/{media_uuid}
2. Click "Remove" / "Xoá" button
3. Click-drag bbox on video (REQUIRED)
4. No prompt needed
5. Click → submit
6. Wait for generation
7. Result: SAME media_id — updated in-place
```

### camera-control (Level 2)
```
1. Navigate to /edit/{media_uuid}
2. Click "Camera" button
3. Select tab: "Camera motion" or "Camera position"
4. Click preset thumbnail (e.g. "Dolly in", "Center")
5. Click → submit
6. Wait for generation
7. Result: SAME media_id — updated in-place
```

## Key Observations for Engine

1. **"cards" in engine log** = media item thumbnails in grid view
2. **`cards=0` after clicking extend** = navigated to edit view — NORMAL
3. **Extend doesn't create modal/popup** — changes toolbar highlight + composer placeholder
4. **Model selector disappears** in Insert/Remove/Camera modes
5. **media_id** is ONLY in URL, not visible in UI info panel
6. **Operations do NOT create new media_id** — update in-place. URL stays same. Each op adds 1 history entry. VERIFIED: Extend → Insert → Remove all kept same media_id.
7. **Camera mode replaces composer entirely** — no textarea, visual preset grid. Use DOM selectors like `generic "Dolly in"` to click.
8. **History panel** = version timeline. Entry count = operations count. Can verify completion.
9. **"+" button** = attachment/ingredient picker, NOT file upload. Shows project media + upload option.
10. **All Veo models have audio** — `volume_up` icon on every model.
11. **LP model credit verification**: Check credit footer for "0 credits" / "0 tín dụng".
12. **Generation loading state**: Blurry gradient + **% counter** (top-right). Download button grayed out.
13. **History version counting**: Each operation adds 1 entry. Poll count to detect completion.
14. **History entry format**: thumbnail + "Veo" label + prompt text. Active = white border.
15. **Locale-independent selectors**: Use icon names (`keyboard_double_arrow_right`, `add_box`, `ink_eraser`, `videocam`) — same in both EN and VI.
