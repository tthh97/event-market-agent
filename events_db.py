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

from models import MONTHLY_BUDGET_USD, ROOT, SECTORS, is_major_outlet

# EVENTS_DB points a run at another database file, e.g. a separate backfill.
DB_PATH = ROOT / os.getenv("EVENTS_DB", "data/events.db")
SCHEMA = (ROOT / "schema.sql").read_text(encoding="utf-8")



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


def source_record(source_id, url, title, published_at, retrieved_at, excerpt):
    """One citable source as the agents, the checks and the reports see it."""
    return {"source_id": source_id, "url": url, "title": title, "major_outlet": is_major_outlet(url),
            "published_at": published_at, "retrieved_at": retrieved_at, "excerpt": excerpt}


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


def download_gdelt(day):
    """The live GDELT adapter: the zipped daily export for one date, or None if not published yet."""
    response = httpx.get(GDELT_DAILY.format(day=f"{day:%Y%m%d}"), follow_redirects=True, timeout=120)
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.content


def refresh_gdelt(day, db=None, download=download_gdelt):
    """Download and import the official GDELT daily export for one date.

    The file for a date is published around 07:00 UTC the next day. Before that it is not found.
    """
    name = f"{day:%Y%m%d}.export.CSV"
    with connect(db) as con:
        if con.execute("SELECT 1 FROM DataImport WHERE Source = 'gdelt' AND FileName = ?", [name]).fetchone():
            return {"status": "already_imported", "file": name}
    try:
        content = download(day)
        if content is None:
            return {"status": "not_published", "file": name,
                    "note": "GDELT publishes a day's export around 07:00 UTC the next day."}
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            text = archive.read(archive.namelist()[0]).decode("utf-8")
    except (httpx.HTTPError, zipfile.BadZipFile) as exc:
        return {"status": "unavailable", "file": name, "error_type": type(exc).__name__}
    return {**import_gdelt_text(text, name, db), "source": GDELT_DAILY.format(day=f"{day:%Y%m%d}")}


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


def candidates(as_of, limit=12, db=None, since=None):
    """GDELT leads for event dates since..as_of (default as_of only), known by as_of. Rows sharing a URL are collapsed."""
    if not 1 <= limit <= 30:
        raise ValueError("limit must be 1 to 30")
    with connect(db) as con:
        rows = con.execute("""
            SELECT SourceUrl AS url, max(NumMentions) AS mentions, max(NumSources) AS source_count,
                   Actor1Name AS actor1, Actor2Name AS actor2, Place AS place,
                   group_concat(DISTINCT EventCode) AS cameo_codes, count(*) AS raw_rows
            FROM GdeltEvent
            WHERE EventDate BETWEEN ? AND ? AND AddedDate <= ? AND SourceUrl LIKE 'http%'
            GROUP BY SourceUrl
            ORDER BY mentions DESC, url
            LIMIT ?""", [str(since or as_of), str(as_of), str(as_of), limit]).fetchall()
        latest = con.execute("SELECT max(AddedDate) FROM GdeltEvent").fetchone()[0]
    period = {"requested_start_date": str(since)} if since and since != as_of else {}
    return {**period, "requested_event_date": str(as_of), "latest_imported_date": latest,
            "candidates": [dict(r) for r in rows],
            "warning": "Coverage-ranked research leads, not verified events or a complete daily news feed. "
                       "URL grouping is not semantic event deduplication."}


# Runs, events and assessments ------------------------------------------------


def save_run(kind, question, as_of, brief, evidence, search_calls, jev_status, judgements,
             market_metrics=(), usage=(), db=None):
    """Save one run. An event with tracked_event_id appends an Assessment to that saved Event.

    judgements is jev_api.Judgements, one EventJudgement per brief event, with tickers in rank order.
    usage is a list of RunUsage row dicts. market_metrics belong to a follow-up's single event.
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

        for event, judged in zip(brief.events, judgements.events, strict=True):
            key = event.tracked_event_id or uuid4().hex[:12]
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
            if severity := judged.severity:
                con.execute("INSERT INTO AssessmentSeverity VALUES (?, ?, ?, ?, ?, ?)",
                            [assessment_id, severity.score, severity.level, severity.confidence,
                             judgements.model, now_utc()])
            # Rank order, so unranked tickers also keep Jev's order by WatchTickerId.
            for judged_ticker in judged.tickers:
                t, fit = event.tickers[judged_ticker.index], judged_ticker.fit
                ticker_id = con.execute(
                    "INSERT INTO WatchTicker (AssessmentId, Ticker, Kind, Name, Reason, Rank, JevScore, JevLevel, "
                    "JevConfidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [assessment_id, t.symbol, t.kind, t.name, t.reason, judged_ticker.rank,
                     fit and fit.score, fit and fit.level, fit and fit.confidence]).lastrowid
                con.executemany("INSERT OR IGNORE INTO WatchTickerSource VALUES (?, ?)",
                                [(ticker_id, s) for s in t.source_ids])
            for exposure, judged_exposure in zip(event.exposures, judged.exposures, strict=True):
                exposure_id = con.execute(
                    "INSERT INTO Exposure (AssessmentId, SectorId, Channel, Reasoning, Status) VALUES (?, ?, ?, ?, ?)",
                    [assessment_id, exposure.sector, exposure.channel, exposure.reasoning, exposure.status]).lastrowid
                con.executemany("INSERT OR IGNORE INTO ExposureSource VALUES (?, ?)",
                                [(exposure_id, s) for s in exposure.source_ids])
                if judged_exposure.direction:
                    save_direction(con, exposure_id, judged_exposure.direction, judgements.model)
                if impact := judged_exposure.impact:
                    con.execute("INSERT INTO ExposureImpact VALUES (?, ?, ?, ?, ?, ?)",
                                [exposure_id, impact.score, impact.level, impact.confidence,
                                 judgements.model, now_utc()])
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


def save_direction(con, exposure_id, direction, model):
    """direction is a jev_api.Direction."""
    con.execute("INSERT OR REPLACE INTO ExposureDirection VALUES (?, ?, ?, ?, ?)",
                [exposure_id, direction.direction, direction.confidence, model, now_utc()])


def cost_summary(month, db=None):
    """Spend for one calendar month (YYYY-MM, by run creation time in UTC), against the monthly budget."""
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
                    "Runs that failed before saving, and runs before usage logging, are not included.",
            "budget_usd": MONTHLY_BUDGET_USD,
            "budget_used_pct": round(100 * round(total, 4) / MONTHLY_BUDGET_USD, 1)}


def load_event(event_id, db=None):
    """Latest assessment view of a saved event, plus every source any of its assessments cited."""
    with connect(db) as con:
        latest = con.execute("""
            SELECT a.AssessmentId, r.AsOf FROM Assessment a JOIN Run r USING (RunId)
            WHERE a.EventId = ? ORDER BY r.CreatedAt DESC, a.AssessmentId DESC LIMIT 1""", [event_id]).fetchone()
        if not latest:
            raise ValueError("Unknown saved event ID. Run list first.")
        ids = [r[0] for r in con.execute("SELECT AssessmentId FROM Assessment WHERE EventId = ?", [event_id])]
        cited = list(dict.fromkeys(s["source_id"] for i in ids for s in assessment_view(con, i)["sources"]))
        sources = con.execute(f"SELECT * FROM Source WHERE SourceId IN ({','.join('?' * len(cited))})", cited)
        evidence = {s["SourceId"]: source_record(s["SourceId"], s["Url"], s["Title"], s["PublishedAt"],
                                                 s["RetrievedAt"], s["Excerpt"]) for s in sources}
        event = assessment_view(con, latest["AssessmentId"])
    return {"event_id": event_id, "event": event, "evidence": evidence, "as_of": latest["AsOf"]}


def get_event(event_id, db=None):
    """One saved Event row as a dict, or None."""
    with connect(db) as con:
        row = con.execute("SELECT * FROM Event WHERE EventId = ?", [event_id]).fetchone()
    return dict(row) if row else None


def saved_events(as_of=None, limit=None, db=None):
    """Saved events, latest assessment first. With as_of, only assessments made on or before it count."""
    with connect(db) as con:
        rows = con.execute("""
            SELECT e.EventId, e.Title, e.EventDate, max(r.AsOf) AS LastAssessedOn, count(*) AS Assessments
            FROM Event e JOIN Assessment a USING (EventId) JOIN Run r USING (RunId)
            WHERE r.AsOf <= ?
            GROUP BY e.EventId
            ORDER BY LastAssessedOn DESC, e.EventId
            LIMIT ?""", [str(as_of or "9999-12-31"), limit or -1]).fetchall()
    return [dict(r) for r in rows]


def parse_day(value):
    return date.fromisoformat(value) if value else None


def assessment_view(con, assessment_id):
    """One saved assessment as every reader sees it: the event, Jev scores, exposures, tickers, sources.

    It also has the Brief Event fields, so a follow-up and Jev can read it directly.

    severity, impact, direction and fit are None without a Jev answer. tickers are in rank order,
    rank None beyond the top three. update is True when the event had an earlier assessment.
    """
    a = con.execute("""
        SELECT a.AssessmentId, a.EventId, a.Title, a.Category, a.Summary, a.WhyWatch, a.Uncertainty,
               a.WatchNext, a.Status, e.EventDate, s.Score, s.Level,
               EXISTS (SELECT 1 FROM Assessment p WHERE p.EventId = a.EventId
                       AND p.AssessmentId < a.AssessmentId) AS IsUpdate
        FROM Assessment a JOIN Event e USING (EventId)
        LEFT JOIN AssessmentSeverity s USING (AssessmentId)
        WHERE a.AssessmentId = ?""", [assessment_id]).fetchone()

    def level(score, name):
        return None if score is None else {"score": score, "level": name}

    exposures = []
    for x in con.execute("""
            SELECT x.ExposureId, x.SectorId, x.Channel, x.Reasoning, x.Status, i.Score, i.Level,
                   d.Direction, d.Confidence
            FROM Exposure x LEFT JOIN ExposureImpact i USING (ExposureId)
            LEFT JOIN ExposureDirection d USING (ExposureId)
            WHERE x.AssessmentId = ? ORDER BY x.ExposureId""", [assessment_id]):
        exposures.append({
            "exposure_id": x["ExposureId"], "sector": x["SectorId"], "channel": x["Channel"], "reasoning": x["Reasoning"], "status": x["Status"],
            "impact": level(x["Score"], x["Level"]),
            "direction": x["Direction"] and {"direction": x["Direction"], "confidence": x["Confidence"]},
            "source_ids": [r[0] for r in con.execute(
                "SELECT SourceId FROM ExposureSource WHERE ExposureId = ? ORDER BY rowid", [x["ExposureId"]])]})
    tickers = [{"symbol": t["Ticker"], "kind": t["Kind"], "name": t["Name"], "reason": t["Reason"],
                "rank": t["Rank"], "fit": level(t["JevScore"], t["JevLevel"]),
                "source_ids": [r[0] for r in con.execute("SELECT SourceId FROM WatchTickerSource WHERE WatchTickerId = ? "
                                                         "ORDER BY rowid", [t["WatchTickerId"]])]}
               for t in con.execute("SELECT * FROM WatchTicker WHERE AssessmentId = ? "
                                    "ORDER BY Rank IS NULL, Rank, WatchTickerId", [assessment_id])]
    # Cited sources, first-cited order: the event's own, then its exposures', then its tickers'.
    cited = con.execute("""
        SELECT SourceId FROM (
            SELECT SourceId, 0 AS part, rowid AS n FROM AssessmentSource WHERE AssessmentId = ?
            UNION ALL
            SELECT es.SourceId, 1, es.rowid FROM ExposureSource es JOIN Exposure x USING (ExposureId)
            WHERE x.AssessmentId = ?
            UNION ALL
            SELECT ts.SourceId, 2, ts.rowid FROM WatchTickerSource ts JOIN WatchTicker t USING (WatchTickerId)
            WHERE t.AssessmentId = ?)
        ORDER BY part, n""", [assessment_id] * 3).fetchall()
    sources = []
    for source_id in dict.fromkeys(r[0] for r in cited):
        src = con.execute("SELECT * FROM Source WHERE SourceId = ?", [source_id]).fetchone()
        sources.append({"source_id": source_id, "url": src["Url"], "title": src["Title"],
                        "published_at": src["PublishedAt"], "major_outlet": is_major_outlet(src["Url"])})
    return {"event_id": a["EventId"], "assessment_id": a["AssessmentId"], "title": a["Title"],
            "event_date": a["EventDate"], "summary": a["Summary"], "category": a["Category"],
            "why_watch": a["WhyWatch"], "uncertainty": a["Uncertainty"], "watch_next": a["WatchNext"],
            "status": a["Status"], "update": bool(a["IsUpdate"]), "severity": level(a["Score"], a["Level"]),
            "source_ids": [r[0] for r in con.execute(
                "SELECT SourceId FROM AssessmentSource WHERE AssessmentId = ? ORDER BY rowid", [assessment_id])],
            "exposures": exposures, "tickers": tickers, "sources": sources}


def run_view(run_id, db=None):
    """Every assessment saved by one run, highest Jev severity first, then the lead's order."""
    with connect(db) as con:
        ids = [r[0] for r in con.execute("""
            SELECT a.AssessmentId FROM Assessment a LEFT JOIN AssessmentSeverity s USING (AssessmentId)
            WHERE a.RunId = ? ORDER BY coalesce(s.Score, 0) DESC, a.AssessmentId""", [run_id])]
        return [assessment_view(con, i) for i in ids]


def digest(start, end, db=None):
    """Every event assessed between start and end: its latest assessment view plus severity per day.

    Severity, impact and tickers come from Jev and are empty for runs made without it.
    """
    with connect(db) as con:
        days = con.execute("""
            SELECT a.EventId, r.AsOf, a.AssessmentId, s.Score, s.Level
            FROM Assessment a JOIN Run r USING (RunId)
            LEFT JOIN AssessmentSeverity s USING (AssessmentId)
            WHERE r.AsOf BETWEEN ? AND ?
            ORDER BY a.EventId, r.AsOf, r.CreatedAt""", [str(start), str(end)]).fetchall()
        events = {}
        for d in days:
            item = events.setdefault(d["EventId"], {"days": {}})
            # A later run on the same day replaces the earlier one.
            item["days"][d["AsOf"]] = {"score": d["Score"], "level": d["Level"]}
            item["latest_assessment"] = d["AssessmentId"]
        stories = []
        for item in events.values():
            scores = [d["score"] for d in item["days"].values() if d["score"] is not None]
            stories.append({**assessment_view(con, item["latest_assessment"]), "days": item["days"],
                            "max_severity": max(scores) if scores else None,
                            "first_seen": min(item["days"]), "last_seen": max(item["days"])})
    return sorted(stories, key=lambda e: (-(e["max_severity"] or 0), -len(e["days"]), e["title"]))
