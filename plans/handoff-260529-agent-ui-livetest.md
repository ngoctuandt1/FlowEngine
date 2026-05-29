# Handoff: Agent-UI livetest autopilot

**Date**: 2026-05-29  
**Branch**: master (head: `cb34b3b`)  
**Livetest profile**: `ngoctuandt20` (only live profile — s1324h1450/default/backups all dead)

---

## Context

Google Flow UI đã thay đổi hoàn toàn (2026-05 rollout): toolbar cũ (Extend/Remove/Camera/Insert) đã bị thay thế bằng AI agent interface "Describe your edit(s)". Session này đã:

1. Xác định root cause: `flowAgent/applets` API trả về danh sách applets → Flow render agent panel thay toolbar
2. Fix `_assert_l2_available` để không raise khi agent edit UI hiện diện
3. Fix `run_extend` và `run_remove` để dùng `submit_via_agent_edit_ui` khi không có toolbar button
4. Fix upscale: download button mới gọi trực tiếp `media.getMediaUrlRedirect` thay vì dropdown → `_try_direct_download_via_button`

---

## R21 Full Matrix Results (last run)

**File**: `/opt/flowengine/tests/live_runs/1780024787_matrix.json`  
**Command**: `--profile=ngoctuandt20 --cates=t2v,t2i,f2v,extend,camera,insert,remove --modes=hybrid,revapi --depth=2`

| Cate | Hybrid | RevAPI | Status |
|------|--------|--------|--------|
| t2v | L1/1 ✅ | L1/1 ✅ | PASS |
| t2i | L0/1 ❌ | L0/1 ❌ | FAIL |
| f2v | L0/1 ❌ | L0/1 ❌ | FAIL |
| extend | L2/2 ✅ | L2/2 ✅ | PASS |
| camera | L1/2 ❌ | L1/2 ❌ | FAIL |
| insert | L1/2 ❌ | L1/2 ❌ | FAIL |
| remove | L2/2 ✅ | L1/2 ❌ | PARTIAL |

### Per-failure errors

| Cate/Mode | Error |
|-----------|-------|
| t2i (both) | `Could not open composer chip (tried 5 icon variants). Flow may have introduced a new chip icon` |
| f2v (both) | `Could not open composer menu for Video mode; visible menu buttons=[more_vert, filter_list, add, settings_2, more_vert]` |
| camera (both) | `Failed to find Camera button` (no agent UI fallback in `run_camera`) |
| insert (both) | `Failed to find Insert button` (no agent UI fallback in `run_insert`) |
| remove/revapi | `Failed to find Remove button` (agent UI fallback exists but submit button not found) |

---

## Root Causes

### 1. camera + insert (EASY — same fix as extend/remove)
`run_camera` và `run_insert` chưa có agent edit UI fallback. Cần thêm pattern giống `run_extend`:
- File: `flow/operations/camera.py`, `flow/operations/insert.py`
- Import: `from flow.operations._base import agent_edit_ui_present, submit_via_agent_edit_ui`
- Khi Camera/Insert button không found → check `agent_edit_ui_present` → `submit_via_agent_edit_ui`
- Camera command: `"Apply {direction} camera movement"` (job có `direction` field)
- Insert command: `"Insert {description} at {region}"` (job có `bbox` + description)

### 2. remove/revapi submit button not found (MEDIUM)
`submit_via_agent_edit_ui` typed text OK nhưng không find submit button. Nguyên nhân có thể:
- Sau khi type, submit button `arrow_forwardCreate` bị disabled/ẩn tạm
- Hoặc selector không match
- Cần debug: thêm screenshot capture khi `submit button not found`
- Quick fix: thêm `await asyncio.sleep(0.5)` sau khi type, tăng visibility timeout từ 1000ms lên 3000ms
- Fallback: thêm JS click selector vào `submit_via_agent_edit_ui`

**Selectors hiện có** (trong `_base.py:submit_via_agent_edit_ui`):
```python
submit_selectors = (
    "button:has(i:text-is('arrow_forward'))",
    "[role='button']:has(i:text-is('arrow_forward'))",
    "button:has-text('arrow_forwardCreate')",
    "button[type='submit']",
)
```

Cần thêm fallback:
```python
"button:has(span:text-is('arrow_forward'))",    # span thay vì <i>
"button:has-text('Create')",                     # broader match
```

Và JS fallback:
```python
clicked = await page.evaluate("""() => {
    const btns = [...document.querySelectorAll('button,[role="button"]')];
    const reversed = btns.reverse();
    for (const btn of reversed) {
        const t = btn.textContent || '';
        if (t.includes('arrow_forward') || (t.includes('Create') && !t.includes('add_2'))) {
            if (btn.offsetParent !== null) { btn.click(); return true; }
        }
    }
    return false;
}""")
```

### 3. t2i composer chip (MEDIUM)
Flow thay đổi composer chip icon cho Image mode. Error: "Could not open composer chip (tried 5 icon variants)".
- File: `flow/composer.py` hoặc tương đương
- Cần probe UI thực để tìm icon variant mới
- Dùng MCP Chrome: navigate to Flow project → inspect composer chip DOM

### 4. f2v composer menu (MEDIUM)
Flow thay đổi composer menu cho frames-to-video. Error: "Could not open composer menu for Video mode".
- Visible buttons: `more_vert More options`, `filter_list Sort & Filter`, `add Add Media`, `settings_2 View Settings`
- "Add Media" button (`add Add Media`) có thể là entry point cho f2v upload
- Cần probe UI thực tế

---

## Files Modified This Session

| File | Change |
|------|--------|
| `flow/agent.py` | Add `install_agent_session_blocker`, block `flowAgent/applets`, `flowAgent/savedSharedApplets`, `agentInfo` |
| `flow/operations/_base.py` | `_AGENT_EDIT_UI_TOKENS`, `agent_edit_ui_present()`, `submit_via_agent_edit_ui()`, `_assert_l2_available` agent UI check, diag button dump + screenshot |
| `flow/operations/extend.py` | Agent edit UI fallback after all button fallbacks fail |
| `flow/operations/remove.py` | Same pattern as extend |
| `flow/upscale.py` | `_try_direct_download_via_button()`, reduce menu wait 10s→3s |

---

## Livetest Commands

```bash
# Trên debian (ssh debian)
cd /opt/flowengine

# Extend/Remove only (đã pass)
DISPLAY=:99 FLOW_CHROME_GPU=disable FLOW_EXTEND_VIA_REVERSE=1 \
  FLOW_ERROR_CAPTURE_DIR=/tmp/flow-captures \
  python3 scripts/live_verify_full_matrix.py \
  --profile=ngoctuandt20 --cates=extend,remove --modes=hybrid,revapi --depth=2

# Full matrix
DISPLAY=:99 FLOW_CHROME_GPU=disable FLOW_EXTEND_VIA_REVERSE=1 \
  FLOW_ERROR_CAPTURE_DIR=/tmp/flow-captures \
  python3 scripts/live_verify_full_matrix.py \
  --profile=ngoctuandt20 --cates=t2v,t2i,f2v,extend,camera,insert,remove \
  --modes=hybrid,revapi --depth=2
```

---

## Key Architecture Facts (new Flow UI)

- **Agent edit UI**: `/edit/{media_id}` shows `<div contenteditable="true">` với placeholder "Describe your edit(s)" + `arrow_forwardCreate` submit button
- **Session blockers**: `flowCreationAgent/sessions` GET→`{"sessions":[]}`, POST→`{}`
- **Applets blocker**: `flowAgent/applets` GET→`{"applets":[]}`, `flowAgent/savedSharedApplets` GET→`{"savedSharedApplets":[]}`
- **Download**: button gọi `/fx/api/trpc/media.getMediaUrlRedirect?name=<media_id>` → direct browser download, NO dropdown menu
- **Submit button**: `button:has-text('arrow_forwardCreate')` hoặc JS click `textContent.includes('arrow_forward')`
- **Editor selector**: `[contenteditable='true']` (duy nhất 1 trên page)

---

## Next Session Priority

1. **camera + insert** (highest): add agent edit UI fallback — same pattern as extend/remove, 30min code + test
2. **remove/revapi** submit button: fix selector/timing in `submit_via_agent_edit_ui` — 15min
3. **t2i chip** + **f2v menu**: probe UI changes — 30min investigation + code
4. Run full matrix again to verify all pass
