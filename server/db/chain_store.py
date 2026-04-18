"""Chain CRUD + aggregated status (B4 — Choice C hybrid).

The `chains` table is INSERT-only for immutable metadata (id, profile,
created_at, updated_at). No UPDATE path from `update_job` touches it, so
the chain row and the jobs rows cannot drift apart.

Aggregated status (pending / running / completed / failed / cancelled) and
progress are computed on-demand from a `SELECT chain_id, status FROM jobs
WHERE chain_id = ?` scan — jobs remain the single source of truth.
"""

from datetime import UTC, datetime
from typing import Optional

from server.db.database import get_db
from server.models.chain import Chain, ChainAggregate, ChainProgress


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ---------------------------------------------------------------------------
# Aggregation helper
# ---------------------------------------------------------------------------

def compute_aggregated_status(statuses: list[str]) -> str:
    """Derive chain status from the list of its jobs' statuses.

    Rules (priority order):
      1. any `failed`                           → failed
      2. any `running` / `claimed`              → running
      3. any `pending`:
           - alongside completed/cancelled      → running (in progress)
           - alone                              → pending
      4. all `cancelled`                        → cancelled
      5. otherwise (≥1 completed, no failures)  → completed

    Empty list → `pending` (defensive — chain row with zero jobs).
    """
    if not statuses:
        return "pending"
    if "failed" in statuses:
        return "failed"
    if any(s in ("running", "claimed") for s in statuses):
        return "running"
    if "pending" in statuses:
        if any(s in ("completed", "cancelled") for s in statuses):
            return "running"
        return "pending"
    if all(s == "cancelled" for s in statuses):
        return "cancelled"
    return "completed"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class _ChainRow(Chain):
    """Internal wrapper that also carries the vestigial `status` column from
    the DB row. Needed by the trip-wire test that asserts `status` stays at
    its INSERT default — i.e. update_job never writes to it."""
    status: str = "active"


async def create_chain(chain: Chain) -> Chain:
    """INSERT a chain metadata row.

    Called once per `POST /api/chains`. No UPDATE path exists by design —
    see module docstring.
    """
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO chains (id, profile, project_url, media_id, status,
                                created_at, updated_at)
            VALUES (?, ?, NULL, NULL, 'active', ?, ?)
            """,
            (
                chain.id,
                chain.profile,
                chain.created_at.isoformat(),
                chain.updated_at.isoformat(),
            ),
        )
        await db.commit()
    return chain


async def get_chain_row(chain_id: str) -> Optional[_ChainRow]:
    """Fetch the raw chain metadata row (or None).

    The returned object carries the vestigial `status` column verbatim so
    tests can assert the no-sync invariant. Public API should go through
    `get_chain_aggregate` instead.
    """
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT id, profile, status, created_at, updated_at FROM chains WHERE id = ?",
            (chain_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None
    return _ChainRow(
        id=row["id"],
        profile=row["profile"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


async def get_chain_aggregate(chain_id: str) -> Optional[ChainAggregate]:
    """Metadata + derived status/progress + ordered job ids. None if unknown.

    Runs two queries against a single connection:
      1. SELECT chain metadata (id, profile, created_at).
      2. SELECT id, status FROM jobs WHERE chain_id = ? ORDER BY created_at ASC.

    The second feeds both `compute_aggregated_status` and the ordered `jobs`
    list in the response.
    """
    async with get_db() as db:
        meta_cur = await db.execute(
            "SELECT id, profile, created_at FROM chains WHERE id = ?",
            (chain_id,),
        )
        meta = await meta_cur.fetchone()
        if meta is None:
            return None

        jobs_cur = await db.execute(
            "SELECT id, status FROM jobs WHERE chain_id = ? ORDER BY created_at ASC",
            (chain_id,),
        )
        rows = await jobs_cur.fetchall()

    statuses = [r["status"] for r in rows]
    job_ids = [r["id"] for r in rows]

    return ChainAggregate(
        id=meta["id"],
        profile=meta["profile"],
        created_at=datetime.fromisoformat(meta["created_at"]),
        status=compute_aggregated_status(statuses),
        progress=ChainProgress(
            completed=sum(1 for s in statuses if s == "completed"),
            total=len(statuses),
        ),
        jobs=job_ids,
    )
