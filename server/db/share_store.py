"""Persistence helpers for job share links."""

from __future__ import annotations

from datetime import UTC, datetime

import secrets

from server.db.database import get_db
from server.db.job_store import get_job
from server.models.job import Job
from server.models.share import JobShare


SHARE_TOKEN_BYTES = 24


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_share_token() -> str:
    return secrets.token_urlsafe(SHARE_TOKEN_BYTES)


async def _ensure_share_table(db) -> None:
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS job_shares (
            job_id      TEXT PRIMARY KEY,
            share_token TEXT,
            share_url   TEXT,
            shared_at   TEXT,
            revoked_at  TEXT,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        )
        """
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_job_shares_active_token
        ON job_shares(share_token)
        WHERE share_token IS NOT NULL AND revoked_at IS NULL
        """
    )


def _row_to_share(row) -> JobShare:
    return JobShare(**dict(row))


async def get_job_share(job_id: str) -> JobShare | None:
    async with get_db() as db:
        await _ensure_share_table(db)
        cursor = await db.execute(
            "SELECT job_id, share_token, share_url, shared_at, revoked_at FROM job_shares WHERE job_id = ?",
            (job_id,),
        )
        row = await cursor.fetchone()
        return _row_to_share(row) if row else None


async def mint_job_share(job_id: str, share_url_factory) -> JobShare | None:
    if await get_job(job_id) is None:
        return None

    async with get_db() as db:
        await _ensure_share_table(db)
        cursor = await db.execute(
            """
            SELECT job_id, share_token, share_url, shared_at, revoked_at
            FROM job_shares
            WHERE job_id = ?
              AND share_token IS NOT NULL
              AND share_url IS NOT NULL
              AND revoked_at IS NULL
            """,
            (job_id,),
        )
        active = await cursor.fetchone()
        if active:
            return _row_to_share(active)

        now = _now_iso()
        token = new_share_token()
        share_url = str(share_url_factory(token))
        await db.execute(
            """
            INSERT INTO job_shares (
                job_id, share_token, share_url, shared_at, revoked_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                share_token = excluded.share_token,
                share_url = excluded.share_url,
                shared_at = excluded.shared_at,
                revoked_at = NULL,
                updated_at = excluded.updated_at
            """,
            (job_id, token, share_url, now, now, now),
        )
        await db.commit()
        return JobShare(
            job_id=job_id,
            share_token=token,
            share_url=share_url,
            shared_at=datetime.fromisoformat(now),
            revoked_at=None,
        )


async def revoke_job_share(job_id: str) -> JobShare | None:
    if await get_job(job_id) is None:
        return None

    async with get_db() as db:
        await _ensure_share_table(db)
        now = _now_iso()
        await db.execute(
            """
            INSERT INTO job_shares (
                job_id, share_token, share_url, shared_at, revoked_at, created_at, updated_at
            ) VALUES (?, NULL, NULL, NULL, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                share_token = NULL,
                share_url = NULL,
                revoked_at = COALESCE(job_shares.revoked_at, excluded.revoked_at),
                updated_at = excluded.updated_at
            """,
            (job_id, now, now, now),
        )
        await db.commit()
        return await get_job_share(job_id)


async def get_job_by_share_token(share_token: str) -> tuple[Job, JobShare] | None:
    async with get_db() as db:
        await _ensure_share_table(db)
        cursor = await db.execute(
            """
            SELECT job_id, share_token, share_url, shared_at, revoked_at
            FROM job_shares
            WHERE share_token = ?
              AND share_url IS NOT NULL
              AND revoked_at IS NULL
            """,
            (share_token,),
        )
        row = await cursor.fetchone()
    if row is None:
        return None

    share = _row_to_share(row)
    job = await get_job(share.job_id)
    if job is None:
        return None
    return job, share

