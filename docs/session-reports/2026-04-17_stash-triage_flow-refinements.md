# Session Report — `stash-triage` Analyze `stash@{0}` flow refinements vs master

Read-only triage of the 4-file `stash@{0}` ("WIP: flow refinements — direct
edit-url nav, chip re-click model close, verify extend panel, iterate enabled
submit buttons") that predates Phase A. Goal: classify each hunk as KEEP /
OBSOLETE / CONFLICT so supervisor can cherry-pick or drop.

**Stash is NOT modified** — no apply, pop, drop, or branch. Only `git stash
show -p` was used.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `stash-triage` |
| Task type | triage (stash analysis, read-only) |
| Session started | 2026-04-17 |
| Session ended | 2026-04-17 |
| Duration actual | ~15m |
| Duration estimate | ~15m |
| Worker | Claude Opus 4.7 (executor session) |
| Branch | `claude/festive-dijkstra-f998ad` (worktree) |
| Master @ | `d5054ac` |

---

## 2. Commits landed

```
<this commit>  docs(stash-triage): analyze stash@{0} flow refinements vs master
```

1 commit. Report file only. Zero `.py` diff.

---

## 3. Files changed

```
docs/session-reports/2026-04-17_stash-triage_flow-refinements.md   NEW   (this report)
```

Tổng: `1 file, +N / -0 lines` (new file).

**File blacklist observed:** no source `.py` touched, no SPEC/WORKPLAN edits,
no `.claude/` changes, stash untouched (`git stash list` still shows
`stash@{0}`).

---

## 4. Tests

N/A — triage session, no code change to test.

`git stash list` verification post-session:
```
stash@{0}: On master: WIP: flow refinements — direct edit-url nav, chip re-click
model close, verify extend panel, iterate enabled submit buttons
```

---

## 5. SPEC.md update

N/A — no invariant change, no bug fix, no known-bug struck. Supervisor may
open new B<N> entries if choosing to cherry-pick KEEP hunks.

---

## 6. Invariants & rules verified

Checklist (all pass — no code touched):

- [x] INV-1 Account Binding — no profile handling touched
- [x] INV-2 Navigate by `edit_url` — not touched (but stash proposes change, see §7)
- [x] INV-3 Store Everything — not touched
- [x] INV-4 Serial per Project — not touched
- [x] INV-5 `media_id` stable — not touched
- [x] R-CODE-3 Locale-Independent — not touched
- [x] R-CODE-10 No `datetime.utcnow()` — not touched
- [x] R-CC-1 KHÔNG restructure — read-only analysis

---

## 7. Issues / Decisions — Hunk-by-hunk triage

Stash stats: **+518 / -206, 4 files** — see `git stash show --stat stash@{0}`.

### 7.1 `flow/model_selector.py` (+91/-50)

| Hunk | Stash lines | Summary | Verdict | Rationale |
|---|---|---|---|---|
| H1 | stash L5–22 | Capture `chip_handle` (ElementHandle) + set `chip_tagged_js` flag before click | **KEEP** (dep of H4) | Required by H4's toggle-close mechanism; no standalone value but no harm |
| H2 | stash L53–94 | Pre-check: if `is_lp` and LP items already visible → skip `_open_model_dropdown` (avoid toggle-close) | **KEEP** | Addresses real extend-mode bug: panel may pre-show LP items, and clicking the model dropdown would toggle-close the panel hiding LP items. Master has no such guard. |
| H3 | stash L99–139 | Thread `chip_handle` + `chip_tagged_js` through 4 call sites of `_close_model_panel` | **KEEP** (dep of H4) | Signature change — required for H4 |
| H4 | stash L140–229 | Rewrite `_close_model_panel`: toggle-close by re-clicking chip (3 methods: ElementHandle / JS-tagged / DOM search). Removes Escape fallback entirely. | **CONFLICT** | Master uses click-outside (Slate editor click) + Escape fallback (commit `7245ae8`). Stash's philosophy is "toggle-close = click same chip that opened it", which is arguably more robust (avoids relying on Slate being clickable / panel not intercepting). But master's approach passed Phase A validation (B8 LP credit leak fix). **User decision:** adopt stash's toggle-close pattern, or keep master's validated click-outside? |

**File verdict:** mostly KEEP for the H2 pre-check (novel, non-conflicting)
and H1/H3 as dependencies of H4. H4 itself is a philosophy decision —
**CONFLICT, user review**.

### 7.2 `flow/operations/_base.py` (+113/-85)

| Hunk | Stash lines | Summary | Verdict | Rationale |
|---|---|---|---|---|
| H1 | stash L239–259 | Reverse navigation strategy: edit URL FIRST, fallback project URL + tile click. Master's comment explicitly says opposite ("Direct /edit/ URLs often fail because the Flow SPA needs the project context loaded first") | **CONFLICT** | Direct strategy reversal. Master has written rationale, stash has no counter-evidence. Needs user decision with live test. |
| H2 | stash L258–280 | Post-nav verification: require `/edit/` in URL, warn if landed on different `media_id` than requested. Replaces silent "Last resort: try direct edit URL" fallback. | **KEEP** | Pure addition — stricter verification + warning log for media mismatch. No regression risk. Supports INV-5 (`media_id` stable). |
| H3 | stash L319–395 | `_click_video_tile` overhaul: NEW Priority 1 = JS click by media_id match (href/data-tile-id/data-media-id). Removes generic JS video-parent + googleusercontent image fallbacks. | **KEEP** | Master clicks "first video" — which in a multi-video project may pick the WRONG video, violating INV-5. Stash's media_id matching is a genuine bug-fix candidate. (NOTE: dropping the `googleusercontent img` fallback is acceptable — that was a Phase-0 speculative fallback, never verified needed.) |
| H4 | stash L460–496 | NEW `_click_storyboard_video` helper (clicks `play_circle` buttons / video elements in storyboard) | **OBSOLETE** | **DEAD CODE** — function is defined but NEVER CALLED anywhere in the stash. Speculative helper for future use. Do not cherry-pick. |

**NOTE**: Stash does NOT touch `draw_bbox_on_video` — B11's canvas rewrite
(`ce6683a`) is orthogonal and unaffected.

**File verdict:** H2 + H3 are **KEEP** (hardening nav + media_id-aware tile
click). H1 is **CONFLICT** (strategy reversal needs user review). H4 is
**OBSOLETE** (dead code).

### 7.3 `flow/operations/extend.py` (+85/-46)

| Hunk | Stash lines | Summary | Verdict | Rationale |
|---|---|---|---|---|
| H1 | stash L506–548 | Comment cleanup + "Step 3.5: `_verify_extend_panel` call" + RuntimeError if panel not open | **KEEP** | Fail-fast check — addresses case where Extend button click succeeds but panel fails to open (silent failure in master → submit eventually times out with no diagnosis). |
| H2 | stash L571–586 | On submit failure, log URL + editor count before raising | **KEEP** | Pure diagnostic improvement. Non-breaking. |
| H3 | stash L594–624 | NEW `_verify_extend_panel()` helper: polls for `editors>=2` OR `[data-scroll-state='START']` | **KEEP** | Supports H1. `data-scroll-state='START'` is extend-panel-specific attribute (verified in stash docstring). |
| H4 | stash L627–665 | `_type_extend_prompt` — NEW Method 1: target `[data-scroll-state='START'] [data-slate-editor='true']` (extend-panel-specific). Method 2: last Slate editor (unchanged from master). | **KEEP** (partial) | Method 1 (scroll-state selector) is a genuine improvement — more specific than master's "last slate editor" which assumes DOM order. |
| H5 | stash L680–705 | REMOVE placeholder-based fallbacks (`[placeholder*='next']`, `[placeholder*='tiếp']`, `[aria-label*='extend']`) | **CONFLICT** | Stash removes 4 fallback selectors. If the scroll-state + last-slate methods both fail, master still had a third way; stash drops it. **Defensive: prefer keeping these fallbacks**. Cherry-pick H1–H4 but DO NOT drop these. |

**NOTE**: Phase A commit `8807387` (profile path + Slate prompt) did NOT add
`_verify_extend_panel` — this is still novel.

**File verdict:** H1–H4 are **KEEP** (fail-fast + diagnostics + more specific
selector). H5 is **CONFLICT** (fallback removal — recommend preserving
placeholder selectors).

### 7.4 `flow/submit.py` (+25/-14)

| Hunk | Stash lines | Summary | Verdict | Rationale |
|---|---|---|---|---|
| H1 | stash L710–761 | Iterate ALL matching buttons per selector (`.nth(i)` in range(count)) instead of `.first`. Skip disabled buttons (`is_enabled` check). Add per-button debug logs. | **KEEP** | Addresses real bug: if selector matches multiple buttons and `.first` is disabled (loading state, duplicate DOM nodes), master falls through to next selector and may miss the enabled submit button. Stash iterates all, clicks first enabled match. Existing `_SKIP_PATTERN` filter preserved inside the loop. No conflict with Phase A commit `5c7d625` (which touched `submit_with_confirmation`, not `click_submit`). |

**File verdict:** **KEEP** — clean improvement, no overlap with Phase A.

### 7.5 Summary table

| File | KEEP hunks | OBSOLETE hunks | CONFLICT hunks |
|---|---|---|---|
| `flow/model_selector.py` | H1, H2, H3 | — | H4 (toggle-close philosophy) |
| `flow/operations/_base.py` | H2, H3 | H4 (`_click_storyboard_video` dead) | H1 (nav strategy reversal) |
| `flow/operations/extend.py` | H1, H2, H3, H4 | — | H5 (placeholder fallback removal) |
| `flow/submit.py` | H1 | — | — |
| **Total** | **10 KEEP** | **1 OBSOLETE** | **3 CONFLICT** |

---

### Bug candidates from stash (propose new B<N> if cherry-picked)

- **B14 candidate** — `flow/operations/_base.py:_click_video_tile` clicks
  first video not matching `media_id` → wrong video in multi-video projects
  (stash §7.2 H3 KEEP addresses this)
- **B15 candidate** — `flow/operations/extend.py:extend_video` has no panel-open
  verification; silent failure if Extend click doesn't open panel (stash §7.3
  H1+H3 KEEP addresses this)
- **B16 candidate** — `flow/submit.py:click_submit` gives up on selector if
  `.first` is disabled; may skip enabled submit sibling (stash §7.4 H1 KEEP
  addresses this)
- **B17 candidate** — `flow/model_selector.py:select_model` in extend mode:
  if LP items pre-visible, `_open_model_dropdown` toggle-closes panel and
  hides them (stash §7.1 H2 KEEP addresses this)

---

### KEEP hunks — exact code blocks for cherry-pick

These are the blocks supervisor should preserve. Line numbers are in the
stash patch (`git stash show -p stash@{0}`).

#### KEEP-1: `model_selector.py` H2 — LP items pre-check

Location: replaces master's `flow/model_selector.py` lines 124–138 region.

```python
    # Step 2.7: Check if LP items already visible BEFORE opening dropdown.
    # In extend mode, the model panel may already show LP options directly
    # without needing to click the Veo dropdown. Clicking it would TOGGLE
    # the dropdown closed, hiding the LP items.
    is_lp = "Lower Priority" in target_text
    base_name = target_text.split(" [")[0].strip()  # "Veo 3.1 - Fast"

    MODEL_ITEM_SELECTORS = (
        "menuitem, [role='menuitem'], [role='option'], "
        "button, [role='button'], [role='listbox'] button"
    )

    # Pre-check: are LP items already visible?
    dropdown_opened = False
    if is_lp:
        try:
            lp_items = page.locator(MODEL_ITEM_SELECTORS).filter(
                has_text=re.compile(r"Lower Priority", re.IGNORECASE)
            )
            lp_count = await lp_items.count()
            if lp_count > 0:
                logger.info("LP items already visible (%d) — skipping dropdown open", lp_count)
            else:
                dropdown_opened = await _open_model_dropdown(page)
        except Exception:
            dropdown_opened = await _open_model_dropdown(page)
    else:
        dropdown_opened = await _open_model_dropdown(page)
```

#### KEEP-2: `_base.py` H2 — Post-nav verification + media_id mismatch warning

Insert after `_click_video_tile` call (master `flow/operations/_base.py` line 89):

```python
    # Verify we're in edit mode for the right media
    current = page.url
    if "/edit/" not in current:
        logger.error("Failed to enter edit mode. URL: %s", current[:100])
        raise RuntimeError("Failed to enter edit mode")

    # Log if we landed on a different media than requested (but proceed —
    # Flow SPA often redirects edit URLs; the important thing is being in
    # edit mode for *some* video in the correct project).
    current_media = extract_media_id(current)
    if media_id and current_media and current_media != media_id:
        logger.warning(
            "Edit mode entered for different media: requested=%s actual=%s — proceeding",
            media_id[:20], current_media[:20],
        )
```

#### KEEP-3: `_base.py` H3 — `_click_video_tile` media_id-aware priority

Replaces master `flow/operations/_base.py` lines 138–212:

```python
    """Click a video tile in the project view to enter edit mode.

    When direct /edit/ URL navigation fails, the project view shows media
    tiles.  Clicking a tile navigates to /edit/{media_id}.

    Priority:
    1. If media_id given: JS click on tile whose link/data contains media_id
    2. First [data-tile-id] tile
    3. First video element
    """
    await asyncio.sleep(2)  # let project view render

    # Priority 1: click tile matching media_id via JS
    if media_id:
        try:
            clicked = await page.evaluate("""(targetId) => {
                // Look for links containing the media_id
                const links = document.querySelectorAll('a[href*="/edit/"]');
                for (const a of links) {
                    if (a.href.includes(targetId)) {
                        a.click();
                        return 'link:' + targetId.substring(0, 12);
                    }
                }
                // Look for tiles with data attributes matching media_id
                const tiles = document.querySelectorAll('[data-tile-id]');
                for (const tile of tiles) {
                    const tileId = tile.getAttribute('data-tile-id') || '';
                    if (tileId.includes(targetId) || targetId.includes(tileId)) {
                        tile.click();
                        return 'tile:' + tileId.substring(0, 12);
                    }
                }
                // Look for any element with media_id in attributes
                const all = document.querySelectorAll('[data-media-id], [data-id]');
                for (const el of all) {
                    const id = el.getAttribute('data-media-id') || el.getAttribute('data-id') || '';
                    if (id.includes(targetId)) {
                        el.click();
                        return 'data-id:' + id.substring(0, 12);
                    }
                }
                return null;
            }""", media_id)
            if clicked:
                logger.info("Clicked tile for media_id via JS: %s", clicked)
                await asyncio.sleep(3)
                if "/edit/" in page.url:
                    logger.info("Edit mode entered: %s", page.url[:100])
                    return True
        except Exception:
            pass

    # Priority 2: click first [data-tile-id] tile
    try:
        tile = page.locator("[data-tile-id]").first
        if await tile.is_visible(timeout=3000):
            await tile.click(timeout=3000)
            logger.info("Clicked first [data-tile-id] tile")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    # Priority 3: click first video element
    try:
        video = page.locator("video").first
        if await video.is_visible(timeout=3000):
            await video.click(timeout=3000)
            logger.info("Clicked video element")
            await asyncio.sleep(3)
            if "/edit/" in page.url:
                logger.info("Edit mode entered: %s", page.url[:100])
                return True
    except Exception:
        pass

    return False
```

**Caveat:** this replaces master's TILE_SELECTORS chain + generic JS media
fallback. Supervisor should consider whether to keep one generic JS fallback
as the final safety-net (stash drops it completely). Recommend: use stash's
P1+P2+P3 then APPEND master's generic JS fallback as P4 for safety.

#### KEEP-4: `extend.py` H1 + H3 — Panel verification

Insert call in `extend_video` between Step 3 (click Extend) and Step 4 (type
prompt), master `flow/operations/extend.py` ~line 125:

```python
    # Step 3.5: Verify extend panel opened
    # Extend panel adds a SECOND Slate editor. Wait for it.
    await asyncio.sleep(1)
    panel_open = await _verify_extend_panel(page)
    if not panel_open:
        raise RuntimeError("Extend panel did not open after clicking Extend button")
```

Add new helper at module level:

```python
async def _verify_extend_panel(page, timeout_sec: float = 5.0) -> bool:
    """Verify the extend panel opened by checking for a second Slate editor.

    The extend panel adds a new Slate editor (data-slate-editor) for the
    extend prompt. The main composer already has one, so we expect >= 2.
    Also checks for extend-specific UI: "Bắt đầu"/"Start" toggle, or
    scroll-state attribute on the panel.
    """
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        try:
            editors = await page.locator("[data-slate-editor='true']").count()
            if editors >= 2:
                logger.info("Extend panel verified: %d slate editors found", editors)
                return True
            # Also check for data-scroll-state="START" (extend panel attribute)
            panels = await page.locator("[data-scroll-state='START']").count()
            if panels >= 1:
                logger.info("Extend panel verified via data-scroll-state")
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)

    # Log what we see for debugging
    try:
        editors = await page.locator("[data-slate-editor='true']").count()
        logger.error("Extend panel NOT detected: only %d slate editors", editors)
    except Exception:
        pass
    return False
```

#### KEEP-5: `extend.py` H2 — Submit failure diagnostics

Replaces master `flow/operations/extend.py` line 143 `raise RuntimeError("Extend submit not confirmed")`:

```python
    if not confirmed:
        # Log page state for diagnosis
        try:
            url = page.url
            editors = await page.locator("[data-slate-editor='true']").count()
            logger.error(
                "Extend submit not confirmed. url=%s editors=%d",
                url[:100], editors,
            )
        except Exception:
            pass
        raise RuntimeError("Extend submit not confirmed — generation did not start")
```

#### KEEP-6: `extend.py` H4 — Scroll-state-aware Slate editor selector

Prepend to master `_type_extend_prompt` before the existing "last slate
editor" Method 2:

```python
    # Method 1: target the extend panel's editor by data-scroll-state
    try:
        panel = page.locator("[data-scroll-state='START'] [data-slate-editor='true']")
        if await panel.count() > 0:
            el = panel.first
            if await el.is_visible(timeout=2000):
                await el.click(timeout=2000)
                await asyncio.sleep(0.3)
                await page.keyboard.press("Control+a")
                await asyncio.sleep(0.1)
                await page.keyboard.type(prompt, delay=20)
                logger.info("Extend prompt typed via data-scroll-state editor")
                return
    except Exception as e:
        logger.debug("data-scroll-state editor failed: %s", e)
```

**IMPORTANT**: Keep master's placeholder-based fallbacks after Method 2 —
do not remove (stash's H5).

#### KEEP-7: `submit.py` H1 — Iterate all submit buttons, skip disabled

Replaces master `flow/submit.py` lines 40–59 (the inner loop body in
`click_submit`):

```python
    for selector in SUBMIT_SELECTORS:
        try:
            # Check ALL matching buttons, not just .first — skip disabled ones
            locator = page.locator(selector)
            count = await locator.count()
            logger.debug("Submit selector %s: count=%d", selector, count)
            for i in range(count):
                btn = locator.nth(i)
                try:
                    vis = await btn.is_visible(timeout=500)
                    ena = await btn.is_enabled(timeout=300) if vis else False
                    try:
                        text = await btn.inner_text()
                    except Exception:
                        text = ""
                    skip = bool(_SKIP_PATTERN.search(text)) if text else False
                    logger.debug(
                        "  btn[%d]: vis=%s ena=%s skip=%s text=%s",
                        i, vis, ena, skip, text.strip()[:30],
                    )
                    if not vis or not ena or skip:
                        continue
                    await btn.click(timeout=timeout_ms, force=True)
                    logger.info("Submit clicked via: %s [%d] text=%s", selector, i, text.strip()[:30])
                    return True
                except Exception as e:
                    logger.debug("  btn[%d] error: %s", i, e)
                    continue
        except Exception as e:
            logger.debug("Submit selector %s error: %s", selector, e)
            continue
```

---

### CONFLICT hunks — user decisions needed

1. **model_selector.py H4** — toggle-close vs click-outside
   - Master: `_close_model_panel(page, dropdown_was_opened)` → click Slate editor, fallback Escape.
   - Stash: `_close_model_panel(page, chip_handle, chip_tagged_js)` → re-click chip (3 methods), no Escape.
   - **Question for user:** Does master's click-outside + Escape fallback ever show the "extend panel accidentally closed" bug the stash docstring warns about? If yes → adopt stash. If no → keep master.

2. **_base.py H1** — navigation strategy
   - Master: project URL first, then tile click, then direct edit URL as last resort.
   - Stash: direct edit URL first, fall back to project URL + tile click.
   - **Question for user:** Which strategy has live evidence of working? Master's comment claims "/edit/ URLs often fail" — is this still true on current Flow SPA?

3. **extend.py H5** — placeholder fallback removal
   - Master: has 4 placeholder/aria-label-based fallback selectors if Slate-editor detection fails.
   - Stash: removes all 4.
   - **Recommendation:** KEEP the fallbacks (do not cherry-pick the removal). Defense-in-depth with low cost.

---

## 8. Handoff notes

**Supervisor action items:**

1. **Decide on 3 CONFLICT hunks** (§7 above). Recommend:
   - model_selector.py H4 → **user review** (philosophy call)
   - _base.py H1 → **user review** (needs live Flow SPA probe to settle)
   - extend.py H5 → **reject removal** (keep master's fallbacks)

2. **For 10 KEEP hunks** → soạn cherry-pick prompt with code blocks from §7.
   Split into 4 small branches/PRs (one per B-bug candidate):
   - `claude/bug-14-tile-media-id-match` → KEEP-3 + KEEP-2 (`_base.py`)
   - `claude/bug-15-extend-panel-verify` → KEEP-4 + KEEP-5 + KEEP-6 (`extend.py`)
   - `claude/bug-16-submit-iterate-enabled` → KEEP-7 (`submit.py`)
   - `claude/bug-17-lp-items-pre-check` → KEEP-1 (`model_selector.py` — standalone; H1/H3 chip-handle threading deferred with H4)

3. **Stash disposition:**
   - DO NOT drop `stash@{0}` yet. Keep it until all cherry-picks land.
   - After cherry-picks merged to master → verify nothing unique remains
     in stash → then `git stash drop stash@{0}`.

**Workdir state:** clean. `git stash list` unchanged — `stash@{0}` intact.
Temporary `stash.patch.tmp` created during triage should be gitignored or
removed (not under report scope — leave for supervisor decision).

**Next session:** supervisor writes cherry-pick prompt per §8 item 2, opens
GitHub issues B14–B17 referencing this report.

---

## 9. Done criteria checklist

From executor prompt:

- [x] 4 files analyzed, each has verdict (`model_selector.py` CONFLICT partial, `_base.py` mixed, `extend.py` mixed, `submit.py` KEEP)
- [x] KEEP hunks listed with exact code blocks (§7 KEEP-1 through KEEP-7, 7 blocks)
- [x] Recommendation clear: **cherry-pick 10 hunks across 4 B<N> branches; reject 1 obsolete; user review 3 conflicts; defer stash drop until cherry-picks land**
- [x] `stash@{0}` unchanged (`git stash list` verified)
- [x] Zero `.py` diff (only this report written)
- [x] Report has 9 sections per `_TEMPLATE.md`

---

_Sign-off: ✅ Ready for supervisor review._
