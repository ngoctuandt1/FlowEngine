# FLOW_ENGINEERING_NOTES — Master Reference (Supervisor's Own Notes)

> **Purpose.** Single-pass consolidation of what I (supervisor) have actually
> understood after reading the session reports, trilogy docs, and source
> code. Not a replacement for `SPEC.md` / `FLOW_BUTTON_EXACT.md` / session
> reports — those are authoritative for their scopes. This file is a
> **navigation aid**: when I forget something mid-session, I check here
> first before grep/read.
>
> **Written:** 2026-04-19 (after reading 30+ session reports).
> **Last updated:** 2026-04-20 (B32-B38 + Run 12-19 + 2 new memories).
> **Maintainer:** supervisor (Claude Opus 4.7 — 1M context).
> **Scope:** notes and cross-references only. No SPEC/decision-making
> authority. Update this file when my mental model shifts, not when the
> code changes (code already has SPEC for that).

---

## 1. What FlowEngine Is (30-second recap)

Playwright-driven browser automation for `labs.google/fx/tools/flow` (Google Flow / Veo 3.1).

```
frontend (JS SPA) ↔ server (FastAPI + SQLite) ↔ worker (claim loop + Playwright) ↔ Chrome profiles
```

**Worker shape (the key pattern):**
```python
while True:
    job = await remote_api.claim(profiles=available_profiles)
    async with ProjectLock(job.project_url):
        async with FlowClient(profile=job.profile) as client:  # fresh Playwright context
            await client.page.goto(edit_url(job.project_url, job.media_id))  # B27 direct goto
            result = await dispatcher.dispatch(job, client)
            await remote_api.update(job.id, result)
```

Each job = fresh browser context + DB-backed metadata recovery. Verified live: `discrete-2job-verify_en.md`.

---

## 2. The Two URLs

| URL | Shape | Meaning |
|---|---|---|
| `project_url` | `https://labs.google/fx/tools/flow/project/{project_id}` | Project library grid (parent of all ops on this project) |
| `edit_url` | `{project_url}/edit/{media_id}` | Editor view for ONE specific video (media_id) |

**Key distinction.** `project_id` (in URL) and `media_id` (in `/edit/{…}`) are different UUIDs. `project_id` is stable per project; `media_id` identifies a specific clip within the project.

**B38 discovery (2026-04-19 Run 17d/17e):** the slug after `/edit/` is NOT always the captured API `media_id` and NOT always the `fe_id_{X}` attribute on tiles. It's the SPA's internal **routing slug** resolved by the router's `pushState`. Consequence:
- `page.goto(/edit/{media_id})` may bounce back to `/project/{pid}` root.
- Captured `fe_id_{X}` may be a third UUID that also differs from the routing slug.
- **Reliable path:** click the tile (`[data-tile-id^="fe_id_"]`) — SPA handles slug resolution internally. See `flow/upscale.py::_ensure_edit_view`. Saved as memory `feedback_flow_edit_nav_click.md`.

**Locale caveat** (`feedback_english_locale.md`): Flow SPA rewrites `/fx/tools/flow/…` → `/fx/vi/tools/flow/…` on VI-locale Google accounts. Direct `page.goto(edit_url)` on VI profile lands on a Next.js catch-all. **All Flow accounts MUST be configured EN at myaccount.google.com/language before first engine run.** The engine uses canonical `/fx/tools/flow/…` URLs; selectors are locale-independent, URLs are not.

**Profile-auth caveat (2026-04-19 Run 19 block):** unauthenticated visitors to `/edit/{X}` get served the **Flow marketing landing page** — "Where the next wave of storytelling happens" / "Create with Flow". The worker may hang silently or trigger false-positive DOM errors (POLICY on footer "Privacy Policy"). Root cause: worker-profile SSO cookies not present / expired / stripped by clone-to-temp. See memory `feedback_profile_full_reset.md` for the prescribed full-delete + fresh sign-in recovery.

---

## 3. The 5 Invariants (latest state)

| # | Invariant | Status | Enforced where |
|---|---|---|---|
| INV-1 | Account Binding — 1 project = 1 Google account = 1 Chrome profile; chain stays on same profile | ✅ | `server/db/job_store.py::claim_next_job` — profile filter |
| INV-2 | Navigate by `edit_url` only — no DOM card counting, no `video_index` | ✅ | `flow/operations/_base.py::navigate_to_edit` (B27: direct goto primary) |
| INV-3 | Store Everything after op — `project_url` / `media_id` / `edit_url` / `profile` / `output_files` / `completed_at` | ✅ | `_base.py::finalize_operation` + B22 claim-time propagation |
| INV-4 | Serial per Project — 2 jobs same `project_url` never concurrent | ✅ | `worker/project_lock.py` + `claim_next_job` `NOT EXISTS active` clause |
| INV-5 | `media_id` re-extracted per op. **Extend mints NEW always**; **camera mints NEW on early-chain (L2 off L1), preserves on deep-chain (L3+ after B32 tile-activation)**; insert/remove preserve in-place (empirical 2026-04-19 Run 10 + Run 12) | ✅ (revised 4×) | Chain inherits parent's FINAL `media_id` via B22; B30 walks up past extend ancestors for `media_id` only; `edit_url` comes from direct parent (B32 split) |

**INV-5 revision history:**
1. Original: "stable across all L2 ops" (Phase A wording)
2. 2026-04-19 `3d7b884`: camera mints new; extend/insert/remove preserve
3. 2026-04-19 Tests 2/3/4 + B30: extend ALSO mints new (empirical J1 → J2 extend = new uuid); insert/remove still TBD (empirically unverified because B28 blocks L3-on-extend chain)
4. 2026-04-19 post-B32 + Run 12 (B33): camera is **context-dependent** — mints NEW on early-chain (L2 direct off L1 t2v, per Run 10), preserves on deep-chain (L3+ where B32 tile-activation pins URL to active clip, per Run 12 J5). Insert/remove confirmed preserve on deep-chain (Run 12 J3/J4). Engine handles both camera modes correctly via `finalize_operation` re-extraction — no engine-side fix needed, INV-5 wording just needs the nuance.

---

## 4. The ~12 Buttons (selector catalogue)

### Homepage
| Mục | Selector | Notes |
|---|---|---|
| New project | `button:has(i:text-is('add_2'))` | B18. Icon-first, locale-independent. VI label "Dự án mới", EN "New project", icon `add_2` same across locales. |

### T2V composer (project view, `/project/{id}`)
| Mục | Selector | Notes |
|---|---|---|
| Aspect chip (open panel) | `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down')):has(i:text-is('crop_16_9 \| crop_9_16 \| crop_1_1'))` | B19. Icon-ligature, NOT model-name regex (model name varies: "Video", "🍌 Nano Banana Pro", etc). |
| Aspect tab trigger | `[id$='-trigger-PORTRAIT \| LANDSCAPE \| VIDEO']` | B1. Radix per-render ID hash — use attribute-ends-with. |
| Aspect chip pre-open guard | check `data-state="open"` before clicking | B19 second fix. If chip already open (from model selector flow leak), skipping click avoids toggle-close. |
| Prompt editor (Slate) | `[data-slate-editor='true'][contenteditable='true']` | All modes. |
| Extend prompt editor (scroll-state) | `[data-scroll-state='START'] [data-slate-editor='true']` | B15 Method 1. More precise than "last slate editor" heuristic. |
| Model chip (open dropdown) | `button[aria-haspopup='menu']:has(i:text-is('arrow_drop_down'))` then `.filter(has_text=re.compile(r"^Veo", re.IGNORECASE))` | B20. Exact-text via icon + anchored regex. |
| Model LP item | Menu item with text matching `re.compile(r"Lower Priority", re.IGNORECASE)` | B17. Pre-check: if already visible, skip `_open_model_dropdown` (avoids toggle-close). |
| Close model panel | `page.locator("[data-slate-editor='true']").click()` (click outside), fallback Escape | B8. NEVER Escape as primary — closes entire composer. |

### Submit
| Mục | Selector | Notes |
|---|---|---|
| Submit (arrow_forward) | `button:has(i:text-is('arrow_forward'))` | B26. Canonical single-selector. On `/edit/` there are 2 matches: decorative disabled (nth 0) + real (nth 1). B16 iterate-and-skip-disabled handles this. |
| Submit with scope | `click_submit(page, scope="[data-scroll-state='START']")` | B26 scope param. Narrows to composer panel when ambiguity. |

### Edit mode (/edit/{media_id}) — 4 action buttons
| Mode | VI title | EN title | Icon | Default |
|---|---|---|---|---|
| Extend | `Mở rộng` | `Extend` | `keyboard_double_arrow_right` | **DEFAULT ACTIVE when entering /edit/** (discrete-2job §4.2 step 14) |
| Insert | `Chèn` | `Insert` | `add_box` | — |
| Remove | `Xoá` / `Xóa` | `Remove` / `Delete` | `ink_eraser` | — |
| Camera | `Camera` | `Camera` | `videocam` | — |

**2-pass selector** (`_base.py::click_action_button`):
1. `button[title='{localized title}']` (primary)
2. `button:has(i:text-is('{icon}'))` (locale-independent fallback)

**B31 (2026-04-19, this session):** extend Step 3 now probes `_verify_extend_panel` FIRST — if panel already open (default case), skip click. Avoids the "click active mode = toggle-close" trap.

### Edit-view top-right Download button + Radix menu (B36 probe → B38 UI upscale)
| Mục | Selector | Notes |
|---|---|---|
| Download button (icon-only) | `page.locator("button").filter(has=page.locator("i").get_by_text("download", exact=True))` | B38 primary. Icon ligature `<i>download</i>` — NOT text `"Download"`. Anchored selector excludes "Download app" etc. See `flow/upscale.py::_click_edit_download_button`. |
| Radix menu — 1080pUpscaled item | `page.locator('[role="menuitem"]').filter(has_text=re.compile(r"^1080pUpscaled$", re.IGNORECASE))` | B38. Anchored `^…$` regex EXCLUDES the sibling `4KUpscaled · 50 credits` which would cost 50 LP per click. Fallback: substring `1080p` (4K doesn't contain "1080p"). |
| Upscale "done" toast | scan body/aria-live/snackbar/toast for `/upscal\w* complete\|đã tăng độ phân giải xong\|1080p ready/i` | `flow/upscale.py::_DONE_RE` / `_popup_state`. Flow surfaces both EN + VI messages depending on account locale. |
| Upscale "busy" / "failed" toast | `/upscaling\|đang tăng độ phân giải/i` · `/upscale failed\|unable to upscale\|không thể tăng/i` | Same scanner. Engine decides `continue` (busy = wait ≤360s), `done` (re-click to pull mp4), `failed` (bail attempt). |

### Camera presets (after Camera click)
| Mục | Selector | Notes |
|---|---|---|
| Preset button | `page.get_by_text(direction, exact=True)` | B12. Only strategy that works — no aria-label, no explicit role=button. |
| Verify selected | `getComputedStyle(labelDiv).color` — selected `rgb(48,48,48)` sum 144; unselected `rgb(255,255,255)` sum 765; threshold R+G+B < 400 | B12. Class names are styled-components hashes (unstable); color is the only semantic signal. |

### Bbox (Insert/Remove) — canvas drag
| Mục | Selector | Notes |
|---|---|---|
| Canvas target | largest visible `<canvas>` with `width ≥ 300 && height ≥ 200` | B11. `<video>` tag is a 105×60 thumbnail — wrong element. Preview is a 598×336 canvas. |
| Bbox verify | **none — pointer-trust** | B11 Option B. Flow paints bbox onto canvas 2D bitmap (no DOM overlay to detect). Pixel-sampling rejected due to video-frame noise + CORS/WebGL risk. |
| Input validation | 0-1 range + overflow clamp (`x+w>1 → w=1-x`) | B2 preserved. Also Pydantic `Field(ge=0, le=1)` at API boundary → 422 before job enters. |

---

## 5. The 5 Operations — What Each Needs

### L1 — text-to-video
1. Homepage → `+ New project` (B18 icon selector)
2. Aspect chip (B19) → pick ratio (B1 Radix tab) → close panel (click outside at `(10, 10)`, NOT Escape)
3. Open model chip → pick LP (B17 pre-check if items already visible)
4. (composer still open) `_set_aspect_ratio` (B1/B19)
5. **Step 4.5: `_set_output_count(page, 1)` (B35)** — force Quantity tablist to `x1` via Radix `[id$="-trigger-1"]`. Without this, accounts defaulting to x≥2 silently submit 2-4 clips = 2-4× LP cost.
6. Focus Slate editor → type prompt
7. Submit (B26 canonical) → URL pushes to `/edit/{new_media_id}`
8. Wait completion (`flow/wait.py`)
9. **Download via UI upscale (B38 primary for 1080p)** — `flow/upscale.py::upscale_and_download_1080p`: `_ensure_edit_view` (tile.click if on project root) → `_click_edit_download_button` → `_click_menu_1080p` (anchored regex EXCLUDES `4KUpscaled · 50 credits`) → poll toast for done/busy/failed, re-click on done, save via `expect_download`. Fallback chain: B38 UI 1080p → 720p API (`_download_via_api`) → UI-text `_download_via_ui` → blob capture. B37 fix makes the 720p harvest deterministic (`evt["mid"]` key).
10. Extract `media_id` from URL or network response (`flow/media_id.py`)

### L2 — extend-video
**Entry state:** `/edit/{parent.media_id}` — Extend mode DEFAULT ACTIVE.
1. `navigate_to_edit(edit_url)` (B27 direct goto)
2. Wait video loaded
3. **Probe panel state (B31)** — `_verify_extend_panel` returns True? skip click; False? click Extend.
4. Type extend prompt (B15 scroll-state selector)
5. Select LP model (B17)
6. Submit (B26) → **mints NEW `media_id`** (INV-5 revised)
7. Download + return new metadata

### L2 — camera-move
1. Navigate to `/edit/{parent.media_id}`
2. Click Camera mode button (icon `videocam`)
3. Grid of presets renders → click preset by exact text (B12)
4. Verify via `getComputedStyle(labelDiv).color` (B12)
5. Submit → `media_id` context-dependent: NEW on early-chain (L2 off L1), preserves on deep-chain (after B32 tile-activation) (INV-5 revised, B33 nuance)
6. Download

### L2 — insert-object
1. Navigate to `/edit/{parent.media_id}`
2. Click Insert mode (icon `add_box`)
3. Type prompt (describes what to insert)
4. `draw_bbox_on_video` (B11 canvas target + pointer-trust)
5. Submit → preserves `media_id` in-place (INV-5 revised — verified Run 12 post-B32)
6. Download

### L2 — remove-object
Same as insert, different icon (`ink_eraser`), different placeholder.

---

## 6. Chain Routing (B22 + B30)

### B22 — L2+ claim inheritance
When worker claims an L2+ job, `claim_next_job` fetches parent row and propagates into child:
- `profile` (from direct parent)
- `project_url` (from direct parent)
- `media_id` (see B30 walk-up below)
- `edit_url` (see B30 walk-up below)

### B30 — extend-ancestor walk-up
For `media_id` + `edit_url`, walks up past `extend-video` ancestors until a non-extend ancestor (or root) is reached. Max 16 iterations.

**Why:** extend-output `/edit/{new_media}` has Insert/Remove/Camera **disabled** (B28 "extend-child lockout"). Chain needs to navigate to a clip where those modes are enabled.

**Example chain:**
```
L1 t2v → L2 extend → L3 insert
         ↑ B22 inherits from L1 t2v (extend's new uuid)
                     ↑ B30 walks up past L2 extend → inherits L1 t2v's media_id
```

**But:** B30 alone doesn't solve chain-with-extend. Navigating `/edit/{L1.media}` post-extend hits B29 (SPA strips `/edit/` segment → lands on project library grid). Unsolved in engine; the FLOW_BUTTON_EXACT §5.1 workaround (navigate to project grid, click L1 timeline thumbnail to re-enter edit mode with sidebar re-enabled) is not implemented.

---

## 7. Known Unsolved Gaps

### Chain pattern matrix

| Pattern | Status | Note |
|---|---|---|
| t2v → camera | ✅ works (Run 10) | — |
| t2v → insert | ✅ works (Run 10) | — |
| t2v → remove | ⚠️ code ready, live-untested | Same fix as insert; likely works |
| t2v → extend | ✅ works (Run 10 J2, discrete-2job) | — |
| t2v → extend → extend → … | ✅ likely works | Extend button stays enabled on extend-output |
| **t2v → extend → insert / remove / camera** | ✅ **UNBLOCKED (B32, Run 12)** | `_activate_clip_tile` fires MouseEvent sequence on `[data-tile-id="fe_id_{target}"]` when URL media ≠ target media. 5-op chain (t2v → extend → insert → remove → camera) completed live 2026-04-19. |
| **Parallel L2 siblings on L1** | ❌ **BLOCKED** | B29 — L1 /edit/ stale after sibling extend. Serial chains work; parallel L2 forks don't (not pursued). |
| **L2 extend on clean new profile (Run 19 pattern)** | ❌ **BLOCKED — profile-auth** | Worker-profile cookies not inherited → Flow serves marketing landing at `/edit/`. Not a chain-logic issue. Fix via `scripts/warm_profile.py` + full profile reset (see `feedback_profile_full_reset.md`). |

### Defensive guards (raise loud instead of silent-fail)
- **B28 guard** (`click_action_button`): visible + disabled → was "raise" (fc31a54), now `logger.warning` + fall through (post-B32 semantics: B32 tile-activation re-enables the sidebar, so a disabled button is recoverable not fatal).
- **B29 guard** (`navigate_to_edit`): post-goto `"/edit/" not in page.url` → raise "SPA stripped /edit/ — stale media_id post-sibling-extend." Still valid — surfaces the SPA-strip condition that B32 can't always resolve (parallel siblings).
- **B38 guard** (`flow/upscale.py::_ensure_edit_view`): if page on `/project/{pid}` root, click `[data-tile-id^="fe_id_"]` first; if not, warn and return (caller falls back to 720p). NEVER `page.goto(/edit/)` — bounces per feedback_flow_edit_nav_click.md.

**Warning (historical):** B28 guard initially shipped as immediate raise (fc31a54). That caused Run 11 J2 extend false-positive on healthy t2v-output (Extend button briefly disabled during Flow progressive render). B31 inverted the logic — `extend_video` now probes `_verify_extend_panel` FIRST (Extend is default-active on /edit/) and only clicks if not open. Pattern to mirror: B15 panel verify, B19 Radix `data-state` wait, B31 probe-first.

---

## 8. Bug Ledger (B1 … B31, quick lookup)

| # | File/Scope | Commit | One-liner |
|---|---|---|---|
| B1 | aspect ratio | `b359c84` | Radix chip `[id$='-trigger-PORTRAIT \| LANDSCAPE']` |
| B2 → B11 | bbox | `a165105` → `ce6683a` | Canvas target ≥300px, pointer-trust verify |
| B3 → B12 | camera verify | `58937d4` → `78d3e40` | `getComputedStyle(labelDiv).color` R+G+B<400 |
| B4 | chains table | `4dcf50f` | INSERT chain row on POST, GET derives status from jobs |
| B5 | completed_at | `4d24c10` | Auto-stamp on terminal status |
| B6 | profile.current_job_id | `0118e6d` | Set on claim, clear on complete |
| B7 | port | `a95c9b5` | Server default 8000 → 8080 |
| B8 | datetime.utcnow | `573cffd` | Migrate 7 callsites → `datetime.now(UTC)` |
| B9 | test foundation | `adca116` | pytest + fixtures + temp DB |
| B10 | pydantic default_factory utcnow | `fe13870` | `default_factory=lambda: datetime.now(UTC)` |
| B13 | docs cleanup | inline | Resolved inline with Tier1 retest |
| B14 | nav verify + tile click media_id-aware | `72e056b` | Stash cherry-pick KEEP-2 + KEEP-3 |
| B15 | extend panel verify + submit diag + Slate selector | `caef3e9` | Stash KEEP-4 + KEEP-5 + KEEP-6 |
| B16 | click_submit iterate + skip disabled | `004d8fb` | Stash KEEP-7 |
| B17 | LP pre-check | `f5dab42` | Stash KEEP-1 |
| B18 | homepage locale | `8dc357c` | Icon-first `add_2` + bilingual text fallbacks |
| B19 | aspect chip | `e1597b2` | Icon-ligature `crop_9_16`/`crop_16_9` + Radix open guard |
| B20 | model_selector fuzzy Veo | `0aa01b8` | 3 sites → icon-anchor + `^Veo` regex |
| B21 | stray print | — | Self-resolved (not present at master 83f621f) |
| B22 | L2+ claim inheritance | `0637c92` | SELECT parent + propagate project_url/media_id/edit_url |
| B23 | _click_video_tile rewrite | `78f7994` | Match media_id via `<video \| img> src` |
| B24 | locale re-detect | `f3313de` | Re-detect locale from page.url at write-time |
| B25 | (skipped) | — | — |
| B26 | submit + model chip exact-text | `d4fca1a` | 3 files → `:text-is()` + MODE_TITLES blacklist |
| B27 | direct goto primary | `9519c06` | `navigate_to_edit` → `goto(edit_url)` primary + tile fallback |
| B28 | disabled-button diagnostic guard | `fc31a54` | `is_enabled()` check → raise "extend-child lockout" |
| B29 | URL-strip guard | `fc31a54` | Post-goto `"/edit/" in page.url` check |
| B30 | extend-ancestor walk-up | `fc31a54` | Skip extend ancestors in claim inheritance |
| B31 | extend panel probe-first | `6aace7f` | Probe `_verify_extend_panel` before click (Extend default active) |
| B32 | navigate_to_edit tile-activation | (post-B30 refinement) | `_activate_clip_tile(page, media_id)` dispatches MouseEvent sequence on `[data-tile-id="fe_id_{id}"]` when URL media ≠ target — re-enables sidebar on chain-with-extend-middle. Run 12 PASS 5-op. |
| B33 | camera-move media_id context | (docs-only, INV-5 nuance) | Camera mints NEW on early-chain (L2 off L1, Run 10); preserves on deep-chain (L3+ after tile-activation, Run 12 J5). Engine handles via `finalize_operation` re-extract. |
| B34 | 1080p upscale poll window | `d454155` | `UPSCALE_POLL_INTERVAL 10→15s` + `UPSCALE_MAX_RETRIES 3→12` (180s). Env-configurable. |
| B34b | upscale retry bump | `26ca413` | Retries 12→24 (360s). **Superseded by B38** — `_upsampled` API endpoint returns 404 permanently, so API polling can never succeed. Env knob preserved for 720p transient recovery. |
| B35 | force output count x1 | `dc486a7` | `_set_output_count(page, 1)` via Radix `[id$="-trigger-1"]` + chip inner_text verify. Prevents x2-default credit leak on accounts like ngoctuandt20. Run 13 + Run 14 + Run 15 all verified `files=1`. |
| B36 | UI-driven download (probe) | `a59d280` | PROBE ONLY, not implemented — icon-only Download button + Radix 1080pUpscaled + tile ⋮ alternate path + UUID dualism documented. Superseded by B38 (B38 ate B36's implementation scope). |
| B37 | download_video harvest key | `7914020` | `evt["mid"]` (was `evt["media_id"]` — silent mismatch w/ `client.py::_record_media_id` storage key) + unwrap `_video_urls` list-of-dicts before `media_id_from_url`. Makes harvest deterministic. Surfaced Run 14, verified Run 15 3/3. |
| B38 | UI-driven 1080p upscale | (uncommitted, Run 17/18 implemented, L2 live-block Run 19) | New `flow/upscale.py` (~405 lines): anchored `^1080pUpscaled$` menu item, toast-poll for done/busy/failed, re-click on done, `expect_download`. `download_video` routes `quality=="1080p"` → UI path first, falls back to 720p API. L1 verified Run 18; L2 blocked by profile-auth not B38 itself. |

**Current tag:** `v0.6.0-chain-complete` @ `fc31a54` (pre-B31, stale). Actual master HEAD ≥ `26ca413`. Tag bump deferred until L2 chain end-to-end validated on the uncommitted B38 branch.

**Total: 36 bugs addressed** (B25 skipped in numbering; B36 was probe-only absorbed by B38). Fixed-and-committed: 34. In-flight uncommitted: B38.

---

## 9. Testing Layout

| File | Scope |
|---|---|
| `tests/conftest.py` | temp DB + api_client fixtures (B9) |
| `tests/test_smoke.py` | fixture smoke tests |
| `tests/test_config.py` | port (B7) |
| `tests/test_datetime_migration.py` | no utcnow in source + tz-aware timestamps (B8) |
| `tests/test_job_store.py` | claim/update/completed_at (B5) |
| `tests/test_profile_store.py` | profile tracking (B6) |
| `tests/test_claim_algorithm.py` | B22 inheritance + B30 walk-up + priority |
| `tests/test_chains.py` | B4 chain CRUD |
| `tests/test_aspect_ratio.py` | B1 + B19 |
| `tests/test_bbox.py` | B11 canvas-target + trip-wires |
| `tests/test_camera.py` | B12 color-verify + trip-wires |
| `tests/test_base.py` | B14 nav verify + B28/B29 guards + B27 direct goto |
| `tests/test_extend.py` | B15 panel verify + B31 probe-first |
| `tests/test_submit.py` | B16 iterate + B26 canonical |
| `tests/test_model_selector.py` | B17 LP pre-check + B20 no-fuzzy-Veo |
| `tests/test_generate.py` | B18 homepage selectors |
| `tests/test_e2e_invariants.py` | INV-1/4/stale-recovery infra tests (no Flow) |
| `tests/test_output_count.py` | B35 `_set_output_count` happy-path + Radix pre-open guard + verify-failure warn + 1..4 range + source trip-wire on `text_to_video` |
| `tests/test_download.py` | B34/B34b upscale-window env contract + B37 source trip-wires (`evt["mid"]` + `entry["url"]` unwrap) |

**Count at master ≥ `26ca413`: 119 tests pass.** After PR#18 frontend-optim merge: 121 tests pass (+2 bulk-delete cases). CI: `.github/workflows/tests.yml` — pytest on PR + push to master.

**Source-level trip-wires** (prevent silent regression):
- `test_bbox_evaluate_script_targets_canvas` — bbox JS uses `canvas` + `300`, not `querySelector('video')`
- `test_bbox_returns_true_after_drag_no_post_verify` — no post-drag verify (B11 Option B)
- `test_verify_script_uses_computed_color_signal` — camera verify uses `getComputedStyle` + `color` + `rgb`
- `test_submit_selectors_use_exact_text_and_no_fuzzy_antipattern` — SUBMIT_SELECTORS len=1, canonical selector, no fuzzy
- `test_chip_selector_uses_icon_not_model_text` — aspect chip uses icon ligature, not `video.*x\d`
- `test_no_fuzzy_veo_selector` — no `:has-text('Veo')` or `filter(has_text="Veo")`
- `test_icon_selector_comes_first` — B18 top-3 uses `add_2`
- `test_navigate_uses_edit_url_as_primary_goto` — B27 first goto carries `/edit/`

---

## 10. Validation Tier Ladder

| Tier | What | Cost | Purpose |
|---|---|---|---|
| 0 | Unit tests (pytest) | 0 | Source contracts + mock Playwright |
| 1 | DOM probe (Chrome MCP, read-only) | 0 | Verify selectors match live DOM |
| 1.5 | DB-layer live (claim snapshot against real DB) | 0 | B22-style — verify DB propagation against real rows |
| 2 | Full chain submit (worker + Playwright) | LP credits (~1 per job) | End-to-end production verification |

**Tier 1 history:**
- Round 1 (2026-04-17 `9facbe3`): B1 ✅, B2/B3 ❌ flipped → created B11/B12
- Round 2 (2026-04-17 `db4c746`): B11/B12 ✅ post-fix

**Tier 2 history:**
- Run 1 (`e618731`): BLOCKED at homepage (VI locale) → B18 opened
- Run 2 (B18 retry): PASS homepage, BLOCKED at aspect chip → B19
- Run 3-8: B19 iterations
- Run 9 (B22 DB-layer, `gallant-jang-cbe036`): DB PASS; full-browser deferred
- Run 10 (VI `9519c06`, post-language-switch): ✅ chain t2v → camera → insert
- Tests 2/3/4 (`eb20092`): chain 5-op blocked at J3 extend output → B28 + B29 discovered
- Run 11 (this session, post-fc31a54): J2 extend fail → B31 (extend default active) discovered and fixed

---

## 11. Session Chronology (selected)

| Date | Commits | Milestone |
|---|---|---|
| 2026-04-16 | legacy #2-#8 (flow-bugs epic) | Pre-rebuild: store media_id, project_url, profile pinning, etc. |
| 2026-04-17 | B7, B9, B8, B5, B6, B1, B2, B3 | Phase A core bugs |
| 2026-04-17 | Tier 1 R1 + R2 | B1 ✅; B2/B3 flipped; B11/B12 fixed |
| 2026-04-17 | Stash triage | 10 KEEP + 1 OBSOLETE + 3 CONFLICT hunks |
| 2026-04-17 | B14, B15 | Stash cherry-pick |
| 2026-04-18 | B16, B17, B10, B4 | Stash cherry-pick + residuals |
| 2026-04-18 | Tier 2 Run 1-8, B18, B19, B22 | Tier 2 iterations |
| 2026-04-19 | B26, B27, discrete-2job, B28/B29 probe | Mid-session discoveries |
| 2026-04-19 | B20/B21 cleanup, CI setup, Tests 5/6/7 infra | Housekeeping |
| 2026-04-19 | Run 10 VI post-language-switch, INV-5 revision | Cross-locale verification |
| 2026-04-19 | Tests 2/3/4 (chain 5-op) → B28/B29 probe | 5-op chain surfaces extend-child lockout |
| 2026-04-19 | B30 + B28/B29 guards + B31 | Inheritance walk-up + defensive guards + extend probe-first |
| 2026-04-19 | B32 tile-activation + Run 12 verify | 5-op chain-with-extend-middle PASS; `_activate_clip_tile` re-enables sidebar |
| 2026-04-19 | B33 INV-5 nuance + B34 upscale window | Camera context-dependent; retries 3→12 (180s) |
| 2026-04-19 | B35 force x1 + Run 13 verify | Credit leak fixed; `_set_output_count` via Radix; Run 13 PASS |
| 2026-04-19 | Run 14 + B37 surfaced + fix same session | `evt["mid"]` key rename makes harvest deterministic; `t2v_blob_` fallback path exposed the regression |
| 2026-04-19 | Run 15 3/3 PASS + B34b retry bump | B37 regression check across 3 diverse t2v; retries bumped 12→24 (360s) before B38 superseded |
| 2026-04-19 | B38 UI upscale (parallel session) + Run 17/18 | `flow/upscale.py` NEW; L1 1080p via UI primary path; L2 block separate issue |
| 2026-04-19 | Run 19 L2 BLOCKED + HANDOFF | Profile-auth → marketing landing on `/edit/`; warm_profile.py crash |
| 2026-04-19 | Frontend-optim parallel session — PR#18 | Incremental WS + shared form constants + bulk-delete endpoint; 121/121 pass |
| 2026-04-20 | NOTES overhaul + 3 new memories | Code-quality codex-review rule + prompt-delivery + cross-check-memory patterns |

---

## 12. What I Got Wrong This Session (honest log)

For my future-self:

1. **Session B28 guard prompt (fc31a54):** I wrote "raise on disabled" instead of the probe report's recommended `logger.warning + fall through`. The executor session applied raise. Result: Run 11 J2 extend fail — healthy t2v-output Extend button briefly disabled during progressive render triggered the guard. **Lesson:** follow the probe report's fix direction verbatim unless I have a documented reason to deviate.

2. **Run 11 post-mortem:** I immediately speculated B28 guard false-positive. User pointed out the actual fail was upstream — `click_action_button` never matched because Extend is default-active on /edit/ and the old code assumed the button must be clicked to open the panel. **Lesson:** trace the exact RuntimeError message to its `raise` site before theorizing.

3. **Docs-reading discipline:** User stated 3 times "phần này đã debug rất kĩ rồi" before I bothered to grep for "default active" in session reports. The fact was in `discrete-2job-verify_en.md:119` the whole time. **Lesson:** when user references prior debug, grep first, theorize second.

4. **Spawned too many sessions:** For a 5-line extend.py fix, I kept reaching for session spawning. User overrode explicitly ("mày bị đần à, mở mcp để làm gì?"). **Lesson:** task scope < ~20 lines + selector/logic edit → self-edit with read/edit tools, not spawn. Spawn is for (a) code .py changes that need TDD loop + commit message + report, (b) research sessions with clear scope.

5. **Live probe via Chrome MCP was available from day one.** I kept asking user to "open a tab" when I could have used `mcp__Claude_in_Chrome__*` directly once loaded. User made this explicit after the fact.

**Meta:** Codex (session con) has outperformed me this session on most landed commits — B18/B19/B22/B23/B24/B26/B27/B30 were all executor sessions, I only committed B31 directly + some docs housekeeping. If supervisor authority is questioned again, the honest answer is: I am useful for chronology/merges/tracking/user-facing updates, not for debug/fix work at this codebase's current complexity.

**2026-04-20 add-ons:**

6. **Pasted HANDOFF.md verbatim without cross-checking memory.** L2-unblock prompt cited HANDOFF Step 1 "wipe Cache preserve Cookies" bisect; `feedback_profile_full_reset.md` (newer) prescribed full delete — user called this out. Saved `feedback_cross_check_memory_before_paste.md`. **Lesson:** `ls ~/.claude/.../memory/` + grep keywords BEFORE paste. Memory wins over doc when they conflict; the outgoing prompt must flag stale doc lines.

7. **Didn't spot the unauthorized ServiceLogin URL in `warm_profile.py`.** I read the file carefully enough to summarize it, but didn't cross-check against what the user actually instructed — the parallel session changed strategy from "mail.google.com + manual sign-in" (Run 19 §5) to "ServiceLogin + auto-credentials" without authorization. **Lesson:** when reviewing handed-back code from a parallel session, explicitly scan for choices the user didn't dictate (URLs, credentials, new deps, new routes). Ask before propagating.

8. **Saved supervisor workflow memories only after user called it out.** `feedback_prompt_delivery_workflow.md` and `feedback_cross_check_memory_before_paste.md` could have been written after the first "cho prompt" turn in the session. Waited until 10+ turns in. **Lesson:** when user instruction is repeated or landed as a clear preference, save the memory the first time, not the third.

9. **Didn't flag B34b as moot when B38 landed.** The two changes are interleaved in time but B38's "`_upsampled` 404 permanent" finding makes B34/B34b retry bumps dead code for the 1080p path. I kept quoting "119 pass +2 B37 trip-wires" without noting B38 made the constants near-obsolete. Flagged retroactively in SPEC §D.4 but not promptly.

---

## 13. When Something Breaks — Where to Look First

| Symptom | First place to look |
|---|---|
| Job fails immediately, worker logs "Failed to find X button" | `click_action_button` — check if X is default-active on /edit/ (B31 pattern) |
| Submit times out with `gen_id=None, new_api_calls=0, url=/project/…` | URL drifted to /project/ mid-op. Check `_switch_to_video_tab` didn't click a mode button (B26 pattern — MODE_TITLES blacklist should prevent this). |
| L2+ job with `project_url=NULL` | B22 inheritance broken. Check `claim_next_job` SELECT includes all 4 fields. |
| L3 op on extend output fails "Failed to find Insert button" | B28 — sidebar disabled. Chain must walk up past extend via B30. |
| `navigate_to_edit` warns "Video element not found after 15s" | B29 — SPA stripped `/edit/`. `page.url` now on `/project/{id}` library grid. Check B30 walk-up didn't hand us a stale L1 media. |
| Camera job raises "Failed to find camera preset" | B12 — `_verify_preset_selected` using wrong signal. Should be `getComputedStyle(labelDiv).color` R+G+B<400. |
| Insert/Remove bbox lands in wrong place | B11 — canvas target. Verify `document.querySelectorAll('canvas')` filtered `width≥300 && height≥200` picks the preview, not a thumbnail. |
| Aspect ratio set but chip text doesn't change | B19 — chip pre-open guard missed. Check `data-state !== "open"` before click. |
| Unicode/mojibake in worker log on Windows | stdout `cp1252` — set `PYTHONIOENCODING=utf-8` (B-candidate, never filed formally). |
| "Failed to find '+ New project' button" | B18 — homepage locale. Check account is EN at myaccount.google.com/language. Icon selector `add_2` should work regardless. |
| `files=2` or `cards 0 → 4` on L1 t2v with no explicit `output_count` | B35 — Step 4.5 `_set_output_count(page, 1)` missing or failed to match the Radix trigger. Check chip inner_text post-close contains `x1`. Account default may be x2/x4. |
| `text-to-video DONE \| files=1 media_id=None` + `t2v_blob_*.mp4` fallback | B37 regression — `download_video` reading wrong key from `client._media_id_events` (must be `evt["mid"]`, not `evt["media_id"]`). |
| Every live run falls through to 720p, zero `_1080p_*.mp4` ever appears | B38 — `_upsampled` API is 404 permanent, must go through UI upscale. Check `quality=="1080p"` routes to `flow.upscale.upscale_and_download_1080p` BEFORE `_download_via_api`. |
| Worker logs POLICY error at `progress=0s` right after `/edit/` nav | Pre-B38 wait.py regex false-positive on footer "Privacy Policy" links. B38 session tightened regex — confirm running latest `flow/wait.py`. |
| `/edit/` URL renders marketing landing ("Where the next wave of storytelling") | Profile-auth lost. See `feedback_profile_full_reset.md` — full delete `chrome-profiles/<profile>/`, re-warm, user signs in fresh. |
| `warm_profile.py` crashes `TargetClosedError` + Chrome exit `0x80000003` | `STATUS_BREAKPOINT`. Profile dir corrupted from Playwright kill mid-startup. Full delete is the fix — memory `feedback_profile_full_reset.md` explicitly rejects cache-preserve-cookies bisect. |
| Login loops on same step ("Email step failed: Timeout 2000ms") | Google overlay intercepts pointer events. Current `flow/login.py` auto-reloads after 3 stuck iterations — see `feedback_login_stuck_reload.md`. |

---

## 14. Cross-References (the real docs)

| Topic | Authoritative doc |
|---|---|
| Rules / invariants / bug ledger | `docs/SPEC.md` |
| Tactical plan / bug queue | `docs/WORKPLAN.md` |
| Architecture / design | `docs/DESIGN.md` |
| Chronological UI walkthrough | `docs/FLOW_BUTTON_EXACT.md` |
| UI reference tables | `docs/FLOW_UI_REFERENCE.md` |
| Multi-level job history | `docs/FLOW_MULTILEVEL_JOBS.md` |
| Pipeline knowledge | `docs/FLOW_PIPELINE_KNOWLEDGE.md` |
| Phase A E2E results | `docs/E2E_RESULTS_PHASE_A.md` |
| Per-task evidence | `docs/session-reports/YYYY-MM-DD_<task>_*.md` |
| Project/user context | `CLAUDE.md` |
| Supervisor's notes (this file) | `docs/FLOW_ENGINEERING_NOTES.md` |
| Memory (user-level, cross-session) | `~/.claude/projects/D--AI-FlowEngine/memory/` (8 files as of 2026-04-20) |
| L2 unblock playbook (latest) | `docs/HANDOFF.md` (Step 1 updated 2026-04-20 to full-reset per memory) |

### Memory files (8) — quick index
| File | Domain |
|---|---|
| `feedback_english_locale.md` | Flow accounts MUST be EN locale before first run |
| `feedback_output_count_x1.md` | Every L1 t2v MUST force x1; never trust account default |
| `feedback_login_stuck_reload.md` | Google overlay pointer-intercept → page.reload after 3 stuck iterations |
| `feedback_flow_edit_nav_click.md` | `/edit/` nav via `tile.click`, NOT `page.goto` (SPA routing slug is opaque) |
| `feedback_profile_full_reset.md` | warm_profile TargetClosedError → full delete profile, NOT cache-preserve bisect |
| `feedback_prompt_delivery_workflow.md` | Supervisor hands out self-contained prompts for parallel sessions; main session doesn't execute heavy work |
| `feedback_cross_check_memory_before_paste.md` | Before pasting HANDOFF/session-report into a new-session prompt, grep memory/ first — memory wins if conflict |
| `feedback_code_quality_codex_review.md` | All code (direct + handed-out) must pass codex senior-reviewer bar; user runs codex on every change |

---

## 15. In-Flight State (as of 2026-04-20)

**Master HEAD:** `26ca413` (B34b retry bump). PR#18 (frontend-optim) pending merge at 121 pass, adds server/routes bulk-delete + shared frontend constants + WS incremental.

**Uncommitted (main working tree, branch `master`) — gated on L2 validation:**
| File | Status | Origin |
|---|---|---|
| `flow/upscale.py` (new, 405 lines) | Validated at L1 Run 18; L2 block separate issue | Run 17/19 parallel session |
| `flow/download.py` | B38 UI upscale wired as primary for 1080p; env knob preserved | Run 17 |
| `flow/wait.py` | POLICY regex tightened + screenshot/HTML dump on every DOM error | Run 19 |
| `flow/login.py` | Stuck-detection + page.reload after 3 repeats (per memory) | Run 19 |
| `scripts/warm_profile.py` (new) | ⚠️ broken (TargetClosedError) AND ⚠️ uses unapproved ServiceLogin URL — pending user decision on revert to mail.google.com manual-signin strategy | Run 19 |
| `docs/HANDOFF.md` (new) | Playbook, Step 1 updated 2026-04-20 to full-reset policy | Run 19 + this session |
| `docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md` (new) | Diagnosis of Run 19 block | Run 19 |
| `docs/FLOW_ENGINEERING_NOTES.md` (this file) | Expanded to cover B32-B38 + new memories + in-flight state | this session |

**Do NOT commit with `git add -A`** — always stage explicit files. Parallel sessions may touch different file sets; `-A` would conflate their uncommitted work.

**Open blockers (prioritized):**
1. **L2 chain blocked by profile-auth (Run 19).** Next action = full delete `chrome-profiles/ngoctuandt20/`, run `scripts/warm_profile.py` (after reverting the ServiceLogin URL issue), user signs in fresh. Once verified → post new L2 extend under chain `1871a218` parent `4a032d83` → if pass, commit the 5 in-flight `.py` files.
2. **`warm_profile.py` uses unapproved ServiceLogin URL** — pending user decision among: (a) revert to `mail.google.com` + `wait_for_event("close")` manual flow from Run 19 §5, (b) different URL user specifies, (c) add confirm-prompt before navigation.
3. **B38 session report missing** — Run 17/18 referenced from HANDOFF but never written. `docs/session-reports/2026-04-19_Tier2_Run17_B38_UI_upscale.md` does NOT exist in the repo. The parallel session should have authored it.

**Safe-to-run (no block):**
- All merged Phase A fixes (B1-B37) + B34b retry bump.
- L1 t2v on an already-warmed profile (Run 13/14/15 all PASS on ngoctuandt20 while SSO was still valid).

**Reconstructable from artifacts if this file drifts:**
- `docs/session-reports/*.md` (authoritative per-run evidence, never deleted).
- `docs/E2E_RESULTS_PHASE_A.md` (append-only log, Runs 1-15).
- `docs/SPEC.md` §D.4 (B1-B37 ledger, B38 entry pending).
- `git log master` (all committed fixes with hashes).
- `logs/worker.log.run{13,14,15}` (archived per-run logs).
- `~/.claude/.../memory/*.md` (user-level persistence across sessions).

Worst-case next-session catch-up = ~10 min reading the above; no knowledge is lost if this NOTES file falls behind.

---

_End of supervisor notes. Update this when mental model shifts, not code changes._
