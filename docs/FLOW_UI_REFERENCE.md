# Google Flow UI Reference (VI + EN) — VERIFIED on Both Locales

**Selector reference document.** Behavioral notes may be stale; see `docs/SPEC.md` for current invariants. Last reviewed against master `408d598` on 2026-05-01.

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
- **L2 ops mint NEW media_id by default; child still INHERITS direct parent's media_id+edit_url for navigation. See `docs/SPEC.md` INV-5.**

## Homepage

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Page title | "Flow" | "Flow" | — |
| New project button | "+ Dự án mới" | "+ New project" | `button:has(i.google-symbols):has-text('add_2')` (see §Homepage New Project Button) |
| Tier badge | "ULTRA" | "ULTRA" | `generic "ULTRA"` |
| Flow TV button | "Flow TV" | "Flow TV" | — |

## Homepage New Project Button

> Verified 2026-04-18 via Chrome MCP DOM probe on `ngoctuandt20` (VI profile).
> See `docs/session-reports/2026-04-18_B18_homepage-locale-fix.md`.

### Ground truth (live DOM)

Probe on `https://labs.google/fx/vi/tools/flow` (automatic VI redirect from
`/fx/tools/flow` because the Google account locale is Vietnamese — URL
`?locale=en` is ignored by Flow):

```html
<button class="sc-16c4830a-1 jsIRVP sc-a38764c7-0 fXsrxE">
  <i class="sc-95c4f607-0 fLjDIG google-symbols undefined"
     font-size="1.125rem" color="currentColor">add_2</i>
  Dự án mới
  <div data-type="button-overlay" class="sc-16c4830a-0 iSFgQn"></div>
</button>
```

Observed body text preview on the homepage (VI profile):

```
Flow
tv
Flow TV
help_outlined
Trung tâm trợ giúp về Flow
more_vert
ULTRA
add_2
Dự án mới
```

### Stable signals (locale-independent)

| Signal | Value | Stability |
|---|---|---|
| Icon ligature text | `"add_2"` | ✅ Material Icon font — same on every locale |
| Icon class | `google-symbols` | ✅ CSS class on `<i>` — stable across releases |
| Tag | `<button>` | ✅ semantic |

### Rejected signals

| Signal | Value | Why rejected |
|---|---|---|
| `aria-label` | EMPTY | Not present |
| `href` | EMPTY | It's a `<button>`, not an anchor |
| `role` attr | (none) | Implicit via `<button>` only; `[role='button']` CSS miss |
| `id`, `data-testid` | (none) | Not set |
| styled-components hashes (`jsIRVP`, `fXsrxE`, …) | rotate per release | Build-dependent |
| Visible text `"Dự án mới"` / `"New project"` | locale-dependent | Breaks on VI profile |

### Uniqueness (homepage context)

Only **one** button on the homepage contains the Material Icon text
`add_2`. Other `i.google-symbols` icons on the homepage carry tokens
like `edit` and `delete` (project-card actions); `add_2` is unique to
the primary CTA.

### Canonical selector (locale-independent)

```python
# Top priority — CSS compound selector
"button:has(i.google-symbols):has-text('add_2')"
```

Engine uses the selector list in `flow/operations/generate.NEW_PROJECT_SELECTORS`.
See `flow/operations/generate.py` for the full priority-ordered list. The
post-login recovery branch of `text_to_video` reuses the same list by
import.

### Pitfalls / Gotchas

1. **URL locale is ignored.** Appending `?locale=en` to `https://labs.google/fx/tools/flow` does NOT force EN rendering — Flow redirects to `/fx/vi/tools/flow` based on the Google account's locale preference. Engine MUST NOT rely on URL locale to predict UI language.
2. **Avoid unconditional Escape on the homepage.** The homepage has no modal overlay in the healthy state (live probe confirms zero visible `[role="dialog"] / [aria-modal="true"] / [class*="overlay"]` elements). Pressing Escape with no overlay can dismiss unrelated focused UI in other states (see B8 LP model-selector lesson). Detect overlay presence first; Escape-as-last-resort.
3. **Do NOT match by styled-components hash.** `sc-16c4830a-1 jsIRVP sc-a38764c7-0 fXsrxE` rotates per Flow release — tying the selector to these tokens breaks on every deploy.
4. **`add_2` is not `add`.** The `<i>` ligature is literally the 5-char string `add_2` — not `add`, `add_circle`, or `add_box`. Matching by `has-text('add')` would match `add_2` (substring) but also any future element whose text starts with `add` — prefer `add_2` exact token.

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

### L2 paywall banner

Free-tier profiles can open the edit URL but Flow replaces L2 editing controls with a paid-tier banner.

| Element | Exact text / selector |
|---|---|
| Banner text | `Video editing is only available for paid subscribers` |
| Banner text selector | `text="Video editing is only available for paid subscribers"` |
| Upgrade CTA text | `Upgrade` |
| Upgrade CTA selector | `button:has-text("Upgrade"), a:has-text("Upgrade")` |
| Canonical engine result | `error_kind="paid_tier_required"`, `error_message="Video editing is only available for paid subscribers"` |

Positive signal is banner text plus Upgrade CTA. Missing Extend/Insert/Remove/Camera buttons are diagnostics only; do not treat missing buttons alone as paid-tier proof.

### Top Bar

| Element | VI ✅ | EN ✅ | DOM Selector |
|---|---|---|---|
| Back | ← | ← | `generic "Back"` / `generic "arrow_back"` |
| Info | ⓘ | ⓘ | `generic "Get more info about this media"` / `generic "info"` |
| Download | (icon-only) | (icon-only) | `button` with child `<i>download</i>` — no `aria-label`, no `title` (see §Download UI) |
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
| **Camera** | camera-move | ❌ No | ❌ No (preset only) | ❌ No |

## Download UI

Two download entry points exist; both produce the same 4 quality options.
Source: live DOM probe 2026-04-19 on `project/7eeb4acf-.../edit/0c22a9f0-...`
(ngoctuandt20, EN locale). Full probe: `docs/session-reports/2026-04-19_download-probe.md`.

### Entry 1 — `/edit/` top-right icon button

- Icon-only `<button>` with child `<i>download</i>` (Material Icons ligature).
- **No `aria-label`, no `title`, no visible "Download"/"Tải xuống" text** on the DOM
  element (the previous doc claim was stale).
- Position: top-right of editor dialog, right of `history`/`info` icons.

### Entry 2 — Project-view tile overflow (⋮)

Hovering any media tile in the project grid reveals 3 icons top-right:

| Icon ligature | a11y name | Purpose |
|---|---|---|
| `favorite` | "Favorite" | toggle favorite |
| `redo` | — | reuse prompt |
| `more_vert` | "More" | open overflow menu (⋮) |

Clicking `more_vert` opens a Radix popover `[role='menu'][data-state='open']`
with 9 items. Item 3 is `<div role='menuitem'>` (not `<button>`) with icon
`download` and `aria-haspopup='menu'` — hovering it expands a submenu inline
(total 13 items) containing the 4 quality options.

### Quality options (identical on both entry points)

| # | textContent | `aria-disabled` | Notes |
|---|---|---|---|
| 1 | `270pAnimated GIF` | (unset) | low-res GIF export |
| 2 | `720pOriginal Size` | (unset) | matches raw video resolution |
| 3 | `1080pUpscaled` | `"false"` | **engine target**; flips to `"true"` while backend upscale pending |
| 4 | `4KUpscaled · 50 credits` | `"false"` | ⚠️ **50 LP credits** — engine MUST NEVER auto-click |

> textContent has no separator between resolution label and sub-label (adjacent
> flex children). Safest unique match for 1080p:
> `button[role='menuitem']:has-text('1080p')` — the literal string `1080p`
> appears in only that item (not in 4K/720p/270p).

### Locale-independent selectors (exact-text, per R-CODE-3)

```
# Entry 1 — /edit/ Download button
btn = page.locator("button").filter(
    has=page.locator("i").get_by_text("download", exact=True)
).first

# Entry 2 — tile ⋮ overflow button
more = tile.locator("button").filter(
    has=page.locator("i").get_by_text("more_vert", exact=True)
).first

# Submenu trigger (tile path) — DIV, not BUTTON
download_mi = page.locator('[role="menu"][data-state="open"] [role="menuitem"]').filter(
    has=page.locator("i").get_by_text("download", exact=True)
)

# 1080p click — anchor on unique '1080p' substring
thousand_eighty = page.locator('[role="menu"][data-state="open"] [role="menuitem"]').filter(
    has_text=re.compile(r'^1080pUpscaled$')  # anchored — excludes '4KUpscaled...'
)
```

### API endpoint — modern UI uses a different path

| Observation | Evidence |
|---|---|
| Engine's assumed endpoint `labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={id}_upsampled` | Returns **HTTP 404** (14 B `text/html`) — endpoint is dead for upsampled variant |
| Non-`_upsampled` variant `?name={true_id}` | Works — this is exactly what `<video>.src` uses on project tiles (720p stream) |
| Modern UI 1080p click | POST `aisandbox-pa.googleapis.com/v1/flow/uploadImage` → status 200, ~1.9s duration |
| 1080p post-click toast | "Frame saved as image" (misleading name — the endpoint may not actually produce a video file) |

### UUID dualism ⚠️

> ⚠️ STALE — do not assume `/edit/{slug}`, `data-tile-id`, and backend download/upscale IDs are a stable 1:1 mapping. See `docs/SPEC.md` INV-5 for chain rules.

Flow surfaces multiple identifiers around the same clip, and current code treats routing slugs vs backend media IDs as separate signals:

| Source | UUID example | Current role |
|---|---|---|
| `/edit/{slug}` URL path | `0c22a9f0-abe4-...` | **Routing slug** used by the SPA |
| `[data-tile-id="fe_id_{slug}"]` and descendant `/edit/{slug}` links | `fe_id_0c22a9f0-...` | UI tile/history slug when the surface is unambiguous; current code cross-checks these and treats mismatches as ambiguous |
| Network `mid` / `<video>.src?name={id}` | `f3471304-f9c1-...` | **Backend media_id** used for download/upscale APIs and preferred by current engine code |

For downloads/upscales, prefer the backend `mid` / `?name=` value. Do not assume the `/edit/` slug or a `data-tile-id` value is the canonical API media ID on every surface.

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

#### Click selector strategy (post-B12: single exact-text strategy)

Implemented in `_click_preset` (`flow/operations/camera.py`). Exactly one strategy:

1. **`page.get_by_text(direction, exact=True).first`** — Playwright exact-text match (case-sensitive, whole node). Native `exact=True` prevents partial-match collisions (direction="Low" does not match a hypothetical "Lower" button).

**Pruned (pre-B12):** `[aria-label='<direction>']` and `page.locator("[role='button']").filter(has_text=re.compile("^<direction>$"))`. Tier1 live-DOM probing (2026-04-17) confirmed both find **0 elements** on production Flow — presets have no `aria-label` and no explicit `role="button"` attribute (Flow uses `<button>` tags; Playwright's CSS `[role='button']` is strict-attribute and does not match implicit roles). Kept in phase-1 as defensive layers, removed in B12 as dead code per spec §1.3.

#### Active state signal (post-click) — computed label color

Flow renders preset buttons with styled-components hash-only class names. No stable keyword (`active` / `selected` / `pressed`) appears anywhere in the DOM, and no `aria-pressed` / `aria-selected` attribute is set in any state. The only semantic, release-stable selection signal is the **computed `color` of the inner label DIV** inside the preset BUTTON:

| State | Label DIV computed color | R+G+B sum |
|---|---|---|
| **Selected** | `rgb(48, 48, 48)` (dim grey — thumbnail is highlighted, label dims for contrast) | **144** |
| **Unselected** | `rgb(255, 255, 255)` (bright white) | **765** |
| **Decision threshold** | `R+G+B < 400` ⇒ selected | halfway between 144 and 765 |

Styled-components hash on the label DIV also flips (`jYmHac` selected vs `hkGUbO` unselected) but hashes may rotate per Flow release; **the color flip is the stable signal**. Do NOT verify via className.

**Verify JS snippet** (used by `_verify_preset_selected`, post-B12):
```javascript
(direction) => {
    const buttons = Array.from(document.querySelectorAll('button'));
    for (const btn of buttons) {
        const labels = btn.querySelectorAll('div');
        for (const lbl of labels) {
            if ((lbl.textContent || '').trim() === direction) {
                const color = getComputedStyle(lbl).color;
                const m = color.match(/rgb\((\d+),\s*(\d+),\s*(\d+)\)/);
                if (!m) return false;
                const sum = (+m[1]) + (+m[2]) + (+m[3]);
                return sum < 400;
            }
        }
    }
    return false;
}
```

Returns `true` when the matching label DIV's color sums below the threshold. Returns `false` when (a) no matching label DIV is found, (b) the color string fails to parse as `rgb(...)`, or (c) the sum is above threshold (unselected). Caller (`_click_preset`) treats `false` as unverified → logs ERROR and returns False; outer `camera_move` raises `RuntimeError("Failed to find camera preset")`.

#### Pitfalls

- **Partial-text matching** — the pre-phase-1 `*:visible` + `re.compile(re.escape(direction), re.IGNORECASE)` regex matched ANY subtree containing the direction as a substring (direction="Low" matched "Lower", "Slow motion", "Follow on Low Priority"). The current `get_by_text(exact=True)` requires full-node equality — no substring match.
- **Case sensitivity** — `exact=True` is case-sensitive. Flow preset labels are Title Case; callers MUST pass the exact canonical label (see `ALL_PRESETS` constant in `flow/operations/camera.py`).
- **className is NOT a state signal** — pre-B12 verify checked `className` for `active|selected|pressed` keywords. Flow's styled-components hashes contain no such keyword; this check always returned false on live DOM and caused the B3 regression. Only `getComputedStyle(label).color` is reliable.
- **Do not key on styled-components hash tokens** — tokens like `jYmHac` or `hkGUbO` do flip between states but are expected to rotate per Flow release. Using them ties the verifier to a specific build.

#### Locale notes (EN vs VI)

- Preset visible text is translated (EN "Dolly in" ↔ VI "Di chuyển ra trước"). Engine callers pass the EN canonical label.
- The surviving `get_by_text(exact=True)` strategy is **EN-only** (matches on the rendered display text). Without `aria-label` available on the DOM, a VI-only profile would require a direction-label map. Out of scope for B12 — recommendation for L2+ camera jobs is to ensure profile locale is EN (matches `flow/navigation.py::detect_locale` expectation).

#### Live-DOM evidence trail

Selector ground truth gathered during Tier1 DOM validation (2026-04-17, project `785d2255-…`) and encoded above:

- Click strategy: only `get_by_text(exact=True)` matches production DOM (2 nodes per preset — the `<button>` container and an inner `<div>` label; `.first` picks the button in document order, the real pointer-click target).
- Verify signal: computed `color` on the label DIV — `rgb(48, 48, 48)` when selected, `rgb(255, 255, 255)` when not. All `aria-*` and `className`-keyword probes miss.
- Detailed probe transcripts: `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §4 + §7 B3.
- Regression fix: `docs/session-reports/2026-04-17_B12_camera-verify-fix.md` (B12).

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

### 2026-05 composer panel structure

Current project-level composer uses a chip-based Radix menu, not a legacy standalone `DropdownMenu` for each field. Open the bottom-right chip, select `VIDEO`, select source sub-tab, force quantity `1`, then choose model.

| Target | Selector / signal |
|---|---|
| Closed chip | `button[aria-haspopup="menu"][data-state="closed"]` near bottom-right composer; text resembles `Image ... x1` or `Video ... x1` |
| Open chip | `button[aria-haspopup="menu"][data-state="open"]` |
| Open panel | `div[role="menu"][data-state="open"].DropdownMenuContent` |
| Video mode trigger | `[id$="-trigger-VIDEO"]` with `data-state="active"` after click |
| Image mode trigger | `[id$="-trigger-IMAGE"]` |
| Frames source sub-tab | `[id$="-trigger-VIDEO_FRAMES"]` inside the open panel |
| Ingredients source sub-tab | `[id$="-trigger-VIDEO_REFERENCES"]` inside the open panel |
| Aspect 16:9 | `[id$="-trigger-LANDSCAPE"]` or chip text containing `crop_16_9` after close |
| Aspect 9:16 | `[id$="-trigger-PORTRAIT"]` or chip text containing `crop_9_16` after close |
| Quantity x1 | `[id$="-trigger-1"]` with `data-state="active"`; reject chip text containing `x2`, `x3`, or `x4` |
| Model menu button | nested `button[aria-haspopup="menu"]` whose text contains a model label and `arrow_drop_down` |
| Credit preview | `a[href*="googleone"]` with text like `5 credits` or `10 credits` |

Video models: `Veo 3.1 - Lite`, `Veo 3.1 - Fast`, `Veo 3.1 - Quality`, and paid `Omni Flash`. Image models: Nano Banana Pro, Nano Banana 2, and Imagen 4. Legacy Lower Priority labels may appear only as fallback labels during rollout.

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

After the user drags on the preview canvas, Flow paints a **selection rectangle**
onto the canvas bitmap itself. There is no DOM overlay element; the engine
cannot detect the bbox post-drag via any DOM query. The verification that
matters is **targeting the correct canvas** — once drag coordinates derive
from the preview canvas rect, Flow accepts the region.

#### Ground truth (live DOM, Tier1 retest 2026-04-17 on L1 project `785d2255-…`; resolved by B11 commit `<B11-COMMIT>`)

The bbox UI has two load-bearing facts, both the opposite of what phase-1 assumed:

**Fact 1 — the preview is a `<canvas>`, not a `<video>`.**
- `document.querySelector('video')` on an L1 project returns a **105×60 card-strip thumbnail**, NOT the main preview.
- The main preview is a `<canvas width=598 height=336>` element, CSS-sized ~479×269, positioned center-screen.
- Proof: `document.querySelectorAll('canvas').length` = 1 visible canvas matching preview bounds; `elementFromPoint(x, y)` for any coordinate inside the visible preview returns `<CANVAS>`.
- Selector for automation: **the largest visible `<canvas>` with `width ≥ 300`**. The 300-px threshold safely excludes card-strip canvases (thumbnails are << 300 px wide). Used as the B11 target.

**Fact 2 — the bbox overlay is canvas-painted, not DOM.**
- After a successful drag, Flow draws the bbox rectangle onto the canvas 2D bitmap via `CanvasRenderingContext2D` calls. There is no `<svg rect>`, no keyword-class div, no `role="region"` element.
- Proof (post-drag, with a visible bbox on screen): `document.querySelectorAll('svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]').length` = **0**. `elementFromPoint` on three points inside the visible bbox — `[[350,280], [420,300], [480,340]]` — all return `CANVAS`.
- Consequence: no DOM query can detect the drawn bbox.

#### B11 implementation — canvas target + pointer-trust verify (commit `<B11-COMMIT>`)

`flow/operations/_base.py::draw_bbox_on_video` — post-B11 contract:

1. **Find the preview canvas** via `page.evaluate`:
   ```js
   Array.from(document.querySelectorAll('canvas'))
     .filter(c => {
       const r = c.getBoundingClientRect();
       return r.width >= 300 && r.height >= 200;
     })
     .reduce((best, c) => {
       const r = c.getBoundingClientRect();
       return (!best || r.width * r.height > best.area) ? {…r, area: r.width * r.height} : best;
     }, null);
   ```
2. **Derive drag coords** from `canvas_rect`, not `video.getBoundingClientRect()`.
3. **Drag** with 5-step interpolation (unchanged from B2 — Flow needs a real gradual drag).
4. **Do not verify post-drag.** Pointer-trust: canvas was found, drag landed on it → Flow accepts. Return True.

**Why pointer-trust and not pixel sampling?** Two reasons:
- Preview canvas plays video frames continuously. `getContext('2d').getImageData(rect)` before/after drag sees natural delta from frame changes alone — impossible to set a noise-floor threshold that reliably distinguishes "bbox painted" from "frame advanced" without hand-tuning per project.
- WebGL-backed canvases throw `SecurityError` on `getImageData` for CORS-tainted contexts; Flow's canvas provenance is unverified. Pointer-trust has zero failure modes from this class.

See `docs/session-reports/2026-04-17_B11_bbox-canvas-fix.md` §7 for the full Option A (pixel sampling) vs Option B (pointer-trust) decision rationale.

**Return-value contract** (post-B11):
- `False` → genuine pre-drag failure: no visible canvas ≥ 300×200, or bbox out-of-range. Caller (`insert.py` / `remove.py`) logs WARNING and continues; Flow falls back to default region.
- `True` → drag completed on the target canvas. Caller proceeds to type prompt (insert) or submit (remove) normally.

#### Pitfalls — don't do these

- ❌ `document.querySelector('video')` — hits the 105×60 thumbnail. This was the B2 bug.
- ❌ Union selector `svg rect, [class*="bbox" i], …` — matches 0 elements (bbox is canvas-painted).
- ❌ Pixel sampling without a before-drag baseline that accounts for video-frame noise — will false-positive on frame advances.
- ❌ A `width ≥ 50` threshold — too permissive; doesn't reliably exclude the 105-px thumbnail if it happens to be the only canvas on a page (e.g. during load).

#### Phase-1 reference (historical; retained for context)

The phase-1 design assumed one of three DOM patterns (SVG `<rect>`, keyword-class div, `role="region"`) and used the union selector `'svg rect, [class*="bbox" i], [class*="selection" i], [class*="region" i], [class*="mask" i]'`. **None of these match Flow.** Tier1 retest disproved the assumption on a live L1 project; B11 superseded it with the canvas-target / pointer-trust approach documented above. See `docs/session-reports/2026-04-17_Tier1_dom-validation.md` §7 B2 for the original live-DOM probe.

### Bbox Coordinate System (engine-side)

- Input: normalized `{x, y, w, h}` in `[0, 1]` relative to the **preview canvas**
  `getBoundingClientRect()` (post-B11 — was video rect pre-B11).
- Validation: any value outside `[0, 1]` → reject (return `False`, log `ERROR`).
- Clamping: if `x + w > 1` → `w = 1 - x` (same for y/h). Flow's canvas coordinates
  do not extend past the canvas rect, so we clip before dragging.
- Pixel conversion: `start = (rect.left + x*rect.width, rect.top + y*rect.height)`;
  `end = (rect.left + (x+w)*rect.width, rect.top + (y+h)*rect.height)`.
- Minimum canvas size: reject if no visible canvas has `width ≥ 300` and
  `height ≥ 200` (preview not loaded or layout collapsed).

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

> ⚠️ STALE — selector flow is still useful, but current engine behavior is: navigate L2+ via stored `edit_url`, activate the target clip when needed, and re-extract final `media_id` after every op. See `docs/SPEC.md` INV-2 and INV-5.

### text-to-video (Level 1)
```
1. Homepage → Click "+ New project" / "+ Dự án mới"
2. New project opens with empty canvas
3. Type prompt in composer
4. Select model (Video tab for video prompts)
5. Click → submit (generic "Create")
6. Wait for generation (blurry gradient + % progress)
7. Result: engine stores `project_url`, final `media_id`, and `edit_url` for downstream jobs
```

### extend-video (Level 2)
```
1. Navigate to stored direct `edit_url` (`/edit/{media_uuid}`)
2. If URL media ≠ target media, activate the intended clip tile before editing
3. Click "Extend" / "Mở rộng" button (or verify Extend is already open)
4. Type prompt in "What happens next?" / "Tiếp theo là gì?" textarea
5. Select LP model (0 credits)
6. Click → submit
7. Wait for generation (blurry gradient + % progress)
8. Result: engine re-extracts final `media_id` after completion; do not assume the input `media_id` is preserved. See `docs/SPEC.md` INV-5.
```

### insert-object (Level 2)
```
1. Navigate to stored direct `edit_url` (`/edit/{media_uuid}`)
2. If URL media ≠ target media, activate the intended clip tile before editing
3. Click "Insert" / "Chèn" button
4. (Optional) Click-drag bbox on video
5. Type description
6. Click → submit
7. Wait for generation
8. Result: engine re-extracts final `media_id` after completion. See `docs/SPEC.md` INV-5.
```

### remove-object (Level 2)
```
1. Navigate to stored direct `edit_url` (`/edit/{media_uuid}`)
2. If URL media ≠ target media, activate the intended clip tile before editing
3. Click "Remove" / "Xoá" button
4. Click-drag bbox on video (REQUIRED)
5. No prompt needed
6. Click → submit
7. Wait for generation
8. Result: engine re-extracts final `media_id` after completion. See `docs/SPEC.md` INV-5.
```

### camera-move (Level 2)
```
1. Navigate to stored direct `edit_url` (`/edit/{media_uuid}`)
2. If URL media ≠ target media, activate the intended clip tile before editing
3. Click "Camera" button
4. Select tab: "Camera motion" or "Camera position"
5. Click preset thumbnail (e.g. "Dolly in", "Center")
6. Click → submit
7. Wait for generation
8. Result: engine re-extracts final `media_id` after completion. See `docs/SPEC.md` INV-5.
```

## Key Observations for Engine

1. **"cards" in engine log** = media item thumbnails in grid view
2. **`cards=0` after clicking extend** = navigated to edit view — NORMAL
3. **Extend doesn't create modal/popup** — changes toolbar highlight + composer placeholder
4. **Model selector disappears** in Insert/Remove/Camera modes
5. **media_id** is ONLY in URL, not visible in UI info panel
6. **L2 ops mint NEW media_id by default; child still INHERITS direct parent's media_id+edit_url for navigation. See `docs/SPEC.md` INV-5.**
7. **Camera mode replaces composer entirely** — no textarea, visual preset grid. Use DOM selectors like `generic "Dolly in"` to click.
8. **History panel** = version timeline. Entry count = operations count. Can verify completion.
9. **"+" button** = attachment/ingredient picker, NOT file upload. Shows project media + upload option.
10. **All Veo models have audio** — `volume_up` icon on every model.
11. **LP model credit verification**: Check credit footer for "0 credits" / "0 tín dụng".
12. **Generation loading state**: Blurry gradient + **% counter** (top-right). Download button grayed out.
13. **History version counting**: Each operation adds 1 entry. Poll count to detect completion.
14. **History entry format**: thumbnail + "Veo" label + prompt text. Active = white border.
15. **Locale-independent selectors**: Use icon names (`keyboard_double_arrow_right`, `add_box`, `ink_eraser`, `videocam`) — same in both EN and VI.
