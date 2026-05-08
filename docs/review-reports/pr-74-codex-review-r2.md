# PR #74 Round-2 Codex Review

## Test results

Sandbox read-only — pytest rejected before execution. `git diff --check` clean.

## Spec compliance

- POSIX `FlowClient` lifecycle launches Chrome in new session, terminates full process group with `os.killpg`.
- `proc.wait(timeout=3)` reached after SIGKILL on both `killpg` success and `proc.kill()` fallback.
- Windows path unchanged (`wmic` branch, `CREATE_NEW_PROCESS_GROUP`).
- `warm_profile.py` adds Linux Chrome/Chromium candidates and updates error message to be cross-platform.

## Findings

### Important

**Path containment check accepts sibling directories** — `worker/dispatcher.py` used `resolved.startswith(allowed_root)` which is not path-boundary aware: `/opt/chrome-profiles2/foo` passes when `allowed_root=/opt/chrome-profiles`. This gates the `pkill -f` call. Fix: `Path(resolved).is_relative_to(Path(allowed_root))`.

### Minor

**`chrome_name` loop is dead** — `for chrome_name in ("chrome", "chromium", "chromium-browser"):` always breaks on first iteration; `chrome_name` is never used in the `pkill` command. Remove the loop.

## Verdict

Both findings fixed before merge: `is_relative_to` replaces `startswith`; dead loop removed.
