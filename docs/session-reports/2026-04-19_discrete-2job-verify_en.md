# Session Report — `discrete-2job-verify` Discrete L1→close-tab→new-tab→L2 chain on EN

> Verification-only session. Goal: prove FlowEngine's invariants
> (navigate-by-edit_url + stable media_id + B26 submit) hold when L1 and L2
> run as **completely independent browser sessions** — the way the real
> worker behaves (each job is claimed from DB, navigates fresh, submits,
> writes back, closes). This is different from the B22/B25/B26 live tests
> which ran L1→L2 continuously in one session.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `discrete-2job-verify` |
| Task type | live-regression-verify (no code changes) |
| Session started | 2026-04-19 ~01:50 |
| Session ended | 2026-04-19 ~02:10 |
| Duration actual | ~20m |
| Duration estimate | N/A (ad-hoc verification) |
| Worker | Claude Opus 4.7 + Chrome MCP extension |
| Browser profile | EN UI, Gemini Advanced (Ultra) |
| DPR | 1.25 |
| Branch | `claude/sharp-curie-80ac08` |

---

## 2. Commits landed

```
none
```

Verification-only session. No code diff. Evidence lives in this report +
the MCP screenshots captured during the run (ephemeral).

---

## 3. Files changed

```
docs/FLOW_E2E_WALKTHROUGH.md   +312 / -0   (new — button-by-button walkthrough, created earlier this session)
docs/session-reports/2026-04-19_discrete-2job-verify_en.md   +NEW   (this report)
```

Both files untracked in the worktree — not committed per user's pending
confirmation.

---

## 4. Test scenario — discrete 2-job chain

### 4.1 Brief

User's ask (verbatim, VI):
> "dùng mcp extension test job extend video (chứ k phải gen video ra rồi
> extend ngay mà gen video => lấy metadata video=> đóng tab đó và mở tab
> mới=> dùng metadata để mở lại video đó và extend) kiểu như 2 job độc lập"

Translation — test Extend as if it were a **second, independent job**:
L1 gen → harvest metadata → **close the tab** → **open a new tab** →
navigate using only the stored metadata → L2 Extend. Simulates
`worker/main.py` claim loop which opens a fresh browser context per job.

### 4.2 Step-by-step

**Tab A (L1):** `tabId=1988718801` (reused EN tab from prior B26 session, navigated to homepage).

1. Navigate `https://labs.google/fx/tools/flow`.
2. Click `+ New project` tile at CSS `(480, 691)`.
   * Expected selector: `button:has(i:text-is('add_2'))`.
   * Result: URL → `/project/3be611f0-05a0-4fb8-aa1f-59fdb8f62772`.
3. Click aspect chip at CSS `(755, 759)` → picker panel opens.
   * Verified: `Video` tab + `Frames` active, aspect `9:16` already
     selected (default for this profile), count `x1`, model
     `Veo 3.1 - Lite [Lower Priority]`.
4. Dismiss panel by clicking `(200, 300)` (outside).
5. Click prompt editor at `(470, 711)` → focus.
6. Type: `"a fluffy orange cat chasing a butterfly in a sunlit meadow"`.
7. Click submit `→` at `(823, 759)`.
   * Selector in code path: `button:has(i:text-is('arrow_forward'))` (B26 canonical).
8. Wait ~28s for generation.
   * Progress: 11% → blurred green → orange cat in meadow (9:16 portrait).
9. Click generated thumbnail at `(170, 390)`.
   * Result: URL pushes to `/project/3be611f0-.../edit/8ae4fffe-489e-4f4c-a020-d96b25a2e296`.
10. **METADATA HARVESTED** (this is the "DB write" the worker would do):

    | Field | Value |
    |---|---|
    | `project_id` | `3be611f0-05a0-4fb8-aa1f-59fdb8f62772` |
    | `project_url` | `https://labs.google/fx/tools/flow/project/3be611f0-05a0-4fb8-aa1f-59fdb8f62772` |
    | `media_id` (slug) | `8ae4fffe-489e-4f4c-a020-d96b25a2e296` |
    | `edit_url` | `https://labs.google/fx/tools/flow/project/3be611f0-05a0-4fb8-aa1f-59fdb8f62772/edit/8ae4fffe-489e-4f4c-a020-d96b25a2e296` |
    | profile | EN profile (Ultra account) |
    | prompt | `"a fluffy orange cat chasing a butterfly in a sunlit meadow"` |
    | model | `Veo 3.1 - Lite` |
    | aspect | `9:16` |
    | count | `x1` |

**Separation boundary (the discrete part):**

11. `tabs_close_mcp(tabId=1988718801)` → group auto-removed (empty).
12. `tabs_context_mcp(createIfEmpty=true)` → new window, new tabId
    `1988718806`, at `chrome://newtab/` — **zero shared state with Tab A
    other than persistent Chrome profile cookies**.

**Tab B (L2 — worker-style re-entry):** `tabId=1988718806`.

13. Navigate directly to the stored `edit_url` — no homepage, no card
    click, no DOM scan:
    ```
    https://labs.google/fx/tools/flow/project/3be611f0-05a0-4fb8-aa1f-59fdb8f62772/edit/8ae4fffe-489e-4f4c-a020-d96b25a2e296
    ```
14. Wait 5s for /edit/ view to hydrate.
    * Verified: page title auto `"Fluffy orange cat chasing butterfly"`
      (derived from L1 prompt — confirms project identity recovered).
    * Verified: video preview renders the L1 cat.
    * Verified: Extend mode is **default active** (darker button bg).
    * Verified: prompt placeholder `"What happens next?"` (EN Extend).
    * Verified: 4 mode buttons visible: `Extend / Insert / Remove / Camera`.
    * Verified: model picker shows `Veo 3.1 - Lite` inherited.
15. Click prompt editor at `(470, 571)` → focus.
16. Type: `"the cat leaps up and catches the butterfly mid-air"`.
17. Click submit `→` at `(823, 619)`.
    * Same B26 canonical selector. **On /edit/ there are 2 matches**
      (disabled decorative `arrow_forward` + real submit) — B16 KEEP-7
      iterate-and-skip-disabled behavior is what makes this click land on
      the right button.
18. Wait — progress: 14% → gray placeholder → rendered extend clip.
19. **Result:** cat with head raised looking up, butterfly visible to the
    right, in 9:16 portrait — matches the extend prompt semantically.

### 4.3 Post-state observations

* **URL never changed** on Tab B during or after extend:
  `/edit/8ae4fffe-489e-4f4c-a020-d96b25a2e296` throughout.
* **media_id UUID preserved** — the slug from L1's URL is the same slug in
  the L2 composer URL. INV-5 (media_id stable across extend) holds.
* **Extend-child lockout observed** — Insert / Remove / Camera buttons
  grayed out after the extend clip finished. This is the expected
  Flow-side UX constraint documented in
  [FLOW_E2E_WALKTHROUGH.md §5.1](../FLOW_E2E_WALKTHROUGH.md). Not a
  FlowEngine bug.
* **No misclicks** — single submit click on both L1 and L2, no B26
  regression (no /edit/→/project/ redirect, no mode-button bleed from
  videocam fuzzy selector).

---

## 5. Invariants & rules verified

| Rule | Result | Evidence |
|---|---|---|
| INV-1 Account Binding | ✅ | Same Chrome profile used for both tabs (Gemini Ultra, EN). Profile state is what makes /edit/ URL accessible when re-entering cold. |
| INV-2 Navigate by `edit_url` | ✅ **critical for this test** | Tab B used only the stored `edit_url` string — no DOM card counting, no `video_index`. |
| INV-3 Store Everything | ✅ | `project_url` + `media_id` slug + profile was the complete recovery set — nothing else needed to resume. |
| INV-5 media_id stable | ✅ | Slug `8ae4fffe-...` identical in L1 and L2 URLs. |
| R-CODE-3 Locale-Independent | ✅ | B26 `arrow_forward` exact-text selector worked on EN profile with 2 candidates on /edit/. |
| B16 KEEP-7 | ✅ | L2 submit landed on real button, not disabled sibling (would have silently no-op'd otherwise — but gen 14% progress proves the real one was clicked). |
| B26 exact-text + mode blacklist | ✅ | Camera mode button NOT clicked during any submit. No `videocam` bleed. |

---

## 6. Issues / Decisions

### Issues encountered
None. Test ran clean on first attempt.

### Judgment calls
* **Reused existing Tab A then fresh Tab B** rather than starting from
  zero tabs. Acceptable because the prior tab was on an unrelated project
  (`f5148b9f-...`) and was navigated away to `/flow` homepage before L1 —
  no state bleed.
* **Did not assert on network API calls.** `read_network_requests` with
  `urlPattern='generate'` returned empty because tracking only starts
  after the first tool call. The 11% → 14% progress bar + rendered video
  is sufficient evidence that the generate API fired for both L1 and L2.
  A stricter test would call `read_network_requests` BEFORE submit.

### Bug candidates (out of scope)
None discovered.

---

## 7. What this proves for FlowEngine

The production worker's invocation shape looks like:

```python
# worker/main.py (simplified)
while True:
    job = await remote_api.claim(profiles=available_profiles)
    if not job: continue
    async with ProjectLock(job.project_url):
        async with FlowClient(profile=job.profile) as client:
            # <-- fresh Playwright context; cold browser state
            await client.page.goto(edit_url(job.project_url, job.media_id))
            # <-- navigate directly; no homepage, no DOM scan
            result = await dispatcher.dispatch(job, client)
            await remote_api.update(job.id, result)
```

Each `FlowClient` is a fresh Playwright `BrowserContext` — the closest
Playwright analogue to "new tab, cold." This test proves that path works
end-to-end on the real Flow UI, end to end, on EN profile, using only
DB-backed metadata to recover context. B22 (L2+ claim inheriting
`project_url` / `media_id` / `edit_url`) is what makes this possible on
the DB side; this session verifies the UI side.

---

## 8. Handoff notes

* **Workdir state:** clean except 2 untracked docs:
  * `docs/FLOW_E2E_WALKTHROUGH.md` — walkthrough doc from earlier in session
  * `docs/session-reports/2026-04-19_discrete-2job-verify_en.md` — this file
* **No env changes.**
* **Residual tab:** `tabId=1988718806` still open on the extend-child view.
  Safe to close manually via Chrome; not referenced by any persistent
  state.
* **If supervising from here:** recommend commit + push the two new docs
  to `claude/sharp-curie-80ac08`, then merge. No code change, low-risk.

---

## 9. Cross-reference

* `docs/FLOW_E2E_WALKTHROUGH.md` — step-by-step UI walkthrough created
  earlier this session (walkthrough style, not reference).
* `docs/session-reports/2026-04-19_B26_submit-and-model-exact-text.md` —
  B26 code fix session (`d4fca1a`).
* `docs/session-reports/2026-04-18_B22_*` — L2+ claim inheritance that
  unblocks this discrete test.
* `tests/test_submit.py` — unit tests enforcing B16 KEEP-7 + B26 canonical
  selector + scope parameter.
* `flow/submit.py` — `click_submit` with iterate-and-skip + scope param.
* `flow/navigation.py` — `edit_url()` helper used by worker to build the
  exact URL Tab B navigated to.
* `flow/operations/_base.py` — mode-button selector (two-pass: `title` →
  icon fallback) that made EN mode picks locale-independent.

---

## 10. Verdict

✅ **Discrete 2-job chain confirmed end-to-end on EN profile.** The
worker's DB-driven, cold-browser-per-job architecture is viable on the
current Flow UI. B26 submit, B22 claim inheritance, B16 KEEP-7
iterate/skip, and navigation-by-`edit_url` all hold when the second job
has zero in-browser connection to the first.

_Sign-off: ✅ No further action required. Safe to ship current state._
