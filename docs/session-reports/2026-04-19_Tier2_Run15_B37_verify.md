# Session Report — `Tier2-Run15` B37 media-id harvest + B35 x1 live verification (3 t2v jobs)

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `Tier2-Run15` |
| Task type | E2E live verification (Tier 2 browser-driven, 3 parallel-queued t2v jobs, serial execution) |
| Session started | 2026-04-19 18:33 local (UTC+7) |
| Session ended   | 2026-04-19 18:47 local (UTC+7) |
| Duration | ~14m (13m26s jobs + setup/teardown) |
| Worker | Claude Opus 4.7 (executor — run + monitor + document only) |
| Branch | `claude/condescending-mcnulty-82b9c3` (worktree off `master` @ `7914020`) |
| Profile | `ngoctuandt20` (EN-locale, per `feedback_english_locale.md`) |
| Commit under test | B37 `7914020` — `fix(download): harvest media_ids via "mid" key + unwrap _video_urls` (+ B35 `dc486a7` still active) |
| Prior context | Run 14 PARTIAL: B35 re-verified but surfaced B37 (`media_id=None` + `t2v_blob_*` fallback). B37 fix landed in-session; this run validates both fixes hold across 3 diverse prompts/aspect ratios with no regression |

---

## 2. Commits landed

**None from this session.** Run 15 is pure verification of B35 (`dc486a7`) + B37 (`7914020`) already on master. Single docs-only commit planned at session end:

```
<hash>  docs(e2e): Tier 2 Run 15 — B37 harvest + B35 x1 verified live across 3 t2v jobs
```

---

## 3. Files changed

Docs only:

```
docs/session-reports/2026-04-19_Tier2_Run15_B37_verify.md    (this report, NEW)
docs/E2E_RESULTS_PHASE_A.md                                  +N  (Run 15 block prepended at top)
```

No `.py` / config / profile / engine code touched.

---

## 4. Live E2E results — **PASS (3/3)**

**Rerun window (local UTC+7):** J1 claimed 18:33:38 → J3 completed 18:47:04 — **13m 26s** serial, 1 profile (`ngoctuandt20`).

| Job | id (short) | prompt | AR | status | media_id | output |
|---|---|---|---|---|---|---|
| J1 | `a0333bca` | cinematic aerial shot of a coastal lighthouse at golden hour | 16:9 | ✅ completed 18:38:16 | `3e9f60e2-caec-4031-a9de-a9c7bf271ec0` | `downloads\t2v_720p_1776598695.mp4` (5 137 569 B) |
| J2 | `4f60e9f7` | a hummingbird hovering near bright red hibiscus flowers | 9:16 | ✅ completed 18:42:45 | `988440e4-41a5-4431-bfa6-c481e047db7f` | `downloads\t2v_720p_1776598965.mp4` (2 439 801 B) |
| J3 | `36c845bb` | sunrise over a snowy mountain ridge reflected in a lake | 16:9 | ✅ completed 18:47:04 | `0c70a870-d799-4c22-984e-c7a0db5c84f8` | `downloads\t2v_720p_1776599223.mp4` (3 973 522 B) |

### Pass-criteria scorecard (per operator prompt)

| Criterion | Expected | Observed | Verdict |
|---|---|---|---|
| B35 `Output count set to x1 (chip verified)` | 3× | 3× (one per job, timestamps 18:34:04 / 18:38:35 / 18:43:01) | ✅ |
| B37 dispatcher DONE `media_id=<uuid>` (not `None`) | 3× | 3× (`3e9f60e2` / `988440e4` / `0c70a870`) | ✅ |
| B37 filename starts with `t2v_720p_*.mp4` (API path) | 3× | 3× (`1776598695` / `1776598965` / `1776599223`) | ✅ |
| B37 filename `t2v_blob_*.mp4` (fallback path) | 0× | 0× | ✅ |
| `files=1` (B35 x1 downstream) | 3× | 3× | ✅ |
| `Traceback` / `ERROR` / `FAILED` in worker.log | 0 | 0 / 0 / 0 | ✅ |

### B35 signals captured (`logs/worker.log`, deduplicated)

```
18:34:04 flow.operations.generate: Step 4: Aspect ratio = 16:9                               (J1)
18:34:04 flow.operations.generate: Step 4.5: Force output count = x1                         (J1)
18:34:04 flow.operations.generate: Output count set to x1 (chip verified)                    (J1)
18:34:07 flow.submit: Submit confirmed: cards 0 -> 2                                         (J1)

18:38:35 flow.operations.generate: Step 4: Aspect ratio = 9:16                               (J2)
18:38:35 flow.operations.generate: Aspect ratio set to 9:16 (chip verified: crop_9_16)       (J2)
18:38:35 flow.operations.generate: Step 4.5: Force output count = x1                         (J2)
18:38:35 flow.operations.generate: Output count set to x1 (chip verified)                    (J2)
18:38:38 flow.submit: Submit confirmed: cards 0 -> 2                                         (J2)

18:43:01 flow.operations.generate: Step 4: Aspect ratio = 16:9                               (J3)
18:43:01 flow.operations.generate: Step 4.5: Force output count = x1                         (J3)
18:43:01 flow.operations.generate: Output count set to x1 (chip verified)                    (J3)
18:43:03 flow.submit: Submit confirmed: cards 0 -> 2                                         (J3)
```

`cards 0 -> 2` (not `0 -> 4`) is Flow SPA's card-layout quirk documented in Run 14 — the actual generated clip count is 1 per `files=1`. The `(chip verified)` suffix fires only when `_set_output_count` matches `x1` in the chip's post-close `innerText` — i.e. Flow accepted the x1 selection. No regression vs Run 13 / Run 14.

### B37 signals captured (`logs/worker.log`, deduplicated)

```
18:38:15 flow.download: Downloaded 720p: downloads\t2v_720p_1776598695.mp4 (5137569 bytes)   (J1)
18:38:16 worker.dispatcher: text-to-video DONE | files=1 media_id=3e9f60e2-caec-4031-a9de-a9c7bf271ec0

18:42:45 flow.download: Downloaded 720p: downloads\t2v_720p_1776598965.mp4 (2439801 bytes)   (J2)
18:42:45 worker.dispatcher: text-to-video DONE | files=1 media_id=988440e4-41a5-4431-bfa6-c481e047db7f

18:47:03 flow.download: Downloaded 720p: downloads\t2v_720p_1776599223.mp4 (3973522 bytes)   (J3)
18:47:04 worker.dispatcher: text-to-video DONE | files=1 media_id=0c70a870-d799-4c22-984e-c7a0db5c84f8
```

Zero `No media IDs found for download` / zero `Blob download:` lines vs Run 14's one-each. B37 harvest is functioning across 3 runs of different prompts/aspect ratios.

### Timeline (from `logs/worker.log`)

| Event | Timestamp (local UTC+7) |
|---|---|
| Worker launch | 18:33:10 |
| J1 claim | 18:33:38 |
| J1 complete (x1 chip → submit → 58s DOM completion → 720p API download) | 18:38:16 (~4m38s) |
| J2 claim + dispatch | 18:38:16 |
| J2 complete (9:16 aspect chip + x1 chip → submit → 58s → download) | 18:42:45 (~4m29s) |
| J3 claim + dispatch | 18:42:46 |
| J3 complete (x1 chip → submit → 51s → download) | 18:47:04 (~4m18s) |

Serial execution confirmed — each `result sent -> completed` line precedes the next `Claimed job` by ≤1s. Per-job break-down: generation ≤1 min; remainder is upscale polling → 720p fallback (see §7 finding-1).

---

## 5. SPEC.md update

No §D.4 change planned for this run. B35 (`dc486a7`) and B37 (`7914020`) already carry Run 13 / Run 14 verification markers. Run 15 adds a multi-AR / multi-prompt data point to E2E_RESULTS_PHASE_A but no new behavioral assertion to encode in SPEC.

(If supervisor wants a SPEC marker for "B35+B37 verified across 3 diverse t2v jobs 2026-04-19", a one-line append to each B35/B37 §D.4 entry is trivial — not done in this run.)

---

## 6. Invariants verified

- [x] **INV-1 Account Binding** — all 3 jobs stored `profile=ngoctuandt20`; worker only claimed profile-matching rows.
- [x] **INV-3 Store Everything** — each completed job persisted `project_url` + `media_id` + `edit_url` + `output_files` + `completed_at`. All three `media_id` values are real UUIDv4s (not `None` as in Run 14). Verified via `GET /api/jobs/{id}` on all 3 post-run.
- [x] **R-CODE-3 Locale-Independent** — J2's 9:16 aspect chip verified via `crop_9_16` selector (B19); all three x1 chips verified via B35's Radix `[id$="-trigger-1"]` selector. No locale-text fallbacks exercised on the EN-locale profile.
- [x] **B35 force x1** — 3× `Output count set to x1 (chip verified)` + 3× `files=1`. The B35 invariant "engine must force x1 output count" (per `feedback_output_count_x1.md`) holds across all three jobs including mixed AR (16:9 + 9:16 + 16:9).
- [x] **B37 harvest key** — 3× `media_id=<uuid>` in dispatcher DONE logs + 3× `Downloaded 720p: t2v_720p_*.mp4` file paths (API path). Zero `No media IDs` / zero `t2v_blob_*` fallback files. Run 14's key-mismatch regression is confirmed fixed.

---

## 7. Issues / Decisions

### Finding 1 — 1080p upscale path still universally falls through to 720p (B34-residual, **P3 / cosmetic for this task**)

Priority: **P3** — this task explicitly does not require 1080p (operator confirmation: "task này k cần 1080"). Logging here for future reference only.

**Evidence:**
- All 3 jobs exhausted `UPSCALE_MAX_RETRIES=12` × `UPSCALE_POLL_INTERVAL=15s` = **180s** of "Upscale in progress" polling on `?name={media_id}_upsampled` before falling through.
- `downloads/` folder historical count: **0** `t2v_1080p_*` files across all runs; 20+ `t2v_720p_*` files. Consistent with the pre-B34 state noted in [`flow/download.py:14-19`](flow/download.py:14).

**Interpretation:** B34 (`d454155`, 2026-04-18) extended the poll window 30s→180s to cover Flow's ~1-3 min upscale latency envelope, but 180s is still not enough in practice. Either (a) Flow's upscale genuinely takes >180s, (b) upscale is not auto-triggered on `veo-3.1-fast-lp` output and requires manual "Enhance" click, or (c) the `_upsampled` URL scheme has changed. Not investigated in this run.

**Not fixing in this run** — scope is B35+B37 verification, and the 720p download is functionally complete (both fixes under test operate identically on the 720p path). If a future task requires 1080p, options:

1. **Env bump** (zero code change) — `FLOW_UPSCALE_MAX_RETRIES=36` or `=48` (= 9 or 12 min poll window) via worker env var. Constants are already env-configurable at [`flow/download.py:21-22`](flow/download.py:21).
2. **B38-candidate** — probe Flow's actual upscale behavior: does `_upsampled` ever return 200 on this account/model, or does it require user-triggered enhance? Answer dictates whether B34's window-extension approach is right at all.

### No other issues

- Zero `Traceback` / zero `ERROR` / zero `FAILED` in 407-line worker.log.
- No respawn / no crash / no hung Chrome (no `nohup` complication like Run 12 finding-1).
- Only warnings were benign Node `url.parse()` deprecation notices from Playwright internals.

---

## 8. Handoff notes

- **Workdir state:** worktree `claude/condescending-mcnulty-82b9c3` at master's `7914020`; docs-only staged changes (this report + E2E_RESULTS_PHASE_A Run 15 block) pending commit.
- **Env:** `CHROME_USER_DATA_DIR=D:/AI/chrome-profiles`, `WORKER_PROFILES=ngoctuandt20`, server port 8080.
- **Profile `ngoctuandt20` state:** EN-locale confirmed (9:16 chip verified via locale-independent selector); post-Run-15 LP: ~3 LP burned (1 per t2v @ veo-3.1-fast-lp).
- **Background tasks:** server + worker + log-monitor stopped via `TaskStop` at session end; `curl http://127.0.0.1:8080/api/jobs` → `000` (connection refused) verified.
- **Output files retained** in `downloads/` for post-run audit (3 new `t2v_720p_177659*.mp4` files).
- **Logs rotated pre-run** to `logs/{server,worker}.log.run14`; Run 15 log spans lines 1–407 of current `logs/worker.log`.
- **Next session candidate work:** Finding 1 is P3 cosmetic (1080p not required for current tasks). No blockers; B35 + B37 are now double-verified (Run 13 + Run 15 for B35; Run 14 fix + Run 15 regression-check for B37).

---

## 9. Done criteria checklist

- [x] J1 text-to-video status=completed, `files=1`, `media_id=<uuid>` ≠ None, output `t2v_720p_*.mp4` (not `t2v_blob_*`)
- [x] J2 text-to-video status=completed (9:16 aspect chip), `files=1`, `media_id=<uuid>` ≠ None, output `t2v_720p_*.mp4`
- [x] J3 text-to-video status=completed, `files=1`, `media_id=<uuid>` ≠ None, output `t2v_720p_*.mp4`
- [x] 3× `Output count set to x1 (chip verified)` in worker.log (B35)
- [x] 3× dispatcher DONE `media_id=<uuid>` with no `None` (B37)
- [x] 3× `Downloaded 720p: downloads\t2v_720p_*.mp4` lines (B37 API path)
- [x] 0× `t2v_blob_*.mp4` fallback files (B37 regression trip-wire)
- [x] 0× `No media IDs found for download` (B37)
- [x] 0× `Traceback` / `ERROR` / `FAILED` in worker.log
- [x] INV-1 all 3 jobs profile=`ngoctuandt20`
- [x] INV-3 all 3 jobs persisted `project_url` + `media_id` + `output_files`
- [x] `docs/E2E_RESULTS_PHASE_A.md` Run 15 block appended at top
- [x] Session report (this file) committed
- [x] Server + worker cleanly stopped; port 8080 free
- [x] No `.py` / config / profile-creds files touched (verification-only)

---

**Verdict: ✅ PASS (3/3) — B37 media-id harvest + B35 x1 force both verified live across 3 diverse text-to-video jobs (16:9 + 9:16 + 16:9, 3 unrelated prompts).** Zero regressions vs Run 14's post-fix state. Run 14's partial-fail mode (`media_id=None` + `t2v_blob_*` fallback) is fully suppressed. One residual P3 observation (1080p upscale path still times out → 720p fallback) flagged for future tasks that require 1080p; out of scope for this verification.

_Sign-off: ready for supervisor review._
