# Google Flow UI Reference (VI + EN) — VERIFIED on Both Locales

> Last updated: 2026-04-16 (v4 — EN labels VERIFIED on English Chrome profile)
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
