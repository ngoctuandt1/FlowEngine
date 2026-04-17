# Session Report — `<TASK_ID>` <SHORT_TITLE>

> **Filename convention:** `YYYY-MM-DD_<task-id>_<slug>.md`
> Example: `2026-04-17_B7_port-mismatch.md`, `2026-04-18_B9_test-foundation.md`,
> `2026-04-17_triage_flow-cleanup.md`

---

## 1. Metadata

| Field | Value |
|---|---|
| Task ID | `B7` / `B9` / `triage-flow` / ... |
| Task type | bug-fix / test-foundation / triage / refactor |
| Session started | YYYY-MM-DD HH:MM |
| Session ended | YYYY-MM-DD HH:MM |
| Duration actual | `30m` |
| Duration estimate | `5m` (from WORKPLAN.md §3.X) |
| Worker | Claude Sonnet 4.6 / Haiku / human |
| Branch | master / feat-xxx |

---

## 2. Commits landed

```
<hash>  <commit message subject>
<hash>  <commit message subject>
```

Nếu 0 commit (task là triage/investigation only) → ghi "none" và giải thích.

---

## 3. Files changed

```
<path>          +N / -M     (<short reason>)
<path>          +N / -M     (<short reason>)
```

Tổng: `X files, +Y / -Z lines`

---

## 4. Tests

| Test | Result | Notes |
|---|---|---|
| `tests/test_X.py::test_Y` | ✅ pass | default case |
| `tests/test_X.py::test_Z` | ✅ pass | env override case |

- Tổng: `N pass / M fail / K skipped`
- Test command dùng: `pytest tests/test_X.py -v` (hoặc manual verify command)
- Coverage delta: `+A%` (nếu có)

Nếu pytest infra chưa có (pre-B9) → ghi manual verify commands + expected output.

---

## 5. SPEC.md update

- [ ] Strike-through §D.3.X (gotcha section)
- [ ] Strike-through §D.4 B<n> (known bugs section)
- [ ] Commit hash reference added

Commit hash cho SPEC.md update: `<hash>` (có thể trùng với commit code fix)

---

## 6. Invariants & rules verified

Checklist anh xác nhận đã KHÔNG vi phạm:

- [ ] INV-1 Account Binding — không đổi profile trong chain
- [ ] INV-2 Navigate by `edit_url` — không scan DOM card
- [ ] INV-3 Store Everything — `media_id` + `project_url` saved
- [ ] INV-4 Serial per Project — không chạy song song cùng project
- [ ] INV-5 media_id stable — không tạo `media_id` mới ở L2+
- [ ] R-CODE-3 Locale-Independent — selector không hardcode VI/EN text
- [ ] R-CODE-10 No `datetime.utcnow()` — dùng `datetime.now(UTC)`
- [ ] R-CC-1 KHÔNG restructure kiến trúc

Nếu có vi phạm cố ý (hiếm) → ghi rõ lý do + design review reference.

---

## 7. Issues / Decisions

### Vấn đề phát sinh (nếu có)
- ...

### Quyết định đã đưa (judgment calls)
- ...

### Bug candidates phát hiện NHƯNG KHÔNG fix (out of scope)
- `<file>:<line>` — mô tả 1 câu — đề xuất tạo B<N+1> trong SPEC.md

---

## 8. Handoff notes

Thông tin session kế tiếp cần biết:
- Workdir state: clean / có stash `<id>` / có `??` files at `<path>`
- Env đã set: `<vars>`
- Nếu session sau là `B<next>` → cần đọc lại: `docs/WORKPLAN.md §3.<next>`

---

## 9. Done criteria checklist

Từ `docs/WORKPLAN.md §3.<task>`, xác nhận từng mục:

- [ ] Code change đúng file + line theo WORKPLAN
- [ ] Test red → green chứng minh được
- [ ] SPEC.md strike-through
- [ ] Commit message đúng format `<type>(#<n>): <subject>`
- [ ] Không chạm file ngoài scope
- [ ] `git status` clean (hoặc có stash rõ ràng)

---

_Sign-off: ✅ Ready for supervisor review._
