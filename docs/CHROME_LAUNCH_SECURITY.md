# Chrome launch & Google security — principle, current state, options

> Why this doc exists: after 2026-04-20 analysis of Run 19 L2 block, the
> original "auth cookies not persisting" hypothesis is no longer the only
> candidate. An alternative hypothesis — Google fingerprints the
> CDP-attached Chrome and serves marketing-landing / challenge instead of
> the editor SPA — needs to be held alongside it. This doc captures the
> current launch architecture, known detection signals, and the three
> mitigation options so the next debug session does not have to
> re-derive them.
>
> **Principle** (user directive 2026-04-20): "nếu không mở bằng Chrome
> giống người dùng thật thì sẽ bị Google secure ngay." Every launch path
> added to this repo MUST be measured against "how close is this to a
> real user double-clicking Chrome?".

---

## 1. Current launch topology

Two entrypoints launch Chrome; both target **system Chrome** (not the
Playwright Chromium bundle). Both use CDP over a TCP port.

| Entrypoint | Flow |
|---|---|
| `scripts/warm_profile.py` | spawn `chrome.exe` via `subprocess.Popen` → `connect_over_cdp("http://127.0.0.1:<dynamic>")` |
| `flow/client.py` Mode A (`_start_cdp`) | spawn `chrome.exe` via `subprocess.Popen` → `connect_over_cdp("http://127.0.0.1:19300")` |
| `flow/client.py` Mode B (`_start_persistent`) | `playwright.chromium.launch_persistent_context(channel="chrome", …)` |

Mode A is the default on Windows outside Docker
(`flow/client.py:197-200`). Mode B is only reached in Docker /
non-Windows.

### Key property of Mode A and warm_profile

Because Chrome is spawned **before** Playwright attaches (and then
Playwright connects via `connect_over_cdp`), Playwright does NOT
inject its bootstrap stubs into page contexts. This is closer to a
real user than `launch_persistent_context`, where Playwright controls
the launch and injects `__pw_*` hooks into every new document.

### Key problem with Mode B

[flow/client.py:347](../flow/client.py:347) sets
`--disable-blink-features=AutomationControlled`. That flag is a
bot-hider tell — the fact that it was disabled is itself a signal.
Combined with `ignore_default_args=["--enable-automation"]`, Mode B
is strictly dirtier than Mode A.

---

## 2. Detection signals that survive Mode A

Even the cleaner Mode A still exposes:

1. **`--remote-debugging-port=<tcp>`** is visible via Chrome's own
   metadata. Google's sign-in flow and Flow's page shell can probe for
   CDP presence through:
   - `navigator.webdriver` (false by default, but CDP `Page.enable`
     can leak state)
   - `chrome.runtime` shape differences under CDP
   - CDP `Target.setDiscoverTargets` visibility
   - Timing of `console.log` echoes back through CDP
2. **`--user-data-dir=<path>`** — visible to Chrome internals; a
   user-data-dir under `%TEMP%\flow_<profile>_<ts>` (the clone path in
   FlowClient) looks nothing like a user's normal `%LOCALAPPDATA%\Google\
   Chrome\User Data\Default`.
3. **Parent process chain** — `python.exe → chrome.exe` instead of
   `explorer.exe → chrome.exe`. Visible to any native code running in
   Chrome (installed extensions, Chrome's own telemetry).
4. **Flag shape** — `--no-first-run --no-default-browser-check
   --new-window` is an unusual combination for an interactive launch.
   Individually benign, as a tuple slightly suspicious.

None of these are fatal on their own. Stacked together against a
brand-new cloned profile on Flow (a tightly policed product), they
plausibly trigger device-trust challenges that cascade into the
"marketing landing instead of editor" failure mode observed in Run 19.

---

## 3. What we have that helps

- Clone-to-temp can be turned off.
  [flow/client.py:380-385](../flow/client.py:380) reads
  `FLOW_USE_BASE_PROFILE=1` and points `_temp_profile` at the base
  `chrome-profiles/<p>/` directly. Base path is the same path
  `warm_profile` writes to, so cookies and Local State remain
  byte-identical between warm and worker. A user re-opening Chrome
  sees the same user-data-dir each time; our base-profile path mimics
  that, temp-clone does not.
- `project_lock.py` already enforces one job per `project_url`, so
  running the worker with `FLOW_USE_BASE_PROFILE=1` does not risk
  concurrent writes to the same profile dir.

---

## 4. Three mitigation options, sorted by effort

### Option 1 — ship `FLOW_USE_BASE_PROFILE=1` as Run 20 default (minutes)

Pros: zero code change, eliminates the temp-profile signal, keeps
cookies in the exact file the warm session wrote.
Cons: worker cannot run two jobs on the same profile concurrently.
`project_lock.py` already guarantees that for same-project chains;
cross-project parallelism on the same profile would need a policy
decision. For Run 20 (single L2 extend) the constraint does not bite.

Applies now. No further code. Wire via worker env.

### Option 2 — switch CDP transport to `--remote-debugging-pipe` (hours)

Chrome supports CDP over stdin/stdout pipes instead of a TCP socket.
No `netstat` row, no `chrome://inspect` visibility, harder to
fingerprint. Playwright's `connect_over_cdp` accepts a WebSocket URL
only, so this requires a pipe↔WebSocket bridge (~100 LOC,
`asyncio.subprocess` on one side, `websockets` server on the other)
or a rewrite to `chromium.launch(pipe=True)` (loses the
"attach-to-already-running" property).

Trade-off: the bridge is extra surface area; `chromium.launch` gives
back the Playwright-controls-launch problem that Mode A was built to
escape.

### Option 3 — CDP stealth patches on every new document (hours)

Use `page.add_init_script` / CDP `Page.addScriptToEvaluateOnNewDocument`
to override `navigator.webdriver`, rebuild `window.chrome.runtime`,
fake `plugins` / `mimeTypes`, tweak `navigator.permissions.query`.
Pattern from undetected-chromedriver. ~30 LOC but must be validated
against Flow's SPA — overrides can break sites that rely on the
features they override.

---

## 5. Hypothesis for Run 19 block (2026-04-20 update)

Run 19 session report
([docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md](session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md))
diagnosed "profile not authenticated; marketing landing served." This
doc proposes a revised framing:

**H1 (original):** worker profile lacks Google SSO cookies → Flow
redirects to marketing.
**H2 (new):** cookies are present, but Google fingerprints the
CDP-attached Chrome + temp-cloned user-data-dir as a device-trust
mismatch, and Flow's page shell defensively renders the marketing
variant instead of editor SPA.

Run 20 discriminates H1 vs H2 as follows. After
`scripts/warm_profile.py` succeeds (verified: inbox loads in the
visible window):

| Run 20 result with `FLOW_USE_BASE_PROFILE=1` | Implication |
|---|---|
| Editor SPA loads, L2 completes | H1 was right; temp-clone was dropping cookies. Ship Option 1 as default. |
| Marketing landing again | H1 partial / H2 in play. Escalate to Option 2 or Option 3 (user decides). |
| New failure mode | Collect logs, re-diagnose; do not speculate further in this doc. |

---

## 6. Rules for future launch-path work

These are derived from the principle and from Phase A regressions
(memory `feedback_warm_profile_manual_gmail.md`,
`feedback_chrome_kill_selective.md`):

1. Any new launch path MUST use system Chrome, not Playwright Chromium.
2. Do NOT add `--disable-blink-features=AutomationControlled`,
   `--enable-automation=false`, or similar bot-hider flags. Their
   presence is a signal.
3. Do NOT add `--headless` for Google-service work. Headless Chrome
   is a separate detection category.
4. Clone-to-temp is acceptable only when concurrency requires it.
   Default for single-profile runs is base profile directly.
5. Adding Option 2 (pipe) or Option 3 (stealth) requires explicit
   user approval — they are architecture changes, not bug fixes.
6. Adding a new launch mode requires a memory entry + (where
   applicable) a trip-wire test per
   `feedback_locked_items_require_user_approval.md`.

---

## 7. Cross-references

- Memory: `feedback_chrome_launch_real_user.md` (the short rule)
- Memory: `feedback_warm_profile_manual_gmail.md` (entry URL + auto-login)
- Memory: `feedback_chrome_kill_selective.md` (selective kill)
- Code: [flow/client.py:173-239](../flow/client.py:173) (Mode A/B
  selector + `start()`)
- Code: [scripts/warm_profile.py:178-211](../scripts/warm_profile.py:178)
  (warm CDP launch + connect)
- Run 19 diagnosis: `docs/session-reports/2026-04-19_Tier2_Run19_L2_chain_blocked.md`
- Supervisor handoff next-action: `docs/SUPERVISOR_HANDOFF.md` §6 P0 + §10 A
