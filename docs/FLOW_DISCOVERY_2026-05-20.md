# Flow Discovery — 2026-05-20

**Method:** MCP Chrome on browser `ngoctuandt1` (Google account: free tier, avatar `T`), live Flow at `labs.google/fx/tools/flow`. Zero-submit (no credits burned).

**Baseline cutoff in engine docs:** ~2026-04-23. Flow has shipped **5 changelogs since**: 4/29, 5/5 (implied), 5/19. Plus older features the engine never integrated.

This document lists DELTA vs `flow/` code only. See companion baseline reports for what the engine already supports.

---

## Changelog timeline (post-baseline)

| Date | Title | Engine impact |
|---|---|---|
| 5/19/2026 | Agent, Google Flow Tools, Gemini Omni Flash, and more! | P0 — new model + new composer mode + new "Tools" sidebar |
| 4/29/2026 | Archive, Shortcuts and more! | P1 — Trash sidebar (soft delete + recover), keyboard shortcuts |
| 4/21/2026 | Link Sharing and 4s/6s Videos | P1 — duration selector new; share URL endpoint new |
| 4/16/2026 | Ingredients to Video with Veo Lite | P2 — Lite (not just LP) accepts ingredients |

Pre-baseline but never integrated:
- Nano Banana 2 (2/26), Nano Banana Pro (11/20/25) — image models in dropdown
- Imagen 4 — image model in dropdown
- Custom Prompt Expanders (9/24/25)
- Doodle on images (10/21/25)
- "Make images talk with Veo 3" (7/1/25) — voice ingredients

---

## P0 — breaking deltas (must fix before next live run)

### 1. Default output count = **x4** (was x1 baseline assumption)
Live evidence: Image panel default `x4` highlighted; Video panel default `x4` → "Generating will use 40 credits" (Veo 3.1 Lite × 4).

Engine impact: `_set_output_count(1)` must run on **every** L1 submit (already done in `generate.py:text_to_video`), but **L2 ops never enforce x1** (see baseline gap §What is NOT implemented). L2 ops on this UI inherit x4 → silent 4× credit leak. Either enforce x1 on L2 too, or confirm L2 panel does not expose count (verify in worker code path before assuming).

### 2. LP (Lower Priority) variants **REMOVED**
Video model dropdown (live, free tier, ngoctuandt1):

```
Omni Flash           [Upgrade]   ← new, paid
Veo 3.1 - Lite       (selected)  ← free default
Veo 3.1 - Fast
Veo 3.1 - Quality
```

No "Veo 3.1 - Lite [Lower Priority]" / "Veo 3.1 - Fast [Lower Priority]" / "(leaving 5/10)" entries. The 2026-05-10 LP deprecation **has happened**.

Engine impact:
- `flow/model_selector.py:7-17` `MODEL_MAP` referencing `veo-3.1-lite-lp` / `veo-3.1-fast-lp` are stale — will fail label lookup.
- Memory `project_lp_deprecation_2026_10_05.md` predicted this; the planned fallback never landed.
- `_verify_credits()` "lower priority" string match → never matches.
- New free baseline = `Veo 3.1 - Lite` (0 credits per generation per memory `feedback_recaptcha_wipe_rewarm.md` rewarm verification 2026-05-01? — re-verify; UI shows "Generating will use 40 credits" at x4 Lite, so **Lite = 10 credits each** now, NOT 0).
  - **Credit budget shifted**: free generations are no longer free; engine assumes 0-credit submit and must surface this.

### 3. Composer panel **completely restructured**
Old (engine baseline §"Composer UI"):
- Single Radix `DropdownMenuContent` opened by chip with `arrow_drop_down`
- 4 tablists: Media Type → Source Type (Video only) → Aspect Ratio → Quantity
- Selectors `[id$="-trigger-IMAGE|VIDEO|PORTRAIT|LANDSCAPE|1|2|3|4"]`

New (live 2026-05-20):
- Radix `[role="menu"]` opened by chip on composer right side (model+count chip e.g. `🍌 Nano Banana Pro x4`)
- Up to 6 rows depending on Image/Video selection:
  - **Row 1**: tablist `Image` / `Video`
  - **Row 2 (Video only)**: tablist `Frames` / `Ingredients` (still part of Video sub-mode but as own tablist, not single source-type)
  - **Row 3**: tablist aspect ratio
    - Image: `16:9` / `4:3` / `1:1` / `3:4` / `9:16` — **5 options**, default `1:1`
    - Video: `9:16` / `16:9` — 2 options, default `16:9`
  - **Row 4**: tablist count `1x` / `x2` / `x3` / `x4` (note: `1x` first one is "1x", others are "x2/x3/x4" — string parsing must handle both shapes)
  - **Row 5**: model picker button (text label of currently selected model + `arrow_drop_down`)
  - **Row 6**: link "Generating will use N credits" with `href=support.google.com/googleone?p=g1_ai_credit_menu`

Engine impact:
- `model_selector.py` chip-opening selector `button[aria-haspopup='menu']:has(i:text-is('crop_16_9'))` matches only when current aspect is 16:9. Default Image is now `1:1` → icon will be `crop_square` or similar. Selector must accept all 5 image aspects + 2 video aspects + `play_circle` (Video tab icon) + Nano Banana emoji as chip variants.
- Aspect ratio selectors `[id$="-trigger-PORTRAIT|LANDSCAPE"]` only cover 9:16/16:9. Need to add `LANDSCAPE_4_3`, `SQUARE`, `PORTRAIT_3_4` (Image mode). Already noted in baseline §What-is-NOT-implemented but never implemented; **NOW BLOCKING** because new default `1:1` ≠ engine assumed `16:9`, and an aspect mismatch silently degrades quality.
- Sub-tab `Frames` / `Ingredients` is now Radix tablist row 2 (not inferred from upload presence). Engine `_ensure_video_mode()` only flips to Video tab; needs `_ensure_frames_subtab()` / `_ensure_ingredients_subtab()` helpers.
- Per-mode default model also changes — **default app mode is now Image with Nano Banana Pro**, NOT Video. Engine entering a fresh project lands in Image mode → first submit could go to image path unless we explicitly click Video tab + select Veo model.

### 4. L2 ops UI **paywalled for free tier**
Live evidence: opened existing project, clicked into video tile → `/edit/{media_uuid}` route → new "Timeline editor" view:
- Title bar: project name + 💖 favorite + 🔗 share + ⬇ download + "Hide history" toggle + ⋮ + **Done**
- Center: video player + 16:9 indicator
- Right side: prompt history panel (e.g. "a small red balloon" entry)
- Bottom: scrubable timeline 00-09s, clip strip with thumbnails, `+` button at clip end
- **Persistent banner**: "Video editing is only available for paid subscribers" + `Upgrade` button. Clicking `+` on timeline does nothing for free tier.

Engine impact:
- Old `/edit/` view exposed 4 sidebar buttons (Extend / Insert / Remove / Camera) accessible to free tier. **These buttons are GONE**.
- Free-tier L2 ops (extend, camera, insert, remove) **may be entirely unavailable** through the UI path. Engine's `_base.py` selectors `button[title='Mở rộng']` etc. will hit no-op.
- Verify on a paid account whether the timeline `+` opens an extend composer, and whether Insert/Remove/Camera surface elsewhere (kebab menu? right-click on clip?). **Critical unknown — must check on ngoctuandt20 (the engine's actual worker profile) if it has paid tier OR escalate to user.**
- If L2 ops require paid tier, engine's free-tier worker pool can only do L1 generation. Either:
  - (a) restrict engine job types to L1 (text-to-video / text-to-image / frames-to-video / ingredients-to-video) for free profiles
  - (b) detect paywall + raise a clean error (similar to RecaptchaError pattern)
  - (c) move L2 logic to reverseAPI path where free tier may still be accepted (API may bypass UI gating)

### 5. Default video duration ~8s (4s/6s/8s selector new)
Live evidence: video player UI shows `00:08:00` on existing generation. Per 4/21/2026 changelog "Link Sharing and 4s/6s Videos", duration is now selectable.

Engine impact: text-to-video API likely takes a `duration` field now. Engine never sets duration → uses default. If default changed from 5s baseline to 8s, credit cost per generation increased. Need to either lock to a known duration OR expose `duration` in `JobCreate.params`.

---

## P1 — new features the engine should support

### 6. Agent mode (persistent per-project toggle)
- Composer chip `Agent` (left of model chip). Click toggles. Click brings up a banner: "Your agent is active! Ask it to brainstorm concepts, generate image variations, rename assets, or answer questions about Flow. Start typing below, or click the expand icon on the top right to open chat mode."
- Composer in Agent mode hides the model+aspect+count chip — generation goes through the agent.

**Endpoints captured:**
- `PATCH https://aisandbox-pa.googleapis.com/v1/projects/{projectId}/agentInfo?updateMask=agent_toggle_state` — status 200, body sets toggle. **Persistent across reloads.**
- `GET https://aisandbox-pa.googleapis.com/v1/flowCreationAgent/sessions?projectId={projectId}` — agent session list.

Engine impact:
- Engine submits assume non-Agent composer layout. If a worker profile lands on a project where Agent was toggled ON (manually or by previous run), composer chip is hidden → engine's chip-open selector fails.
- **Engine should explicitly toggle Agent OFF** at session start by PATCH-ing `agent_toggle_state=false` (reverseAPI), OR by clicking the Agent chip if currently active (UI check via highlighted state).

### 7. Sidebar entities (left rail)
On project view, sidebar shows:

```
All Media     [dashboard icon]    — default selected
Videos        [movie icon]         — appears when project has any videos
Characters    [accessibility_new]  — NEW entity, supports @tag reference
Scenes        [movie icon, alt]    — NEW entity
─── divider ───
Tools         [apps_spark_2]       — NEW community marketplace
─── divider ───
Trash         [delete]             — NEW soft-delete archive
Collapse      [left_panel_close]   — sidebar collapse toggle
```

Each sidebar entry navigates to `/fx/tools/flow/project/{pid}/{characters|scenes|tools|trash}`.

Engine impact (priority by use case):
- **Trash (P1)**: engine deletes assets via `Job.deleted_at`; corresponding Flow soft-delete endpoint unknown. If user manually moves a video to Trash in the UI, downstream L2 jobs targeting that media should fail clean. Need: discovery endpoint `*/trash` or equivalent.
- **Characters (P2)**: optional new entity. Engine could expose `JobCreate.character_refs: list[uuid]` to inject `@character` mentions into prompts.
- **Scenes (P2)**: similar to characters.
- **Tools (P3)**: paid-tier marketplace (built-in tools = `Converge`, `Grid Architect`, `Character X-Ray`, `Style Writer`, `Storyboard Studio`, `Prompt Tree`, `Story Sketch` + Video category). Out of scope for free tier.

### 8. Asset picker on composer `+` button
Click `+` opens a panel with categories:

```
All
Images
Videos
Voices       ← NEW asset type (audio)
Characters
Uploads
```

Plus "Upload media" button at bottom + search bar + "Recent" sort dropdown.

Engine impact:
- Old composer accepted media via direct file upload only (frames-to-video, ingredients-to-video paths).
- New composer can attach **existing project media** (Images / Videos) + **Voices** (audio) + **Characters** as references.
- Engine's `frames_to_video` / `ingredients_to_video` always upload from local FS. Should also support attaching existing assets by `media_id` for chained generation without re-upload. This unlocks: feed L1 output as L2 ingredient on same project without download+re-upload.
- **Voices** asset type means image-to-video can now bind a voice asset → adds audio narration. New job param: `voice_asset_id`.

### 9. Link sharing (4/21/2026)
- Edit view top bar has a `🔗` (share) button.
- Per changelog "Link Sharing and 4s/6s Videos".

Engine impact:
- Server-side: jobs table doesn't track share URL. Add `share_url: str | None` column + endpoint to mint+revoke. Out of scope for engine-side automation unless user requests it.

### 10. Edit view history panel
- Right side panel: clip thumbnail + prompt text ("a small red balloon").
- Toggle: `Hide history` / `Show history`.

Engine impact:
- Engine reads `media_id` after generation but doesn't introspect history panel for prompt-level lineage. Likely no change needed — history is UI-only.

### 11. Per-project auto-name
- Old projects: untitled (engine treated as UUID).
- New projects: auto-named by timestamp (`May 20, 09:43 PM`) or first generation prompt ("River running through forest").

Engine impact: minor. Server stores `project_id`, never queried project name. If we want to surface a human-readable name in dashboard, add a `project_title` field populated by scraping or by reading first-generation prompt.

---

## P2 — image-mode catch-up (long known gaps now blocking)

### 12. Five image aspect ratios — engine only handles two
Live: Image panel has tabs `16:9`, `4:3`, `1:1`, `3:4`, `9:16`. Default `1:1`.

Engine: `RATIO_IDS` in `generate.py:956` has only `9:16` and `16:9`. Other values silently dropped. Already known gap; now blocking because default is `1:1` (engine never sets it, runs with whatever was last selected → image at wrong ratio).

**Add**: `LANDSCAPE_4_3`, `SQUARE`, `PORTRAIT_3_4` Radix trigger IDs + `_set_image_aspect_ratio()`.

### 13. Three image models — engine never sets one
Live: Image model dropdown:
- 🍌 Nano Banana Pro (selected default)
- 🍌 Nano Banana 2
- Imagen 4

Engine: `MODEL_MAP` only has Veo entries. Image-mode `text-to-image` never explicitly selects a model — uses whatever default the account had. Now default is Nano Banana Pro (could change again).

**Add**: image model entries in `MODEL_MAP` + `select_image_model()` helper. Engine config `FLOW_IMAGE_MODEL=nano-banana-pro|nano-banana-2|imagen-4`.

---

## P3 — reverseAPI endpoints captured (sparse but useful)

| Method | URL | Use |
|---|---|---|
| `GET` | `https://aisandbox-pa.googleapis.com/v1/flowCreationAgent/sessions?projectId={pid}` | List agent sessions on project |
| `PATCH` | `https://aisandbox-pa.googleapis.com/v1/projects/{pid}/agentInfo?updateMask=agent_toggle_state` | Toggle agent on/off; engine should toggle OFF at startup |
| `POST` | `https://labs.google/fx/api/trpc/videoFx.updateUserSettings` | Persist user settings (locale, defaults) |

Not yet captured (need a submit to observe — costs credits):
- text-to-image with Nano Banana Pro
- text-to-video with Veo 3.1 Lite at x1 vs x4 (cost confirmation)
- Frames sub-tab submit
- Ingredients sub-tab submit
- Agent message submit
- L2 ops (paid-gated for free tier — may need user's paid profile)
- Link share mint
- Trash move/restore
- Character create / @tag reference

**Recommendation for endpoint capture**: queue 1 paid-tier live session on a paid profile with credit budget allocated, run each new flow once with network capture ON, dump full `aisandbox-pa.*` traffic. Cheaper than re-deriving from baseline.

---

## Follow-up zero-credit probes (2026-05-20)

After initial discovery, second pass on the same MCP session covered these surfaces. All zero credit.

### Edit-view kebab `⋮` (top bar)
- Items: `Download Project` (operational) + Help/Feedback/Legal/Privacy/Changelogs (informational).
- **No hidden Insert/Remove/Camera options** — confirms L2 ops are fully paywalled for free tier (no escape hatch).

### Edit-view share `🔗` modal
- Layout: thumbnail preview + caption + switch `Include inputs` (default ON) + checkbox per-prompt + `Copy link` button + warning "Anyone with this link can view and use your creation".
- Available to free tier. No API endpoint captured (need to click `Copy link` with network capture ON during paid live-verify pass).

### Settings `⚙` panel (View Settings)
View preferences only — **no duration default here**:
- `View Mode`: Grid / Batch
- `Grid Size`: S / **M** (default) / L
- `Sound on hover`: Off / On
- `Return silent videos`: Off / On — could be useful for engine if pure-video output desired (no Veo audio); unverified cost impact
- `Show tile details`: Off / On
- `Clear prompt on submit`: Off / **On** (default)

### Project root kebab `⋮` (header next to project title)
- `Rename` — inline project rename
- `View Trash` — navigates to `/trash`
- `Delete` (red) — soft-delete project (likely PATCH or DELETE on project resource)

### Composer Video > Frames sub-tab
- UI gains 2 upload chips above prompt: `Start` ↔ `End` (reverse arrow between to swap order).
- Aspect ratio still 9:16/16:9 only; count still x4 default; cost preview 40 credits with Veo 3.1 Lite.

### Composer Video > Ingredients sub-tab
- Composer reverts to base layout. Ingredients upload likely happens via the `+` asset picker (Voices/Images/Uploads); did not click-test to avoid mode-confused submit.

### Composer in Agent mode (toggle ON state)
- Model+count chip is **hidden** (no aspect/quantity/model row).
- Right of submit arrow: 2 icons both labeled `Agent Instructions` (system-prompt editor for the per-project agent).
- Composer essentially becomes a chat input. Engine must keep Agent OFF.

### Characters page `/{pid}/characters`
- Full page (not modal). 4 sample templates: `The Familiar`, `The Eccentric`, `The Wicked`, `The Fantastical` — each with portrait + 2-3 sentence character description as starter prompt.
- Composer at bottom: `Describe your character...` + `+` + model = **🍌 Nano Banana 2** (NOT Banana Pro — Characters uses the cheaper Image model) + submit.
- Below composer: `Upload` button + `Add from Project` button (attach existing image as character ref).

### Scenes page `/{pid}/scenes`
- **HTTP 404**. Sidebar entry exists but route returns "ERROR: 404 / We don't know what you're looking for". Feature not yet shipped — sidebar is a teaser. Engine ignores.

### Trash page `/{pid}/trash`
- Header: `0 items in trash` + `Restore All` + `Delete All` buttons.
- Empty state: "Trash is empty".
- Sidebar reduces to: All Media / Characters / Scenes (Tools/Trash hidden in this context) + `← Back to {project_name}` link.
- Engine could query this endpoint to detect manually-trashed media before retry.

### Composer normal mode (Agent OFF) — final inventory
With user profile T (free tier, ngoctuandt1):
- `+` (left) → asset picker `[All | Images | Videos | Voices | Characters | Uploads]` + Upload media
- `Agent` chip (toggle, default OFF) → switches composer to chat mode
- Model+count chip on right (shows current mode + selected count, e.g. `Video x4`)
- Submit arrow `→`
- **No duration selector anywhere in free-tier composer** — duration defaults to model's behavior (Veo 3.1 Lite observed at 8s). Per changelog `Link Sharing and 4s/6s Videos` (4/21), duration *is* configurable. Likely paid-tier-only OR exposed via a per-clip control on the timeline edit view (which is itself paywalled).

### Endpoints accumulated (zero-credit)
| Method | URL | Use |
|---|---|---|
| `GET` | `aisandbox-pa.googleapis.com/v1/flowCreationAgent/sessions?projectId={pid}` | Agent sessions |
| `PATCH` | `aisandbox-pa.googleapis.com/v1/projects/{pid}/agentInfo?updateMask=agent_toggle_state` | Toggle agent |
| `POST` | `labs.google/fx/api/trpc/videoFx.updateUserSettings` | Persist view settings + clear-prompt-on-submit |

Other endpoints implied by UI but not captured (would need a click that triggers them):
- Project rename, project delete, project trash
- Share `Copy link` mint
- Asset picker list (`+` button likely calls a list endpoint)
- Characters list/create
- Trash list/restore/delete
- Settings update (only the user-settings tRPC captured; per-project settings may differ)

### Implications for PRD (post-probe)
1. **Duration param** — DEFERRED from Unit B. Free tier worker pool cannot set duration; remove from JobCreate schema until paid-tier confirms a selector exists. PRD §3 Unit B AC#6/7 strike `duration` plumbing.
2. **Project rename/delete** — out of scope (manual user action, not automation surface).
3. **Trash query** — optional health check: engine could `GET /{pid}/trash` before L2 retry to detect manually-trashed parents. Defer to follow-up epic.
4. **Sound on hover + Return silent videos** — engine never enables (no MCP UI hover); irrelevant.
5. **Characters with Banana 2** — confirms cheap-image-model availability. PRD §6 Q1 default `nano-banana-pro` for general image, `nano-banana-2` for Characters.
6. **Scenes 404** — confirm Scenes is dead route; PRD §2 deferral still correct.

---

## Out of scope (confirmed not changed)

- URL structure: `/fx/tools/flow/project/{uuid}/edit/{media_uuid}` unchanged.
- `aisandbox-pa.googleapis.com` is still the generation host.
- Slate.js editor (`[data-slate-editor='true']`) is still the prompt textbox.
- Submit confirmation via `arrow_forward` icon submit button — still present (but submit chip layout changed).
- reCAPTCHA detection path (network 403/429 signals) — unchanged.
- Free-credits-refresh-daily policy (2/3/2026 changelog) — orthogonal, no engine change.

---

## Recommended next step

1. **User confirms** which deltas above are in scope. P0 (5 items) is non-negotiable for next live run.
2. **Capture submit-time endpoints** on a paid profile before fan-out (one live-fire session per new flow).
3. **PRD**: `docs/PRD_FLOW_FEATURE_UPDATE_2026-05.md` decomposing the in-scope deltas into file-disjoint codex units. Recommended split (4 units, max parallelism):
   - Unit A — `flow/model_selector.py` + `MODEL_MAP`: LP removal, image models, Omni Flash, default-1:1 handling
   - Unit B — `flow/operations/generate.py` + new helpers: 5 image ratios, Frames/Ingredients tablist, x1 enforcement on L2, duration param
   - Unit C — `flow/agent.py` (new file): Agent toggle OFF + session enumeration via reverseAPI
   - Unit D — `flow/operations/_base.py` + `flow/edit_view.py`: timeline-editor paywall detection, free-tier L2 graceful fail, share/trash endpoint stubs
