# PRD — Web UI Phase: Flow-Style Clone

**Status:** Draft, 2026-04-25
**Branch:** `claude/phase-web-ui`
**Owner:** Supervisor (Claude tech-lead)

## 1. Goal

Re-skin the FlowEngine dashboard to mirror Google Flow's own UI as closely as possible. Flow's layout is already optimised for the exact workflow we drive (mode tabs, model selector, prompt composer, project grid), so cloning it removes UX guesswork and gives operators a familiar surface for managing automation jobs.

This is a **front-end-only** phase — no server, no worker, no Playwright touches.

## 2. Non-Goals

- No server / API contract changes. Existing endpoints (`/api/jobs`, `/api/chains`, `/api/profiles`, `/api/uploads`, `/ws/jobs`) stay 1:1.
- No new build pipeline. Stay vanilla JS + plain CSS, single static-mount under FastAPI.
- No reuse of Google logo, brand wordmarks, copyrighted iconography. Visual *layout* and *interaction grammar* only.
- Not a full clone of Flow's `/edit/<media_id>` canvas (timeline + RTE bbox painter). Out of scope here; tracked as future phase.

## 3. Reference Surfaces (from Flow)

Source of truth = Flow's live UI plus existing engineering notes in `docs/FLOW_UI_REFERENCE.md`, `docs/FLOW_BUTTON_EXACT.md`, `docs/FLOW_PIPELINE_KNOWLEDGE.md`.

| Flow surface | What we clone | What we drop |
|---|---|---|
| Landing `/tools/flow` (signed-in home) | Top app bar, big centered composer card, project grid | Marketing variants, "Create with Flow" CTA |
| Composer chip cluster | Mode tabs (Image/Video/Frames/Ingredients), model dropdown trigger, aspect chip, count chip, settings chip, send button | LP credit balance widget (we don't track credits in-app) |
| Project tile | Thumbnail + title + meta row | Hover-play preview (perf-heavy) |
| `/edit/` view | A simplified single-job detail panel reachable via tile click | Full timeline + bbox painter |

## 4. Information Architecture

```
#home          NEW   Flow-style landing: composer + project grid (replaces #dashboard as default route)
#dashboard           Existing job table, kept for power users / debugging
#chains              Existing chain builder, reskinned to fit new shell
#profiles            Existing, reskinned
#settings            Existing, reskinned
```

Default route on first load redirects `''` → `#home` (was `#dashboard`).

## 5. Component Spec

### 5.1 App shell

- **Top app bar** (replaces sidebar as primary nav on `#home`):
  - Left: logo mark + "FlowEngine" wordmark.
  - Center: nothing (Flow's bar is asymmetric — left-aligned brand, right-aligned actions).
  - Right: profile selector chip (dropdown of warm Chrome profiles), WS status dot, overflow menu (Dashboard / Chains / Profiles / Settings).
- **Main canvas**: scroll container, max-width ≈ 1200px, centered.
- Sidebar from current build is **retired on `#home`** but preserved on `#dashboard` / `#chains` / `#profiles` / `#settings` for table-heavy pages.

### 5.2 Composer card (`#home` hero)

Single elevated card, centered, ~720px wide. Inside, top-to-bottom:

1. **Mode tab strip** — segmented control with 4 tabs:
   `Video` (default) · `Image` · `Frames` · `Ingredients`
   Maps to `text-to-video` / `text-to-image` / `frames-to-video` / `ingredients-to-video`. Tab change re-renders dynamic fields.
2. **Prompt textarea** — full-width, 3 visible lines, autoresize up to 8.
3. **Attachment row** (only shown when mode is Frames or Ingredients or Image):
   - Frames → Start image dropzone + End image dropzone (side-by-side).
   - Ingredients → multi-image strip (up to 10), `+` tile to add.
   - Image → optional reference dropzone.
4. **Chip row** — left-aligned chips, right-aligned send:
   - Model chip (icon + abbreviated label, opens dropdown).
   - Aspect chip (16:9 / 9:16 / etc).
   - Profile chip (which Chrome profile / Google account).
   - Count chip (always `×1`, locked, with tooltip "Engine forces ×1 to control credits"; per memory `feedback_output_count_x1.md`).
   - Send button (icon + "Generate") on the far right.
5. **Submit feedback strip** — inline below card on success/fail, replaces toast for hero action.

### 5.3 Project grid

- Below composer.
- Section heading: "Recent" + "View all" link → `#dashboard`.
- 4-column grid (responsive: 4 → 3 → 2 → 1 col).
- Tile = job card (`status`, `type` badge, prompt snippet, profile, time-ago). Status colour ring around thumbnail.
- Click tile → modal job detail (reuse existing `App.openModal`).
- Live updates from WS: insert / update / animate the relevant tile.

### 5.4 Secondary pages (reskin only, no behaviour change)

- Reuse existing `#dashboard` `#chains` `#profiles` `#settings` pages.
- Wrap them in the new top-app-bar shell when navigated to from `#home`.
- Keep sidebar collapsed by default; expose via hamburger in top bar.

## 6. Visual Tokens

Use Flow's palette family (cool dark + violet accent), inheriting most from PR #56 already merged. Where Flow differs:

| Token | Old | New |
|---|---|---|
| `--bg-canvas` | `#0a0a0c` | `#0f0f12` (slightly warmer) |
| `--bg-surface` | `#16161a` | `#1a1b1f` |
| `--bg-chip` | n/a | `#23252b` |
| `--accent` | `#7c5cff` | keep |
| `--accent-soft` | n/a | `rgba(124,92,255,0.16)` |
| `--ring-focus` | n/a | `#a892ff` |
| `--radius-card` | `12px` | `20px` (Flow uses big rounded cards) |
| `--radius-chip` | `8px` | `999px` (pill chips) |

Keep Material Icons + Inter font, already loaded.

## 7. Acceptance

- [ ] `#home` is the default route; loads composer + grid; no sidebar visible at this route.
- [ ] All 4 mode tabs render the right field set; submit hits `POST /api/jobs` with the correct payload — verified by submitting one of each and confirming `200 OK` in console.
- [ ] Project grid shows latest 12 jobs; tile click opens existing detail modal.
- [ ] WS-driven live updates land in the new grid (status badge changes colour without reload).
- [ ] `#dashboard` `#chains` `#profiles` `#settings` still work (regression check, no behavioural diff).
- [ ] Lighthouse-ish smoke: no console errors on first paint; no >1MB asset added (Inter + Material Icons CDN already counted).
- [ ] All new code passes `pytest` baseline (308 / 1 / 3) — no Python touched, but run anyway as sanity.

## 8. Out-of-Scope / Follow-up

- Inline `/edit/` canvas (timeline + bbox painter) — phase 2.
- Keyboard shortcut overlay (Flow has `?` overlay) — nice-to-have.
- Drag-reorder chain builder visual graph — separate epic.
- Account credit telemetry — needs server work; out of scope.

## 9. Risks

- **Live verify is not feasible from this branch alone** (no Chrome worker run, no live job submitted). Visual / interactive verification will be browser-only (open `http://localhost:8080` after `python run_server.py`).
- **Memory `feedback_locked_items_require_user_approval.md`** — `feature_output_count_x1` chip stays locked at ×1. Do not add a "set count" UI.
- **Memory `feedback_english_locale.md`** — UI strings stay English (operators run accounts in English locale; Vietnamese strings in our UI are tolerable, but keep field labels English to avoid confusing screenshots when debugging Flow itself).
