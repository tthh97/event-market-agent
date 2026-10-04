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
    topic TEXT,                             -- research's topic, joins to claim.topic; NULL on runs before 2026-10
    summary TEXT,                           -- research's summary; NULL on runs before 2026-10
    themes TEXT,                            -- GDELT market themes, ;-separated; NULL on runs before 2026-10
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
    country TEXT,                           -- set with sector; NULL on runs before 2026-10
    PRIMARY KEY (run_id, text)
);
CREATE TABLE IF NOT EXISTS rejection (      -- a claim verify removed, and why
    run_id TEXT NOT NULL REFERENCES run,
    line TEXT NOT NULL,
    PRIMARY KEY (run_id, line)
);
CREATE TABLE IF NOT EXISTS market_move (    -- one tag's ETF move in the run's price window
    run_id TEXT NOT NULL REFERENCES run,
    sector TEXT NOT NULL,
    country TEXT NOT NULL,
    etf TEXT NOT NULL,
    benchmark TEXT NOT NULL,                -- SPY or ACWI
    move REAL NOT NULL,                     -- percent
    vs_benchmark REAL NOT NULL,             -- percentage points
    PRIMARY KEY (run_id, sector, country)
);
"""


def connect(path=None):
    """An open connection to events.db (or path), with the tables created if missing."""
    path = Path(path or PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    upgrade(connection)
    return connection


def upgrade(connection):
    """Bring a database made before countries up to SCHEMA. Adds columns, copies old price rows as US
    moves, and drops price_move. Safe to run on every connect."""
    for table, column in [("story", "topic"), ("story", "summary"), ("story", "themes"), ("claim", "country")]:
        if column not in {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name = 'price_move'").fetchone():
        from prices import SECTOR_ETFS
        rows = connection.execute("SELECT run_id, sector, move, vs_spy FROM price_move").fetchall()
        connection.executemany("INSERT INTO market_move VALUES (?, ?, 'us', ?, 'SPY', ?, ?) ON CONFLICT DO NOTHING",
                               [(r, s, SECTOR_ETFS[s], m, v) for r, s, m, v in rows])
        connection.execute("UPDATE claim SET country = 'us' WHERE sector IS NOT NULL AND country IS NULL")
        connection.execute("DROP TABLE price_move")
    connection.commit()
