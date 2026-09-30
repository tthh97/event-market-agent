# events_db.py
"""Read and write data/events.db, a Chinook-style SQLite database defined in schema.sql.

Patterns reused: local SQLite database like chinook.db (m1.5).
Run: imported by cli.py and the tools. Inspect with `sqlite3 data/events.db`.
"""

import csv
import hashlib
import io
import os
import sqlite3
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pandas as pd

from jev_api import ranked_tickers
from models import ROOT, SECTORS, TIMEZONE, is_major_outlet

# EVENTS_DB points a run at another database file, e.g. a separate backfill.
DB_PATH = ROOT / os.getenv("EVENTS_DB", "data/events.db")
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")

GPR_SOURCE = "https://www.matteoiacoviello.com/gpr.htm"
GPR_DOWNLOAD = "https://www.matteoiacoviello.com/gpr_files/data_gpr_daily_recent.xls"
GPR_ATTRIBUTION = "Dario Caldara and Matteo Iacoviello, Measuring Geopolitical Risk (2022). CC BY."


def now_utc():
    return datetime.now(UTC).isoformat()


@contextmanager
def connect(db=None):
    """Open the database as one transaction: commit on success, roll back on error."""
    path = Path(db or DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(SCHEMA)
        with con:
            con.executemany(
                "INSERT OR IGNORE INTO Sector VALUES (?, ?, ?)",
                [(key, key.replace("_", " ").title(), etf) for key, etf in SECTORS.items()],
            )
            yield con
    finally:
        con.close()


def read_only_query(sql, db=None, max_rows=50):
    """Run one SELECT on a read-only connection. Writes fail at the SQLite level."""
    path = Path(db or DB_PATH)
    if not path.exists():
        raise ValueError("Database not created yet. Run a command that writes data first.")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cursor = con.execute(sql)
        columns = [c[0] for c in cursor.description or []]
        rows = cursor.fetchmany(max_rows + 1)
    finally:
        con.close()
    return columns, rows[:max_rows], len(rows) > max_rows


# GDELT -----------------------------------------------------------------------


GDELT_DAILY = "https://data.gdeltproject.org/events/{day}.export.CSV.zip"
# URLs kept per event date, most-mentioned first. A full day is ~110k rows and ~25MB, too big
# for a Git-tracked database. Briefs read at most 30 candidates, so 300 keeps every one they see.
GDELT_KEEP_URLS = 300


def import_gdelt(path, db=None):
    """Import a local 58-column GDELT daily export file."""
    path = Path(path).expanduser().resolve()
    return import_gdelt_text(path.read_text(encoding="utf-8"), path.name, db)


def refresh_gdelt(day, db=None):
    """Download and import the official GDELT daily export for one date.

    The file for a date is published around 07:00 UTC the next day. Before that it is not found.
    """
    name = f"{day:%Y%m%d}.export.CSV"
    with connect(db) as con:
        if con.execute("SELECT 1 FROM DataImport WHERE Source = 'gdelt' AND FileName = ?", [name]).fetchone():
            return {"status": "already_imported", "file": name}
    url = GDELT_DAILY.format(day=f"{day:%Y%m%d}")
    try:
        response = httpx.get(url, follow_redirects=True, timeout=120)
        if response.status_code == 404:
            return {"status": "not_published", "file": name,
                    "note": "GDELT publishes a day's export around 07:00 UTC the next day."}
        response.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
    except (httpx.HTTPError, zipfile.BadZipFile) as exc:
        return {"status": "unavailable", "file": name, "error_type": type(exc).__name__}
    return {**import_gdelt_text(text, name, db), "source": url}


def import_gdelt_text(text, name, db=None):
    """Import one GDELT daily export, keeping event date and ingestion date distinct.

    Keeps the GDELT_KEEP_URLS most-mentioned URLs per event date, with all their rows.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rows, dates = [], Counter()
    for line, row in enumerate(csv.reader(io.StringIO(text), delimiter="\t"), 1):
        if len(row) != 58:
            raise ValueError(f"Line {line}: expected 58 columns (GDELT daily export), got {len(row)}")
        event_day = datetime.strptime(row[1], "%Y%m%d").date().isoformat()
        added_day = datetime.strptime(row[56][:8], "%Y%m%d").date().isoformat()
        dates[event_day] += 1
        rows.append([row[0], event_day, added_day, row[6], row[16], row[26],
                     int(row[31]), int(row[32]), int(row[33]), row[50], row[51], row[57]])
    with connect(db) as con:
        if con.execute("SELECT 1 FROM DataImport WHERE Sha256 = ?", [digest]).fetchone():
            return {"status": "already_imported", "rows": len(rows), "event_dates": dict(dates)}
        import_id = con.execute(
            "INSERT INTO DataImport (Source, FileName, Sha256, RowCount, ImportedAt) VALUES (?, ?, ?, ?, ?)",
            ["gdelt", name, digest, len(rows), now_utc()],
        ).lastrowid
        con.executemany("INSERT OR IGNORE INTO GdeltEvent VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [r + [import_id] for r in rows])
        prune_gdelt(con)
        kept = con.execute("SELECT count(*) FROM GdeltEvent WHERE ImportId = ?", [import_id]).fetchone()[0]
    # Pruned rows leave free pages. Without this the file keeps the size of the full day (~25MB).
    con = sqlite3.connect(Path(db or DB_PATH))
    con.execute("VACUUM")
    con.close()
    return {"status": "imported", "rows": len(rows), "rows_kept": kept, "event_dates": dict(dates), "sha256": digest}


def prune_gdelt(con, keep=None):
    """Delete GDELT rows outside the most-mentioned URLs of their event date."""
    con.execute("""
        DELETE FROM GdeltEvent WHERE rowid NOT IN (
            SELECT g.rowid FROM GdeltEvent g JOIN (
                SELECT EventDate, SourceUrl FROM (
                    SELECT EventDate, SourceUrl, row_number() OVER (
                        PARTITION BY EventDate ORDER BY max(NumMentions) DESC, SourceUrl) AS n
                    FROM GdeltEvent WHERE SourceUrl LIKE 'http%' GROUP BY EventDate, SourceUrl)
                WHERE n <= ?) top USING (EventDate, SourceUrl))""", [keep or GDELT_KEEP_URLS])


def candidates(as_of, limit=12, db=None):
    """GDELT leads for one event date, known by that day. Rows sharing a URL are collapsed."""
    if not 1 <= limit <= 30:
        raise ValueError("limit must be 1 to 30")
    with connect(db) as con:
        rows = con.execute("""
            SELECT SourceUrl AS url, max(NumMentions) AS mentions, max(NumSources) AS source_count,
                   Actor1Name AS actor1, Actor2Name AS actor2, Place AS place,
                   group_concat(DISTINCT EventCode) AS cameo_codes, count(*) AS raw_rows
            FROM GdeltEvent
            WHERE EventDate = ? AND AddedDate <= ? AND SourceUrl LIKE 'http%'
            GROUP BY SourceUrl
            ORDER BY mentions DESC, url
            LIMIT ?""", [str(as_of), str(as_of), limit]).fetchall()
        latest = con.execute("SELECT max(AddedDate) FROM GdeltEvent").fetchone()[0]
    return {"requested_event_date": str(as_of), "latest_imported_date": latest,
            "candidates": [dict(r) for r in rows],
            "warning": "Coverage-ranked research leads, not verified events or a complete daily news feed. "
                       "URL grouping is not semantic event deduplication."}


# GPR -------------------------------------------------------------------------


def refresh_gpr(db=None):
    """Download the official daily GPR series and replace GprDaily."""
    response = httpx.get(GPR_DOWNLOAD, follow_redirects=True, timeout=45)
    response.raise_for_status()
    frame = pd.read_excel(io.BytesIO(response.content))
    frame.columns = [str(x).strip() for x in frame.columns]
    if "date" not in frame or "GPRD" not in frame:
        raise ValueError(f"GPR schema changed: expected date and GPRD, found {list(frame.columns)}")
    days = pd.to_datetime(frame["date"], errors="coerce")
    records = [(d.date().isoformat(), float(v)) for d, v in zip(days, frame["GPRD"], strict=True)
               if pd.notna(d) and pd.notna(v)]
    # Guards against numeric day codes parsed as 1970 timestamps.
    if not records or any(day < "1985-01-01" for day, _ in records):
        raise ValueError("Invalid daily GPR observation dates; refusing to save.")
    digest = hashlib.sha256(response.content).hexdigest()
    retrieved_at = now_utc()
    with connect(db) as con:
        con.execute("DELETE FROM GprDaily")
        con.executemany("INSERT INTO GprDaily VALUES (?, ?)", records)
        con.execute("DELETE FROM DataImport WHERE Source = 'gpr'")
        con.execute("INSERT INTO DataImport (Source, FileName, Sha256, RowCount, ImportedAt) VALUES (?, ?, ?, ?, ?)",
                    ["gpr", GPR_DOWNLOAD, digest, len(records), retrieved_at])
    return {"source": GPR_SOURCE, "retrieved_at": retrieved_at, "sha256": digest,
            "attribution": GPR_ATTRIBUTION, "latest_observation": max(day for day, _ in records)}


def gpr_context(as_of, db=None):
    """Latest GPR value on or before as_of, if the downloaded snapshot was available that day."""
    with connect(db) as con:
        retrieved_at = con.execute("SELECT max(ImportedAt) FROM DataImport WHERE Source = 'gpr'").fetchone()[0]
        latest = con.execute("SELECT Date AS date, Gprd AS gprd FROM GprDaily WHERE Date <= ? "
                             "ORDER BY Date DESC LIMIT 1", [str(as_of)]).fetchone()
    if retrieved_at is None:
        return {"status": "not_downloaded", "source": GPR_SOURCE, "note": "Run refresh-gpr for official index context."}
    # A snapshot downloaded later cannot show what was known on an earlier date.
    if datetime.fromisoformat(retrieved_at).astimezone(TIMEZONE).date() > as_of:
        return {"status": "excluded_for_historical_as_of", "source": GPR_SOURCE,
                "note": "Snapshot retrieved after requested date; not valid point-in-time evidence."}
    return {"status": "ok" if latest else "no_observation", "latest": dict(latest) if latest else None,
            "source": GPR_SOURCE, "retrieved_at": retrieved_at, "attribution": GPR_ATTRIBUTION,
            "note": "Aggregate geopolitical news context only; not event severity, a market signal, "
                    "or coverage of non-geopolitical risks. Latest observations may be revised."}


# Runs, events and assessments ------------------------------------------------


def save_run(kind, question, as_of, brief, evidence, search_calls, jev_status,
             event_id=None, market_metrics=(), judgements=None, usage=(), db=None):
    """Save one run. A follow-up passes event_id and appends an Assessment to that Event.

    A brief event with tracked_event_id appends to that saved Event instead of creating one.
    judgements is jev_api.assess() output (answers keyed e{i}_severity, e{i}_x{j}, e{i}_x{j}_impact,
    e{i}_t{k}); usage is a list of RunUsage row dicts.
    Returns (run_id, event_ids) with one event ID per brief event.
    """
    run_id = uuid4().hex
    event_ids = []
    with connect(db) as con:
        con.execute("INSERT INTO Run VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [run_id, kind, question, str(as_of), now_utc(), search_calls, jev_status])
        for source in evidence.values():
            con.execute("INSERT OR IGNORE INTO Source VALUES (?, ?, ?, ?, ?, ?)",
                        [source["source_id"], source["url"], source.get("title", ""),
                         source["published_at"], source.get("retrieved_at", now_utc()), source.get("excerpt", "")])
            con.execute("INSERT OR IGNORE INTO RunSource VALUES (?, ?)", [run_id, source["source_id"]])

        answers = (judgements or {}).get("answers", {})
        jev_model = (judgements or {}).get("model", "jev")
        for i, event in enumerate(brief.events):
            key = event_id or event.tracked_event_id or uuid4().hex[:12]
            event_ids.append(key)
            event_date = str(event.event_date) if event.event_date else None
            con.execute("INSERT OR IGNORE INTO Event VALUES (?, ?, ?, ?)", [key, event.title, event_date, str(as_of)])
            # A known onset date never changes. An unknown one may be filled once.
            con.execute("UPDATE Event SET EventDate = ? WHERE EventId = ? AND EventDate IS NULL", [event_date, key])
            assessment_id = con.execute(
                "INSERT INTO Assessment (EventId, RunId, Title, Category, Summary, WhyWatch, Uncertainty, "
                "WatchNext, Status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [key, run_id, event.title, event.category, event.summary, event.why_watch,
                 event.uncertainty, event.watch_next, event.status]).lastrowid
            con.executemany("INSERT OR IGNORE INTO AssessmentSource VALUES (?, ?)",
                            [(assessment_id, s) for s in event.source_ids])
            if severity := answers.get(f"e{i}_severity"):
                con.execute("INSERT INTO AssessmentSeverity VALUES (?, ?, ?, ?, ?, ?)",
                            [assessment_id, severity["score"], severity["level"], severity.get("confidence"),
                             jev_model, now_utc()])
            for t in ranked_tickers(event.model_dump(mode="json"), i, answers):
                jev = t["jev"] or {}
                ticker_id = con.execute(
                    "INSERT INTO WatchTicker (AssessmentId, Ticker, Kind, Name, Reason, Rank, JevScore, JevLevel, "
                    "JevConfidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [assessment_id, t["symbol"], t["kind"], t["name"], t["reason"], t["rank"],
                     jev.get("score"), jev.get("level"), jev.get("confidence")]).lastrowid
                con.executemany("INSERT OR IGNORE INTO WatchTickerSource VALUES (?, ?)",
                                [(ticker_id, s) for s in t["source_ids"]])
            for j, exposure in enumerate(event.exposures):
                exposure_id = con.execute(
                    "INSERT INTO Exposure (AssessmentId, SectorId, Channel, Reasoning, Status) VALUES (?, ?, ?, ?, ?)",
                    [assessment_id, exposure.sector, exposure.channel, exposure.reasoning, exposure.status]).lastrowid
                con.executemany("INSERT OR IGNORE INTO ExposureSource VALUES (?, ?)",
                                [(exposure_id, s) for s in exposure.source_ids])
                if f"e{i}_x{j}" in answers:
                    save_direction(con, exposure_id, answers[f"e{i}_x{j}"], jev_model)
                if impact := answers.get(f"e{i}_x{j}_impact"):
                    con.execute("INSERT INTO ExposureImpact VALUES (?, ?, ?, ?, ?, ?)",
                                [exposure_id, impact["score"], impact["level"], impact.get("confidence"),
                                 jev_model, now_utc()])
            con.executemany(
                "INSERT INTO MarketWindow (AssessmentId, Ticker, WindowName, BaselineDate, StartSession, "
                "EndSession, ReturnPct, SpyReturnPct, ExcessPp) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [(assessment_id, m["ticker"], m["window"], m["baseline_date"], m["start_session"],
                  m["end_session"], m["return_pct"], m["spy_return_pct"], m["excess_percentage_points"])
                 for m in market_metrics])
        con.executemany(
            "INSERT INTO RunUsage VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, u["service"], u["model"], u.get("calls"), u.get("input_tokens"), u.get("output_tokens"),
              u.get("cache_read_tokens"), u.get("cache_write_tokens"), u.get("cost_usd")) for u in usage])
    return run_id, event_ids


def save_direction(con, exposure_id, answer, model):
    con.execute("INSERT OR REPLACE INTO ExposureDirection VALUES (?, ?, ?, ?, ?)",
                [exposure_id, answer["direction"], answer.get("confidence"), model, now_utc()])


def cost_summary(month, db=None):
    """Spend for one calendar month (YYYY-MM, by run creation time in UTC)."""
    with connect(db) as con:
        runs = con.execute("SELECT count(*) FROM Run WHERE substr(CreatedAt, 1, 7) = ?", [month]).fetchone()[0]
        rows = con.execute("""
            SELECT u.Service, u.Model, count(DISTINCT u.RunId) AS runs, sum(u.Calls) AS calls,
                   sum(u.InputTokens) AS input_tokens, sum(u.OutputTokens) AS output_tokens,
                   sum(u.CacheReadTokens) AS cache_read_tokens, sum(u.CostUsd) AS cost_usd
            FROM RunUsage u JOIN Run r USING (RunId)
            WHERE substr(r.CreatedAt, 1, 7) = ?
            GROUP BY u.Service, u.Model ORDER BY cost_usd DESC""", [month]).fetchall()
        costed = con.execute("""SELECT count(DISTINCT u.RunId) FROM RunUsage u JOIN Run r USING (RunId)
                                WHERE substr(r.CreatedAt, 1, 7) = ?""", [month]).fetchone()[0]
    total = sum(r["cost_usd"] or 0 for r in rows)
    return {"month": month, "runs": runs, "runs_with_cost_data": costed,
            "claude_cost_usd": round(total, 4),
            "average_per_run_usd": round(total / costed, 4) if costed else None,
            "by_model": [dict(r) for r in rows],
            "note": "Claude cost only. Tavily credits and Jev usage are counted but not priced. "
                    "Runs that failed before saving, and runs before usage logging, are not included."}


def load_event(event_id, db=None):
    """Latest assessment of a saved event, in the Brief Event shape, plus its cited sources."""
    with connect(db) as con:
        row = con.execute("""
            SELECT a.AssessmentId, a.Title, a.Category, a.Summary, a.WhyWatch, a.Uncertainty,
                   a.WatchNext, a.Status, e.EventDate, r.AsOf
            FROM Assessment a
            JOIN Event e ON e.EventId = a.EventId
            JOIN Run r ON r.RunId = a.RunId
            WHERE a.EventId = ?
            ORDER BY r.CreatedAt DESC, a.AssessmentId DESC
            LIMIT 1""", [event_id]).fetchone()
        if not row:
            raise ValueError("Unknown saved event ID. Run list first.")
        assessment_id = row["AssessmentId"]
        source_ids = [r[0] for r in con.execute(
            "SELECT SourceId FROM AssessmentSource WHERE AssessmentId = ? ORDER BY SourceId", [assessment_id])]
        exposures = []
        for x in con.execute("SELECT * FROM Exposure WHERE AssessmentId = ? ORDER BY ExposureId", [assessment_id]):
            cited = [r[0] for r in con.execute(
                "SELECT SourceId FROM ExposureSource WHERE ExposureId = ? ORDER BY SourceId", [x["ExposureId"]])]
            exposures.append({"sector": x["SectorId"], "channel": x["Channel"], "reasoning": x["Reasoning"],
                              "status": x["Status"], "source_ids": cited})
        tickers = []
        for t in con.execute("SELECT * FROM WatchTicker WHERE AssessmentId = ? ORDER BY Rank IS NULL, Rank, "
                             "WatchTickerId", [assessment_id]):
            cited = [r[0] for r in con.execute(
                "SELECT SourceId FROM WatchTickerSource WHERE WatchTickerId = ? ORDER BY SourceId", [t["WatchTickerId"]])]
            tickers.append({"symbol": t["Ticker"], "kind": t["Kind"], "name": t["Name"], "reason": t["Reason"],
                            "source_ids": cited})
        # Every source cited by any earlier assessment of this event.
        sources = con.execute("""
            SELECT DISTINCT s.* FROM Source s
            WHERE s.SourceId IN (
                SELECT asrc.SourceId FROM AssessmentSource asrc
                JOIN Assessment a ON a.AssessmentId = asrc.AssessmentId WHERE a.EventId = ?
                UNION
                SELECT esrc.SourceId FROM ExposureSource esrc
                JOIN Exposure x ON x.ExposureId = esrc.ExposureId
                JOIN Assessment a ON a.AssessmentId = x.AssessmentId WHERE a.EventId = ?
                UNION
                SELECT tsrc.SourceId FROM WatchTickerSource tsrc
                JOIN WatchTicker t ON t.WatchTickerId = tsrc.WatchTickerId
                JOIN Assessment a ON a.AssessmentId = t.AssessmentId WHERE a.EventId = ?)""",
            [event_id, event_id, event_id]).fetchall()
    event = {"tracked_event_id": event_id, "title": row["Title"], "event_date": row["EventDate"],
             "summary": row["Summary"], "category": row["Category"], "why_watch": row["WhyWatch"], "source_ids": source_ids,
             "exposures": exposures, "tickers": tickers, "uncertainty": row["Uncertainty"], "watch_next": row["WatchNext"],
             "status": row["Status"]}
    evidence = {s["SourceId"]: {"source_id": s["SourceId"], "url": s["Url"], "title": s["Title"],
                                "major_outlet": is_major_outlet(s["Url"]),
                                "published_at": s["PublishedAt"], "retrieved_at": s["RetrievedAt"],
                                "excerpt": s["Excerpt"]} for s in sources}
    return {"event_id": event_id, "event": event, "evidence": evidence, "as_of": row["AsOf"]}


def get_event(event_id, db=None):
    """One saved Event row as a dict, or None."""
    with connect(db) as con:
        row = con.execute("SELECT * FROM Event WHERE EventId = ?", [event_id]).fetchone()
    return dict(row) if row else None


def saved_events(as_of, limit=20, db=None):
    """Events assessed on or before as_of, newest first. Given to the lead so it can spot repeats."""
    with connect(db) as con:
        rows = con.execute("""
            SELECT e.EventId, e.Title, e.EventDate, max(r.AsOf) AS LastAssessedOn
            FROM Event e
            JOIN Assessment a ON a.EventId = e.EventId
            JOIN Run r ON r.RunId = a.RunId
            WHERE r.AsOf <= ?
            GROUP BY e.EventId
            ORDER BY LastAssessedOn DESC, e.EventId
            LIMIT ?""", [str(as_of), limit]).fetchall()
    return [dict(r) for r in rows]


def list_events(db=None):
    """Saved events, newest assessment first."""
    with connect(db) as con:
        rows = con.execute("""
            SELECT e.EventId AS event_id, max(r.AsOf) AS as_of, e.Title AS title,
                   count(a.AssessmentId) AS assessments
            FROM Event e
            JOIN Assessment a ON a.EventId = e.EventId
            JOIN Run r ON r.RunId = a.RunId
            GROUP BY e.EventId
            ORDER BY as_of DESC, e.EventId""").fetchall()
    return [dict(r) for r in rows]


def parse_day(value):
    return date.fromisoformat(value) if value else None
