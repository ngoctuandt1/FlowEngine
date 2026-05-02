"""B22 regression — L2+ `claim_next_job` inheritance behavior.

**B22 (2026-04-18)** — `claim_next_job` L2+ branch must inherit
`profile`, `project_url`, `media_id`, `edit_url` from the parent row.
Before B22 only `profile` was inherited; worker's `navigate_to_edit` had
no target so every L2 op failed before any Flow interaction.

Chain invariants gap (pre-B22):
- INV-2 (Navigate by edit_url) — caller had no `edit_url` to navigate to.
- INV-3 (Store everything) — child row persisted without inherited context.

Run 20 follow-up validation (2026-04-20) showed that splitting `edit_url`
from `media_id` is wrong for direct children of `extend-video`: Flow can
open the extend-output `/edit/{new_media}` correctly after landing
recovery, but a walked-up ancestor `media_id` then makes worker state
self-contradictory. Current contract: child jobs always inherit the direct
parent's final `media_id` + `edit_url` together.
"""

from datetime import UTC, datetime
from typing import Optional

from server.db.job_store import claim_next_job, create_job, get_job, update_job
from server.db.profile_store import create_profile
from server.db.project_store import create_project
from server.models.job import Job, JobStatus, JobType, JobUpdate
from server.models.profile import Profile, ProfileStatus
from server.models.project import Project


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
    project_id: Optional[str] = None,
    *,
    job_type: JobType = JobType.TEXT_TO_VIDEO,
    job_level: int = 1,
    parent_job_id: Optional[str] = None,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.COMPLETED,
        job_level=job_level,
        parent_job_id=parent_job_id,
        profile=profile,
        project_id=project_id,
        project_url=project_url,
        media_id=media_id,
        edit_url=edit_url,
        prompt="parent prompt" if job_type == JobType.TEXT_TO_VIDEO else None,
        direction="Dolly in" if job_type == JobType.CAMERA_MOVE else None,
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
    job_type: JobType = JobType.CAMERA_MOVE,
    job_level: int = 2,
) -> Job:
    now = datetime.now(UTC)
    return Job(
        id=job_id,
        type=job_type,
        status=JobStatus.PENDING,
        job_level=job_level,
        parent_job_id=parent_id,
        direction="Dolly in" if job_type == JobType.CAMERA_MOVE else None,
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
    project = Project(id="project-b22-a", name="B22 Project A")
    await create_project(project)
    await create_job(
        _make_completed_parent(
            "b22-parent-a",
            profile="b22-prof-a",
            project_id="project-b22-a",
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
    assert claimed.project_id == "project-b22-a", "F2: project_id must be inherited from parent"
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
    assert persisted.project_id == claimed.project_id
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


# ---------------------------------------------------------------------------
# Direct-parent inheritance after extend-video
# ---------------------------------------------------------------------------


async def test_extend_parent_inherits_direct_parent_media(db):
    """Child of extend-video must inherit the direct parent's final output.

    Chain: L1 t2v (media-A1) → L2 extend (media-E2, NEW per INV-5) → L3 insert.

    Run 20 follow-up jobs showed that inheriting L1's media while keeping
    L2's edit_url lands the worker on one clip and targets another. The
    child must instead edit the direct parent's final clip end-to-end.
    """
    await create_profile(_make_profile("b30-prof-a"))
    # L1 t2v — original media
    await create_job(
        _make_completed_parent(
            "b30-l1-a",
            profile="b30-prof-a",
            project_url="https://labs.google/fx/tools/flow/project/p-b30a",
            media_id="media-b30a-L1",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30a/edit/media-b30a-L1",
            job_type=JobType.TEXT_TO_VIDEO,
            job_level=1,
        )
    )
    # L2 extend — mints NEW media (INV-5) pointing at extend-output
    await create_job(
        _make_completed_parent(
            "b30-l2-a",
            profile="b30-prof-a",
            project_url="https://labs.google/fx/tools/flow/project/p-b30a",
            media_id="media-b30a-L2-extend",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30a/edit/media-b30a-L2-extend",
            job_type=JobType.EXTEND_VIDEO,
            job_level=2,
            parent_job_id="b30-l1-a",
        )
    )
    # L3 pending insert-object child of L2
    await create_job(
        _make_pending_child(
            "b30-l3-a",
            "b30-l2-a",
            job_type=JobType.INSERT_OBJECT,
            job_level=3,
        )
    )

    claimed = await claim_next_job("worker-1", ["b30-prof-a"])

    assert claimed is not None
    assert claimed.id == "b30-l3-a"
    assert claimed.profile == "b30-prof-a"
    assert claimed.media_id == "media-b30a-L2-extend", (
        "Child of extend-video must keep the direct parent's media_id so "
        "target media stays aligned with edit_url"
    )
    assert claimed.edit_url == (
        "https://labs.google/fx/tools/flow/project/p-b30a/edit/media-b30a-L2-extend"
    )
    assert claimed.project_url == (
        "https://labs.google/fx/tools/flow/project/p-b30a"
    )


async def test_extend_chain_inherits_immediate_parent_media(db):
    """Multi-extend chains should still inherit the immediate parent's clip.

    Chain: L1 t2v → L2 extend → L3 extend → L4 insert. Each extend mints a
    new media_id (INV-5). L4 should edit L3's output, not walk back to L1.
    """
    await create_profile(_make_profile("b30-prof-b"))
    await create_job(
        _make_completed_parent(
            "b30-l1-b",
            profile="b30-prof-b",
            project_url="https://labs.google/fx/tools/flow/project/p-b30b",
            media_id="media-b30b-L1",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30b/edit/media-b30b-L1",
            job_type=JobType.TEXT_TO_VIDEO,
            job_level=1,
        )
    )
    await create_job(
        _make_completed_parent(
            "b30-l2-b",
            profile="b30-prof-b",
            project_url="https://labs.google/fx/tools/flow/project/p-b30b",
            media_id="media-b30b-L2-extend",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30b/edit/media-b30b-L2-extend",
            job_type=JobType.EXTEND_VIDEO,
            job_level=2,
            parent_job_id="b30-l1-b",
        )
    )
    await create_job(
        _make_completed_parent(
            "b30-l3-b",
            profile="b30-prof-b",
            project_url="https://labs.google/fx/tools/flow/project/p-b30b",
            media_id="media-b30b-L3-extend",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30b/edit/media-b30b-L3-extend",
            job_type=JobType.EXTEND_VIDEO,
            job_level=3,
            parent_job_id="b30-l2-b",
        )
    )
    await create_job(
        _make_pending_child(
            "b30-l4-b",
            "b30-l3-b",
            job_type=JobType.INSERT_OBJECT,
            job_level=4,
        )
    )

    claimed = await claim_next_job("worker-1", ["b30-prof-b"])

    assert claimed is not None
    assert claimed.id == "b30-l4-b"
    assert claimed.media_id == "media-b30b-L3-extend", (
        "Child must inherit the immediate parent's output even when the "
        "parent itself is an extend child"
    )
    assert claimed.edit_url == (
        "https://labs.google/fx/tools/flow/project/p-b30b/edit/media-b30b-L3-extend"
    )


async def test_non_extend_parent_uses_parent_media(db):
    """Non-extend parent keeps the same direct-parent inheritance contract.

    Chain: L1 t2v → L2 camera-move (mints NEW media per INV-5, non-extend) →
    L3 insert. L3 claims L2's camera-output media_id, preserving the direct
    parent contract for camera-move/insert-object/remove-object parents.
    """
    await create_profile(_make_profile("b30-prof-c"))
    await create_job(
        _make_completed_parent(
            "b30-l1-c",
            profile="b30-prof-c",
            project_url="https://labs.google/fx/tools/flow/project/p-b30c",
            media_id="media-b30c-L1",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30c/edit/media-b30c-L1",
            job_type=JobType.TEXT_TO_VIDEO,
            job_level=1,
        )
    )
    # Camera-move parent — mints NEW media_id (INV-5) and should be inherited
    # directly like any other completed parent.
    await create_job(
        _make_completed_parent(
            "b30-l2-c",
            profile="b30-prof-c",
            project_url="https://labs.google/fx/tools/flow/project/p-b30c",
            media_id="media-b30c-L2-camera",
            edit_url="https://labs.google/fx/tools/flow/project/p-b30c/edit/media-b30c-L2-camera",
            job_type=JobType.CAMERA_MOVE,
            job_level=2,
            parent_job_id="b30-l1-c",
        )
    )
    await create_job(
        _make_pending_child(
            "b30-l3-c",
            "b30-l2-c",
            job_type=JobType.INSERT_OBJECT,
            job_level=3,
        )
    )

    claimed = await claim_next_job("worker-1", ["b30-prof-c"])

    assert claimed is not None
    assert claimed.id == "b30-l3-c"
    assert claimed.media_id == "media-b30c-L2-camera", (
        "Non-extend parent should inherit directly from parent"
    )
    assert claimed.edit_url == (
        "https://labs.google/fx/tools/flow/project/p-b30c/edit/media-b30c-L2-camera"
    )
