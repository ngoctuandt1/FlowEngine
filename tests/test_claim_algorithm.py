"""B22 regression — L2+ `claim_next_job` must inherit parent's target fields.

Before B22: `claim_next_job` SELECTed only `parent.profile` and UPDATEd only
`jobs.profile` on the L2+ branch. The child job's `project_url` / `media_id` /
`edit_url` stayed NULL even when the parent had those fields populated after
its own completion. The worker's `navigate_to_edit` then had no target → every
L2 extend / insert / remove / camera failed before any Flow interaction.

Chain invariants gap:
- INV-2 (Navigate by edit_url) — caller had no `edit_url` to navigate to.
- INV-3 (Store everything) — child row persisted without the inherited context
  that the chain needs to make progress.

After B22: the same IMMEDIATE transaction that inherits `profile` also inherits
`project_url`, `media_id`, `edit_url` from the parent row. Parent is the single
source of truth — if the child row had stale values from an earlier API request
(edge case, normally these are NULL at POST time), the parent values still win.
L1 claims (priority-2 branch) are untouched.
"""

from datetime import UTC, datetime
from typing import Optional

from server.db.job_store import claim_next_job, create_job, get_job, update_job
from server.db.profile_store import create_profile
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus


def _make_profile(name: str) -> Profile:
    return Profile(
        name=name,
        google_account=f"{name}@example.com",
        locale="en",
        tier="ultra",
        status=ProfileStatus.AVAILABLE,
        created_at=datetime.now(UTC),
    )


def _make_completed_parent(
    job_id: str,
    profile: str,
    project_url: str,
    media_id: str,
    edit_url: Optional[str] = None,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.COMPLETED,
        job_level=1,
        profile=profile,
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        prompt="parent prompt",
        created_at=now,
        updated_at=now,
        completed_at=now,
    )


def _make_pending_child(
    job_id: str,
    parent_id: str,
    *,
    project_url: Optional[str] = None,
    media_id: Optional[str] = None,
    edit_url: Optional[str] = None,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=JobType.CAMERA_MOVE,
        status=JobStatus.PENDING,
        job_level=2,
        parent_job_id=parent_id,
        direction="Dolly in",
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        created_at=now,
        updated_at=now,
    )


async def test_l2_claim_inherits_project_url_media_id_edit_url(db):
    """B22 core contract: L2 claim must populate all 3 target fields from parent.

    Parent completed with project_url / media_id / edit_url; child was POSTed
    with those fields NULL (the common case — frontend doesn't know them yet).
    After claim, the child row must carry the parent's values so
    `navigate_to_edit` has something to open.
    """
    await create_profile(_make_profile("b22-prof-a"))
    await create_job(
        _make_completed_parent(
            "b22-parent-a",
            profile="b22-prof-a",
            project_url="https://labs.google/fx/tools/flow/project/p-aaa",
            media_id="media-aaa-0001",
            edit_url="https://labs.google/fx/tools/flow/project/p-aaa/edit/media-aaa-0001",
        )
    )
    await create_job(_make_pending_child("b22-child-a", "b22-parent-a"))

    claimed = await claim_next_job("worker-1", ["b22-prof-a"])

    assert claimed is not None, "child should be claimable once parent is completed"
    assert claimed.id == "b22-child-a"
    assert claimed.profile == "b22-prof-a", "profile inherit is the pre-B22 baseline"
    assert claimed.project_url == (
        "https://labs.google/fx/tools/flow/project/p-aaa"
    ), "B22: project_url must be inherited from parent"
    assert claimed.media_id == "media-aaa-0001", (
        "B22: media_id must be inherited from parent"
    )
    assert claimed.edit_url == (
        "https://labs.google/fx/tools/flow/project/p-aaa/edit/media-aaa-0001"
    ), "B22: edit_url must be inherited from parent"

    # And persisted — a fresh SELECT must see the same values.
    persisted = await get_job("b22-child-a")
    assert persisted.project_url == claimed.project_url
    assert persisted.media_id == claimed.media_id
    assert persisted.edit_url == claimed.edit_url


async def test_l2_claim_overwrites_child_fields_from_parent(db):
    """B22: parent is single source of truth — overwrite stale child values.

    Edge case: frontend submits a chain where a child job already has
    `project_url` / `media_id` set (e.g. from a replay, or a client that
    pre-populates these). The parent row, completed after its own live run,
    carries the authoritative values. On claim, the inherit step must
    OVERWRITE the child's values so the worker navigates to the parent's
    actual output, not the client's guess.
    """
    await create_profile(_make_profile("b22-prof-b"))
    await create_job(
        _make_completed_parent(
            "b22-parent-b",
            profile="b22-prof-b",
            project_url="https://labs.google/fx/tools/flow/project/p-real",
            media_id="media-real-0002",
            edit_url="https://labs.google/fx/tools/flow/project/p-real/edit/media-real-0002",
        )
    )
    await create_job(
        _make_pending_child(
            "b22-child-b",
            "b22-parent-b",
            project_url="https://labs.google/fx/tools/flow/project/p-STALE",
            media_id="media-STALE-9999",
            edit_url="https://labs.google/fx/tools/flow/project/p-STALE/edit/media-STALE-9999",
        )
    )

    claimed = await claim_next_job("worker-1", ["b22-prof-b"])

    assert claimed is not None
    assert claimed.id == "b22-child-b"
    assert claimed.project_url == (
        "https://labs.google/fx/tools/flow/project/p-real"
    ), "parent must overwrite stale child project_url"
    assert claimed.media_id == "media-real-0002", (
        "parent must overwrite stale child media_id"
    )
    assert claimed.edit_url == (
        "https://labs.google/fx/tools/flow/project/p-real/edit/media-real-0002"
    ), "parent must overwrite stale child edit_url"


async def test_l1_claim_does_not_inherit_anything(db):
    """B22 blast-radius guard: L1 fresh-claim branch must NOT run inherit logic.

    L1 jobs have `parent_job_id=None` — there is no parent to inherit from.
    The L1 UPDATE path is unchanged by B22 and must continue to leave
    `project_url` / `media_id` / `edit_url` as their POSTed values (typically
    NULL for a text-to-video job, which writes them back itself via
    `finalize_operation` after the Flow run).
    """
    await create_profile(_make_profile("b22-prof-c"))
    now = datetime.now(UTC)
    await create_job(
        Job(
            id="b22-l1-c",
            type=JobType.TEXT_TO_VIDEO,
            status=JobStatus.PENDING,
            job_level=1,
            prompt="fresh t2v",
            created_at=now,
            updated_at=now,
        )
    )

    claimed = await claim_next_job("worker-1", ["b22-prof-c"])

    assert claimed is not None
    assert claimed.id == "b22-l1-c"
    assert claimed.job_level == 1
    assert claimed.parent_job_id is None
    assert claimed.profile == "b22-prof-c", "L1 claim assigns available profile"
    assert claimed.project_url is None, (
        "L1 fresh claim must not populate project_url from anywhere"
    )
    assert claimed.media_id is None, (
        "L1 fresh claim must not populate media_id from anywhere"
    )
    assert claimed.edit_url is None, (
        "L1 fresh claim must not populate edit_url from anywhere"
    )


async def test_l2_claim_inherits_when_parent_edit_url_null(db):
    """B22 realism: parent may complete with NULL edit_url — inherit whatever is there.

    Current worker path stores `project_url` + `media_id` but `edit_url` is
    derived (`Job.computed_edit_url`) in some call paths and may stay NULL in
    the DB column. The inherit step must copy exactly what the parent column
    holds — including NULL — rather than synthesise a value, so the B22 fix
    remains a pure propagation and does not introduce a new side-effect.
    """
    await create_profile(_make_profile("b22-prof-d"))
    await create_job(
        _make_completed_parent(
            "b22-parent-d",
            profile="b22-prof-d",
            project_url="https://labs.google/fx/tools/flow/project/p-ddd",
            media_id="media-ddd-0004",
            edit_url=None,  # parent never wrote the column
        )
    )
    await create_job(_make_pending_child("b22-child-d", "b22-parent-d"))

    claimed = await claim_next_job("worker-1", ["b22-prof-d"])

    assert claimed is not None
    assert claimed.project_url == "https://labs.google/fx/tools/flow/project/p-ddd"
    assert claimed.media_id == "media-ddd-0004"
    assert claimed.edit_url is None, (
        "inherit must copy NULL when parent has NULL — no synthesis"
    )
