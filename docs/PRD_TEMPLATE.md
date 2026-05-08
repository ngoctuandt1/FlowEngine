# PRD_TEMPLATE.md — Implementation-plan template

> **Use this template** for any new epic. Copy to `docs/PRD_<EPIC>.md` (e.g. `docs/PRD_AUTH_GATE.md`) and fill in. Apply Self-Review checklist before handoff to Codex.
> **Note:** New epic planning belongs here (`docs/PRD_<EPIC>.md`), NOT in `docs/WORKPLAN.md` — that file is a closed historical ledger as of 2026-05-02.

---

# [Feature Name] PRD

**Goal:** [One sentence describing what this builds]

**Why:** [1-2 sentences on the user/business motivation. Compliance? Bug class? New capability?]

**Architecture:** [2-3 sentences on the approach]

**Tech stack touched:** [server / worker / flow / frontend / docs]

**Out of scope:** [Explicit list of what this PRD does NOT cover]

---

## File Structure

Map every file that will be created or modified, with one-line responsibility per file. This is where decomposition is locked in.

| File | Action | Responsibility |
|---|---|---|
| `path/to/new.py` | Create | … |
| `path/to/existing.py:120-180` | Modify | … |
| `tests/test_new.py` | Create | … |

Each file should have ONE clear responsibility. If a file's responsibility line has "and", consider splitting it.

---

## Tasks (bite-sized, 2-5 minutes each)

Each task = one acceptance-testable unit. Each step = one action a developer (or Codex) can perform in 2-5 minutes. Steps use checkbox `- [ ]` for tracking.

### Task 1: [Component name]

**Files:**
- Create: `exact/path/to/file.py`
- Modify: `exact/path/to/existing.py:123-145`
- Test: `tests/exact/path/to/test.py`

- [ ] **Step 1.1: Write the failing test** (fe-tdd RED)

  ```python
  def test_specific_behavior():
      result = function(input)
      assert result == expected
  ```

- [ ] **Step 1.2: Verify test fails for the right reason**

  Run: `pytest tests/path/test.py::test_name -v`
  Expected: FAIL with `<verbatim expected message>`

- [ ] **Step 1.3: Hand off to Codex (or implement)**

  If delegating: prompt template per global CLAUDE.md rule #4 (file paths, acceptance, branch, commit, PR).

- [ ] **Step 1.4: Verify test passes**

  Run: `pytest tests/path/test.py::test_name -v`
  Expected: PASS

- [ ] **Step 1.5: Run broader scope to catch regressions**

  Run: `pytest tests/<scope> -q --tb=short`
  Expected: PASS (no new failures)

- [ ] **Step 1.6: Commit**

  ```bash
  git add tests/path/test.py src/path/file.py
  git commit -m "feat(<scope>): <one-line description>"
  ```

### Task 2: [Next component]

<!-- replace with the same step structure as Task 1 -->

---

## No Placeholders — Red Flags Banned

The following patterns are PRD failures. Never write them in this document — they leak into Codex prompts and produce garbage:

<!-- The tokens below are intentionally obfuscated (T-B-D not TBD) so a `grep -i TBD` scan on a real PRD won't false-positive on this template section. -->
- `T-B-D`, `T-O-D-O`, "implement later", "fill in details"
- "Add appropriate error handling" / "add validation" / "handle-edge-cases"
- "Write tests for the above" (without actual test code)
- "Similar to Task N" (repeat the code — engineer may read out of order)
- Steps that describe WHAT without showing HOW (code blocks required for code steps)
- References to types, functions, or methods not defined in any task
- Vague verbs without subject/object: "improve", "clean up", "make better"

If you find any of these in your draft → fix inline before submitting.

---

## Self-Review Checklist

After writing the complete PRD, look at it with fresh eyes:

- [ ] **Spec coverage** — every requirement in the "Goal" / "Why" sections has at least one task implementing it. List any gaps.
- [ ] **Placeholder scan** — searched for the red-flag patterns above. None found.
- [ ] **Type / name consistency** — function `clearLayers()` in Task 3 isn't called `clearFullLayers()` in Task 7. Field names match across tasks.
- [ ] **Scope check** — focused enough for ONE implementation cycle? If it covers multiple independent subsystems, decompose into sub-PRDs.
- [ ] **Ambiguity check** — could any requirement be interpreted two ways? Pick one and make it explicit.
- [ ] **File-disjoint decomposition** — tasks group such that they can be parallelized across Codex agents (CLAUDE.md global rule #2).
- [ ] **Acceptance criteria** — every task's "expected" output is concrete (test name + pass/fail, command + exit code), not handwave.

Fix issues inline. Then ask user to review the PRD before dispatching Codex.

---

## Handoff to Implementation

After PRD is user-approved:

1. Decompose into N file-disjoint units (CLAUDE.md rule #2)
2. Per unit: dispatch Codex with self-contained prompt (rule #4) — file paths + acceptance + branch (`claude/bug-N-slug` off master) + commit msg (`Closes #N` in body) + PR template (`--base master` explicit) + DO NOT list
3. Each Codex dispatch arms Monitor + ScheduleWakeup re-arm (rule #3)
4. After each PR CI green: 2-reviewer reconcile (rule #5)
5. After all units green: live-verify gate if browser-automation (memory: `feedback_live_verify_gates_done.md`)
6. Closing-the-branch 4-option closer (CLAUDE.md global)

---

## Skill triggers in this workflow

- New idea with ambiguity → scope-lock pattern → fill this template
- Bug fix → fe-debug Phase 1 → fe-tdd RED → Codex GREEN
- Before "done" claim → completion gate (evidence-before-claims) + live-verify if browser

See `~/.claude/CLAUDE.md` for full trigger map.
