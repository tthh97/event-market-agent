-- schema.sql
-- data/events.db, modelled on the course chinook.db: PascalCase singular tables,
-- <Table>Id keys, ISO-8601 text dates (string order is date order).
-- Open it with: sqlite3 data/events.db

-- Reference data -----------------------------------------------------------

-- The 11 US sectors and the SPDR ETF used as each sector's price proxy.
CREATE TABLE IF NOT EXISTS Sector (
    SectorId   TEXT PRIMARY KEY,          -- e.g. energy
    Name       TEXT NOT NULL,             -- e.g. Energy
    EtfTicker  TEXT NOT NULL UNIQUE       -- e.g. XLE
);

-- Raw inputs ----------------------------------------------------------------

-- One row per imported file or download. Sha256 blocks duplicate imports.
CREATE TABLE IF NOT EXISTS DataImport (
    ImportId    INTEGER PRIMARY KEY,
    Source      TEXT NOT NULL,            -- gdelt
    FileName    TEXT NOT NULL,
    Sha256      TEXT NOT NULL UNIQUE,
    RowCount    INTEGER NOT NULL,
    ImportedAt  TEXT NOT NULL             -- UTC timestamp
);

-- GDELT daily export rows: unverified research leads, not confirmed events.
-- EventDate is when the event happened, AddedDate is when GDELT ingested it.
CREATE TABLE IF NOT EXISTS GdeltEvent (
    GlobalEventId  TEXT PRIMARY KEY,
    EventDate      TEXT NOT NULL,
    AddedDate      TEXT NOT NULL,
    Actor1Name     TEXT,
    Actor2Name     TEXT,
    EventCode      TEXT,                  -- CAMEO code, leading zeroes kept
    NumMentions    INTEGER,
    NumSources     INTEGER,
    NumArticles    INTEGER,
    Place          TEXT,
    CountryCode    TEXT,
    SourceUrl      TEXT,
    ImportId       INTEGER REFERENCES DataImport (ImportId)
);
CREATE INDEX IF NOT EXISTS IX_GdeltEvent_EventDate ON GdeltEvent (EventDate);

-- Agent runs and results ----------------------------------------------------

-- One row per brief or follow-up.
CREATE TABLE IF NOT EXISTS Run (
    RunId        TEXT PRIMARY KEY,
    Kind         TEXT NOT NULL CHECK (Kind IN ('brief', 'followup')),
    Question     TEXT NOT NULL,
    AsOf         TEXT NOT NULL,           -- requested day, EVENT_TIMEZONE
    CreatedAt    TEXT NOT NULL,           -- UTC timestamp
    SearchCalls  INTEGER NOT NULL,
    JevStatus    TEXT NOT NULL
);

-- Dated news articles retrieved by Tavily. Only these can be cited.
CREATE TABLE IF NOT EXISTS Source (
    SourceId     TEXT PRIMARY KEY,        -- s_ + hash of the URL
    Url          TEXT NOT NULL,
    Title        TEXT,
    PublishedAt  TEXT NOT NULL,
    RetrievedAt  TEXT NOT NULL,
    Excerpt      TEXT
);

-- Every source a run retrieved, cited or not.
CREATE TABLE IF NOT EXISTS RunSource (
    RunId     TEXT NOT NULL REFERENCES Run (RunId),
    SourceId  TEXT NOT NULL REFERENCES Source (SourceId),
    PRIMARY KEY (RunId, SourceId)
);

-- A tracked real-world development. EventDate is the original onset date and
-- never changes on follow-up.
CREATE TABLE IF NOT EXISTS Event (
    EventId         TEXT PRIMARY KEY,
    Title           TEXT NOT NULL,
    EventDate       TEXT,                 -- NULL when the evidence does not say
    FirstAssessedOn TEXT NOT NULL
);

-- What the agent concluded about an event on one run. Follow-ups append rows.
CREATE TABLE IF NOT EXISTS Assessment (
    AssessmentId  INTEGER PRIMARY KEY,
    EventId       TEXT NOT NULL REFERENCES Event (EventId),
    RunId         TEXT NOT NULL REFERENCES Run (RunId),
    Title         TEXT NOT NULL,
    Category      TEXT NOT NULL,
    Summary       TEXT NOT NULL,
    WhyWatch      TEXT NOT NULL,
    Uncertainty   TEXT NOT NULL,
    WatchNext     TEXT NOT NULL,
    Status        TEXT NOT NULL CHECK (Status IN ('developing', 'ongoing', 'resolved', 'unclear')),
    UNIQUE (EventId, RunId)
);

CREATE TABLE IF NOT EXISTS AssessmentSource (
    AssessmentId  INTEGER NOT NULL REFERENCES Assessment (AssessmentId),
    SourceId      TEXT NOT NULL REFERENCES Source (SourceId),
    PRIMARY KEY (AssessmentId, SourceId)
);

-- Event -> economic channel -> US sector link, with its own citations.
CREATE TABLE IF NOT EXISTS Exposure (
    ExposureId    INTEGER PRIMARY KEY,
    AssessmentId  INTEGER NOT NULL REFERENCES Assessment (AssessmentId),
    SectorId      TEXT NOT NULL REFERENCES Sector (SectorId),
    Channel       TEXT NOT NULL,
    Reasoning     TEXT NOT NULL,
    Status        TEXT NOT NULL CHECK (Status IN ('reported_exposure', 'hypothesis'))
);

CREATE TABLE IF NOT EXISTS ExposureSource (
    ExposureId  INTEGER NOT NULL REFERENCES Exposure (ExposureId),
    SourceId    TEXT NOT NULL REFERENCES Source (SourceId),
    PRIMARY KEY (ExposureId, SourceId)
);

-- Jev's expected direction for an exposure: will the sector beat or trail SPY over the
-- next five sessions? A prediction made before prices are known, scored in evals/.
CREATE TABLE IF NOT EXISTS ExposureDirection (
    ExposureId  INTEGER PRIMARY KEY REFERENCES Exposure (ExposureId),
    Direction   TEXT NOT NULL CHECK (Direction IN ('up', 'down', 'unclear')),
    Confidence  REAL,
    Model       TEXT NOT NULL,           -- e.g. jev-1.13.0
    JudgedAt    TEXT NOT NULL            -- UTC timestamp
);

-- Jev's severity of one assessed event, from the event text. Score is Jev's expected level
-- (0 minor .. 3 severe); Level is the nearest named level.
CREATE TABLE IF NOT EXISTS AssessmentSeverity (
    AssessmentId  INTEGER PRIMARY KEY REFERENCES Assessment (AssessmentId),
    Score         REAL NOT NULL,
    Level         TEXT NOT NULL CHECK (Level IN ('minor', 'moderate', 'high', 'severe')),
    Confidence    REAL,
    Model         TEXT NOT NULL,
    JudgedAt      TEXT NOT NULL
);

-- Jev's size of the effect on the sector through one exposure (0 negligible .. 3 large).
CREATE TABLE IF NOT EXISTS ExposureImpact (
    ExposureId  INTEGER PRIMARY KEY REFERENCES Exposure (ExposureId),
    Score       REAL NOT NULL,
    Level       TEXT NOT NULL CHECK (Level IN ('negligible', 'small', 'material', 'large')),
    Confidence  REAL,
    Model       TEXT NOT NULL,
    JudgedAt    TEXT NOT NULL
);

-- Stocks or ETFs the lead named as exposed to an event, never an S&P 500 tracker.
-- Jev scores how directly each is exposed (0 none .. 3 core). Rank 1-3 marks the top three
-- by that score; NULL for the rest. Without Jev the lead's order decides.
CREATE TABLE IF NOT EXISTS WatchTicker (
    WatchTickerId  INTEGER PRIMARY KEY,
    AssessmentId   INTEGER NOT NULL REFERENCES Assessment (AssessmentId),
    Ticker         TEXT NOT NULL,
    Kind           TEXT NOT NULL CHECK (Kind IN ('stock', 'etf')),
    Name           TEXT NOT NULL,
    Reason         TEXT NOT NULL,
    Rank           INTEGER CHECK (Rank BETWEEN 1 AND 3),
    JevScore       REAL,
    JevLevel       TEXT CHECK (JevLevel IN ('none', 'indirect', 'direct', 'core')),
    JevConfidence  REAL,
    UNIQUE (AssessmentId, Ticker)
);

CREATE TABLE IF NOT EXISTS WatchTickerSource (
    WatchTickerId  INTEGER NOT NULL REFERENCES WatchTicker (WatchTickerId),
    SourceId       TEXT NOT NULL REFERENCES Source (SourceId),
    PRIMARY KEY (WatchTickerId, SourceId)
);

-- What one run used: one row per Claude model, plus Jev and Tavily. CostUsd is NULL when
-- the price is unknown (Jev, Tavily credits).
CREATE TABLE IF NOT EXISTS RunUsage (
    RunId             TEXT NOT NULL REFERENCES Run (RunId),
    Service           TEXT NOT NULL,     -- anthropic | jev | tavily
    Model             TEXT NOT NULL,
    Calls             INTEGER,
    InputTokens       INTEGER,
    OutputTokens      INTEGER,
    CacheReadTokens   INTEGER,
    CacheWriteTokens  INTEGER,
    CostUsd           REAL,
    PRIMARY KEY (RunId, Service, Model)
);

-- Sector ETF vs SPY returns computed in Python on follow-up. Observations,
-- not causal attribution.
CREATE TABLE IF NOT EXISTS MarketWindow (
    MarketWindowId   INTEGER PRIMARY KEY,
    AssessmentId     INTEGER NOT NULL REFERENCES Assessment (AssessmentId),
    Ticker           TEXT NOT NULL,
    WindowName       TEXT NOT NULL,       -- D0_to_D0 | D0_to_D1 | D0_to_D5
    BaselineDate     TEXT NOT NULL,
    StartSession     TEXT NOT NULL,
    EndSession       TEXT NOT NULL,
    ReturnPct        REAL NOT NULL,
    SpyReturnPct     REAL NOT NULL,
    ExcessPp         REAL NOT NULL        -- ReturnPct - SpyReturnPct
);
