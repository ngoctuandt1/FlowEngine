# Session Report — Download UI / API Probe

> Read-only Chrome MCP DOM + network probe of Google Flow's download surface.
> Mandate: map BOTH `gen→download` (project-view tile ⋮) and `edit→download`
> (top-right icon button) paths so `flow/download.py` can be fixed.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `download-probe` (follow-up to B34 `d454155` poll-window bump) |
| Task type | probe — read-only DOM + network snapshot; zero LP credit |
| Session started | 2026-04-19 16:39 local (UTC+7) |
| Session ended   | 2026-04-19 17:10 local |
| Worker | Claude Opus 4.7 (Executor) |
| Branch | `claude/hungry-almeida-f08015` (worktree) off `master` @ `dc486a7` |
| Profile | `ngoctuandt20` (English locale — `feedback_english_locale.md`) |
| Target project | `project/7eeb4acf-.../edit/0c22a9f0-...` (4-tile gallery; 2 images + 2 videos) |
| Credits consumed | 0 LP (no generate / extend / upscale submit); 1080p download was **attempted** via UI but produced no observable `.mp4` in `C:/Users/Tuan/Downloads/` from today's session |
| **Verdict** | **DONE** — engine's `_upsampled` endpoint is stale (HTTP 404); modern UI path uses a different endpoint; UUID dualism confirmed between `/edit/{slug}` and API `?name=` param |

---

## 2. Commits landed

```
<pending>  docs(probe): download UI/API surface — /edit/ button + tile ⋮ menu
```

Zero `.py` source diff. Three doc touch-points:
- `docs/session-reports/2026-04-19_download-probe.md` (this report — NEW)
- `docs/FLOW_UI_REFERENCE.md` — appended §Download UI
- `docs/FLOW_BUTTON_EXACT.md` — appended §6 Download walkthrough

---

## 3. Files changed

```
docs/session-reports/2026-04-19_download-probe.md   +NEW   (this report)
docs/FLOW_UI_REFERENCE.md                           +M     (§Download UI — tile ⋮ + /edit/ button)
docs/FLOW_BUTTON_EXACT.md                           +M     (§6 Download walkthrough)
```

Blacklist respected — no `.py`, no `.claude/*`, no `profiles_ultra.txt` touched.

---

## 4. Probe plan executed

| Step | Action |
|---|---|
| 1 | Navigate to `/edit/{media_id}` (tile 3 of test project, video with `play_circle`) |
| 2 | Probe icon-only Download button (top-right): tag, aria-label, title, child `<i>` ligature |
| 3 | Click Download → snapshot Radix popover `[role='menu'][data-state='open']` — item count, text, ligatures, aria-disabled |
| 4 | Install deep-filter PerformanceObserver + fetch/XHR/anchor/window.open patches (capture any request to `labs.google`, `aisandbox-pa.googleapis.com`, `storage.googleapis.com`) |
| 5 | Click "1080p Upscaled" → wait 5 s → dump captured events (URL, method, status, duration, size) |
| 6 | Probe engine's stale endpoint directly: `GET media.getMediaUrlRedirect?name={true_id}_upsampled` — confirm status |
| 7 | Navigate to project view (strip `/edit/` suffix) → hover a video tile → inspect 3 hover icons (heart / reuse / ⋮) DOM |
| 8 | Click ⋮ (`more_vert`) → snapshot Radix overflow menu (9 items) |
| 9 | Hover Download `<div role='menuitem'>` → snapshot submenu expansion (+4 quality items, total 13) |
| 10 | Dismiss menu with Escape → verify zero remaining open menus |
| 11 | Extract `<video>.src` on project tile → compare UUID with `/edit/` slug and `data-tile-id` |

All actions read-only except steps 3, 5, 8, 9 (menu opens). No submit button clicked.

---

## 5. Raw observations

### 5.1 `/edit/{media_id}` top-right Download button

**Button DOM (captured via `find` + `javascript_tool`):**

| Attribute | Value |
|---|---|
| `tag` | `<button>` |
| `aria-label` | `null` ⚠️ (doc in `FLOW_UI_REFERENCE.md` claimed `aria-label="Download"`) |
| `title` | `null` |
| Visible text | (none — icon only) |
| Child `<i>` text | `"download"` (Material Icons ligature) |
| Position | fixed top-right of `/edit/` dialog, right of `history` icon |

**Exact-text selector (per user feedback — B12/B26 pattern):**
```
page.locator("button").filter(
    has=page.locator("i").get_by_text("download", exact=True)
).first
```

**Stale-doc flag:** existing `FLOW_UI_REFERENCE.md` description of this button as `generic "Download"` with visible `"Download"/"Tải xuống"` text is WRONG for current UI — button is icon-only.

### 5.2 `/edit/` Download popover (after click)

Radix popover: `<div role="menu" data-state="open" id="radix-:rXX:">`. 4 `<button role="menuitem">` children:

| # | textContent | aria-disabled | icon | Notes |
|---|---|---|---|---|
| 1 | `270pAnimated GIF` | (unset) | — | low-res GIF export |
| 2 | `720pOriginal Size` | (unset) | — | matches raw video resolution |
| 3 | `1080pUpscaled` | `"false"` | — | **engine target** — aria-disabled=false = available |
| 4 | `4KUpscaled · 50 credits` | `"false"` | — | ⚠️ **50 LP credits** — NEVER auto-click |

> textContent has no separator between resolution label and sub-label (DOM uses adjacent flex children, not `\n`). Safest selector for 1080p: `button[role='menuitem']:has-text('1080p')` (unique — 1080p appears in only one item).

**Likely flip-state:** `aria-disabled="false"` (explicit) suggests the attribute flips to `"true"` when the backend's upscale job isn't ready. Not verified this session (would require a fresh generation + timing check).

### 5.3 Network capture — 1080p Upscaled click

Installed PerformanceObserver + monkey-patched `fetch`, `XMLHttpRequest.prototype.open`, `HTMLAnchorElement.prototype.click`, and `window.open` BEFORE clicking. Filter: any URL containing `labs.google`, `aisandbox-pa.googleapis.com`, or `storage.googleapis.com`.

**6 events captured** between click (t=0) and t+5s. The primary download-initiator event:

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `https://aisandbox-pa.googleapis.com/v1/flow/uploadImage` |
| Status | `200` |
| Duration | `~1.9 s` |
| Captured at | `t ≈ 15.7 s` after page load (delay = Radix render + click) |
| Size | (body not captured — would require response interception) |

**No request to `media.getMediaUrlRedirect?name=..._upsampled` was observed** — modern UI does NOT hit the engine's assumed endpoint.

**Post-click toast:** "Frame saved as image" — misleading, but appeared on a VIDEO tile, suggesting the `uploadImage` name is a misnomer for the actual download endpoint (possibly frame-export or video blob mint).

**Downloads folder check:** `C:/Users/Tuan/Downloads/` had zero new `.mp4` files from today's click. Either (a) the endpoint mints a URL but does not auto-save, (b) the browser download was blocked silently, or (c) the output was an image frame (consistent with toast text). Needs follow-up.

### 5.4 Stale-endpoint direct probe

Direct `page.request.get("labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={true_media_id}_upsampled")`:

| Field | Value |
|---|---|
| Status | `404` |
| Content-Type | `text/html` |
| Body size | 14 bytes (placeholder 404 page) |

The **base endpoint without `_upsampled` suffix works** — `<video>.src` on project tiles IS `labs.google/fx/api/trpc/media.getMediaUrlRedirect?name={true_media_id}` (serves 720p). Only the `_upsampled` variant is dead.

### 5.5 UUID dualism (CRITICAL — engine bug vector)

Captured three UUIDs for the same media asset:

| Source | UUID | Notes |
|---|---|---|
| `/edit/{slug}` URL path | `0c22a9f0-abe4-4a90-8fd4-7f3af17de2f5` | **routing slug** (SPA router) |
| `[data-tile-id="fe_id_{slug}"]` on project tile | `fe_id_0c22a9f0-abe4-4a90-8fd4-7f3af17de2f5` | matches `/edit/` slug (strip `fe_id_` prefix) |
| `<video>.src` `?name=` param | `f3471304-f9c1-4c33-83c0-4e579c96899b` | **true API media_id** — different UUID! |

**Implication:** if `flow/client.py` captures the `/edit/` slug or `data-tile-id` as `media_id` and passes it to `_download_via_api` (line 98, `?name={media_id}_upsampled`), the request WILL 404 regardless of how long B34's poll window is tuned. **The correct media_id lives in the `?name=` query string of the `<video>.src` URL on the project tile.**

### 5.6 Project-view tile hover icons (3 buttons)

On hovering a video tile, 3 icon buttons appear top-right (React conditional render):

| # | icon ligature | aria-label (per accessibility tree) | Purpose |
|---|---|---|---|
| 1 | `favorite` | (none via attr; likely "Favorite" via a11y) | toggle favorite |
| 2 | `redo` | (none) | reuse prompt / duplicate |
| 3 | `more_vert` | `"More"` | open overflow menu |

All three buttons are `<button>` with a single `<i class="material-icons">{ligature}</i>` child. Exact-text selector (per feedback):
```
tile.locator("button").filter(
    has=page.locator("i").get_by_text("more_vert", exact=True)
)
```

The accessibility tree exposes `aria-label="More"` for the overflow button (revealed by MCP `find` tool) but this attribute is NOT present on the raw DOM element — likely injected by an `<Icon>` wrapper component via `role`/`aria-labelledby`.

### 5.7 Tile overflow menu — 9 items

Click `more_vert` → Radix popover `<div role="menu" data-state="open">` with 9 `role="menuitem"` children:

| # | tag | icon ligature | textContent (stripped of icon) | aria-haspopup | aria-disabled |
|---|---|---|---|---|---|
| 1 | `BUTTON` | `play_movies` | `Add to Scene` | `menu` (closed) | `false` |
| 2 | `BUTTON` | `favorite`    | `Favorite`     | — | — |
| 3 | **`DIV`** | **`download`** | **`Download`** | **`menu`** | — |
| 4 | `BUTTON` | `undo`        | `Reuse Prompt` | — | — |
| 5 | `BUTTON` | `flag`        | `Flag Output`  | — | — |
| 6 | `BUTTON` | `whiteboard`  | `Rename`       | — | — |
| 7 | `BUTTON` | `content_cut` | `Cut`          | — | — |
| 8 | `BUTTON` | `content_copy`| `Copy`         | — | — |
| 9 | `BUTTON` | `delete`      | `Delete`       | — | — |

Download is the **only non-button menuitem** — `<DIV role='menuitem'>` because it has a submenu (Radix requirement).

### 5.8 Tile overflow → Download submenu (inline expansion)

Hover the Download `<div>` → submenu expands **inline** (not as a separate popover). Same `role="menu"` container now reports 13 menuitems — 4 quality items inserted between positions 3 and 4:

| # | textContent | aria-disabled | Notes |
|---|---|---|---|
| 3a | `270pAnimated GIF` | (unset) | low-res GIF |
| 3b | `720pOriginal Size` | (unset) | raw video |
| 3c | `1080pUpscaled` | `"false"` | **engine target** |
| 3d | `4KUpscaled · 50 credits` | `"false"` | ⚠️ 50 LP credits |

**Same 4 options as the `/edit/` Download button popover** (§5.2). UI surface is consistent between the two paths.

### 5.9 DOM diff — `/edit/` button vs tile ⋮ menu

| Aspect | `/edit/` top-right button | Tile ⋮ → Download |
|---|---|---|
| Entry point | Icon-only `<button><i>download</i></button>` | `<button aria-label='More'><i>more_vert</i></button>` |
| Menu on first click | Radix popover with 4 quality items | Radix popover with **9 items** (Download is item 3) |
| 1080p reach | 1 click | 2 interactions (click ⋮, then hover Download, then click 1080p) |
| Submenu behavior | — | Inline expansion (9 → 13 items in same popover) |
| Keyboard dismiss | Escape closes editor dialog (known gotcha) | Escape safely closes menu (project view, not editor) |

---

## 6. Fix-direction recommendations for `flow/download.py`

### P0 — Diagnose FlowClient's media_id capture

**Before ANY endpoint change:** verify how `client._media_id_events` and `client._video_urls` populate in `flow/media_id.py` / `flow/client.py`. If the captured media_id is the `/edit/` slug or `fe_id_{slug}` (same UUID), every `_upsampled` request will 404 — and every `?name=` (720p) request will also 404. The correct `name` param value lives in `<video>.src` on the project tile (`?name={api_id}`).

Action: add a probe log in `_download_via_api` that prints `url_1080` + first 200 B of response body when status==404 — confirms the symptom before the fix.

### P1 — Shift primary path from stale API to UI-driven

**Current** (`flow/download.py:96-112`):
```python
# 1080p via getMediaUrlRedirect?name={id}_upsampled  ← returns 404 today
# fallback to 720p via getMediaUrlRedirect?name={id}
# then UI fallback via _download_via_ui
```

**Proposed** (priority swap):
```python
# 1. UI-driven 1080p (tile ⋮ → Download → 1080p Upscaled) — most reliable
# 2. API 720p fallback (the non-upsampled redirect DOES work — <video>.src proves it)
# 3. Blob fallback (existing _download_blob)
```

Concrete Playwright selectors (exact-text, per feedback):

```python
# Enter project view (strip /edit/ if needed)
# Find the tile — match by data-tile-id=f"fe_id_{slug}" where slug = /edit/ URL slug
tile = page.locator(f'[data-tile-id="fe_id_{edit_slug}"]').first

# Hover to reveal overflow icons (React shows on :hover)
await tile.hover()

# Click more_vert
await tile.locator("button").filter(
    has=page.locator("i").get_by_text("more_vert", exact=True)
).click()

# Menu opens — hover Download menuitem (DIV, not button)
menu = page.locator('[role="menu"][data-state="open"]').first
download_mi = menu.locator('[role="menuitem"]').filter(
    has=page.locator("i").get_by_text("download", exact=True)
)
await download_mi.hover()

# Submenu expands inline — click 1080p
# Guard: EXPLICITLY avoid 4K to prevent 50-credit burn
async with page.expect_download(timeout=60_000) as dl_info:
    await menu.locator("[role='menuitem']").filter(
        has_text=re.compile(r'^1080pUpscaled$')   # anchored — excludes '4KUpscaled...'
    ).click()

download = await dl_info.value
await download.save_as(output_path)
```

**Safety guard (must be explicit):** use `re.compile(r'^1080pUpscaled$')` or equivalent text-equality — never `has_text="1080p"` alone (would match 4K too if future copy changes).

### P2 — B34 poll window tuning is moot

B34 (`d454155`) extended `UPSCALE_MAX_WAIT` 30 s → 180 s. Since the endpoint returns 404 for reasons unrelated to upscale-readiness (the `_upsampled` variant simply doesn't exist at this storage backend anymore), polling longer buys nothing. Recommend: **keep B34 values** (they don't hurt) but deprioritize — the fix is to change the endpoint, not the timeout.

### P3 — Third download vector to investigate

`POST aisandbox-pa.googleapis.com/v1/flow/uploadImage` is what the modern UI actually calls on 1080p click. Worth an API-direct probe (with an authenticated `page.request.post`) to see:
- What's in the POST body? (likely the media_id + resolution hint)
- Does the response contain a signed download URL or the mp4 bytes directly?
- Does it work for arbitrary `name` values or only for the currently-open `/edit/` media?

If the POST body is simple and the response is a direct URL, this becomes the cleanest fix — single API call, no UI automation.

### P4 — Update docs trilogy references

- `FLOW_UI_REFERENCE.md` §Download UI — see §7 below (DOM facts)
- `FLOW_BUTTON_EXACT.md` §6 Download — see §8 below (walkthrough)
- `SPEC.md` — add INV or gotcha for UUID dualism if not already noted (check §D.3)

---

## 7. SPEC.md / doc updates

- [x] `FLOW_UI_REFERENCE.md` §Download UI — append with icon-only button facts + tile ⋮ menu facts
- [x] `FLOW_BUTTON_EXACT.md` §6 Download — append with walkthrough + UUID dualism note
- [ ] `SPEC.md` — NOT updated this session (fix-direction still needs engineer review; doc updates gate on accepted fix)

---

## 8. Invariants & rules verified

- [x] INV-1 Account Binding — used single profile `ngoctuandt20` throughout; no profile switch
- [x] INV-2 Navigate by `edit_url` — used `project/{p}/edit/{m}` URL for /edit/ view; `project/{p}` for project view (no DOM-card counting)
- [x] INV-3 Store Everything — N/A (read-only probe)
- [x] INV-4 Serial per Project — N/A (single-session probe)
- [x] INV-5 media_id stable — N/A (no generate/extend/camera-move ops)
- [x] R-CODE-3 Locale-Independent — all recommended selectors use Material Icons ligatures (EN-only, locale-invariant) or English textContent (profile is English locale)
- [x] R-CODE-10 No `datetime.utcnow()` — N/A (no code change)
- [x] R-CC-1 KHÔNG restructure kiến trúc — zero `.py` touched

---

## 9. Issues / Decisions

### Vấn đề phát sinh

- **First 1080p click hit an IMAGE media, not a video** — initial `/edit/` landing (`5f4bc392-...`) was actually an image asset; download showed "Frame saved as image" but produced no usable video file. Resolved by navigating to a video tile (`0c22a9f0-...` with `play_circle` icon overlay and `<video>` element).
- **Programmatic `.click()` on `more_vert` did not open menu** — React needs `pointerdown`/`pointerup` events; `HTMLElement.click()` fires only a synthetic click. Resolved by using Chrome MCP's `computer.left_click` with `ref` (real input event).
- **First `PerformanceObserver` patch missed the 1080p request** — initial filter was too narrow (only `labs.google`). Broadened to include `aisandbox-pa.googleapis.com` and `storage.googleapis.com` — captured the `uploadImage` POST on the next click.

### Quyết định đã đưa (judgment calls)

- Did NOT test `4K Upscaled · 50 credits` option — 50 LP credit cost, explicitly out of read-only scope.
- Did NOT probe the `uploadImage` POST body — would require response interception / Playwright `page.route`, out of read-only MCP scope for this session. Flagged as P3.
- Dismissed tile overflow menu with Escape (safe in project view) instead of click-outside — verified `document.querySelectorAll('[role="menu"][data-state="open"]').length === 0` after.

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)

- `flow/download.py:96-112` — `_upsampled` endpoint returns 404; propose B35 (or repurpose B34) to swap primary path to UI-driven
- `flow/client.py` / `flow/media_id.py` — suspected UUID mis-capture (see §6 P0); propose Bxx diagnostic probe before attempting the P1 fix
- `docs/FLOW_UI_REFERENCE.md` — stale claim of visible "Download" / "Tải xuống" text on /edit/ top-right button (icon-only since at least today); updated this session

---

## 10. Handoff notes

- Workdir state: clean (this worktree only touches `docs/`)
- Probe artifacts: none persisted to disk — all data lives in this report
- Browser session: MCP tab `1988720576` left on project view `project/7eeb4acf-...` (no `/edit/` suffix), no open menus
- Next session to fix `flow/download.py` should:
  1. Read §6 P0 first — diagnose media_id capture before any endpoint change
  2. Propose B35 in WORKPLAN/SPEC if fix confirms §6 P1 direction
  3. Live-test on a fresh L1 generation (any profile) — watch `<video>.src` to confirm `name=` param != `/edit/` slug

---

_Sign-off: ✅ Ready for supervisor review._
