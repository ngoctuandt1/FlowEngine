# Session Report — Wave 1 Flow 2026-05 feature update (`fix-live-test-bugs-2026-05`)

## 1. Metadata

| Field | Value |
|---|---|
| Session date | 2026-05-24 |
| Branch | `claude/fix-live-test-bugs-2026-05` |
| PR | #287 (base master) |
| Profile (live) | `ngoctuandt20` (free tier) |
| MCP probe browser | user's personal Chrome `ngoctuandt1` (ULTRA tier) |
| Codex 9router | OFFLINE today — 401 on every dispatch |
| Outcome | Bug B **LIVE PASS** ✓ / Bug C-F revealed spec mismatch |

## 2. Wave 1 commits

```
22bed6d  fix(warm): switch warm entry from Gmail to Flow
1cfd356  fix(composer): dismiss pointer-intercepting overlays + omni model support
8a5aa3c  fix(livetest): bug B ingredients — broader picker upload selectors
3c063b2  docs(livetest): probe artifacts + 2026-05-24 plan + Bug B-F session report
d908181  fix(livetest): bug B picker tile-commit + diagnostic capture
1d80d6d  fix(livetest): bug B diagnostic restructure + Wave 1 session report
8a289bd  fix(livetest): bug B chip counter — match Flow media-redirect thumbnails
7c5629b  fix(livetest): wait for 'Add to Prompt' button to enable before clicking
```

8 commits, 1415 unit tests pass.

## 3. Live verify

7 L1 ingredients attempts on `ngoctuandt20`. Final pass `1327f805`:

| Attempt | Symptom | Fix shipped |
|---|---|---|
| #1-3 | `Ingredient upload action not found` — picker `[role='dialog']` restriction too tight | `8a5aa3c` broaden selector list |
| #4-5 | `Ingredient attach mismatch: expected 1, found 0` — clicked wrong tile / chip counter stale | `d908181`, `1d80d6d` diagnostic; revealed real picker DOM |
| #6 | Same — but diagnostic showed `Add to Prompt` button DISABLED (upload not yet settled) | `7c5629b` wait for button enable |
| **#7** | **PASS** — Submit confirmed cards 4→6, video downloaded | — |

Final L1 metadata:
- `media_id`: `719d72d9-313c-4532-96b8-be8b5e393862`
- `project_url`: `https://labs.google/fx/tools/flow/project/6fe00b29-0bc3-4e9d-b40b-1e0d7d5097d4`
- `output_files`: `downloads/ingredients_720p_1779623608.mp4`
- Submit at 18:52:12, completed at 18:53:29 (~77 s wall).

### L2 chain C/D/E/F outcome

All 4 L2 jobs failed with `Failed to find <Op> button` — NOT the canonical
`paid_tier_required` symptom the spine §9 checklist expected on free-tier.

Screenshots captured (`error-captures/1779623641_889fb9cf_extend_button_not_found.png`
and 3 siblings) show: L1 video DID render in the edit view, but the right-rail
Extend/Insert/Remove/Camera buttons are simply absent. NO paywall banner is
shown. ngoctuandt20 free-tier 2026-05 UI silently hides L2 affordances rather
than gating them with the documented "Video editing is only available for paid
subscribers" banner that `flow/operations/_base.py::_assert_l2_available`
expects.

This is a **spec/code mismatch**: spine §9 says free-tier shows a banner →
canonical `error_kind=paid_tier_required`. Live reality on ngoctuandt20 →
buttons just hidden → generic `RuntimeError`, no canonical error_kind.

Follow-up needed (Wave 1.5 or Wave 2):
- Update `_assert_l2_available` to treat "L2 op button absent + free-tier
  profile + no paywall banner" as `paid_tier_required` too.
- Or update each op's button-not-found path to surface `paid_tier_required`
  when the profile is known free-tier.

## 4. Credit tally

| Attempt | Credit (est.) | Notes |
|---|---:|---|
| L1 #1-3 (upload not found) | ~5 each | No video burn — failed pre-submit |
| L1 #4-5 (chip counter) | ~5 each | No video burn — failed pre-submit |
| L1 #6 (Add to Prompt disabled) | ~0 | No submit |
| L1 #7 (PASS) | ~5 | Submit + 8 s video generation |
| L2 ×4 (button not found) | ~0 each | No submit — failed at edit-view button lookup |
| **Total** | **~25-30 credits** | `ngoctuandt20` still alive, no reCAPTCHA |

## 5. MCP probe findings (2026-05-24)

Full DOM evidence at [docs/livetest-2026-05-24/probe_findings.md](../livetest-2026-05-24/probe_findings.md).
**New Flow surfaces** beyond the 6-bug scope (deferred to Wave 2-4):

| Surface | Status |
|---|---|
| Agent mode + Agent Instructions | New op type, not in engine |
| View Scenes | New left-rail surface |
| Tools marketplace (10+ mini-apps) | New op family |
| Inline image edit `crop` / `select` / `draw` | New L2 op family on image media |

## 6. Done criteria

- [x] PR #287 opened, 8 commits
- [x] Unit tests pass (1415/1429)
- [x] MCP probe findings recorded
- [x] Multi-wave plan written
- [x] Claude in-session code review (codex 9router offline today)
- [x] **L1 ingredients live PASS** — `1327f805`
- [ ] L2 C/D/E/F live verify with canonical `paid_tier_required` — **revealed spec mismatch; needs follow-up code change**
- [x] Forensic diagnostic captures for next session

## 7. Handoff notes

- Worker `20503` exited after L2 chain. Server `13156` still up on `:8080`.
- Profile `ngoctuandt20` alive after 7 attempts; no reCAPTCHA hit.
- Memory `project_codex_status.md` flipped to UNAVAILABLE 2026-05-24.
- Memory `feedback_warm_profile_manual_gmail.md` superseded by commit `22bed6d`.
- **Next session**: small follow-up commit to map `Failed to find <X> button` →
  `paid_tier_required` when no paywall banner present + free-tier signal. This
  closes Bug C-F per spine §9 contract.

**Status:** DONE_WITH_CONCERNS — Bug B fully live-verified end-to-end; Bug C-F code structurally complete but 2026-05 UI on free-tier hides L2 buttons silently (no banner). Follow-up patch needed to surface canonical `error_kind=paid_tier_required` on button-absent path.
