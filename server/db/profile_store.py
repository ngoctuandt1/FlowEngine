"""Profile CRUD operations (async, aiosqlite)."""

from datetime import datetime
from typing import Optional

from server.db.database import get_db
from server.models.profile import Profile, ProfileStatus, ProfileUpdate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_profile(row) -> Profile:
    """Convert an aiosqlite.Row to a Profile model."""
    return Profile(**dict(row))


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

async def create_profile(profile: Profile) -> Profile:
    """Insert a new profile row and return it."""
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO profiles (
                name, google_account, locale, tier,
                status, current_job_id, worker_id,
                last_used_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                profile.name,
                profile.google_account,
                profile.locale,
                profile.tier,
                profile.status.value,
                profile.current_job_id,
                profile.worker_id,
                profile.last_used_at.isoformat() if profile.last_used_at else None,
                profile.created_at.isoformat(),
            ),
        )
        await db.commit()
    return profile


async def get_profile(name: str) -> Optional[Profile]:
    """Fetch a single profile by name, or None."""
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM profiles WHERE name = ?", (name,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return _row_to_profile(row)


async def list_profiles(
    *, status: Optional[ProfileStatus] = None
) -> list[Profile]:
    """List profiles with optional status filter."""
    if status is not None:
        query = "SELECT * FROM profiles WHERE status = ? ORDER BY name"
        params: tuple = (status.value if isinstance(status, ProfileStatus) else status,)
    else:
        query = "SELECT * FROM profiles ORDER BY name"
        params = ()

    async with get_db() as db:
        cursor = await db.execute(query, params)
        rows = await cursor.fetchall()
        return [_row_to_profile(r) for r in rows]


async def update_profile(name: str, update: ProfileUpdate) -> Optional[Profile]:
    """Apply partial update to a profile. Returns updated Profile or None."""
    fields = update.model_dump(exclude_unset=True)
    if not fields:
        return await get_profile(name)

    sets: list[str] = []
    params: list = []

    for key, value in fields.items():
        if key == "status":
            sets.append("status = ?")
            params.append(value.value if isinstance(value, ProfileStatus) else value)
        else:
            sets.append(f"{key} = ?")
            params.append(value)

    params.append(name)
    sql = f"UPDATE profiles SET {', '.join(sets)} WHERE name = ?"

    async with get_db() as db:
        cursor = await db.execute(sql, params)
        await db.commit()
        if cursor.rowcount == 0:
            return None
    return await get_profile(name)


async def get_available_profiles(worker_id: str) -> list[str]:
    """Return profile names that a given worker can use right now.

    A profile is available when:
      - It is assigned to this worker (worker_id matches), OR
      - It has no worker assigned (worker_id IS NULL)
    AND it is not quarantined.
    """
    async with get_db() as db:
        cursor = await db.execute(
            """
            SELECT name FROM profiles
            WHERE status != 'quarantined'
              AND (worker_id = ? OR worker_id IS NULL)
            ORDER BY last_used_at ASC NULLS FIRST
            """,
            (worker_id,),
        )
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]
