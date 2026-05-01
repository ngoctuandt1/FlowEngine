# Session Reports — Index Map

> Single source of truth for navigating every debug / verify / handoff / RCA
> session report under `docs/session-reports/`. Update when adding a new
> report. The `_TEMPLATE.md` defines the report skeleton (do not list it).

**Last regenerated:** 2026-05-01 (covers 57 reports across `2026-04-17 -> 2026-05-01`).

---

## How to use this index

1. **Reading a specific area?** Jump to [§5 Cross-reference by engine area](#5-cross-reference-by-engine-area).
2. **Looking for the latest state?** Read [§6 Parked-item tracker](#6-parked-item-tracker) and the most-recent handoff in §3.
3. **Onboarding / archaeology?** Walk [§3 Timeline](#3-timeline) phase-by-phase top-to-bottom.
4. **Adding a new report?** Use `_TEMPLATE.md`, append a row to the right phase in §3, then update §5/§6 if applicable.

---

## 1. Status legend

| Symbol | Meaning |
|---|---|
| ✅ | Shipped + live-verified, no follow-up debt |
| 🟡 | Shipped but a known gap remains (verification debt or edge case) |
| 🔴 | Blocked / parked — depends on external input or future session |
| 🗄️ | Historical / superseded — kept for archaeology, not load-bearing |
| 🧪 | Probe / read-only investigation, no code touched |
| 📋 | Handoff or retro — meta document, not a fix |

---

## 2. Report-type taxonomy

| Type | Convention | What it captures |
|---|---|---|
| `B<n>` bug-fix | `YYYY-MM-DD_B<n>_<slug>.md` | One bug from `WORKPLAN.md §3.B<n>`, single PR, code + test |
| Tier validation | `YYYY-MM-DD_Tier1_*` (DOM probe) / `Tier2_*` (live e2e) | Cross-bug DOM/live verify across multiple `B<n>` |
| Discrete verify | `_discrete-*_verify*` | Targeted live run for one invariant or one chain shape |
| Probe | `_*_probe*`, `_dom-validation*` | Read-only DOM/network probe — no code touched |
| RCA | `_root-cause*`, `_*_rca*`, `_cdp-wrong-page*` | Root-cause analysis when prior fix did not land |
| Live-verify | `_live_verify_*`, `_*-live*`, `_*-live-verified*` | Live runs against Flow with credit cost |
| Codex / parallel | `_codex_*` | Work delegated to Codex CLI |
| Handoff / retro | `_session-handoff*`, `_post_rebase_*`, `_stash-triage_*`, `_triage_*` | Meta — review state at session boundary |
| Re-verify | `_*_re*verify*`, `_low-items*` | Re-running an old fix on current master |
| Infra | `_CI_setup*`, `_tests_*` | Test foundation / pipeline / coverage |

---

## 3. Timeline

### Public web cutover + dashboard expansion (2026-05-01)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 05-01 | [web-ai-hassio-flowengine-cutover](2026-05-01_web-ai-hassio-flowengine-cutover.md) | deploy + live verify | ✅ | Public `ai.hassio.io.vn` moved from legacy `video-ai-studio` to FlowEngine; auth/page/backend PR train merged and 26 categories live-verified |

### Phase A foundation — bug-fix sweep (2026-04-17)

Single-day landing of B1-B12 + Tier 1 DOM validation. Tag: `v0.2.0-phase-a` at `db4c746`.

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-17 | [B1a_aspect-ratio-research](2026-04-17_B1a_aspect-ratio-research.md) | RCA + probe | 🗄️ | Identified Radix chip pattern (precursor to B1b impl) |
| 04-17 | [B1b_aspect-ratio-impl](2026-04-17_B1b_aspect-ratio-impl.md) | `B1` fix | ✅ | Aspect ratio via Radix chip (`b359c84`) |
| 04-17 | [B2_bbox-verify](2026-04-17_B2_bbox-verify.md) | `B2` verify | 🗄️ | Initial bbox verify; flipped → B11 needed |
| 04-17 | [B3_camera-preset-verify](2026-04-17_B3_camera-preset-verify.md) | `B3` verify | 🗄️ | Initial camera verify; flipped → B12 needed |
| 04-17 | [B5_completed-at](2026-04-17_B5_completed-at.md) | `B5` fix | ✅ | Auto-set `completed_at` on terminal status (`4d24c10`) |
| 04-17 | [B6_profile-current-job](2026-04-17_B6_profile-current-job.md) | `B6` fix | ✅ | Track `profiles.current_job_id` (`0118e6d`) |
| 04-17 | [B7_port-mismatch](2026-04-17_B7_port-mismatch.md) | `B7` fix | ✅ | Server port default 8000 → 8080 (`a95c9b5`) |
| 04-17 | [B8_datetime-utcnow](2026-04-17_B8_datetime-utcnow.md) | `B8` fix | ✅ | 7× `datetime.utcnow()` → `datetime.now(UTC)` (`573cffd`) |
| 04-17 | [B9_test-foundation](2026-04-17_B9_test-foundation.md) | `B9` fix | ✅ | pytest + fixtures + temp DB + api_client (`adca116`) |
| 04-17 | [B11_bbox-canvas-fix](2026-04-17_B11_bbox-canvas-fix.md) | `B11` fix | ✅ | Bbox: target largest `<canvas>` ≥300px (`ce6683a`) |
| 04-17 | [B12_camera-verify-fix](2026-04-17_B12_camera-verify-fix.md) | `B12` fix | ✅ | Camera preset verify via `getComputedStyle` color sum (`78d3e40`) |
| 04-17 | [B14_base-nav-verify](2026-04-17_B14_base-nav-verify.md) | `B14` verify | ✅ | Base navigation harness verified |
| 04-17 | [B15_extend-panel-verify](2026-04-17_B15_extend-panel-verify.md) | `B15` verify | ✅ | Extend panel `_verify_extend_panel` + slate selector |
| 04-17 | [Tier1_dom-validation](2026-04-17_Tier1_dom-validation.md) | Tier 1 probe | 🗄️ | First DOM probe round; flipped B2/B3 |
| 04-17 | [Tier1r2_revalidation](2026-04-17_Tier1r2_revalidation.md) | Tier 1 r2 | ✅ | Post-fix re-probe; B11/B12 verified |
| 04-17 | [stash-triage_flow-refinements](2026-04-17_stash-triage_flow-refinements.md) | 📋 triage | 🗄️ | Analyzed 4-file stash; KEEP-4/5/6 cherry-picked |
| 04-17 | [triage_flow-cleanup](2026-04-17_triage_flow-cleanup.md) | 📋 triage | 🗄️ | Retroactive report (pre-rule §1.6) |

### Phase A wrap + locale + chain inheritance (2026-04-18)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-18 | [B4_chains-table](2026-04-18_B4_chains-table.md) | `B4` fix | ✅ | Persist chain metadata + aggregated status API (`4dcf50f`) — un-deferred from Phase A |
| 04-18 | [B10_pydantic-default-factory](2026-04-18_B10_pydantic-default-factory.md) | `B10` fix | ✅ | Migrate `default_factory=datetime.utcnow` → tz-aware (`fe13870`) |
| 04-18 | [B16_submit-iterate](2026-04-18_B16_submit-iterate.md) | `B16` fix | ✅ | Submit confirmation iterate strategy |
| 04-18 | [B17_lp-precheck](2026-04-18_B17_lp-precheck.md) | `B17` fix | ✅ | LP pre-flight credit check |
| 04-18 | [B18_homepage-locale-fix](2026-04-18_B18_homepage-locale-fix.md) | `B18` fix | ✅ | Locale-independent homepage detection |
| 04-18 | [B19_aspect-chip-multiline](2026-04-18_B19_aspect-chip-multiline.md) | `B19` fix | ✅ | Aspect chip multi-line label |
| 04-18 | [B22_l2-inheritance](2026-04-18_B22_l2-inheritance.md) | `B22` fix | 🟡 | Direct-parent media_id inheritance (later refined; B30/B32 walk-up superseded 2026-04-20) |
| 04-18 | [Tier2_e2e-live](2026-04-18_Tier2_e2e-live.md) | Tier 2 live | ✅ | First end-to-end live run on `ngoctuandt20` |

### Phase A polish + multi-bug post-merge (2026-04-19)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-19 | [B20_B21_cleanup](2026-04-19_B20_B21_cleanup.md) | `B20`+`B21` | ✅ | Locale + helper cleanup |
| 04-19 | [B26_submit-and-model-exact-text](2026-04-19_B26_submit-and-model-exact-text.md) | `B26` fix | ✅ | Submit + LP model exact-text matching |
| 04-19 | [B28_B29_probe](2026-04-19_B28_B29_probe.md) | 🧪 probe | 🗄️ | DOM probe for extend-output sidebar + stale URL |
| 04-19 | [B30_B28_B29_combined](2026-04-19_B30_B28_B29_combined.md) | combined fix | 🟡 | Walk-up inheritance — superseded by B22 direct-parent on 2026-04-20 |
| 04-19 | [Tier2_Run10_VI_final](2026-04-19_Tier2_Run10_VI_final.md) | Tier 2 live | ✅ | VI locale chain final run |
| 04-19 | [Tier2_Run12_B32_verify](2026-04-19_Tier2_Run12_B32_verify.md) | live verify | 🟡 | B32 verify (later superseded) |
| 04-19 | [Tier2_Run15_B37_verify](2026-04-19_Tier2_Run15_B37_verify.md) | live verify | ✅ | B37 verify (locale-independent submit) |
| 04-19 | [Tier2_Run19_L2_chain_blocked](2026-04-19_Tier2_Run19_L2_chain_blocked.md) | live RCA | ✅ | L2 chain blocked → fixed in B30→B22 work |
| 04-19 | [discrete-2job-verify_en](2026-04-19_discrete-2job-verify_en.md) | discrete live | ✅ | L1 → close-tab → new-tab → L2 chain on EN |
| 04-19 | [download-probe](2026-04-19_download-probe.md) | 🧪 probe | 🗄️ | Download UI/API surface probe |
| 04-19 | [tests_2-3-4_ui](2026-04-19_tests_2-3-4_ui.md) | infra | ✅ | WORKPLAN §5.2 Tests 2/3/4 full-UI chain |
| 04-19 | [tests_5-6-7_infra](2026-04-19_tests_5-6-7_infra.md) | infra | ✅ | WORKPLAN §5.2 invariants suite |
| 04-19 | [CI_setup](2026-04-19_CI_setup.md) | infra | ✅ | GitHub Actions pytest workflow |

### Image upscale + Codex parallel + post-rebase (2026-04-20)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-20 | [Tier2_Run20_L2_extend_H1_confirmed](2026-04-20_Tier2_Run20_L2_extend_H1_confirmed.md) | live verify | ✅ | L2 extend H1 confirmed on real chain |
| 04-20 | [composer-chip-fix-and-4k-live](2026-04-20_composer-chip-fix-and-4k-live.md) | live verify | ✅ | Composer-chip selector fix; 4K image x3 verified |
| 04-20 | [image-composer-race-fix](2026-04-20_image-composer-race-fix.md) | RCA + fix | ✅ | Image composer race in fallback path (PR #25) |
| 04-20 | [upscale-c6b-impl](2026-04-20_upscale-c6b-impl.md) | impl | ✅ | C6b upscale + image 2K/4K env-gated path (PR #24) |
| 04-20 | [upscale-c6c-review-response](2026-04-20_upscale-c6c-review-response.md) | response | 🗄️ | Superseded by PR #28 multi-image iterate |
| 04-20 | [codex_gap_fill_i2v_t2i](2026-04-20_codex_gap_fill_i2v_t2i.md) | codex | ✅ | i2v + t2i gap fill (PR #19) |
| 04-20 | [codex_ingredients_image](2026-04-20_codex_ingredients_image.md) | codex | ✅ | Ingredients image refs (PR #20) |
| 04-20 | [post_rebase_hash_reconciliation](2026-04-20_post_rebase_hash_reconciliation.md) | 📋 meta | 🗄️ | Post-rebase commit-hash bookkeeping |
| 04-20 | [session-handoff](2026-04-20_session-handoff.md) | 📋 handoff | 🗄️ | Session handoff after image-upscale epic — superseded by [2026-04-25_session-handoff](2026-04-25_session-handoff.md) |

### L2 media_id resolution (2026-04-23)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-23 | [l2-media-id-fix-live-verified](2026-04-23_l2-media-id-fix-live-verified.md) | live verify | ✅ | L2 insert/remove media_id resolved; commits `0bb9d29` + refactor `b62ac73` (PR #37); doc PR `e79405d`/#53 |

### CDP / marketing-landing hardening (2026-04-24)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-24 | [marketing-landing-hardening](2026-04-24_marketing-landing-hardening.md) | RCA + fix | 🟡 | 4-iteration hardening (force=True + reload-retry + tag-agnostic). PR #52. Edge case from 04-24 live re-verify still open. |
| 04-24 | [cdp-wrong-page-root-cause](2026-04-24_cdp-wrong-page-root-cause.md) | RCA | ✅ | CDP picked `chrome://omnibox-popup` instead of real tab — fixed by `_start_cdp` filter (PR #52) |
| 04-24 | [live_verify_post_45_44](2026-04-24_live_verify_post_45_44.md) | live verify | 🟡 | J1 fail (marketing landing edge case), J2 ok, J3 reused-project finding (later NO-REPRO), INS ok, REM fail single-shot |

### Live re-verify + frontend phase entry (2026-04-25)

| Date | Report | Type | Status | Outcome |
|---|---|---|---|---|
| 04-25 | [low-items-live-reverify](2026-04-25_low-items-live-reverify.md) | re-verify | ✅ | LOW-items 3/5 re-verified; PR #49 + #54 |
| 04-25 | [finding3-no-repro](2026-04-25_finding3-no-repro.md) | re-verify | ✅ | 2026-04-24 Finding 3 → NO REPRO; root cause likely stale concurrent worker |
| 04-25 | [session-handoff](2026-04-25_session-handoff.md) | 📋 handoff | 🟡 | Current handoff. 5 gaps tracked; Gap 2 (#45/#46 multi-profile) BLOCKED on second account; Gap 4 (doc cleanup) DONE |

---

## 4. PR ↔ report cross-reference

| PR | Description | Primary report(s) |
|---|---|---|
| #90-#108 (excluding #93) | Public dashboard/pages, auth gate, deploy hardening, public cutover | `2026-05-01_web-ai-hassio-flowengine-cutover` |
| #2-#8 | flow-bugs epic | `B<n>` reports + Tier 1 r2 |
| #19 | i2v + t2i gap fill | `2026-04-20_codex_gap_fill_i2v_t2i` |
| #20 | Ingredients image refs | `2026-04-20_codex_ingredients_image` |
| #24 | Image 2K/4K UI path | `2026-04-20_upscale-c6b-impl` + `composer-chip-fix-and-4k-live` |
| #25 | Composer-chip fallback | `2026-04-20_image-composer-race-fix` |
| #26 | Unified composer-menu selectors | `2026-04-20_session-handoff` §1 |
| #27 | +36 unit tests image upscale | `2026-04-20_session-handoff` §1 |
| #28 | Iterate all image media_ids | `2026-04-20_session-handoff` §1 |
| #37 | `resolve_final_media_id` refactor | `2026-04-23_l2-media-id-fix-live-verified` |
| #43 | L1 parallelism task pool | (no dedicated report) |
| #44 | Marketing-landing bypass | `2026-04-24_marketing-landing-hardening` |
| #46 | Cold-start DOM scrape fallback | `2026-04-24_live_verify_post_45_44` (NOT EXERCISED) |
| #49 | Window size/position env | `2026-04-25_low-items-live-reverify` part 2 |
| #52 | CDP page filter + browser pool | `2026-04-24_cdp-wrong-page-root-cause` + `marketing-landing-hardening` |
| #53 | Mark L2 media_id RESOLVED | `2026-04-23_l2-media-id-fix-live-verified` |
| #54 | LOW-items live re-verify docs | `2026-04-25_low-items-live-reverify` |
| #55 | Selector-fix perf + cleanup + handoff | `2026-04-25_finding3-no-repro` + `session-handoff` |
| #56 | Frontend Flow-style dark theme | (no engine session report — frontend phase) |

---

## 5. Cross-reference by engine area

### Job-type coverage

| Job type | Implementation | Last live-verify report |
|---|---|---|
| `text-to-video` (L1) | ✅ `flow/operations/generate.py` | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `text-to-image` | ✅ `flow/operations/generate.py` (image branch) | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `extend-video` | ✅ `flow/operations/extend.py` | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `camera-move` | ✅ `flow/operations/camera.py` | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `insert-object` | ✅ `flow/operations/insert.py` | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `remove-object` | ✅ `flow/operations/remove.py` | `2026-05-01_web-ai-hassio-flowengine-cutover` (public deploy cutover) |
| `frames-to-video` | ✅ `flow/operations/generate.py` (frames branch) | `2026-05-01_web-ai-hassio-flowengine-cutover` (first live verify) |
| `ingredients-to-video` | ✅ `flow/operations/generate.py` (ingredients branch) | `2026-05-01_web-ai-hassio-flowengine-cutover` (first live verify) |

### Engine layers

| Layer | Status | Reports touching it |
|---|---|---|
| Server (FastAPI + SQLite) | ✅ | `B7_port-mismatch`, `B5_completed-at`, `B6_profile-current-job`, `B9_test-foundation`, `2026-05-01_web-ai-hassio-flowengine-cutover` |
| Dashboard auth gate | ✅ | PR #95 + `2026-05-01_web-ai-hassio-flowengine-cutover` |
| Worker claim loop | ✅ | `B6_profile-current-job`, PR #43 |
| Profile pinning | ✅ | `bug-4-profile-pinning` (epic) + `B6` |
| Project lock | ✅ | `bug-7-project-lock` (epic) |
| Browser pool (per-profile) | ✅ | PR #52 + `marketing-landing-hardening` |
| CDP page selection | ✅ | `cdp-wrong-page-root-cause` |
| WebSocket keepalive | ✅ | PR #104 + `2026-05-01_web-ai-hassio-flowengine-cutover` |
| Upload validation + media mounts | ✅ | PR #105 + `2026-05-01_web-ai-hassio-flowengine-cutover` |
| CORS / proxy guard | ✅ | PR #107 + `2026-05-01_web-ai-hassio-flowengine-cutover` |
| Login auto-handling | 🟡 | `feedback_warm_profile_manual_gmail` memory + `feedback_flow_service_not_allowed_account_dead` memory |
| Marketing-landing bypass | 🟡 | `marketing-landing-hardening` + `live_verify_post_45_44` (edge case open) |
| Aspect ratio | ✅ | `B1a/B1b_aspect-ratio*` + `B19_aspect-chip-multiline` |
| Bbox (insert/remove) | ✅ | `B2_bbox-verify` → `B11_bbox-canvas-fix` |
| Camera preset | ✅ | `B3_camera-preset-verify` → `B12_camera-verify-fix` |
| Output count enforcement | ✅ | `feedback_output_count_x1` memory |
| Model selector (LP) | ✅ | `bug-8-lp-credit-leak` (epic) + `B26_submit-and-model-exact-text` |
| Composer mode chip | ✅ | `composer-chip-fix-and-4k-live` |
| Submit confirmation | ✅ | `B16_submit-iterate` + `B26` |
| Wait-for-completion | ✅ | `B14_base-nav-verify` + Tier 2 runs |
| Download pipeline | ✅ | `download-probe` + `B19` |
| Image upscale (2K/4K) | ✅ | `upscale-c6b-impl` + `composer-chip-fix-and-4k-live` + `low-items-live-reverify` |
| Media_id extraction | 🟡 | `l2-media-id-fix-live-verified` (production OK; 3 xfail edge cases remain) |
| Cold-start download race | 🟡 | PR #46 partial; `live_verify_post_45_44` "NOT EXERCISED" |
| Chain inheritance | ✅ | `B22_l2-inheritance` + `B30_B28_B29_combined` (walk-up superseded) |
| L2 navigation by edit_url | ✅ | `bug-5-nav-media-id` (epic) |
| Locale independence | ✅ | `B18_homepage-locale-fix` + `Tier2_Run10_VI_final` |
| `+ New project` perf | ✅ | (this branch — selector fix `4721912` saves ~16s/job) |
| Window geometry env | ✅ | `low-items-live-reverify` part 2 |
| Frontend (vanilla SPA) | ✅ | PRs #90/#91/#92/#94/#96/#97/#98/#99 + fixes #100-#103; see `2026-05-01_web-ai-hassio-flowengine-cutover` |

---

## 6. Parked-item tracker

Each item lists the reports that touched it, in order. The most recent entry is the source of truth for current status.

### P-01 — Cold-start download race (#45 / PR #46) 🟡

- 2026-04-24 [live_verify_post_45_44](2026-04-24_live_verify_post_45_44.md) — PR #46 NOT EXERCISED on single-profile run; needs ≥4 profile multi-profile retest.
- 2026-04-25 [session-handoff §Gap 2](2026-04-25_session-handoff.md) — **BLOCKED** on second Flow-eligible account (only `ngoctuandt20` viable).

### P-02 — Marketing-landing A/B variance edge case 🟡

- 2026-04-24 [marketing-landing-hardening](2026-04-24_marketing-landing-hardening.md) — 4-iteration fix landed in PR #52.
- 2026-04-24 [live_verify_post_45_44 §"PR #44 verdict"](2026-04-24_live_verify_post_45_44.md) — J1 still failed; selector matched scroll-anchor variant. Hardening incomplete for this A/B variant.
- 2026-04-25 [session-handoff §Gap 1 + finding3-no-repro](2026-04-25_session-handoff.md) — Finding 3 (separate from this) NO-REPRO; underlying landing edge case still open.

### P-03 — REM `Failed to find Remove button` (single occurrence) 🟢

- 2026-04-24 [live_verify_post_45_44 §"REM failure"](2026-04-24_live_verify_post_45_44.md) — single sample; could be DOM shift post-INS.
- 2026-04-25 [session-handoff §Gap 3](2026-04-25_session-handoff.md) — LOW priority; file issue only if repro on next run.

### P-04 - frames-to-video / ingredients-to-video live verification ✅

- 2026-04-20 [codex_gap_fill_i2v_t2i](2026-04-20_codex_gap_fill_i2v_t2i.md) - code complete (PR #19).
- 2026-04-20 [codex_ingredients_image](2026-04-20_codex_ingredients_image.md) - code complete (PR #20).
- 2026-05-01 [web-ai-hassio-flowengine-cutover](2026-05-01_web-ai-hassio-flowengine-cutover.md) - both categories live-verified on the public `ai.hassio.io.vn` deploy.

### P-05 — Defensive image-upscale branches not exercised live 🟢

- 2026-04-25 [low-items-live-reverify §"Branches still NOT live-exercised"](2026-04-25_low-items-live-reverify.md) — `done`-immediate / `failed`-retry / exhausted-fallback covered by unit tests only. Acceptable.

### P-06 — `media_id` xfail edge cases (3) 🟢

- 2026-04-23 [l2-media-id-fix-live-verified](2026-04-23_l2-media-id-fix-live-verified.md) — production resolver passes live.
- `tests/test_extend.py` + `tests/test_camera_l2.py` — 3 synthetic-fixture edge cases stay `xfail` (commit `4721912` rewrites the reasons; bug fix scope did not cover these orderings).

### P-07 — RESOLVED ✅ (was: B4 chains table + B10 Pydantic residual)

Both items shipped on 2026-04-18 — kept as a closed entry only so the
historical "Deferred" classification in earlier `CLAUDE.md` revisions does
not lead future readers astray:

- B4 — landed `4dcf50f` (chain metadata + aggregated status API) per [B4_chains-table report](2026-04-18_B4_chains-table.md).
- B10 — landed `fe13870` (tz-aware `default_factory`) per [B10 report](2026-04-18_B10_pydantic-default-factory.md).

---

## 7. Quick lookup — by query

**"How does X work today?"** — read the most recent report that touched X (per §5), then `flow/<module>.py` source.

**"What changed when?"** — `git log --oneline -p -- <path>` + the report filenames in the commit messages.

**"Why was Y left in this state?"** — find Y in §6 parked tracker, walk the chain of reports.

**"What's next?"** — read the latest `*_session-handoff.md` (currently [2026-04-25_session-handoff.md](2026-04-25_session-handoff.md)).

**"Where's a report on bug-fix B<n>?"** — the file is named `*_B<n>_*.md`. If absent, check if multiple bugs were combined (e.g. `B30_B28_B29_combined`) or if it was deferred (B4, B10).

---

## 8. Maintenance rules

1. **Append-only.** Reports are an audit trail — never delete, edit only the index entry's status if the underlying claim changes.
2. **One PR ↔ ≥1 session report.** Either a dedicated report file or coverage in a multi-bug / handoff report.
3. **Mark superseded entries 🗄️.** Add a one-line "superseded by `<file>`" note in the §3 timeline row.
4. **Parked items live in §6.** When a parked item closes, mark it ✅ + add the closing report; do not delete the entry.
5. **Update `Last regenerated:`** whenever you add or re-classify a report.
