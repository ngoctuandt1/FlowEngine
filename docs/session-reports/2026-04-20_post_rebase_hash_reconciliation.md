# 2026-04-20 — Post-rebase hash reconciliation

Rebase was performed so PR #19 (`claude/codex-gap-fill`) and PR #20 (`claude/codex-ingredients-image`) could fast-forward onto `master` after the `AGENTS.md` shim commit `a721c60`.

## Commit mapping

- `d2e590f` -> `20776a6`  (`fix(web): align Veo model list`)
- `bd480f0` -> `130fe10`  (`feat(engine): frames-to-video`)
- `ece1822` -> `d6847f2`  (`feat(engine): text-to-image`)
- `d0e85cf` -> `3e2ffc8`  (`feat(engine): ingredients-to-video, image refs`)

Old hashes remain referenced inside the session reports on those two feature branches intentionally as point-in-time snapshots and were not rewritten.
