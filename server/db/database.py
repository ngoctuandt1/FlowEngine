"""SQLite database connection manager using aiosqlite."""

import logging
from contextlib import asynccontextmanager

import aiosqlite

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

CREATE TABLE IF NOT EXISTS projects (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    description    TEXT,
    cover_chain_id TEXT,
    cover_job_id   TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
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
    project_id      TEXT,

    -- Operation params
    prompt          TEXT,
    model           TEXT NOT NULL DEFAULT 'veo-3.1-lite',
    aspect_ratio    TEXT NOT NULL DEFAULT '16:9',
    bbox_json       TEXT,          -- JSON serialised BBox
    direction       TEXT,
    start_image_path TEXT,
    end_image_path  TEXT,
    ingredient_image_paths_json TEXT,
    ref_image_path  TEXT,
    safety_filter   TEXT,

    -- Output
    output_files_json TEXT,        -- JSON serialised list[str]
    generation_id   TEXT,

    -- Worker tracking
    worker_id       TEXT,
    claimed_at      TEXT,
    completed_at    TEXT,
    error           TEXT,
    error_kind      TEXT,
    error_message   TEXT,

    -- Timestamps
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,

    FOREIGN KEY (parent_job_id) REFERENCES jobs(id),
    FOREIGN KEY (chain_id) REFERENCES chains(id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS jobs_fts USING fts5(
    id,
    project_url,
    type,
    profile,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS jobs_fts_after_insert
AFTER INSERT ON jobs
BEGIN
    INSERT INTO jobs_fts(rowid, id, project_url, type, profile)
    VALUES (new.rowid, new.id, new.project_url, new.type, new.profile);
END;

CREATE TRIGGER IF NOT EXISTS jobs_fts_after_update
AFTER UPDATE ON jobs
BEGIN
    DELETE FROM jobs_fts WHERE rowid = old.rowid;
    INSERT INTO jobs_fts(rowid, id, project_url, type, profile)
    VALUES (new.rowid, new.id, new.project_url, new.type, new.profile);
END;

CREATE TRIGGER IF NOT EXISTS jobs_fts_after_delete
AFTER DELETE ON jobs
BEGIN
    DELETE FROM jobs_fts WHERE rowid = old.rowid;
END;

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

CREATE TABLE IF NOT EXISTS characters (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT,
    image_paths TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS render_jobs (
    id           TEXT PRIMARY KEY,
    status       TEXT NOT NULL DEFAULT 'queued',
    progress     INTEGER NOT NULL DEFAULT 0,
    ratio        TEXT NOT NULL,
    payload      TEXT NOT NULL,
    output_path  TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS veo_accounts (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    token       TEXT NOT NULL,
    cookie      TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- Indices for hot query paths
CREATE INDEX IF NOT EXISTS idx_jobs_status           ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_status_created   ON jobs(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_chain_id         ON jobs(chain_id);
CREATE INDEX IF NOT EXISTS idx_jobs_parent           ON jobs(parent_job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_profile          ON jobs(profile);
CREATE INDEX IF NOT EXISTS idx_jobs_project_url      ON jobs(project_url);
CREATE INDEX IF NOT EXISTS idx_projects_updated      ON projects(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_profiles_status       ON profiles(status);
CREATE INDEX IF NOT EXISTS idx_characters_name       ON characters(name);
CREATE INDEX IF NOT EXISTS idx_render_jobs_status    ON render_jobs(status);
"""

logger = logging.getLogger(__name__)


async def _ensure_table(db: aiosqlite.Connection, name: str, ddl: str) -> None:
    """Create a table if it does not already exist."""
    await db.execute(f"CREATE TABLE IF NOT EXISTS {name} ({ddl})")


async def _ensure_job_column(db: aiosqlite.Connection, name: str, ddl: str) -> None:
    """Add a jobs column if it is missing from an existing database."""
    cursor = await db.execute("PRAGMA table_info(jobs)")
    rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    if name not in columns:
        await db.execute(f"ALTER TABLE jobs ADD COLUMN {ddl}")


async def _ensure_character_column(
    db: aiosqlite.Connection, name: str, ddl: str
) -> None:
    """Add a characters column if it is missing from an existing database."""
    cursor = await db.execute("PRAGMA table_info(characters)")
    rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    if name not in columns:
        await db.execute(f"ALTER TABLE characters ADD COLUMN {ddl}")


async def _ensure_template_column(db: aiosqlite.Connection, name: str, ddl: str) -> None:
    """Add a templates column if it is missing from an existing database."""
    cursor = await db.execute("PRAGMA table_info(templates)")
    rows = await cursor.fetchall()
    columns = {row[1] for row in rows}
    if name not in columns:
        await db.execute(f"ALTER TABLE templates ADD COLUMN {ddl}")


async def _backfill_jobs_fts(db: aiosqlite.Connection) -> int:
    """Idempotently rebuild jobs_fts rows from jobs."""
    cursor = await db.execute("SELECT COUNT(*) FROM jobs")
    count_row = await cursor.fetchone()
    await db.execute(
        """
        INSERT OR REPLACE INTO jobs_fts(rowid, id, project_url, type, profile)
        SELECT rowid, id, project_url, type, profile
        FROM jobs
        """
    )
    return count_row[0]


async def _ensure_jobs_fts_trigram(db: aiosqlite.Connection) -> bool:
    """Recreate index-only jobs_fts if it uses a legacy tokenizer."""
    cursor = await db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'jobs_fts'"
    )
    row = await cursor.fetchone()
    sql = (row[0] or "").lower() if row else ""
    if "trigram" in sql:
        return False

    await db.executescript(
        """
        DROP TRIGGER IF EXISTS jobs_fts_after_insert;
        DROP TRIGGER IF EXISTS jobs_fts_after_update;
        DROP TRIGGER IF EXISTS jobs_fts_after_delete;
        DROP TABLE IF EXISTS jobs_fts;

        CREATE VIRTUAL TABLE jobs_fts USING fts5(
            id,
            project_url,
            type,
            profile,
            tokenize='trigram'
        );

        CREATE TRIGGER jobs_fts_after_insert
        AFTER INSERT ON jobs
        BEGIN
            INSERT INTO jobs_fts(rowid, id, project_url, type, profile)
            VALUES (new.rowid, new.id, new.project_url, new.type, new.profile);
        END;

        CREATE TRIGGER jobs_fts_after_update
        AFTER UPDATE ON jobs
        BEGIN
            DELETE FROM jobs_fts WHERE rowid = old.rowid;
            INSERT INTO jobs_fts(rowid, id, project_url, type, profile)
            VALUES (new.rowid, new.id, new.project_url, new.type, new.profile);
        END;

        CREATE TRIGGER jobs_fts_after_delete
        AFTER DELETE ON jobs
        BEGIN
            DELETE FROM jobs_fts WHERE rowid = old.rowid;
        END;
        """
    )
    return True


async def _backfill_job_chain_ids(db: aiosqlite.Connection) -> int:
    """Populate legacy NULL chain_id values on persisted jobs."""
    cursor = await db.execute("SELECT id, parent_job_id, chain_id FROM jobs")
    rows = await cursor.fetchall()
    jobs = {
        row[0]: {
            "parent_job_id": row[1],
            "chain_id": row[2],
        }
        for row in rows
    }
    cache: dict[str, str] = {}

    def resolve_chain_id(job_id: str, trail: set[str] | None = None) -> str:
        if job_id in cache:
            return cache[job_id]

        trail = set() if trail is None else set(trail)
        if job_id in trail:
            cache[job_id] = job_id
            return job_id
        trail.add(job_id)

        job = jobs[job_id]
        existing_chain_id = job["chain_id"]
        if existing_chain_id:
            cache[job_id] = existing_chain_id
            return existing_chain_id

        parent_job_id = job["parent_job_id"]
        if not parent_job_id:
            cache[job_id] = job_id
            return job_id

        if parent_job_id not in jobs:
            cache[job_id] = parent_job_id
            return parent_job_id

        resolved = resolve_chain_id(parent_job_id, trail)
        cache[job_id] = resolved
        return resolved

    updates: list[tuple[str, str]] = []
    for job_id, job in jobs.items():
        if job["chain_id"] is not None:
            continue
        updates.append((resolve_chain_id(job_id), job_id))

    if not updates:
        return 0

    await db.executemany(
        "UPDATE jobs SET chain_id = ? WHERE id = ? AND chain_id IS NULL",
        updates,
    )
    return len(updates)


async def init_db() -> None:
    """Create tables and indices if they don't exist yet."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.executescript(_SCHEMA_SQL)
        await _ensure_table(
            db,
            "templates",
            """
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            description TEXT,
            steps_json  TEXT NOT NULL,
            created_at  TEXT,
            updated_at  TEXT
            """,
        )
        await _ensure_job_column(db, "start_image_path", "start_image_path TEXT")
        await _ensure_job_column(db, "end_image_path", "end_image_path TEXT")
        # `project_id` stays as an application-level association. A DB-level
        # FK on `jobs.project_id` conflicts with existing chain/job flows
        # (L1 jobs self-seed chain_id before any chain row exists) and also
        # breaks legacy-schema tests that intentionally drop the column.
        await _ensure_job_column(db, "project_id", "project_id TEXT")
        # Index must be created AFTER the column-ensure step because executescript
        # cannot reference a column that the additive migration has not yet added.
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_project_id ON jobs(project_id)"
        )
        await _ensure_job_column(
            db,
            "ingredient_image_paths_json",
            "ingredient_image_paths_json TEXT",
        )
        await _ensure_job_column(db, "ref_image_path", "ref_image_path TEXT")
        await _ensure_job_column(db, "error_kind", "error_kind TEXT")
        await _ensure_job_column(db, "error_message", "error_message TEXT")
        await _ensure_character_column(db, "description", "description TEXT")
        await _ensure_character_column(
            db,
            "image_paths",
            "image_paths TEXT NOT NULL DEFAULT '[]'",
        )
        await _ensure_character_column(db, "created_at", "created_at TEXT")
        await _ensure_character_column(db, "updated_at", "updated_at TEXT")
        await _ensure_template_column(db, "description", "description TEXT")
        await _ensure_template_column(db, "steps_json", "steps_json TEXT NOT NULL DEFAULT '[]'")
        await _ensure_template_column(db, "created_at", "created_at TEXT")
        await _ensure_template_column(db, "updated_at", "updated_at TEXT")
        backfilled = await _backfill_job_chain_ids(db)
        logger.info("backfilled chain_id on %d jobs", backfilled)
        recreated = await _ensure_jobs_fts_trigram(db)
        if recreated:
            logger.info("recreated jobs_fts with trigram tokenizer")
        fts_backfilled = await _backfill_jobs_fts(db)
        logger.info("backfilled jobs_fts on %d jobs", fts_backfilled)
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
    await db.execute("PRAGMA foreign_keys = ON")
    try:
        yield db
    finally:
        await db.close()
