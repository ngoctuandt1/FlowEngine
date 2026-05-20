# PRD — Flow Feature Update 2026-05

**Status:** Draft for user review
**Author:** Claude (Opus 4.7)
**Date:** 2026-05-20
**Based on:** [FLOW_DISCOVERY_2026-05-20.md](FLOW_DISCOVERY_2026-05-20.md)
**Worker profile constraint:** ngoctuandt20 = **free tier** (no L2 ops, no Omni Flash)

---

## 1. Goal

Bring `flow/` code in sync with Flow's 4 changelogs since baseline (4/16 → 5/19/2026) **AND** clean up 3 pre-baseline gaps that the engine left behind (image aspect ratios, image models, voice ingredients).

**Non-goals:**
- Tools marketplace integration (paid create, low ROI)
- Agent mode as a job type (engine wants deterministic generation; Agent = exploratory chat)
- Scenes/Characters as engine entity (defer; engine works at media_id level)
- Paid-tier-only features (Omni Flash, L2 via timeline) beyond graceful-fail handling

---

## 2. Scope decisions

### IN SCOPE (P0 + P1 from discovery doc)

| # | Item | Strategy | Unit |
|---|---|---|---|
| 1 | Remove LP variants from `MODEL_MAP` | Click UI | A |
| 2 | Add image models (Nano Banana Pro/2, Imagen 4) | Click UI | A |
| 3 | Add Omni Flash entry with `tier="paid"` marker (don't auto-select) | Click UI | A |
| 4 | New chip-open selector handling 5 img + 2 video + emoji icons | Click UI | A |
| 5 | 5 image aspect ratios (LANDSCAPE_4_3, SQUARE, PORTRAIT_3_4) | Click UI | B |
| 6 | Default-mode handling: force Video tab on entry if Image is sticky | Click UI | B |
| 7 | Frames/Ingredients as Radix sub-tablist (not source-type combo) | Click UI | B |
| 8 | x1 enforcement on EVERY submit (L1 + composer-visible L2) | Click UI | B |
| 9 | `duration` param plumbing (4s/6s/8s) — pass via JobCreate, UI selector TBD | Click UI (deferred verify) | B |
| 10 | Agent mode toggle OFF at session start | **reverseAPI PATCH** | C |
| 11 | L2 paywall detection → `L2PaywallError` clean fail | Click UI (banner detection) | D |
| 12 | Worker dispatcher catches `L2PaywallError`, fails job `paid_tier_required_<op>` | — | D |

### DEFERRED (P2/P3, separate epic)

- Voice asset attach (needs paid profile to capture API; revisit when voice job type proposed)
- Trash/Share endpoints
- Characters/Scenes/Tools integration
- Submit-time endpoint capture (Veo Lite payload, Nano Banana payload) → needs credit budget; defer until reverseAPI replay path is unblocked
- Custom Prompt Expanders (9/24/25 changelog)

---

## 3. File-disjoint unit decomposition

### Unit A — Model registry + selector
**OWNS:**
- `flow/model_selector.py` (full edit)
- `tests/flow/test_model_selector.py` (full edit)

**READS:**
- `flow/operations/generate.py` (signature compatibility check, no edit)
- `docs/FLOW_DISCOVERY_2026-05-20.md` (reference)

**FORBIDDEN:**
- `flow/operations/*.py` (Unit B owns)
- `flow/agent.py` (Unit C owns)

**Acceptance criteria:**
1. `MODEL_MAP` purged of all `*-lp` entries; replaced with current model list (video: `omni-flash`, `veo-3.1-lite`, `veo-3.1-fast`, `veo-3.1-quality`; image: `nano-banana-pro`, `nano-banana-2`, `imagen-4`).
2. Each entry has fields: `key`, `display_label`, `mode` (`video|image`), `tier` (`free|paid`), `aliases` (list for backwards compat with old config strings like `veo-3.1-lite-lp`).
3. `select_model()` accepts both new keys and old `-lp` aliases (back-compat shim — old aliases map to new free-tier equivalent + WARN log).
4. Chip-open selector matches all variants: 5 image aspect icons (`crop_16_9`, `crop_4_3`, `crop_square`, `crop_3_4`, `crop_9_16`) + 2 video icons + `play_circle` + `image` Material Symbols.
5. Default selection when `free_mode=True`: video → `veo-3.1-lite`, image → `nano-banana-pro`.
6. New unit test: `test_lp_aliases_map_to_lite()`, `test_chip_open_selector_matches_all_variants()`, `test_paid_tier_model_raises_on_free_account()`.
7. All existing tests in `test_model_selector.py` still pass (post-update where old assertions reference LP).

**Reasoning level:** `high`

### Unit B — Composer (aspect + sub-tabs + count + mode)
**OWNS:**
- `flow/operations/generate.py` (full edit)
- `tests/flow/operations/test_generate.py` (full edit)
- `server/models/job.py` — only the `JobCreate` schema for the new `duration: int | None` field (additive)
- `server/db/schema.sql` migration if needed for `duration`

**READS:**
- `flow/model_selector.py` (Unit A — call its API only, no edit)
- `flow/operations/extend.py`, `camera.py`, `insert.py`, `remove.py` (signature only, no edit)

**FORBIDDEN:**
- `flow/model_selector.py` (Unit A owns)
- `flow/operations/_base.py` (Unit D owns)

**Acceptance criteria:**
1. `RATIO_IDS` extended to 5 entries for Image mode: `16:9 → -trigger-LANDSCAPE`, `4:3 → -trigger-LANDSCAPE_4_3`, `1:1 → -trigger-SQUARE`, `3:4 → -trigger-PORTRAIT_3_4`, `9:16 → -trigger-PORTRAIT`. Video mode keeps 2 entries.
2. New helper `_set_image_aspect_ratio(page, ratio)` selecting from 5 options; raises `ValueError` on unsupported.
3. `_ensure_video_mode()` flips Image → Video; new helper `_ensure_frames_subtab()` / `_ensure_ingredients_subtab()` for Video sub-mode.
4. **Entry handling**: on first composer open in a project, if current mode is Image (default), engine clicks Video tab BEFORE other selectors for any `*-to-video` job type.
5. `_set_output_count()` is called from every L1 submit path (already done); add same call to extend/insert/remove/camera if the composer in their UI exposes count (verify per op). If L2 composer hides count, skip silently (no error).
6. `JobCreate` accepts optional `duration: int | None` in {4, 6, 8} (UI selector path is TBD — for now, pass through to API in payload if present; if UI selector doesn't exist on free tier, skip silently).
7. New unit tests: `test_five_image_ratios`, `test_video_mode_forced_on_entry`, `test_frames_subtab_click`, `test_ingredients_subtab_click`, `test_duration_passes_through_jobcreate`.
8. All existing tests pass.

**Reasoning level:** `xhigh`

### Unit C — Agent mode disable (reverseAPI)
**OWNS:**
- `flow/agent.py` (new file)
- `tests/flow/test_agent.py` (new file)

**READS:**
- `flow/client.py` (call site — add call after navigation; do NOT edit client.py except 1 line `await disable_agent_mode_if_active(self.page, project_id)`)

**FORBIDDEN:**
- All other files

**Acceptance criteria:**
1. `flow/agent.py` exports `async def disable_agent_mode_if_active(page, project_id: str) -> bool` returning `True` if a toggle was made, `False` if already off.
2. Implementation strategy:
   - Read current state via DOM check: `[aria-pressed="true"]` on Agent button, OR active highlight class.
   - If active: call reverseAPI `PATCH https://aisandbox-pa.googleapis.com/v1/projects/{project_id}/agentInfo?updateMask=agent_toggle_state` with body `{"agent_toggle_state": "DISABLED"}` (exact body shape verified via one live capture during implementation; codex must capture before assuming).
   - Use `page.evaluate(async () => fetch(...))` with same credentials as the page (cookies, auth header propagated automatically since same origin).
   - Wait for HTTP 200, then reload page or wait for composer chip re-render.
3. Call site in `flow/client.py`:`FlowClient.navigate_to_project()` — single line addition, after URL settles.
4. Tests: mock the page + assert correct PATCH URL + body shape on the simulated call; assert no PATCH made if Agent already off.
5. Handle 404 / 403 gracefully: log + continue (don't crash session). Agent toggle endpoint may be removed or renamed later.

**Reasoning level:** `high`

### Unit D — Free-tier L2 paywall handling
**OWNS:**
- `flow/operations/_base.py` (full edit — paywall detection + raise path)
- `flow/edit_view.py` (new file — paywall banner detection helper)
- `worker/dispatcher.py` (only the L2 exception catch — minimal change)
- `tests/flow/test_edit_view.py` (new)
- `tests/worker/test_dispatcher_l2_paywall.py` (new)

**READS:**
- `flow/operations/extend.py`, `camera.py`, `insert.py`, `remove.py` (verify they call `_base.finalize_operation` and friends; no edit there)

**FORBIDDEN:**
- `flow/operations/extend.py`, `camera.py`, `insert.py`, `remove.py` (call site changes go in `_base.py` only)
- `flow/model_selector.py`, `flow/operations/generate.py` (other units)

**Acceptance criteria:**
1. New `flow/edit_view.py` with `async def detect_l2_paywall(page) -> bool` checking for the banner text in EN + VI: `"Video editing is only available for paid subscribers"` / VI equivalent (capture from live). Also checks for absence of legacy L2 buttons (`button[title='Extend']`, etc.) as a secondary signal.
2. New exception `flow.exceptions.L2PaywallError(op: str, profile: str)` (single-line addition to existing exceptions module).
3. In `_base.py`, add `_assert_l2_available(page, op_name)` called as the first action of every L2 operation entry. Raises `L2PaywallError(op_name, ...)` on positive paywall detection.
4. Worker `dispatcher.py` catches `L2PaywallError` → sets `job.status = failed`, `job.error_kind = "paid_tier_required"`, `job.error_message = f"{op} requires paid tier (profile={profile})"`. Job is NOT retried; profile is NOT burned (paywall is not transient).
5. Tests:
   - `test_detect_l2_paywall_true_when_banner_present` (mocked DOM)
   - `test_detect_l2_paywall_false_when_no_banner`
   - `test_assert_raises_on_paywall_each_op` (extend/camera/insert/remove)
   - `test_dispatcher_marks_job_paid_tier_required_not_burned`
6. Live verification deferred to autopilot pass with ngoctuandt20: queue 1 extend job on existing project → expect status=`failed`, error_kind=`paid_tier_required_extend`, profile still active.

**Reasoning level:** `xhigh`

---

## 4. Cross-cutting concerns

### Tests
Each unit owns its own test files (disjoint). Integration smoke test added in `tests/integration/test_2026_05_engine_update.py` — runs after all 4 units merge:
- L1 text-to-image with Nano Banana Pro + ratio `1:1` + count `x1`
- L1 text-to-video with Veo 3.1 Lite + ratio `16:9` + count `x1`
- L2 extend on existing project → expect `paid_tier_required_extend` (do not consume credits)
- Agent toggle OFF verified via DOM after navigation

### Migration
- DB: `ALTER TABLE jobs ADD COLUMN duration INTEGER NULL;` (Unit B, optional).
- DB: `ALTER TABLE jobs ADD COLUMN error_kind TEXT NULL;` if not already present (Unit D, dispatcher).
- Memory updates after merge:
  - `feedback_lp_models_removed.md` — supersedes `project_lp_deprecation_2026_10_05.md`
  - `feedback_default_output_count_x4.md` — supersedes `feedback_output_count_x1.md` (rule still holds; reason updated)
  - `feedback_l2_paywall_free_tier.md` — new
  - `feedback_default_image_aspect_1_1.md` — new

### Docs
- `docs/PROJECT_SPINE.md` — update §"Composer UI" + §"Models" + §"Edit view" (minor, post-merge in single docs PR)
- `docs/FLOW_UI_REFERENCE.md` — append §"2026-05 composer panel structure"

### Risks
1. **Submit-time endpoint shape unknown** — Unit B passes `duration` through JobCreate but UI selector for duration is not yet identified. Codex implementer MUST capture this via 1 zero-credit composer probe (open composer in different mode, look for duration chip) BEFORE assuming the UI exposes it. If not exposed, skip silently.
2. **Agent toggle endpoint shape** — captured PATCH URL is known but body shape (`{"agent_toggle_state": "DISABLED"}` vs `false` vs `0`) needs live confirmation. Unit C codex MUST capture one live PATCH via MCP browser session before coding.
3. **Free-tier worker pool capacity** — if L2 jobs fail `paid_tier_required`, throughput drops. Out of scope for this PRD but flag for follow-up: engine may need to refuse L2 job creation when no paid profile available (server-side guard).

---

## 5. Sequencing

All 4 units are **file-disjoint** → fan-out parallel (rule §2 Codex orchestration).

```
                       ┌─ Unit A (model registry)
master ── branch ──────┼─ Unit B (composer)
                       ├─ Unit C (agent disable)
                       └─ Unit D (paywall)
                            ↓ all PRs ready
                       hybrid review per PR (Claude + Codex)
                            ↓
                       reconcile → round-2 fix
                            ↓
                       merge in order: A, C, B, D (B reads A, D reads nothing)
                            ↓
                       integration smoke test
                            ↓
                       live-verify on ngoctuandt20
                            ↓
                       memory + docs PR (single follow-up)
```

Branch names:
- `claude/fe-models-2026-05`
- `claude/fe-composer-2026-05`
- `claude/fe-agent-disable-2026-05`
- `claude/fe-l2-paywall-2026-05`

Each PR `--base master --title "feat(scope): ..."`, body `Closes #N` once issues are minted.

---

## 6. Open questions for user

1. **Image model default** — engine entries free tier ngoctuandt20. Default `nano-banana-pro` OK or prefer `nano-banana-2` (cheaper? unverified)?
2. **Duration default for video** — accept Flow's new default (probably 8s) or force to a known value? 8s = higher credit cost than baseline 5s.
3. **L2 free-tier fail behavior** — fail clean with `paid_tier_required` (PRD assumption) OR auto-skip L2 jobs at server-side when no paid profile in pool?
4. **Veo 3.1 Lite cost** — UI shows "40 credits at x4" → 10 credits each. Memory had Lite = 0 credits previously (rewarm verification). Re-verify before "free engine" claims hold? Likely needs live capture.
5. **Submit-time endpoint capture** — burn ~30 credits on ngoctuandt20 to capture Veo 3.1 Lite + Nano Banana Pro payloads BEFORE codex fan-out, OR let each codex unit do its own probe?

Recommended answers (Claude's defaults if no override):
1. `nano-banana-pro` (matches Flow's own default)
2. Keep at Flow's default (8s); add `duration` to JobCreate so caller can override
3. `paid_tier_required` clean fail (Unit D's plan). Server-side guard is follow-up.
4. Re-verify: include 1 live submit in autopilot live-verify pass post-merge (counted in autopilot's credit tally)
5. Burn ~30 credits upfront for shared capture — saves duplicate work across 4 units
