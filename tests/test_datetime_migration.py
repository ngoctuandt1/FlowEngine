"""B8 regression — forbid `datetime.utcnow()` and verify tz-aware timestamps.

Python 3.12+ deprecates `datetime.utcnow()` (returns naive UTC). Python 3.13
already prints DeprecationWarning; later versions may remove it. SPEC §R-CODE-10
requires `datetime.now(UTC)` (tz-aware) everywhere.

Two guards here:
1. Source scan — no `datetime.utcnow()` call syntax left in server/, worker/, flow/.
2. Round-trip — a Job written with tz-aware timestamps comes back tz-aware.
"""
from datetime import UTC, datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_no_utcnow_in_code():
    """B8: no `datetime.utcnow()` call expressions remain in production dirs."""
    scan_dirs = ["server", "worker", "flow"]
    offenses: list[str] = []
    for d in scan_dirs:
        for py in (REPO_ROOT / d).rglob("*.py"):
            if "__pycache__" in py.parts:
                continue
            text = py.read_text(encoding="utf-8")
            if "datetime.utcnow()" in text:
                offenses.append(str(py.relative_to(REPO_ROOT)))

    assert not offenses, (
        f"datetime.utcnow() is deprecated. Found in: {offenses}. "
        "Use datetime.now(UTC) instead. See SPEC §R-CODE-10."
    )


async def test_utc_timestamps_have_timezone(db):
    """B8: timestamps round-tripped through SQLite must stay tz-aware."""
    from server.db.job_store import create_job, get_job
    from server.models.job import Job, JobStatus, JobType

    job = Job(
        id="test-1",
        type=JobType.TEXT_TO_VIDEO,
        status=JobStatus.PENDING,
        prompt="x",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    await create_job(job)

    got = await get_job("test-1")
    assert got is not None
    assert got.created_at.tzinfo is not None, (
        "created_at lost its timezone after DB round-trip — "
        "check isoformat() includes +00:00 and Pydantic parses it back aware."
    )
    assert got.updated_at.tzinfo is not None
