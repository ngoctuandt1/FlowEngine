# PRD Extension — Flow Feature Coverage Beyond v3

**Status:** Discovery extension from zero-credit paid-profile probe  
**Probe profile:** `s17524h173` ULTRA / `PAYGATE_TIER_TWO` / `WS_ULTRA`  
**Probe window:** 2026-05-20 UTC / 2026-05-21 Asia/Bangkok  
**Project used:** `d254e570-f789-4afd-a0df-457682534809` (`May 21, 12:11 AM`)  
**Credit burn:** 0 — no generation submit; mutation-looking non-GET requests were route-aborted before send.

## Surfaces explored (priority + maturity)

- Characters: ready — route is live; character composer, templates, model picker, upload/project attach controls, and preset-prompt endpoint observed.
- Trash: partial — route is live with empty-state list, Restore All, Delete All; no restore/delete body captured because project trash was empty and destructive actions were not confirmed.
- Link Share: blocked — project/share button not visible on empty project; tool share affordance observed, but project share mint/revoke not triggered.
- Project kebab: partial — Rename, View Trash, Delete menu DOM observed; mutation endpoints not fired under zero-mutation constraint.
- Voices: ready — composer asset picker exposes Voices tab; `projectInitialData` returns 30 preset audio assets as `mediaType: "AUDIO"`.
- Archive settings: partial — View Settings DOM observed; app config exposes `isReturnSilentVideosEnabled`; persistence path known from prior capture as `videoFx.updateUserSettings`, but toggle request did not fire in this probe.
- Tools marketplace: ready — route is live; community/system applet list endpoint and tool detail route observed.
- Scenes: blocked — sidebar entry exists, but `/project/{pid}/scenes` returned Flow 404 on paid profile.
- Edit-view paid surface: blocked — selected project had no media/edit links, so L2 timeline editor could not be reached without credit burn or an existing-media project.
- Submit-time endpoints: blocked by design — no Create/Generate submit clicked; only safe pre-submit DOM/model/credit surfaces observed.

## Per-surface findings

### Characters

**URL pattern:** `/fx/tools/flow/project/{project_id}/characters`

**DOM signals:**
- Top controls: `button` text `arrow_back Back`, `New character`, `ULTRA`.
- Template cards: button text starts `The Familiar`, `The Eccentric`, `The Wicked`, `The Fantastical`.
- Prompt editor: `div[role="textbox"][contenteditable="true"]` with placeholder-like text `Describe your character…`.
- Create controls: `button[type="button"]` text `add_2 Create`; submit button text `arrow_forward Create`.
- Model picker: Radix button text `🍌 Nano Banana 2 arrow_drop_down`.
- Asset attach: buttons `upload Upload` and `add Add from Project`; hidden `input[type="file"]`.
- `@tag` reference: banner copy from app config says characters can be referenced "with a simple @tag"; no live character existed to inspect tag autocomplete.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `labs.google/fx/api/trpc/flow.projectInitialData?input={projectId}` | query: `{json:{projectId}}` | project envelope; empty project had only `externalReferenceMedia` audio presets |
| GET | `labs.google/fx/api/trpc/videoFx.getFlowAppConfig` | none | `siteContent.banners[]`; character banner includes `ctaText: "Create a character"` and @tag copy |
| GET | `aisandbox-pa.googleapis.com/v1/flow/models/statuses` | none | model status envelope; response body not retained beyond preview |
| POST | `labs.google/fx/api/trpc/flow.generateCharacterPrompt` | `{json:{archetype:"THE_FAMILIAR"}}` | aborted before send; UI displayed `Failed to generate preset prompt` |
| POST | `labs.google/fx/api/trpc/flow.generateCharacterPrompt` | `{json:{archetype:"THE_ECCENTRIC"}}` | aborted before send; body shape confirmed |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/entities` | not observed | path literal in loaded Next.js chunks; likely character/entity mutation/read surface |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/entities:copyEntity` | not observed | path literal only |

**Data shape:**
- Character route reuses `projectInitialData` plus static UI templates.
- Create prompt is contenteditable text, not `<textarea>`.
- Character archetype enum observed: `THE_FAMILIAR`, `THE_ECCENTRIC`; UI implies `THE_WICKED`, `THE_FANTASTICAL`.

**Mutation operations:**
- Preset prompt generation uses tRPC `flow.generateCharacterPrompt`.
- Final character create was not clicked; exact generation/create body remains verification debt.
- Asset attachment can be file upload or existing-project picker; endpoint not triggered.

**Engine integration recommendation:** add first-class `Character` entity and `character-create` job family after v3. Add `flow/operations/character.py` that can navigate `/characters`, fill contenteditable prompt, select `Nano Banana 2`, attach reference media, and block submit behind credit guard. Server likely needs `characters` table with Flow `entity_id`, display tag/name, media refs, prompt, model, and source project.

**Implementation complexity:** L

**Reverse-API viability:** medium — tRPC prompt endpoint is clear; final create likely involves `aisandbox-pa` entity/generation APIs with bearer auth and must be live-captured before coding.

### Trash

**URL pattern:** `/fx/tools/flow/project/{project_id}/trash`

**DOM signals:**
- Header: `Trash`, search, `Sort & Filter`, `0 Items in trash`.
- Bulk buttons: `undo Restore All`, `delete Delete All`.
- Sidebar remains active: `All Media`, `Characters`, `View scenes`, `Back to {project_name}`.
- Empty list: `div[data-testid="virtuoso-scroller"]` text `Trash is empty`.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `labs.google/fx/api/trpc/flow.projectInitialData?input={projectId}` | query: `{json:{projectId}}` | same project envelope; no trash list array present because trash empty |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow:batchDeleteAssets` | not observed | path literal in loaded chunks; likely bulk permanent-delete/move surface |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowMedia/{media_id}` | not observed | path literal; likely asset mutation/read surface |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowWorkflows/{workflow_id}` | not observed | path literal; likely workflow mutation/read surface |

**Data shape:** trash empty state does not expose deleted item schema. No `deletedMedia`/`deletedWorkflows` arrays appeared in this empty project's `projectContents`.

**Mutation operations:** Restore All and Delete All are visible but were not clicked/confirmed. Per-item restore/permanent delete needs a project with deleted assets.

**Engine integration recommendation:** add Trash support as maintenance/admin operations, not generation jobs. Server can expose `trash.list`, `trash.restore(media_ids|workflow_ids|all)`, and `trash.delete_permanent(...)` only after endpoint confirmation. UI automation fallback is viable via route and visible bulk buttons.

**Implementation complexity:** M

**Reverse-API viability:** medium-low until non-empty trash capture proves item IDs and mutation bodies.

### Link Share

**URL pattern:** expected on project/editor top bar; not visible on empty project. Tool-detail share route observed at `/project/{pid}/tool/{applet_id}`.

**DOM signals observed:**
- Empty project top bar did not expose `Share` or `Copy link`.
- Tool detail exposed button text `share Sharing allows anyone with the link to view, remix and reshare your tool. Share responsibly, delete anytime.` plus `Done`.
- Requested `Include inputs` toggle and per-prompt checkbox were not reached.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent:shareApplet` | not observed | path literal for tool sharing, not project sharing |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent/sharedApplets/{id}` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent:saveSharedApplet` | not observed | path literal only |

**Data shape:** not observed for project shares. Tool sharing copy warns that public links can view, remix, and reshare tools.

**Mutation operations:** Copy link/mint and revoke were not clicked to avoid sharing. No project share endpoint observed.

**Engine integration recommendation:** separate Flow-owned share tokens from Engine state unless product wants Engine-managed public links. If adopted, add `share_token`, `share_url`, `include_inputs`, `shared_at`, `revoked_at` columns on project/media share table; do not overload job output URLs.

**Implementation complexity:** M

**Reverse-API viability:** low until a non-empty project/editor share modal is captured with mutation abort or safe test artifact.

### Project Kebab

**URL pattern:** `/fx/tools/flow/project/{project_id}` and homepage project cards.

**DOM signals:**
- Project header menu: Radix `button` text `more_vert More options`.
- Menu items: `edit Rename`, `delete View Trash`, `delete Delete`.
- Project title input: `input[aria-label="Editable text"][type="text"]` visible in header.
- Homepage cards expose direct buttons `edit Edit project` and `delete Delete project`.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| STATIC | `aisandbox-pa.googleapis.com/v1/projects/{project_id}` | not observed | path literal in loaded chunks; likely rename/delete project route |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/projects/{project_id}` | not observed | path literal in loaded chunks; likely Flow-specific project route |

**Data shape:** `projectInitialData` returns `projectName`, `projectId`, `projectContents`, `modelConfig`, `appConfig`, `userData`, `agentInfo`.

**Mutation operations:** Rename and Delete were visible but not confirmed. Exact PATCH/DELETE body remains unobserved.

**Engine integration recommendation:** add project maintenance endpoints in Engine separate from generation jobs: `rename_project(project_url, name)` and `delete_project(project_url, confirm=true)`. Prefer DOM first unless live reverse-API PATCH/DELETE is captured with abort.

**Implementation complexity:** S

**Reverse-API viability:** medium after one abort-captured rename/delete on disposable project.

### Voices

**URL pattern:** composer asset picker inside `/fx/tools/flow/project/{project_id}`.

**DOM signals:**
- Composer attach button: `button[type="button"]` text `add_2 Create` opens picker.
- Picker tabs: `dashboard All`, `image Images`, `videocam Videos`, `voice_selection Voices`, `accessibility_new Characters`, `drive_folder_upload Uploads`.
- Picker controls: `upload Upload media`, `search Recent`, empty result state `No results found` for current project-specific lists.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `labs.google/fx/api/trpc/flow.projectInitialData?input={projectId}` | query: `{json:{projectId}}` | `projectContents.externalReferenceMedia[]` contains 30 preset audio assets |

**Data shape:**

```json
{
  "mediaId": "achernar",
  "mediaType": "AUDIO",
  "workflowDisplayName": "Achernar",
  "media": {
    "name": "achernar",
    "audio": {
      "generatedAudio": {
        "name": "Achernar",
        "description": "Female, soft, high pitch",
        "isPresetAudioSample": true,
        "audioSamplePath": "https://gstatic.com/aitestkitchen/voices/samples/Achernar.wav"
      }
    }
  }
}
```

Observed preset names include `Achernar`, `Achird`, `Algenib`, `Algieba`, `Alnilam`, `Aoede`, `Autonoe`, `Callirrhoe`, `Charon`, `Despina`, `Enceladus`, `Erinome`.

**Mutation operations:** none. Voice attach-to-prompt was not selected/submitted.

**Engine integration recommendation:** add `voice_asset_id` / `reference_audio_id` to generation job input only after submit body is captured. Store voice presets as external assets, not user media, because they arrive under `externalReferenceMedia` and `media.audio.generatedAudio.isPresetAudioSample`.

**Implementation complexity:** M

**Reverse-API viability:** high for listing; medium for attaching until generation request shape is captured.

### Archive Settings

**URL pattern:** `/fx/tools/flow/project/{project_id}` View Settings popover.

**DOM signals:**
- Trigger: Radix button text `settings_2 View Settings`.
- View mode tabs: `button[role="tab"][aria-label="Grid"]`, `button[role="tab"][aria-label="Batch"]`.
- Grid size tabs: `Small`, `Medium`, `Large`.
- Switch groups: `Sound on hover` Off/On, `Return silent videos` Off/On, `Show tile details` Off/On, `Clear prompt on submit` Off/On.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `labs.google/fx/api/trpc/videoFx.getUserSettings` | none | `lastAcknowledgedChangeLogId`, `completedOnboardingIds` |
| GET | `labs.google/fx/api/trpc/videoFx.getFlowAppConfig` | none | includes `isReturnSilentVideosEnabled: true` |
| POST | `labs.google/fx/api/trpc/videoFx.updateUserSettings` | not captured this run | prior discovery reportedly observed this persistence endpoint |

**Data shape:** current `getUserSettings` did not include explicit view/silent settings values; defaults may live client-side/local storage until persisted.

**Mutation operations:** toggle click did not produce `updateUserSettings` in this probe, likely because JS clicked current value or settings are debounced/local until changed via Radix state. No generation request was sent, so `Return silent videos` effect on submit body remains unknown.

**Engine integration recommendation:** v3 composer should treat `Return silent videos` as a user preference hazard. For future unit, capture submit body with setting off/on under abort or disposable no-credit endpoint inspection before adding server field. If Flow uses this setting to omit audio, Engine needs `return_silent_videos: bool` on generation config.

**Implementation complexity:** M

**Reverse-API viability:** medium; tRPC persistence path known but body semantics not live-confirmed here.

### Tools Marketplace

**URL pattern:** `/fx/tools/flow/project/{project_id}/tools`; detail route `/fx/tools/flow/project/{project_id}/tool/{applet_id}`.

**DOM signals:**
- Tabs: `Discover`, `My Tools`.
- CTA: `Create Tool`.
- Marketplace list cards with `more_vert Tool options` buttons.
- Observed tools: `Simple Sketch`, `Scene Explorer`, `Mockup`, `Image Editor`, `Shot Explorer`, `Mask Magic`, `Converge`, `Grid Architect`, `Video Shader Effects`, `Type Overlays`, `pixelBento`, `Poster Designer`, `Video Sketch`, `Transition Machine`, `Weirdcore`, `Video Resizer`, `Stringout Creator`, `Video Granulator`, `Character X-Ray`, `Style Writer`, `Storyboard Studio`, `Prompt Tree`, `Story Sketch`, `Frame Deconstructor`, `Blob Tracking`, `DepthWarp 4D`, `Webcam Set`, `Datamosh`, `3D Model Visualizer`, `Scout360`, `Ribbit`, `Whisk`, `Pose Text`, `3D Face Swap`.
- Detail page for Simple Sketch: `/tool/8ffd7fee-0821-4ff5-9fe0-0e3cc43e968e`; buttons `Remix tool`, `Favorite`, share warning, `Report`, `Done`; disclaimer says app may consume credits.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `aisandbox-pa.googleapis.com/v1/flowAgent/applets` | none | `applets[]` with `appletId`, `displayName`, `description`, `currentVersionId`, `creatorDisplayName`, `source`, `thumbnailUrl`, `hasCode` |
| GET | `aisandbox-pa.googleapis.com/v1/flowAgent/savedSharedApplets` | none | `{}` in this account |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent/applets/{applet_id}` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent:copyApplet` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent:favoriteApplet` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flowAgent:shareApplet` | not observed | path literal only |

**Data shape:** example applet record:

```json
{
  "appletId": "6b649a77-e480-45e0-9d45-664e2b2e72ed",
  "displayName": "Grid Architect",
  "description": "Create image grids and extract individual images from them",
  "currentVersionId": "279e4fd3-f4fc-41ec-9f6d-201cbce2bf4c",
  "creatorDisplayName": "Henry Daubrez",
  "source": "SOURCE_SYSTEM",
  "hasCode": true
}
```

**Mutation operations:** `Remix tool`, `Favorite`, `Share`, app run/generate flows were not invoked. Tool detail warns credit consumption.

**Engine integration recommendation:** do not put marketplace into core generation path yet. Add optional `ToolApplet` catalog sync/read model first (`applet_id`, `version_id`, name, creator, source, thumbnail, has_code). Execution/remix is a separate paid/credit-risk epic.

**Implementation complexity:** L for execution, S for catalog-only.

**Reverse-API viability:** high for catalog; medium-low for running tools until applet input schemas and submit endpoints are captured.

### Scenes

**URL pattern:** `/fx/tools/flow/project/{project_id}/scenes`

**DOM signals:**
- Sidebar button exists: `movie View scenes`.
- Direct route returned body `ERROR: 404 We don't know what you're looking for, but we hope you find it` and `Home` link.

**Endpoints captured:**

| Method | URL pattern | Body shape | Response |
|---|---|---|---|
| GET | `labs.google/fx/tools/flow/project/{project_id}/scenes` | none | Flow 404 shell |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/scene/{scene_id}` | not observed | path literal in loaded chunks |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/scene:addWorkflowsToScene` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/scene:copyScene` | not observed | path literal only |
| STATIC | `aisandbox-pa.googleapis.com/v1/flow/scene/sceneWorkflows:update` | not observed | path literal only |

**Data shape:** none live; rollout appears incomplete for this account/project.

**Mutation operations:** none.

**Engine integration recommendation:** defer. Keep route detector so future workers fail fast with `surface_not_available` instead of timeout if asked for scenes.

**Implementation complexity:** unknown / likely L when live.

**Reverse-API viability:** low until route returns non-404.

### Edit-view Paid Surface

**URL pattern:** `/fx/tools/flow/project/{project_id}/edit/{media_id}` expected.

**DOM signals:** none; selected paid-profile project had no media and no edit links.

**Endpoints captured:** none for edit timeline operations.

**Mutation operations:** none.

**Engine integration recommendation:** run a separate zero-credit probe on an existing project with at least one completed video. Capture timeline DOM for extend/insert/remove/camera controls under paid profile; do not infer from v3 free-tier paywall work.

**Implementation complexity:** already in v3 for paywall; paid functional editor follow-up M.

**Reverse-API viability:** unknown.

### Submit-time Endpoints

**URL pattern:** generation submit remains composer `arrow_forward Create`.

**DOM signals:**
- Project composer: `Agent` toggle, model picker `🍌 Nano Banana 2 crop_16_9 x2`, submit `arrow_forward Create`.
- Character composer: model picker `🍌 Nano Banana 2`, submit `arrow_forward Create`.
- Tool detail warns `This app may consume credits`.

**Endpoints captured:** none by design.

**Mutation operations:** no generation/character/tool submit was clicked.

**Engine integration recommendation:** future submit capture must use a disposable project and route-abort mutation request before network send, or DevTools request inspection if Flow prepares request in memory. Do not code body fields from static JS alone.

**Implementation complexity:** M for capture; variable for implementation.

**Reverse-API viability:** unknown until safe submit request capture.

## Cross-surface invariants

- Project shell is shared: routes reuse `projectInitialData`, `getUserSettings`, `getFlowAppConfig`, credits, agent sessions, applets, and likeness eligibility.
- `projectInitialData.projectContents.externalReferenceMedia` is not just project media; it can preload global voice presets.
- Many mutating surfaces likely use `aisandbox-pa.googleapis.com` REST with bearer auth, while some preference/template helpers use same-origin tRPC.
- Static JS path literals are useful for discovery but not sufficient for Engine contracts; body shape and auth must be live-captured.
- Route-abort probing safely captures tRPC body for non-credit helper calls, but it can cause Flow React Query error toasts and should not be left active during list/read probes.
- Share URLs should be treated as Flow-owned unless product explicitly needs Engine-managed link lifecycle; Engine should store share metadata only after mint/revoke endpoints are proven.
- Voice asset attach likely needs a new `voice_asset_id` or `reference_audio_id` field, but submit-time request must prove exact API field.
- Characters likely need their own entity table; reusing media-only `media_id` would lose @tag/name/persona semantics.

## Proposed Wave N units

- Unit I — Characters: entity model, DOM automation, create prompt/model/attach flow, live endpoint capture before submit automation.
- Unit J — Trash + project mutations: list/restore/permanent delete, rename/delete project, guarded destructive confirmations.
- Unit K — Link sharing: project/editor share modal, include-inputs semantics, mint/revoke storage, share-token lifecycle.
- Unit L — Voices + audio references: preset catalog, composer picker attach, generation submit body capture.
- Unit M — Tools catalog: applet catalog sync and read-only UX; execution/remix deferred behind credit-risk gate.
- Unit N — Scenes rollout detector: route availability probe and schema capture once non-404.
- Unit O — Paid edit-view confirmation: paid L2 timeline controls and endpoint capture on existing completed-video project.
- Unit P — Submit-body capture harness: route-abort or DevTools-only request inspection for generation/character/tool submit payloads.

## Verification debt

- Capture final character create request body and response on disposable project without credit burn or with explicit credit budget.
- Capture non-empty Trash item schema plus Restore All, per-item Restore, Delete All, and per-item permanent delete bodies via route abort.
- Capture project Rename PATCH/body and Delete endpoint on disposable project via route abort, then verify no server mutation occurred.
- Find project/editor Share modal on project with media; capture `Include inputs`, per-prompt checkbox, Copy link mint, and revoke bodies.
- Toggle `Return silent videos` off/on and capture `videoFx.updateUserSettings` body plus generation submit delta.
- Select a voice in composer and capture whether submit body uses `mediaId`, `REFERENCE_AUDIO_ID`, or another field.
- Use a completed video project on ULTRA profile to inspect paid L2 edit timeline controls and request bodies.
- Open Tool app execution pane deeply enough to capture applet input schema without running credit-consuming operation.
- Re-probe `/scenes` after rollout; current paid profile returns 404.
