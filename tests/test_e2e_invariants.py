"""WORKPLAN §5.2 Tests 5/6/7 — infra invariants at the DB + claim-algorithm layer.

These are the three §5.2 E2E cases that can be proven WITHOUT a real Flow
browser submit: they assert scheduler / persistence behaviour, not Flow UI
interaction. Running them as integration tests (on the SQLite store used by
the running server) gives us fast regression coverage for the three
invariants that a full Tier-2 chain can only hit by coincidence.

Test 5 — INV-1 Profile pinning
  `claim_next_job` must refuse to hand an L2+ job to a worker whose profile
  list does not contain the parent's profile. Cross-account claim = 404 on
  `project_url` in the live Flow call, so the DB is the last line of defence.

Test 6 — INV-4 Serial per project_url
  `claim_next_job`'s NOT EXISTS subquery must prevent a second L2 job on the
  same `project_url` from being claimed while another job on that URL is
  still `claimed` or `running`. Two concurrent Chrome sessions on the same
  Flow project clobber each other's state — the scheduler has to serialise.

Test 7 — Stale-worker recovery
  `recover_stale_jobs` must reset any `claimed`/`running` row whose
  `updated_at` fell behind the cutoff. Worker crash / disconnect is the
  common trigger — without recovery a dead worker's claim pins the row
  forever, and the chain stalls silently.

All three tests assert DB-layer contracts only. No `flow/` code is touched;
the dispatcher and `FlowClient` are not invoked.
"""

from datetime import UTC, datetime, timedelta
from typing import Optional

from server.db.database import get_db
from server.db.job_store import (
    claim_next_job,
    create_job,
    get_job,
    recover_stale_jobs,
    update_job,
)
from server.db.profile_store import create_profile, get_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus


# ---------------------------------------------------------------------------
# Builders (mirror the style of tests/test_claim_algorithm.py)
# ---------------------------------------------------------------------------

def _make_profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _make_pending_l1(job_id: str, *, profile: Optional[str] = None) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        job_level=1,
        profile=profile,
        prompt="fresh t2v",
        created_at=now,
        updated_at=now,
    )


def _make_completed_parent(
    job_id: str,
    *,
    profile: str,
    project_url: str,
    media_id: str,
) -> Job:
    now = datetime.now(UTC)
    edit_url = f"{project_url.rstrip('/')}/edit/{media_id}"
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=1,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        prompt="parent",
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _make_pending_child(job_id: str, parent_id: str) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.CAMERA_MOVE,
        status=JobStatus.PENDING,
        job_level=2,
        parent_job_id=parent_id,
        direction="Dolly in",
        created_at=now,
        updated_at=now,
    )


# ---------------------------------------------------------------------------
# Test 5 — INV-1 Profile pinning (2 workers × 2 profiles)
# ---------------------------------------------------------------------------

async def test_5_profile_pinning_l2_claim_respects_profile_list(db):
    """INV-1: an L2 child whose parent ran on `p1` is invisible to a worker
    that only holds `p2`, even though the child row itself is pending.

    Failure mode this test guards against: pre-bug-#4, `claim_next_job`
    would hand any pending L2 to any available worker. That would have the
    worker open `p2`'s Chrome profile, navigate to `project_url` created
    under `p1`'s Google account, and hit 404 (projects are scoped to the
    account that created them). The 404 path returned a generic error which
    was easy to miss in the log.

    Contract now: the `parent.profile IN (...)` predicate on the L2 branch
    rejects mismatched workers. A second worker with the right profile
    claims successfully — proving the filter doesn't also break the happy
    path.
    """
    # Two Chrome profiles in the pool — each worker will mount exactly one.
    await create_profile(_make_profile("t5-p1"))
    await create_profile(_make_profile("t5-p2"))

    # Parent L1 completed on p1 (so its project_url is bound to p1's Google
    # account). Child L2 is pending and inherits nothing yet — `profile`
    # will come from parent on claim per B22.
    await create_job(
        _make_completed_parent(
            "t5-parent",
            profile="t5-p1",
            project_url="https://labs.google/fx/tools/flow/project/proj-5",
            media_id="media-5-0001",
        )
    )
    await create_job(_make_pending_child("t5-child", "t5-parent"))

    # Worker B holds p2 only → MUST NOT see the L2 child (parent.profile=p1
    # not in ['t5-p2']). Nothing else is claimable by B either → None.
    claimed_b = await claim_next_job("worker-B", ["t5-p2"])
    assert claimed_b is None, (
        "profile-mismatched worker must not claim an L2 child — would "
        "cross-account navigate and 404 on the live Flow project"
    )

    # Worker A holds p1 → claims the L2 child, inheriting parent fields (B22).
    claimed_a = await claim_next_job("worker-A", ["t5-p1"])
    assert claimed_a is not None, "profile-matched worker must claim the L2 child"
    assert claimed_a.id == "t5-child"
    assert claimed_a.profile == "t5-p1", (
        "L2 claim must inherit parent profile — anything else violates INV-1"
    )
    assert claimed_a.status == JobStatus.CLAIMED
    assert claimed_a.worker_id == "worker-A"


async def test_5_profile_pinning_l1_with_null_profile_claimable_by_any(db):
    """INV-1 edge: a fresh L1 with `profile IS NULL` is claimable by either
    worker — the invariant only binds L2+ to the parent's account.

    This is the counter-example that stops test 5's predicate from going
    too far. If `claim_next_job` ever tightens to reject `profile IS NULL`
    on L1, first-time text-to-video submissions would never claim — the
    scheduler would deadlock waiting for a profile pre-assignment the
    frontend doesn't make.
    """
    await create_profile(_make_profile("t5b-p1"))
    await create_profile(_make_profile("t5b-p2"))
    await create_job(_make_pending_l1("t5b-job"))  # profile defaults to None

    # Worker holding only p2 can still claim the unpinned L1 — it picks up
    # p2 as the assigned profile (first entry of the availability list).
    claimed = await claim_next_job("worker-B", ["t5b-p2"])
    assert claimed is not None, "L1 with NULL profile must be claimable"
    assert claimed.id == "t5b-job"
    assert claimed.profile == "t5b-p2", (
        "L1 fresh claim should adopt the requesting worker's first profile"
    )


# ---------------------------------------------------------------------------
# Test 6 — INV-4 Project lock (serial per project_url)
# ---------------------------------------------------------------------------

async def test_6_project_lock_serialises_two_l2_on_same_project_url(db):
    """INV-4: two L2 jobs on the same `project_url` must run serially.

    Scenario: a user submits two independent extend/camera operations
    against the same Flow project (same `project_url`, distinct parents is
    common — e.g. separate chains sharing a project). Both become eligible
    L2 claims. Without the NOT EXISTS guard, a second worker (or a second
    claim tick on the same worker after the first finishes its slot) could
    claim job 2 while job 1's browser is still open on that project — two
    Chrome sessions racing on the same editor would corrupt state.

    Contract: once job 1 is `claimed` (or `running`), job 2 stays
    `pending` even for the same profile. When job 1 completes, job 2
    becomes claimable on the very next tick.
    """
    await create_profile(_make_profile("t6-p1"))

    # Two independent parents, both completed, both bound to the SAME
    # project_url. (In practice each parent is its own L1 t2v that output
    # into the same shared project — unusual but allowed by the model.)
    shared_project = "https://labs.google/fx/tools/flow/project/proj-6"
    await create_job(
        _make_completed_parent(
            "t6-parent-1",
            profile="t6-p1",
            project_url=shared_project,
            media_id="media-6-aaaa",
        )
    )
    await create_job(
        _make_completed_parent(
            "t6-parent-2",
            profile="t6-p1",
            project_url=shared_project,
            media_id="media-6-bbbb",
        )
    )
    await create_job(_make_pending_child("t6-child-1", "t6-parent-1"))
    await create_job(_make_pending_child("t6-child-2", "t6-parent-2"))

    # First claim picks up child-1 (oldest pending L2 with the right
    # profile). The UPDATE flips its status to 'claimed' in-transaction,
    # which is what the NOT EXISTS subquery on the next claim will detect.
    first = await claim_next_job("worker-A", ["t6-p1"])
    assert first is not None
    assert first.id == "t6-child-1"
    assert first.status == JobStatus.CLAIMED
    assert first.project_url == shared_project, "B22 inheritance baseline"

    # Second claim on the same profile MUST be blocked — child-2 shares
    # project_url with the now-claimed child-1. Priority-1 branch returns
    # nothing, and there is no pending L1 → overall None.
    blocked = await claim_next_job("worker-A", ["t6-p1"])
    assert blocked is None, (
        "project-lock violated: second L2 claimed on a project_url already "
        "held by child-1 (status='claimed') — two Chrome sessions would "
        "race on the same Flow project"
    )

    # Finish child-1 — terminal status removes it from the NOT EXISTS set
    # (predicate is `active.status IN ('claimed','running')`). No other
    # state change is needed; `update_job` handles the profile release.
    await update_job("t6-child-1", JobUpdate(status=JobStatus.COMPLETED))

    # Now child-2 is claimable. The serial contract held end-to-end.
    unblocked = await claim_next_job("worker-A", ["t6-p1"])
    assert unblocked is not None, (
        "child-2 must become claimable once child-1 completes — otherwise "
        "project-lock would be a permanent deadlock, not a mutex"
    )
    assert unblocked.id == "t6-child-2"
    assert unblocked.project_url == shared_project


# ---------------------------------------------------------------------------
# Test 7 — Stale-worker recovery
# ---------------------------------------------------------------------------

async def test_7_stale_recovery_resets_claimed_and_reopens_for_claim(db):
    """`recover_stale_jobs` must unstick a claim older than the cutoff.

    Simulation: worker claims job, then dies without reporting back (kill
    -9, network partition, OS crash). The row sits in `claimed` forever;
    without recovery the profile is also pinned (current_job_id set) and
    no new job can run on it.

    Implementation detail: `recover_stale_jobs` filters by
    `updated_at < cutoff`, not `claimed_at`. A fresh claim sets
    `updated_at` to now, so to simulate a 40-minute-old stuck claim we
    backdate `updated_at` directly via SQL — this is test-only plumbing
    and doesn't imply the production path ever writes a past timestamp.

    After recovery: status='pending', worker_id=NULL, claimed_at=NULL,
    and the row is immediately re-claimable by a worker holding the
    (still-assigned) profile.
    """
    await create_profile(_make_profile("t7-p1"))
    await create_job(_make_pending_l1("t7-job"))

    # Legitimate claim — sets status='claimed', worker_id, claimed_at, and
    # updated_at all to ~now. This is the "healthy" state we then freeze.
    claimed = await claim_next_job("worker-dead", ["t7-p1"])
    assert claimed is not None and claimed.id == "t7-job"
    assert claimed.status == JobStatus.CLAIMED
    assert claimed.worker_id == "worker-dead"
    assert claimed.claimed_at is not None

    # Backdate updated_at to 40 minutes ago so it falls behind the 30-min
    # cutoff below. Direct SQL — this is the only way to simulate a
    # crashed worker; the production API never writes a past timestamp.
    stale_ts = (datetime.now(UTC) - timedelta(minutes=40)).isoformat()
    async with get_db() as conn:
        await conn.execute(
            "UPDATE jobs SET updated_at = ? WHERE id = ?",
            (stale_ts, "t7-job"),
        )
        await conn.commit()

    recovered = await recover_stale_jobs(stale_minutes=30)
    assert len(recovered) == 1, (
        "exactly one stuck job should have been recovered — the one we "
        "backdated"
    )
    assert recovered[0].id == "t7-job"

    after = await get_job("t7-job")
    assert after.status == JobStatus.PENDING, "recovered job must be re-queued"
    assert after.worker_id is None, (
        "recovered job must release the dead worker's id so a fresh claim "
        "can take over"
    )
    assert after.claimed_at is None, (
        "claimed_at must clear — otherwise the old timestamp could confuse "
        "downstream diagnostics"
    )
    assert after.error is not None and "stale" in after.error.lower(), (
        "recovery marks the row with an error breadcrumb so operators can "
        "trace why it was reset"
    )

    # The recovered row is now claimable by a worker holding the pinned
    # profile. `recover_stale_jobs` keeps `profile` intact on purpose: a
    # reclaim on a different account would violate INV-1 (same account
    # across a chain).
    reclaimed = await claim_next_job("worker-alive", ["t7-p1"])
    assert reclaimed is not None, "recovered job must be immediately re-claimable"
    assert reclaimed.id == "t7-job"
    assert reclaimed.worker_id == "worker-alive", (
        "new worker now owns the claim — no ghost-binding to the dead worker"
    )


async def test_7_stale_recovery_skips_fresh_claims(db):
    """Blast-radius guard: a claim younger than the cutoff must survive.

    Without this guard, `recover_stale_jobs` would clobber every in-flight
    claim on every tick — effectively disabling the scheduler. The cutoff
    is the whole point: only "forgotten" rows get reset.
    """
    await create_profile(_make_profile("t7b-p1"))
    await create_job(_make_pending_l1("t7b-job"))

    claimed = await claim_next_job("worker-live", ["t7b-p1"])
    assert claimed is not None and claimed.status == JobStatus.CLAIMED

    # Fresh claim → updated_at is ~now → safely inside any reasonable
    # cutoff. Recovery must find nothing.
    recovered = await recover_stale_jobs(stale_minutes=30)
    assert recovered == [], (
        "fresh claim must not be recovered — would race every healthy worker"
    )
    still_claimed = await get_job("t7b-job")
    assert still_claimed.status == JobStatus.CLAIMED
    assert still_claimed.worker_id == "worker-live"

    # Profile pointer also stays — otherwise the dashboard would show the
    # profile as free while the worker is still running the job.
    profile = await get_profile("t7b-p1")
    assert profile.current_job_id == "t7b-job"
