"""SQLite database connection manager using aiosqlite."""

import aiosqlite
from contextlib import asynccontextmanager
from server.config import DATABASE_PATH

# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS chains (
    id          TEXT PRIMARY KEY,
    profile     TEXT,
    project_url TEXT,
    media_id    TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id              TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending',

    -- Chain / hierarchy
    job_level       INTEGER NOT NULL DEFAULT 1,
    parent_job_id   TEXT,
    chain_id        TEXT,

    -- Account binding
    profile         TEXT,
    project_url     TEXT,
    media_id        TEXT,
    edit_url        TEXT,

    -- Operation params
    prompt          TEXT,
    model           TEXT NOT NULL DEFAULT 'veo-3.1-fast-lp',
    aspect_ratio    TEXT NOT NULL DEFAULT '16:9',
    bbox_json       TEXT,          -- JSON serialised BBox
    direction       TEXT,
    start_image_path TEXT,
    end_image_path  TEXT,
    ref_image_path  TEXT,

    -- Output
    output_files_json TEXT,        -- JSON serialised list[str]
    generation_id   TEXT,

    -- Worker tracking
    worker_id       TEXT,
    claimed_at      TEXT,
    completed_at    TEXT,
    error           TEXT,

    -- Timestamps
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    FOREIGN KEY (parent_job_id) REFERENCES jobs(id),
    FOREIGN KEY (chain_id) REFERENCES chains(id),
    FOREIGN KEY (profile) REFERENCES profiles(name)
);

CREATE TABLE IF NOT EXISTS profiles (
    name            TEXT PRIMARY KEY,
    google_account  TEXT,
    locale          TEXT NOT NULL DEFAULT 'en',
    tier            TEXT NOT NULL DEFAULT 'ultra',
    status          TEXT NOT NULL DEFAULT 'available',
    current_job_id  TEXT,
    worker_id       TEXT,
    last_used_at    TEXT,
    created_at      TEXT NOT NULL
);

-- Indices for hot query paths
CREATE INDEX IF NOT EXISTS idx_jobs_status      ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_chain_id    ON jobs(chain_id);
CREATE INDEX IF NOT EXISTS idx_jobs_parent      ON jobs(parent_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_profile     ON jobs(profile);
CREATE INDEX IF NOT EXISTS idx_jobs_project_url ON jobs(project_url);
CREATE INDEX IF NOT EXISTS idx_profiles_status  ON profiles(status);
"""


async def _ensure_job_column(db: aiosqlite.Connection, name: str, ddl: str) -> None:
    """Add a jobs column if it is missing from an existing database."""
    cursor = await db.execute("PRAGMA table_info(jobs)")
    rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    if name not in columns:
        await db.execute(f"ALTER TABLE jobs ADD COLUMN {ddl}")


async def init_db() -> None:
    """Create tables and indices if they don't exist yet."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(_SCHEMA_SQL)
        await _ensure_job_column(db, "start_image_path", "start_image_path TEXT")
        await _ensure_job_column(db, "end_image_path", "end_image_path TEXT")
        await _ensure_job_column(db, "ref_image_path", "ref_image_path TEXT")
        await db.commit()


@asynccontextmanager
async def get_db():
    """Async context manager that yields an aiosqlite Connection.

    Usage::

        async with get_db() as db:
            await db.execute(...)
    """
    db = await aiosqlite.connect(DATABASE_PATH)
    db.row_factory = aiosqlite.Row          # dict-like access
    try:
        yield db
    finally:
        await db.close()
