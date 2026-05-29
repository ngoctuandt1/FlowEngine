# Handoff: Flow agent-UI rework (2026-05-29 session 2)

**master = CLEAN** (nothing merged; only a throwaway local `integration-rework` branch was used for live testing). 4 PRs open, NOT merged.

## What was discovered (HIGH VALUE — read findings doc)
`plans/findings-260529-flow-agent-ui-redesign.md` — full DOM map of Flow's 2026-05 agent-centric redesign:
- **Single agent composer**, no Video/Image/Frames mode tabs. Output type = which **model** (Nano Banana=image, Omni Flash=video).
- Settings (aspect/count/model/confirm) live behind the **`tune Settings`** button → "Agent settings" panel.
- **Confirm before generating: Always/Never** — Never = auto-generate. Suspected (UNCONFIRMED) root cause of `no_signal_timeout`.
- Project composer submit = `arrow_forward Create`. Edit page (L2) box placeholder = **"Describe your edits"**, submit `add_2 Create`/Enter.
- Homepage "+ New project" button GONE (now an `add_2` card) → `_click_new_project` broken too.

## Validated live (debian, ngoctuandt20, 0–2 credits)
- ✅ `ensure_agent_settings` (PR #295, head `f3e8887`) OPENS the Agent settings panel + clicks Never + Save, returns True. Root cause of earlier panel-open failures: (1) JS `.click()` ignored by React → use Playwright real click via `data-as-*` marker; (2) `has_text="Settings"` matched "View Settings" (settings_2) not tune.

## NOT working / regressions
- 🔴 **PR #298 (L1 rewrite) REGRESSED t2v**: `submit_l1_prompt: no generate request observed within 5s after submit`. t2v L1 previously PASSED (R23). The rewrite replaced a working path. `ensure_agent_settings` did NOT appear in the L1 run logs → either not called, import-guarded-out, or submit path differs. **t2v should NOT have been rewritten — it worked.**
- ❓ L2 end-to-end (extend/camera/insert/remove) never validated because the chain died at L1.
- ❓ Confirm=Never → auto-generate hypothesis NEVER actually confirmed end-to-end (blocked by L1 regression).

## The 4 open PRs
| PR | Branch | Content | State |
|----|--------|---------|-------|
| #295 | claude/agent-settings-module | `flow/agent_settings.py` (NEW) + test script | panel-open VALIDATED live |
| #296 | claude/wait-agent-signal | `flow/wait.py` 4-method detection | branched off #295 (NOT master) — needs rebase; wait.py only via cherry-pick |
| #297 | claude/l2-edit-agent-ui | extend/camera/insert/remove/_base.py | unit-green, NOT live-verified |
| #298 | claude/l1-agent-composer | generate/image/frames_to_video.py | **regresses t2v — needs rework** |

All import `ensure_agent_settings` guarded by try/except ImportError.

## Next session — recommended path
1. **Do NOT merge #298 as-is.** Keep the OLD working t2v L1 path. Only apply agent-settings (Confirm=Never) + the new submit where needed.
2. **First confirm the keystone hypothesis cheaply**: on master + only #295, set Confirm=Never once, then run ONE existing-flow generation to see if `submit_l1_prompt`/old submit fires a generate request. Diagnose why `submit_l1_prompt` sees no generate request in 5s (wrong submit affordance? wrong endpoint match? composer not focused?).
3. **Probe the actual generate network endpoint** (the wait.py agent D flagged this unknown) — capture network during a successful manual generation to learn the real request URL, then fix both submit-detection and wait.py.
4. Rebase #296 onto master (drop stale agent_settings commit).
5. Only after t2v works again end-to-end, layer t2i (Nano Banana model) + f2v (Add Media) + L2 (Confirm=Never).

## CRITICAL UPDATE (session 2, ~23:15) — generation endpoint ERRORS

Traced what actually fires on L1 submit in the new UI (probe `probe_streamchat_resp.py`):
- Submit (type prompt + click `arrow_forward Create`) fires `POST flowCreationAgent:streamChat?alt=sse`.
- **The SSE response is an ERROR**: `data: {"agentMessage":{"agentEvents":[{"errorEvent":{}}]},"isFinalResponse":true}` — empty `errorEvent`, no generation, no new video, composer clears.
- Session GET shows `"agentMode": "AGENT_MODE_PLANNING"` on a STALE session (created 14:42, updated 16:09).
- No `batchAsync`/generate request ever fires. Only streamChat (errored) + logging + analytics.
- Tested with `FLOW_KEEP_AGENT=1` (new guard, see below) so sessions were NOT blocked and a real session existed — STILL errored.

### New code added this session (KEEP — correct but insufficient alone)
- `flow/agent.py`: `_keep_agent()` + `FLOW_KEEP_AGENT=1` env guard that early-returns from `disable_agent_mode_if_active` and `install_agent_session_blocker`. Rationale: the legacy agent-disable/session-blocker logic (built to preserve the old toolbar) sabotages the new agent flow. This guard is needed but did NOT fix generation (streamChat still errors).
- NOT yet committed to a branch — lives in working tree / scp'd to debian only. Commit it next session.

### Open questions blocking the rework (need answers before more code)
1. **Does manual generation even work for `ngoctuandt20` right now?** The empty `errorEvent` could be account/quota/safety, not automation. NEXT STEP: have a human do ONE manual generation in the new Flow UI on this profile while capturing network — if it ALSO errors, it's an account/Flow issue, not our code.
2. **`AGENT_MODE_PLANNING`** — is generation gated behind a planning→execute step? Does the agent show a plan + execute/confirm button that Confirm=Never should auto-accept? The stale planning session may need resetting/deleting first.
3. **Exact successful streamChat request shape** — capture a known-good generation to compare against our driven submit.
4. **New-project nav is broken** — the homepage "+ New project" button is gone (now an `add_2` card that our matchers don't hit); `_click_new_project` fails. Needs a new selector before fresh-project tests work.

### DEFINITIVE root cause (session 2, ~23:25) — agent streamChat rejects automated requests
Captured FULL request + response (probe `probe_full.py`). The request is PERFECT:
```json
{"agentSessionId":"...","agentClientContext":{"projectId":"projects/...","recaptchaContext":{"token":"<len 2126>","applicationType":"RECAPTCHA_APPLICATION_TYPE_WEB"},"turnNumber":1},
 "userMessage":{"userPrompt":{"parts":[{"text":"Create a video of a calm river..."}]}}}
```
Response: `{"agentEvents":[{"errorEvent":{}}],"isFinalResponse":true}` — empty errorEvent.
- Prompt text present, session present, recaptcha token present (len 2126). Request is well-formed.
- Tried deleting sessions + fresh session: STILL errorEvent.
- **User confirmed manual generation WORKS on ngoctuandt20.** So it's not account/quota.
- Conclusion: the agent `streamChat` endpoint REJECTS our automated browser's request (empty errorEvent = server-side reject). Most likely **reCAPTCHA-enterprise trust score**: the cloned-profile/automated Chrome scores too low for the stricter agent endpoint, while the human's real browser scores fine. Aligns with FlowEngine's documented reCAPTCHA history.

### STRATEGIC REFRAME (most important takeaway)
- **The OLD generation path (batchAsync) STILL WORKS**: R23 `t2v` PASSED running master's OLD code (rework #298 was never merged). So L1 generation does NOT need the agent composer at all.
- **Rework PR #298 was wrong to switch L1 (t2v) to the agent `streamChat` path** → that's the t2v regression. **Revert L1 to the old composer+batchAsync submit.** Only fix t2i (mode→model via tune Settings, but keep old SUBMIT) and f2v (composer reveal) on top of the old path.
- **L2 is the real problem**: the old L2 toolbar buttons are GONE, so L2 seemingly must use the agent edit UI → streamChat → which is reCAPTCHA-blocked for automation.
  - **Most promising avenue: reverse-API for L2.** FlowEngine already has `FLOW_EXTEND_VIA_REVERSE` etc. that call batchAsync directly (no UI). If batchAsync still works (it does for t2v), L2 ops may be doable via reverse-API, BYPASSING the agent streamChat wall entirely. R22/R23 extend-revapi failed on `no_signal` but that may be wait.py detection, not generation — investigate.
  - If reverse-API can't drive L2, then L2 is blocked until the agent endpoint's reCAPTCHA trust can be raised (warm profile / real-er browser), which is a separate hard problem.

### Next session concrete plan
1. **Do NOT merge #298.** Revert L1 to old path; layer only t2i model-select + f2v reveal on top.
2. **Investigate L2 via reverse-API** (batchAsync direct) as the primary L2 strategy — most likely to bypass the streamChat reCAPTCHA wall.
3. Keep the `FLOW_KEEP_AGENT` guard (committed) only if/when an agent path is actually needed.
4. If agent path is unavoidable for some op, the reCAPTCHA trust of the automated browser is the blocker to solve (warm profile, fingerprint).

### Bottom line
The rework's UI interactions (open settings, set Confirm=Never, type, submit) all WORK mechanically. The blocker is that Flow's agent generation endpoint returns an opaque error for our submission. This is likely NOT fixable by more selector/code iteration — it needs a human to confirm manual generation works in the new UI + capture a successful request, OR confirmation the account/quota is fine.

## Probe scripts (reusable, on debian /opt/flowengine/scripts)
- `probe_l1_generate_trace.py` — traces network after L1 submit
- `probe_streamchat_resp.py` — captures streamChat SSE response body (shows the errorEvent)
- `probe_after_submit.py` — screenshots + visible-text after submit
- `probe_new_ui_map.py <profile>` — maps composer + tune Settings panel
- `probe_l2_edit_ui_map.py <profile> <project_url> <media_id>` — maps edit-page agent UI
- `test_agent_settings_live.py <profile> [project_url]` — agent_settings smoke test

## Known-good test project (ngoctuandt20)
`https://labs.google/fx/tools/flow/project/99999961-b858-46d4-8d98-597b10577c4f`
