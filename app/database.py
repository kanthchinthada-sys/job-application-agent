"""SQLite connection handling and schema creation.

Design notes
------------
* We use Python's built-in ``sqlite3`` module: no extra packages, and the whole
  database is a single file you can inspect or delete.
* Every HTTP request gets its own short-lived connection (see ``get_connection``
  in main.py). This avoids sharing one connection across threads.
* ``PRAGMA foreign_keys = ON`` must be set per connection in SQLite; without it
  the FOREIGN KEY constraints below would be silently ignored.
* The schema is created with ``CREATE TABLE IF NOT EXISTS`` on startup. That is
  fine while the schema is young. Once real data exists and the schema changes,
  we will introduce versioned migrations (e.g. Alembic with PostgreSQL).
"""

import sqlite3
from pathlib import Path

SCHEMA = """
-- One row only: the profile is a single JSON document owned by the user.
CREATE TABLE IF NOT EXISTS profile (
    id          INTEGER PRIMARY KEY CHECK (id = 1),
    data        TEXT    NOT NULL,          -- JSON validated by schemas.Profile
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    title            TEXT NOT NULL,
    company          TEXT NOT NULL,
    location         TEXT,
    source_url       TEXT UNIQUE,          -- same posting cannot be added twice
    description      TEXT NOT NULL,
    required_skills  TEXT NOT NULL,        -- JSON list of strings
    preferred_skills TEXT NOT NULL,        -- JSON list of strings
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS applications (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id                  INTEGER NOT NULL UNIQUE   -- at most one application per job
                            REFERENCES jobs(id),
    status                  TEXT NOT NULL,
    notes                   TEXT NOT NULL DEFAULT '',
    submission_confirmation TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- Application materials. Every save is a new row (version), nothing is overwritten.
CREATE TABLE IF NOT EXISTS documents (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id),
    kind            TEXT NOT NULL,            -- cover_letter | screening_answer
    question        TEXT NOT NULL DEFAULT '', -- screening question ('' for cover letters)
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    source          TEXT NOT NULL,            -- ai | user_edit
    model           TEXT,
    review          TEXT NOT NULL,            -- JSON: facts used, warnings, questions
    created_at      TEXT NOT NULL,
    UNIQUE (application_id, kind, question, version)
);

-- The latest document generation run per application, so a restart can tell what was interrupted.
CREATE TABLE IF NOT EXISTS generation_runs (
    application_id  INTEGER PRIMARY KEY REFERENCES applications(id),
    data            TEXT NOT NULL,            -- JSON: preparation.Preparation
    updated_at      TEXT NOT NULL
);

-- Adding a job: what the model was asked to resolve after the job was saved, and what it changed or left
-- alone because you had corrected it (app/intake_runs.py).
CREATE TABLE IF NOT EXISTS intake_runs (
    job_id          INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    data            TEXT NOT NULL,            -- JSON
    updated_at      TEXT NOT NULL
);

-- Keep the most recent completed free search in this workspace so any app tab can resume it.
CREATE TABLE IF NOT EXISTS last_job_search (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    filters TEXT NOT NULL,
    result TEXT NOT NULL
);

-- Append-only history: every status change is recorded with its timestamp.
CREATE TABLE IF NOT EXISTS application_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    application_id  INTEGER NOT NULL REFERENCES applications(id),
    from_status     TEXT,
    to_status       TEXT NOT NULL,
    note            TEXT,
    occurred_at     TEXT NOT NULL
);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI may open the connection in one worker
    # thread and run the endpoint in another. Each connection is still used by
    # only one request at a time, so this is safe.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["title"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
