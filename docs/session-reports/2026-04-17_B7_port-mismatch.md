# Session Report — `B7` Port default mismatch

> Retroactive report — B7 đã đóng trước khi chuẩn report được thiết lập (§1.6 WORKPLAN). File này viết sau để làm mẫu cho session con tương lai.

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B7` |
| Task type | bug-fix (P0 blocker) |
| Session started | 2026-04-17 (supervisor session) |
| Session ended | 2026-04-17 |
| Duration actual | ~15m (gồm split-commit docs + fix) |
| Duration estimate | `5m` (WORKPLAN §3.1) |
| Worker | Claude Sonnet 4.6 (supervisor — ngoại lệ, về sau chỉ spawn session con code) |
| Branch | master |

---

## 2. Commits landed

```
336ba75  docs: add design trilogy (DESIGN/SPEC/WORKPLAN) + CLAUDE context
a95c9b5  fix(#7): align server default port with worker (8080)
```

Commit `336ba75` là tiền đề (add trilogy docs). Commit `a95c9b5` là fix B7 thực sự.

---

## 3. Files changed

### Commit `a95c9b5` (B7 fix)
```
server/config.py         +1 / -1    (default "8000" → "8080")
run_worker.py            +1 / -1    (docstring comment sync)
tests/test_config.py     +42 / -0   (NEW — regression test + env override test)
docs/SPEC.md             +4 / -5    (strike-through §D.3.3 + §D.4 B7)
```

Tổng: `4 files, +48 / -7 lines`

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_config.py::test_server_port_default_is_8080` | ✅ pass (manual) | default = 8080 |
| `tests/test_config.py::test_server_port_respects_env_override` | ✅ pass (manual) | SERVER_PORT=9999 → 9999 |

- Tổng: `2 pass / 0 fail / 0 skipped`
- Pytest infra chưa có (B9 chưa chạy) → verify bằng `python -c`:
  ```
  $ unset SERVER_PORT; python -c "from server.config import SERVER_PORT; assert SERVER_PORT == 8080"
  (no output = pass)
  $ SERVER_PORT=9999 python -c "from server.config import SERVER_PORT; assert SERVER_PORT == 9999"
  (no output = pass)
  ```
- RED → GREEN verified: trước fix `assert SERVER_PORT == 8080` fail với `AssertionError: FAIL: expected 8080` (actual 8000). Sau fix pass.

---

## 5. SPEC.md update

- [x] Strike-through `§D.3.3 — ~~Port default mismatch (B7)~~ ✅ FIXED (commit 336ba75..HEAD)`
- [x] Strike-through `§D.4 — ~~B7 — Port mismatch (P0)~~ ✅ FIXED`
- [x] Commit hash reference added (`336ba75..HEAD` trong §D.3.3)

Commit hash cho SPEC.md update: `a95c9b5` (cùng commit với code fix — đúng rule §1.5).

---

## 6. Invariants & rules verified

- [x] INV-1..INV-5 — N/A (B7 chỉ chạm config constant, không đụng chain/media)
- [x] R-CODE-3 Locale-Independent — N/A
- [x] R-CODE-10 No `datetime.utcnow()` — N/A
- [x] R-CC-1 KHÔNG restructure — chỉ đổi 1 constant, không đụng kiến trúc
- [x] R-CC-5 Docs update cùng commit — SPEC.md strike-through trong `a95c9b5`

---

## 7. Issues / Decisions

### Vấn đề phát sinh
- `git status` lúc start có 4 file `flow/*` modified từ session trước (không phải B7 scope). Supervisor quyết định tách riêng — thấy §8 handoff.
- 3 docs (DESIGN/SPEC/WORKPLAN) lúc này vẫn untracked. Supervisor split thành 2 commit: `336ba75` docs trilogy (với B7 vẫn "mở") + `a95c9b5` B7 fix (kèm strike-through). Tránh commit B7 chứa 3,395 dòng docs lẫn.

### Quyết định đã đưa
- **Split commit strategy:** Docs trilogy commit trước (không chứa strike-through) → B7 commit sau (kèm strike-through). Lý do: giữ thời gian causality đúng (SPEC.md lúc docs commit vẫn ghi B7 là P0; sau khi B7 fix mới strike).
- **Không auto-commit `flow/*` leftover:** chuyển sang session triage riêng → xem handoff.
- **Fix docstring `run_worker.py:8`:** ngoài `server/config.py:19`, có comment `default: http://localhost:8000` cần sync. Cùng root cause → đưa vào B7 scope (không coi là "tiện thể fix").

### Bug candidates phát hiện NHƯNG KHÔNG fix
- `PLAN.md:493,498` ghi `SERVER_PORT=8000` / `SERVER_URL=http://localhost:8000`. File này là historical planning (Phase 1-5 checklist đã xong), không phải config runtime. **Không fix** — chỉ ghi chú.

---

## 8. Handoff notes

- Workdir state: **KHÔNG sạch** — còn 4 file uncommitted từ trước:
  ```
  M flow/model_selector.py
  M flow/operations/_base.py
  M flow/operations/extend.py
  M flow/submit.py
  ```
- Đã delegate sang session triage riêng (xem prompt trong chat supervisor 2026-04-17).
- Session B9 (tiếp theo trong WORKPLAN §3.2) **không đụng `flow/*`** → có thể chạy song song với triage, không phải block.
- Env khi verify: PYTHONPATH mặc định, không cần set gì.
- `.env.example` + `docker/*` đã đúng 8080 từ trước — không cần đụng.

---

## 9. Done criteria checklist (từ WORKPLAN §3.1)

- [x] Code change: `server/config.py:19` `"8000"` → `"8080"` ✅
- [x] Test red → green: verify bằng `python -c` trước và sau fix ✅
- [x] SPEC.md strike-through (§D.3.3 + §D.4 B7) ✅
- [x] Commit message đúng format `fix(#7): <subject>` ✅
- [x] Không chạm file ngoài scope (flow/* leftover để nguyên cho session triage) ✅
- [x] `git status` (trong scope B7) clean — uncommitted flow/* đã document trong §8 handoff ✅

---

_Sign-off: ✅ B7 đóng đúng chuẩn WORKPLAN._
