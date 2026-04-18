# Phase A Coverage Report

> Measured: 2026-04-18 (master `d1065c9`, post-B14/B15/B16/B17/B10 cherry-picks)
> Tool: `pytest-cov>=5.0.0` (added to `requirements-dev.txt`)
> Command: `pytest tests/ --cov=server --cov=worker --cov=flow --cov-report=term`
> Total: `63 passed in 7.30s`

## Overall

```
TOTAL                          2978 stmts   2073 miss   30% cover
```

## Per-module breakdown

### ≥70% — target met or exceeded

| Module | Coverage | Target (WORKPLAN §4.1) |
|---|---|---|
| `server/models/profile.py` | 100% | — |
| `server/db/database.py` | 100% | — |
| `server/models/job.py` | 95% | — |
| `server/app.py` | 75% | — |
| `flow/navigation.py` | 70% | ≥80% (below) |

### 40-69% — partial

| Module | Coverage | Target |
|---|---|---|
| `flow/operations/extend.py` | 66% | — |
| `flow/operations/_base.py` | 60% | — |
| `server/db/job_store.py` | 58% | ≥80% (below) |
| `server/routes/worker.py` | 58% | ≥70% (below) |
| `flow/operations/camera.py` | 46% | — |
| `server/routes/profiles.py` | 44% | — |
| `flow/model_selector.py` | 43% | — |
| `flow/submit.py` | 43% | — |
| `server/config.py` | 43% | — |
| `server/db/profile_store.py` | 38% | ≥70% (below) |

### <30% — low

| Module | Coverage | Target |
|---|---|---|
| `server/routes/jobs.py` | 28% | ≥70% (below) |
| `flow/operations/remove.py` | 22% | — |
| `flow/media_id.py` | 20% | ≥80% (below) |
| `flow/operations/insert.py` | 17% | — |
| `flow/operations/generate.py` | 16% | — |
| `flow/client.py` | 16% | — |
| `flow/download.py` | 14% | — |
| `flow/wait.py` | 13% | — |
| `flow/login.py` | 10% | — |

### 0% — no coverage

| Module | Coverage | Target |
|---|---|---|
| `worker/main.py` | 0% | — |
| `worker/dispatcher.py` | 0% | ≥50% (below) |
| `worker/profile_manager.py` | 0% | ≥70% (below) |
| `worker/project_lock.py` | 0% | ≥90% (below) |
| `worker/remote_api.py` | 0% | — |
| `flow/account.py` | 0% | — |
| `flow/retry.py` | 0% | — |

## Honest assessment

**Overall 30% ≪ WORKPLAN §4.1 target of 70% server+worker.**

Reason:
- Phase A tests focused on specific bug repro + fix (B1/B2/B3/B5/B6/B7/B8/B11/B12 + B14-B17) + B9 smoke tests. Not an integration test push.
- Worker package 0% because all worker tests were deferred (WORKPLAN §4.3: "CI target future — không implement trong Phase A").
- Flow operations tests are unit-level with mocked `page` — code paths touching real Playwright (launch, download, wait, recaptcha) are untested.

## What's covered

- All Phase A bug fixes have TDD guards (B1 aspect_ratio, B2→B11 bbox, B3→B12 camera verify, B5 completed_at, B6 profile tracking, B7 port, B8 datetime, B14 nav, B15 extend, B16 submit iterate, B17 LP pre-check).
- Pydantic models (Job, Profile, JobUpdate) fully covered via fixture usage.
- DB layer (`init_db`, CRUD happy paths) covered via conftest `db` fixture.

## What's not covered (known gap)

- Worker orchestration (claim loop, dispatch, profile manager, project lock).
- Flow Playwright lifecycle (client launch, login, download, recaptcha, retry).
- API routes beyond smoke (`/api/jobs` CRUD, `/api/worker/*`, WebSocket events).
- L1 text-to-video E2E (generate.py full flow).

## Recommendation

- **Phase A: accept 30% as baseline.** Per B9 done-criteria: "Coverage target ≥ 70% deferred to post-phase-A".
- **Phase B: prioritize worker package tests** (project_lock is 90% achievable with pure async mocks; profile_manager similar). These are P0 gaps for any CI gate.
- **Flow operations deeper coverage** needs either (a) heavier mocking of Playwright page API, or (b) integration tests against a live Flow — high cost.

## Command to reproduce

```bash
pip install -r requirements-dev.txt
pytest tests/ --cov=server --cov=worker --cov=flow --cov-report=term
```

For HTML report:
```bash
pytest tests/ --cov=server --cov=worker --cov=flow --cov-report=html
# Output: htmlcov/index.html
```
