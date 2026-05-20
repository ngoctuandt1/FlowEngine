# Codex Review 2 — PRD v2

## Round-1 fix verification
### Critical
- C1 (strip duration): PASS — v2.md:62, v2.md:354, v2.md:372 — active duration schema/AC removed; remaining text is defer/probe only.
- C2 (file-disjoint): PASS — v2.md:75, v2.md:113, v2.md:225, v2.md:324 — A→B and D→E serialization now explicit; no false all-parallel claim remains.
- C3 (Agent hook): PARTIAL — v2.md:149, v2.md:153, v2.md:170 — nonexistent `FlowClient.navigate_to_project()` replaced with real `reset_for_next_job`, but PRD does not grant ownership for callers that pass `target_url`; current dispatcher lease path still cannot trigger project-scoped Agent OFF.
- C4 (reverseAPI auth): PASS — v2.md:159, v2.md:162, v2.md:164, v2.md:165 — cross-origin auth capture is now a pre-coding gate; DOM fallback required if auth cannot be reproduced.
- C5 (`error_kind` persistence): PASS — v2.md:227, v2.md:248, v2.md:249, v2.md:250 — Unit E owns models, DDL/migration, and store serialization for `error_kind`/`error_message`.
- C6 (batch/multitab paywall swallow): PASS — v2.md:187, v2.md:211, v2.md:212 — `_multitab.py` re-raise and L2 batch dispatcher inspection are now in Unit D.
- C7 (Unit B L2 count AC): PASS — v2.md:69, v2.md:137, v2.md:213 — L2 count enforcement removed from Unit B and deferred until paid L2 composer is observed.

### Important
- I1 (`L2PaywallError` ownership): PASS — v2.md:186, v2.md:204 — exception lives in owned `_base.py`; no unowned `flow/exceptions.py`.
- I2 (`schema.sql` wrong): PASS — v2.md:229, v2.md:249, v2.md:317 — schema/migrations point to `server/db/database.py` and `_ensure_job_column`.
- I3 (test paths): PASS — v2.md:103, v2.md:138, v2.md:269, v2.md:311 — tests are repo-root `tests/test_*.py`; no `tests/flow/...` tree.
- I4 (image registry over-scope): PASS — v2.md:31, v2.md:32, v2.md:63, v2.md:88 — image registry migration removed; `image.py` remains read-only.
- I5 (old LP defaults outside Unit A): PASS — v2.md:99, v2.md:246, v2.md:253, v2.md:254 — aliases stay in Unit A; server/worker/frontend LP defaults move to Unit E.
- I6 (missing L2 buttons as paywall): PASS — v2.md:206, v2.md:207 — only banner + Upgrade CTA are positive signal; missing buttons are diagnostics.
- I7 (canonical error kind): PASS — v2.md:208, v2.md:209, v2.md:288, v2.md:292 — canonical shape is `error_kind="paid_tier_required"` plus separate op/job type.
- I8 (Agent persistent mutation): PASS — v2.md:169, v2.md:172, v2.md:173, v2.md:174 — per-job timing, mutation log, optional restore, and fallback failure behavior are specified.
- I9 (chip icon over-spec): PASS — v2.md:130, v2.md:131 — role/text/menu discovery replaces exact icon inventory.
- I10 (integration smoke ownership): PASS — v2.md:262, v2.md:266, v2.md:277, v2.md:313 — smoke test is Unit F final reconciliation only.
- I11 (Unit C reasoning): PASS — v2.md:146 — Unit C raised to `xhigh`.

## New issues introduced
### Critical
- NC1 Unit C Agent OFF hook still not reachable — v2.md:149-v2.md:157 owns only `flow/agent.py` + `flow/client.py`, while v2.md:170 requires a project-bearing `target_url`; current `worker/dispatcher.py:123` through `worker/dispatcher.py:139` calls `_client_lease(profile)` without `reset_url`, and `worker/browser_pool.py:133` is read-only to Unit C. Result: L2 handlers navigate after lease, so Agent can stay ON and Unit C AC becomes mostly no-op. Fix: grant Unit C narrow ownership of dispatcher/browser-pool reset URL plumbing, or move Agent helper into actual L1/L2 navigation helpers.
- NC2 Unit D cannot produce canonical single-job paywall result — v2.md:188 limits `worker/dispatcher.py` ownership to L2 batch path around `1288`-`1315`, but v2.md:214 requires single-job dispatcher handling and v2.md:288-v2.md:293 require `error_kind`/`error_message`. Current single-job catch at `worker/dispatcher.py:577` and `worker/dispatcher.py:709` through `worker/dispatcher.py:749` would return generic `error` only. Fix: explicitly give Unit D ownership of a narrow `L2PaywallError` catch/result adapter in `dispatch_job`.

### Important
- NI1 Unit A does not explicitly remove stale “free = 0 credits” selector semantics — v2.md:97-v2.md:102 updates labels/default only, while v2.md:300 says Lite must not be assumed free. Current `flow/model_selector.py:194`, `flow/model_selector.py:391`, and `flow/model_selector.py:444` still verify 0 credits and `flow/model_selector.py:205` through `flow/model_selector.py:209` coerces free mode back to `*-lp`. Fix: Unit A AC must retire LP coercion/0-credit hard fail and leave budget enforcement to Unit B.
- NI2 Unit B credit guard budget is undefined — v2.md:136 and v2.md:299 require “configured budget” but name no env/config field, default, or test value. Implementers can pick incompatible thresholds. Fix: specify one source, e.g. `FLOW_MAX_CREDITS_PER_JOB`, default, and failure message.
- NI3 LP purge misses `_multitab.py` fallback defaults — v2.md:187 gives Unit D `_multitab.py` for paywall work, v2.md:240 forbids Unit E from `flow/*`, and no AC replaces `flow/operations/_multitab.py:305` or `flow/operations/_multitab.py:586` `veo-3.1-fast-lp` fallbacks. Fix: assign those fallback replacements to Unit D while it already owns `_multitab.py`, or create explicit post-D ownership transfer.

### Minor
- NM1 Live capture artifact path unclear — v2.md:162-v2.md:164 says paste Agent PATCH details into PR notes, while v2.md:280 asks final checklist screenshots/network captures. PR notes can vanish from implementer context. Prefer named artifact path or issue comment template.
- NM2 Unit C gets no immediate tests — v2.md:175 pushes Agent coverage to Unit F only. File-disjointness improves, but failures surface late; acceptable only if NC1 hook ownership is fixed and Unit F smoke asserts real call path.

## Spec-compliance vs Discovery
- [PASS] LP removal + aliases covered — v2.md:45-v2.md:47, v2.md:97-v2.md:101, v2.md:246-v2.md:255 | discovery.md:36-discovery.md:52.
- [PASS] Composer restructure and Video force covered — v2.md:49-v2.md:52, v2.md:130-v2.md:137 | discovery.md:61-discovery.md:77, discovery.md:252-discovery.md:258.
- [GAP] Credit preview guard exists but config/selector semantics incomplete — v2.md:136, v2.md:295-v2.md:300 | discovery.md:31-discovery.md:34, discovery.md:52-discovery.md:53, discovery.md:71.
- [PASS] Free-tier L2 paywall detection covered — v2.md:203-v2.md:219 | discovery.md:79-discovery.md:94, discovery.md:230-discovery.md:232.
- [PASS] Duration deferral covered — v2.md:62, v2.md:354, v2.md:372-v2.md:373 | discovery.md:284, discovery.md:301-discovery.md:302.
- [GAP] Agent OFF intent covered but hook is not wired to job project URLs — v2.md:159-v2.md:175 | discovery.md:105-discovery.md:115, discovery.md:259-discovery.md:262.
- [PASS] Asset picker existing-media/voice deferred explicitly — v2.md:34, v2.md:64-v2.md:65 | discovery.md:140-discovery.md:158.
- [PASS] Project auto-name deferred explicitly — v2.md:66 | discovery.md:174-discovery.md:178.
- [PASS] Image catch-up no longer over-scoped — v2.md:31-v2.md:32, v2.md:63 | discovery.md:184-discovery.md:199 and current `flow/operations/image.py:58`, `flow/operations/image.py:71`, `flow/operations/image.py:341`.
- [PASS] False-assumption flags carried forward — v2.md:361-v2.md:376 | review1.md:51-review1.md:60.

## Fan-out readiness assessment
- Wave 1 (A, C, D parallel): NOT READY — file overlap mostly fixed, but Unit A stale credit semantics, Unit C unreachable hook, and Unit D missing single-job dispatcher ownership remain blockers.
- Wave 2 (B after A): NOT READY — B depends on A retiring 0-credit selector hard fail and needs an explicit credit-budget config.
- Wave 3 (E after D): NOT READY — D must emit canonical `error_kind` in single-job and batch results before E persistence can verify it; D/E dispatcher line split itself is okay.
- Wave 4 (F): NOT READY — smoke/docs can verify integration only after A/C/D ownership gaps are patched.

## Unit-by-unit Verdict
- Unit A: REQUEST_CHANGES
- Unit B: REQUEST_CHANGES
- Unit C: REQUEST_CHANGES
- Unit D: REQUEST_CHANGES
- Unit E: APPROVE
- Unit F: REQUEST_CHANGES

## Recommendation
- NEEDS ROUND-2 FIX
- v2 fixed most Round-1 doc blockers, but fan-out still unsafe. Patch three things before branch creation: Agent target-url ownership, paywall single-job dispatcher result ownership, and model/credit-budget contract.
