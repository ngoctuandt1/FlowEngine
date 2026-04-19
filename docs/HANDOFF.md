# HANDOFF — 2026-04-19 16:30 (Run 19 L2 chain, BLOCKED)

Next session picks up from here. Read top-to-bottom before running anything.

## TL;DR

L1 t2v stable (Run 18, 4/4). L2 extend BLOCKED: worker profile
`ngoctuandt20` serves Flow **marketing landing page** at `/edit/`
URLs instead of editor SPA → no UI elements found → silent hang or
false-positive POLICY error. Root cause: profile lacks Google SSO
session. Remediation via manual Gmail login through `scripts/warm_profile.py`
fails with Playwright `TargetClosedError` — Chrome immediately closes on
launch. Needs fresh diagnosis.

## Repo state

**Main repo** `D:/AI/FlowEngine/`, branch `master`:
- Uncommitted changes (see §Uncommitted below).
- Up-to-date with `origin/master` except 3 local commits (last: `26ca413`).

**Worktree** `.claude/worktrees/blissful-almeida-59b7fc/`, branch
`claude/blissful-almeida-59b7fc`:
- Clean. Last commit `396876d` (Run 18 docs). PR #17 open.

## Uncommitted (main repo, working tree)

| File | Change | Status |
|---|---|---|
| `flow/wait.py` | Tightened POLICY regex; added screenshot+HTML dump on DOM error | Works; needs L2 validation before commit |
| `flow/download.py` | B38 UI-upscale as primary path for 1080p | Validated at L1 (Run 18) |
| `flow/login.py` | Stuck-detection + page.reload() to dismiss Google overlays | Per existing memory `feedback_login_stuck_reload.md` |
| `flow/upscale.py` (new) | UI-driven 1080p upscale module | Validated at L1 (Run 18) |
| `scripts/warm_profile.py` (new) | Manual Gmail login helper | BROKEN — TargetClosedError |

**Do not commit until L2 validated end-to-end.**

## DB state (`data/flowengine.db`)

Chain `1871a218` on profile `ngoctuandt20`:

| ID | Type | Status | Notes |
|---|---|---|---|
| `4a032d83` | text-to-video | completed | L1, parent of L2 attempts |
| `7969b8de` | extend-video | failed | false POLICY (regex bug) |
| `27051d66` | extend-video | failed | false POLICY |
| `582761dc` | camera-move | failed | no Camera button (= no editor DOM) |
| `7c41a24a` | extend-video | **claimed** | killed mid-run; DB locked, create new |

Parent media_id for new L2: `df78a409-9a01-4594-a47c-948e2bef71d3`
Project URL: `https://labs.google/fx/tools/flow/project/fb9728e5-a5f4-4bb5-8579-df258dd8969f`

## Reproduce the block

```bash
cd /d/AI/FlowEngine
# 1. kill ghost Chromes first (tasklist usually shows 5-15)
powershell -NoProfile -File scripts/kill_engine_chrome.ps1

# 2. try warm script — currently FAILS
python scripts/warm_profile.py ngoctuandt20
# Expected: visible Chrome window at mail.google.com, waits for close.
# Actual: Traceback TargetClosedError, Chrome exits code 2147483651 (0x80000003).
```

Playwright call-log excerpt:
```
<launched> pid=53200
[pid=53200] <gracefully close start>
[pid=53200] <kill>
[pid=53200] taskkill stderr: ERROR: The process "53200" not found.
[pid=53200] <process did exit: exitCode=2147483651, signal=null>
```

## Next-session playbook

### Step 1 — reset profile (superseded bisect approach)

> **Policy update (2026-04-19 Run 20):** the "wipe Cache preserve Cookies"
> bisect in the original handoff was rejected by user as "đồ ngu". See
> memory `feedback_profile_full_reset.md`. Go straight to full reset —
> cookie-preserve bisect rarely fixes `STATUS_BREAKPOINT` (0x80000003)
> anyway (root cause is usually `Preferences` / `Secure Preferences`
> corruption from a Playwright kill mid-startup, not cache).

```bash
powershell -NoProfile -File scripts/kill_engine_chrome.ps1
cp -r chrome-profiles/ngoctuandt20 chrome-profiles/ngoctuandt20.bak-$(date +%s)   # optional backup
rm -rf chrome-profiles/ngoctuandt20
python scripts/warm_profile.py ngoctuandt20
```

User signs in fresh (Google account + 2FA, ~30s). Close window when mail.google.com is reachable; cookies persist.

**If warm_profile STILL crashes after full reset** → Playwright Chromium 1200 bundle issue (not profile state). Try:
- Pin older bundle: `playwright install chromium-1199`.
- Use system Chrome via `channel="chrome"` in `launch_persistent_context`.
- Remove `--remote-debugging-pipe`, switch to TCP `--remote-debugging-port=0`.

### Step 2 — once Chrome stays up

User signs into Google (primary account for profile), closes window.
Cookies persist.

### Step 3 — restart server + worker

```bash
# Kill any residual Python
taskkill //F //IM python.exe
# Start server (background)
python run_server.py
# Start worker pinned to ngoctuandt20
WORKER_PROFILES=ngoctuandt20 python run_worker.py
```

### Step 4 — post NEW L2 extend job

(Do NOT re-use `7c41a24a` — it's stuck in `claimed`.) Post via API:

```bash
curl -X POST http://localhost:8080/api/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "type": "extend-video",
    "job_level": 2,
    "parent_job_id": "4a032d83-...",
    "chain_id": "1871a218-...",
    "profile": "ngoctuandt20",
    "project_url": "https://labs.google/fx/tools/flow/project/fb9728e5-a5f4-4bb5-8579-df258dd8969f",
    "media_id": "df78a409-9a01-4594-a47c-948e2bef71d3",
    "prompt": "continue the scene, more dramatic lighting",
    "output_count": 1,
    "model": "veo-3-fast",
    "duration": 8
  }'
```

(Fill full UUIDs from DB. Model/duration should match what Run 18 used.)

### Step 5 — monitor

Watch for:
- `logs/flow-worker-*.log` progressing past `_ensure_edit_view`
- `downloads/extend_1080p_*.mp4` appearing (file size ≥ 2MB for 1080p)
- DOM errors: check `logs/error_<label>_<ts>.{png,html}` — screenshot
  should now show what the page actually looks like at error time.

### Step 6 — if it succeeds

- Verify with ffprobe: `ffprobe downloads/extend_1080p_*.mp4` → confirm
  1920×1080.
- Check DB: L2 `media_id` populated (new UUID per INV-5), `project_url`
  matches L1, `profile=ngoctuandt20`.
- Commit uncommitted files: `flow/wait.py` + `scripts/warm_profile.py`
  as `fix(wait): POLICY regex tighten + screenshot-on-error` and
  `ops: add warm_profile.py helper for Google SSO bootstrap`.
- Write Run 20 session report documenting L2 pass.

### Step 7 — if it fails again

- Read `logs/error_*.png` to see page state.
- If still landing page → warm didn't stick; investigate whether
  FlowClient clone-to-temp is discarding cookies. Try setting
  `FLOW_USE_BASE_PROFILE=1`.
- If editor loads but wrong UI → L2 operation module bug, not auth.
  Drop into `worker/dispatcher.py::_run_extend` and `flow/operations/extend.py`.

## References

- Full diagnosis: [docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md](session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md)
- L1 baseline (Run 18): [docs/session-reports/2026-04-19_Tier2_Run17_B38_UI_upscale.md](session-reports/2026-04-19_Tier2_Run17_B38_UI_upscale.md) §11
- Chain inheritance spec: [docs/SPEC.md](SPEC.md) §A.1 INV-5
- Memory: `feedback_flow_edit_nav_click.md` — page.goto(/edit/) bounces; tile.click needed
- Memory: `feedback_login_stuck_reload.md` — reload dismisses overlays

## Session meta

- Worktree: `.claude/worktrees/blissful-almeida-59b7fc/`
- PR: https://github.com/.../pull/17 (Run 18 docs, merged state TBD)
- Transcript: `C:\Users\Tuan\.claude\projects\D--AI-FlowEngine--claude-worktrees-blissful-almeida-59b7fc\1c64dd8e-0adf-414d-b31a-4a3163594365.jsonl`
