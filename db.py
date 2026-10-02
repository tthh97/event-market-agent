# db.py
"""data/events.db: every market GDELT row fetched, and every run's results.

Rows are only appended. Each table has a natural key, and a row that is already stored is skipped
(INSERT ... ON CONFLICT DO NOTHING). gdelt writes gkg_file and gkg_row. output writes the rest.
LangGraph's own data (checkpoints and the findings memory) is in data/threads.db, not here.

    sqlite3 data/events.db "SELECT title, count(DISTINCT site) FROM gkg_row GROUP BY title ORDER BY 2 DESC LIMIT 10"
"""

import sqlite3
from pathlib import Path

PATH = Path(__file__).resolve().parent / "data" / "events.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS gkg_file (       -- one GDELT 15-minute GKG file, once downloaded
    at TEXT PRIMARY KEY,                    -- YYYYMMDDHHMMSS, UTC
    rows INTEGER NOT NULL,                  -- market rows kept; 0 when GDELT skipped the update
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS gkg_row (        -- one market-themed article from a GKG file
    record_id TEXT PRIMARY KEY,             -- GDELT's GKGRECORDID
    at TEXT NOT NULL REFERENCES gkg_file,
    site TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    themes TEXT NOT NULL,                   -- market themes, ;-separated
    actors TEXT NOT NULL,                   -- people and organizations, ;-separated
    places TEXT NOT NULL                    -- ;-separated
);
CREATE INDEX IF NOT EXISTS gkg_row_at ON gkg_row (at);

CREATE TABLE IF NOT EXISTS run (            -- one graph run: a new report or a follow-up
    run_id TEXT PRIMARY KEY,                -- the report's file name
    thread_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('events', 'followup')),
    jev_status TEXT NOT NULL,
    verify_status TEXT NOT NULL,
    price_window TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS story (          -- a story triage kept, on new reports only
    run_id TEXT NOT NULL REFERENCES run,
    rank INTEGER NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    severity REAL,                          -- NULL when Jev did not score
    articles INTEGER NOT NULL,
    sites INTEGER NOT NULL,
    PRIMARY KEY (run_id, rank)
);
CREATE TABLE IF NOT EXISTS claim (          -- a claim verify kept
    run_id TEXT NOT NULL REFERENCES run,
    topic TEXT NOT NULL,
    text TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_date TEXT NOT NULL,
    sector TEXT,
    unverified INTEGER NOT NULL,
    PRIMARY KEY (run_id, text)
);
CREATE TABLE IF NOT EXISTS rejection (      -- a claim verify removed, and why
    run_id TEXT NOT NULL REFERENCES run,
    line TEXT NOT NULL,
    PRIMARY KEY (run_id, line)
);
CREATE TABLE IF NOT EXISTS price_move (     -- a sector ETF's move in the run's price window
    run_id TEXT NOT NULL REFERENCES run,
    sector TEXT NOT NULL,
    move REAL NOT NULL,                     -- percent
    vs_spy REAL NOT NULL,                   -- percentage points
    PRIMARY KEY (run_id, sector)
);
"""


def connect(path=None):
    """An open connection to events.db (or path), with the tables created if missing."""
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    return connection
