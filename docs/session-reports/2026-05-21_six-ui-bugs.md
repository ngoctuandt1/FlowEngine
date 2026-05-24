# Session Report — `six-ui-bugs` Flow 2026-05 UI bugs

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `six-ui-bugs` |
| Task type | live UI bug-fix / blocker triage |
| Session started | 2026-05-21 19:54 Asia/Bangkok |
| Session ended | 2026-05-21 20:25 Asia/Bangkok |
| Duration actual | `31m` |
| Worker | Codex CLI |
| Branch | `claude/fix-live-test-bugs-2026-05` |
| Profile | `s17524h173` |
| Stop reason | `reCAPTCHA v3 invisible` burned `s17524h173` during Bug B live verify |

---

## 2. Commits landed

```
866c93f  fix(livetest): frames upload via media picker
```

No Bug B-F fix commits landed. Bug B reached submit but hit hard-stop reCAPTCHA before terminal pass; Bugs C-F were not attempted after profile burn.

---

## 3. Files changed

```
flow/operations/frames_to_video.py                 (Bug A committed fix)
scripts/probe_l1_composer_uploads.py               (Bug A/B DOM probe helper)
docs/livetest-2026-05-21/l1_frames_upload_*.png    (Bug A DOM screenshots)
docs/livetest-2026-05-21/l1_frames_upload_probe.json (Bug A DOM JSON)
docs/livetest-2026-05-21/l1_ingredients_upload_*.png (Bug B DOM screenshots, uncommitted)
docs/livetest-2026-05-21/l1_ingredients_upload_probe.json (Bug B DOM JSON, uncommitted)
flow/operations/ingredients.py                     (Bug B attempted patch, not committed)
docs/session-reports/2026-05-21_six-ui-bugs.md     (this report)
```

Pre-existing dirty files remained untouched/unstaged: `flow/landing.py`, `flow/login.py`, `flow/model_selector.py`, `flow/operations/generate.py`, `scripts/warm_profile.py`, and related tests.

---

## 4. Tests / Live Verification

| Bug | Type | Result | Evidence |
|---|---|---|---|
| A | `frames-to-video` | PASS | job `cb41d1dd-37ef-43b3-9a1a-66be3a0e7011`, media `067ba7c3-712d-40bb-b0b2-3738ff7a5d6c`, output `downloads/f2v_720p_1779369059.mp4` |
| B | `ingredients-to-video` | BLOCKED | job `08fba036-4305-4f4f-af52-20811e4b1272`, `recaptcha_v3_invisible_burned_s17524h173` |
| C | `extend-video` | NOT RUN | hard stop after Bug B profile burn |
| D | `insert-object` | NOT RUN | hard stop after Bug B profile burn |
| E | `remove-object` | NOT RUN | hard stop after Bug B profile burn |
| F | `camera-move` | NOT RUN | hard stop after Bug B profile burn |

Validation commands:

```
python -m py_compile flow/operations/frames_to_video.py scripts/probe_l1_composer_uploads.py
python .codex/tmp/submit_poll.py --job cb41d1dd-37ef-43b3-9a1a-66be3a0e7011
python .codex/tmp/submit_poll.py --job 08fba036-4305-4f4f-af52-20811e4b1272
```

---

## 5. Per-Bug Details

### Bug A — frames-to-video upload

- Hypothesis: Flow moved Start/End uploads behind the new media picker, not colocated hidden inputs.
- DOM evidence: `docs/livetest-2026-05-21/l1_frames_upload_probe.json`, `docs/livetest-2026-05-21/l1_frames_upload_before.png`, `docs/livetest-2026-05-21/l1_frames_upload_after.png`.
- Observed DOM: Start slot is a visible `DIV` at composer bottom; one hidden `input[type=file][accept=image/*][multiple]` exists globally; clicking Start opens media picker with `Upload media`, then rights notice `I agree`.
- Fix summary: `flow/operations/frames_to_video.py` now clicks the frame slot, handles media-picker `Upload media`, accepts the rights notice, and waits for media to attach before submit.
- Live PASS: job `cb41d1dd-37ef-43b3-9a1a-66be3a0e7011` completed with media `067ba7c3-712d-40bb-b0b2-3738ff7a5d6c`; project `https://labs.google/fx/tools/flow/project/f456642f-2a3a-4dbd-852d-389c2173e07e`; output `downloads/f2v_720p_1779369059.mp4`.
- Credit cost: preview log `5 credits <= budget 15`; counted as 5 credits.
- Commit: `866c93f fix(livetest): frames upload via media picker`.

### Bug B — ingredients-to-video upload

- Hypothesis: the Ingredients `+` button now opens the same media picker rather than a legacy `Upload image` menu item.
- DOM evidence: `docs/livetest-2026-05-21/l1_ingredients_upload_probe.json`, `docs/livetest-2026-05-21/l1_ingredients_upload_before.png`, `docs/livetest-2026-05-21/l1_ingredients_upload_after.png`.
- Observed DOM: Ingredients mode exposes `button` text `add_2 Create`; clicking opens media picker. Upload flow requires `Upload media`, wait for uploaded asset, click `Add to Prompt`; attached image appears as a thumbnail inside composer.
- Fix summary: attempted `flow/operations/ingredients.py` patch follows media-picker `Upload media` + `Add to Prompt`, supports `add_2`, and counts composer thumbnails. Patch was not committed because live terminal pass was not reached.
- Live FAIL/BLOCKED: job `08fba036-4305-4f4f-af52-20811e4b1272` uploaded both ingredients, clicked `Add to Prompt` twice, submitted (`cards 4 -> 6`), then Flow returned HTTP 403 on `video:batchAsyncGenerateVideoReferenceImages` and `recaptcha_v3_invisible_burned_s17524h173`.
- Credit cost: preview log `5 credits <= budget 15`; terminal failed before media id. Counted as 5 credits at risk/attempted, actual charge not verified due hard stop.
- Commit: none, per rule “commit only after live PASS”.

### Bug C — L2 extend

- Not probed. Hard stop after Bug B reCAPTCHA burn.

### Bug D — L2 insert-object

- Not probed. Hard stop after Bug B reCAPTCHA burn.

### Bug E — L2 remove-object

- Not probed. Hard stop after Bug B reCAPTCHA burn.

### Bug F — L2 camera-move

- Not probed. Hard stop after Bug B reCAPTCHA burn.

---

## 6. Credit Tally

| Bug | Job | Preview cost | Counted cost | Notes |
|---|---|---:|---:|---|
| A | `cb41d1dd-37ef-43b3-9a1a-66be3a0e7011` | 5 | 5 | Completed |
| B | `08fba036-4305-4f4f-af52-20811e4b1272` | 5 | 5 | Submitted, then reCAPTCHA/403; actual charge unknown |
| C | not run | 0 | 0 | hard stop |
| D | not run | 0 | 0 | hard stop |
| E | not run | 0 | 0 | hard stop |
| F | not run | 0 | 0 | hard stop |
| Total |  | 10 | 10 | below 80 cap |

---

## 7. Hard Stop

- reCAPTCHA detected: `v3_invisible` at `https://www.google.com/recaptcha/enterprise/clr?k=6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV`.
- Worker archived profile: `D:/AI/FlowEngine/chrome-profiles/s17524h173.burned-1779369566`.
- Worker pool exhausted: `No fresh credentials left after burning s17524h173`.
- No further live jobs were submitted after this point.

---

## 8. Handoff Notes

- Workdir is not clean. Bug A committed. Bug B attempted patch/evidence/report remain uncommitted.
- Worker `run_worker.py` is still running but has no usable profile in pool after burn.
- Resume requires a fresh healthy profile or restored credentials before any C-F live verification.
- Do not use `s17524h173` until profile burn is resolved.

---

## 9. Done Criteria Checklist

- [x] Bug A probed in live DOM
- [x] Bug A fixed and live-verified
- [x] Bug A committed per-fix
- [x] Bug B probed in live DOM
- [x] Hard stop honored after reCAPTCHA
- [ ] Bugs B-F live PASS
- [ ] Final all-six DONE state

---

**Status:** BLOCKED — reCAPTCHA burned `s17524h173` during Bug B live verify.
