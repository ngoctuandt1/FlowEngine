# Parked Items — deferred from active workflow

> Items that are **out of the current cycle**. Each entry has its own
> trigger condition (what unblocks it) and a self-contained brief so a
> future session can pick it up cold.
>
> Active session work lives in [docs/session-reports/2026-04-25_session-handoff.md](session-reports/2026-04-25_session-handoff.md)
> and the [INDEX](session-reports/INDEX.md) parked-tracker §6. This file
> holds items that are **NOT** expected to make progress in the next 1-2
> sessions and should NOT bias active planning.

---

## P-MULTISESSION-01 — Cold-start download race + marketing-landing edge case (multi-session live verification)

**Triggers needed before reactivating:**
- ≥1 additional Flow-eligible Google account (currently only `ngoctuandt20` viable; `s1324h1450` is org-disabled per [feedback_flow_service_not_allowed_account_dead.md](../../../C:/Users/Tuan/.claude/projects/D--AI-FlowEngine/memory/feedback_flow_service_not_allowed_account_dead.md))
- Account must be added to canonical credentials at `D:/AI/AI-Engine3-Project/profiles_ultra.txt`
- English locale per `feedback_english_locale.md`
- TOTP secret base32-valid

**Context (frozen 2026-04-25):**

### A — Cold-start download race (PR #45/#46)

`flow/operations/_base.py` had a race where the FIRST L1 after a fresh Chrome launch could return empty `output_files` because the download capture buffer hadn't subscribed before the response landed. PR #46 (`2dacd96`) added a DOM scrape fallback (`scrape DOM tile media_ids when network capture is empty`).

**Status:** code merged but **not exercised live** — the 2026-04-24 verify run was single-profile and never hit the race. The original failure mode required ≥4 profiles cold-launching concurrently to reproduce. With 1 profile, the race window is too narrow.

**Reactivation steps when a 2nd account lands:**
1. Pre-flight new account: `python scripts/warm_profile.py <name>` and verify `Login complete (url=...)` in tail does NOT contain `ServiceNotAllowed`.
2. Cold-clean before run: kill all chromes whose cmdline contains `chrome-profiles`; remove `chrome-profiles/<n>/SingletonLock` if present.
3. Worker env: `WORKER_PROFILES=ngoctuandt20,<acc2>[,<acc3>,<acc4>]` with `MAX_CONCURRENT_JOBS=N` (where N = profile count).
4. Submit N L1 t2v jobs simultaneously (e.g. via UI or `for i in $(seq 1 N); do curl -X POST ... &`).
5. Search worker log for `DOM media-id scrape recovered` / `Completion via DOM` — presence = #46 fallback exercised.
6. Pass criteria: all N jobs end `completed` with non-empty `output_files`. If any job fails with empty `output_files`, the race is still live and PR #46 fix is incomplete.

**Cost (when ready):** N× t2v at 1080p (no 4K) = ~N credits per Flow's per-op rates.

### B — Marketing-landing A/B variance edge case

PR #44/#52 hardened the bypass (force=True click + reload-retry + `+ New project` tag-agnostic fallback) and the cdp-wrong-page filter (PR #52). The 2026-04-24 J1 failure showed an A/B variant where the "Create with Flow" CTA matched a scroll-anchor (`#capabilities`) instead of the hero CTA — `dismiss_flow_marketing_landing` reload-retried 2× without ever mounting the app, then `+ New project` lookup timed out.

**Status:** rare A/B variant; single occurrence; root cause hypothesis = the hero-CTA selector matches both the in-page anchor link AND the actual hero button on this variant.

**Reactivation steps:**
1. With multi-profile pool, submit ~10 sequential L1 t2v jobs across profiles to maximize chance of hitting the variant.
2. If failure repros, screenshot the `debug_screens/new_project_btn_missing_*.png` artifact + grep worker log for `did not mount app within 8s (url=https://labs.google/fx/tools/flow#`.
3. Fix candidate: scope CTA selector to `<main>` + `not([href^='#'])` + post-click URL assertion (already partially in PR #52 — may need tightening).

**Cost (when ready):** ~10× t2v 1080p.

---

## P-MULTISESSION-02 — Web phase planning (post-engine)

**Triggers needed before reactivating:**
- Engine phase declared "done" — engine ~95% complete, but the 2 HIGH items above need to close OR be explicitly waived for shipping.
- User decision on what the web phase actually delivers (currently ambiguous — frontend dark UI shipped via PR #56 but functional scope beyond that is undecided).

**Context (frozen 2026-04-25):**

The frontend currently has 5 pages (Dashboard, Create Job, Chains, Profiles, Settings) wired to the existing REST + WebSocket API. PR #56 reskinned to Flow-inspired dark theme (Lighthouse a11y 98). What's NOT yet decided for "web phase":

- **API surface gaps:** is the current REST API enough for a real user-facing dashboard, or does it need polish (pagination, filtering, search, batch actions)?
- **Auth / multi-user:** currently no login on the FlowEngine dashboard itself. Single-user-trusted-LAN model. Public deployment would need auth.
- **Job creation UX:** are bbox/camera-direction inputs intuitive? Inline preview of frame before submit?
- **Chain builder UX:** drag-drop step ordering? Live cost preview? Account-pinning visualization?
- **Real-time updates:** WS broadcasts job state changes — but does the frontend subscribe to the right event types?
- **Observability for the user:** credit ledger view? Per-account quota? Failed-job retry-from-error UI?

**Reactivation steps:**
1. User decides scope: "what does v1 web deliver?"
2. Write `docs/PRD_WEB_V1.md` per CLAUDE.md §6 epic convention.
3. Open issues, branch per issue, ship per issue.

**Cost (when ready):** multi-session feature-epic — not a single-session task.

---

## Maintenance rules

1. **Append-only.** Closing an item moves it to "Closed" section at the bottom (don't delete).
2. **Each entry must be self-contained** — future-Claude reads it cold without prior context. Include trigger conditions, frozen context, reactivation steps, cost.
3. Active items live in `docs/session-reports/*_session-handoff.md` + `INDEX.md` parked tracker. **This file is for items deferred ≥2 sessions.**

---

## Closed (none yet)
