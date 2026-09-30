# evals/scorecard.py
"""Score saved briefs: Jev's direction calls vs what sectors did, linked vs other sectors, your labels, cost.

Patterns reused: none from the course; offline eval over the Chinook-style database.
Run:
    uv run evals/scorecard.py --db data/backfill-2026-09-17-to-30.db --fill-directions
Writes evals/scorecard-<db>.md and .json, creates evals/labels-<db>.csv for you to fill in,
and records the scores in LangSmith as an "Eval scorecard" run when tracing is on.
"""

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yfinance as yf  # noqa: E402
from langsmith import traceable  # noqa: E402

import events_db  # noqa: E402
import jev_api  # noqa: E402
from market_returns import calculate_windows  # noqa: E402
from models import MONTHLY_BUDGET_USD, ROOT, SECTORS  # noqa: E402

EVALS = Path(__file__).resolve().parent
TICKER_SECTOR = {v: k for k, v in SECTORS.items()}
WINDOWS = {"D0_to_D1": "D+1", "D0_to_D5": "D+5"}


def first_assessments(con):
    """Each event's first assessment: the prediction made when the story first appeared."""
    return con.execute("""
        SELECT e.EventId, e.EventDate, e.FirstAssessedOn, a.AssessmentId, a.Title, a.Summary
        FROM Event e JOIN Assessment a ON a.AssessmentId = (
            SELECT a2.AssessmentId FROM Assessment a2 JOIN Run r2 USING (RunId)
            WHERE a2.EventId = e.EventId ORDER BY r2.CreatedAt, a2.AssessmentId LIMIT 1)
        ORDER BY e.FirstAssessedOn, e.EventId""").fetchall()


def exposures_of(con, assessment_id):
    return con.execute("""
        SELECT x.ExposureId, x.SectorId, x.Channel, x.Reasoning, x.Status, d.Direction, d.Confidence
        FROM Exposure x LEFT JOIN ExposureDirection d USING (ExposureId)
        WHERE x.AssessmentId = ? ORDER BY x.ExposureId""", [assessment_id]).fetchall()


def fill_directions(db, batch=8):
    """Ask Jev for missing directions on first assessments. Jev sees event text only, never prices."""
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    todo = []
    for a in first_assessments(con):
        xs = exposures_of(con, a["AssessmentId"])
        if not xs or all(x["Direction"] for x in xs):
            continue
        cited = [r[0] for r in con.execute("""
            SELECT SourceId FROM AssessmentSource WHERE AssessmentId = ?
            UNION SELECT es.SourceId FROM ExposureSource es JOIN Exposure x USING (ExposureId)
            WHERE x.AssessmentId = ?""", [a["AssessmentId"], a["AssessmentId"]])]
        excerpts = {r["SourceId"]: {"excerpt": r["Excerpt"]} for r in con.execute(
            f"SELECT SourceId, Excerpt FROM Source WHERE SourceId IN ({','.join('?' * len(cited))})", cited)}
        event = {"title": a["Title"], "summary": a["Summary"], "source_ids": cited,
                 "exposures": [{"sector": x["SectorId"], "channel": x["Channel"], "reasoning": x["Reasoning"],
                                "source_ids": []} for x in xs]}
        todo.append((jev_api.event_payload(event, excerpts), [x["ExposureId"] for x in xs]))
    con.close()
    saved = 0
    for start in range(0, len(todo), batch):
        chunk = todo[start:start + batch]
        result = jev_api.assess([payload for payload, _ in chunk], parts=("direction",))
        if result["status"] != "ok":
            raise RuntimeError(f"Jev directions failed: {result}")
        with events_db.connect(db) as con:
            for i, (_, exposure_ids) in enumerate(chunk):
                for j, exposure_id in enumerate(exposure_ids):
                    if f"e{i}_x{j}" in result["answers"]:
                        events_db.save_direction(con, exposure_id, result["answers"][f"e{i}_x{j}"], result["model"])
                        saved += 1
    return saved


def measure_from(event_date, first_assessed, window_start):
    """Event date when it falls just before first report; otherwise the first-report day."""
    first = date.fromisoformat(first_assessed)
    onset = date.fromisoformat(event_date) if event_date else None
    if onset and window_start - timedelta(days=3) <= onset <= first:
        return onset
    return first


def direction_hit(direction, excess):
    """True/False for an up/down call against the realized sector-minus-SPY move; None otherwise."""
    if direction not in ("up", "down") or excess is None or excess == 0:
        return None
    return (excess > 0) == (direction == "up")


def score(db, cutoff):
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    firsts = first_assessments(con)
    window_start = min(date.fromisoformat(a["FirstAssessedOn"]) for a in firsts)
    frame = yf.download(sorted(SECTORS.values()) + ["SPY"], start=str(window_start - timedelta(days=14)),
                        end=str(cutoff + timedelta(days=1)), auto_adjust=True, progress=False, threads=False)
    close = frame["Close"]

    calls = {w: [] for w in WINDOWS}
    magnitude = {w: [] for w in WINDOWS}
    unclear = scored_exposures = 0
    for a in firsts:
        start = measure_from(a["EventDate"], a["FirstAssessedOn"], window_start)
        by = defaultdict(dict)
        for r in calculate_windows(close, start, cutoff):
            by[r["window"]][TICKER_SECTOR[r["ticker"]]] = r["excess_percentage_points"]
        xs = exposures_of(con, a["AssessmentId"])
        linked = {x["SectorId"] for x in xs}
        for x in xs:
            scored_exposures += 1
            unclear += x["Direction"] == "unclear"
        for w in WINDOWS:
            if not by[w]:
                continue
            for x in xs:
                hit = direction_hit(x["Direction"], by[w].get(x["SectorId"]))
                if hit is not None:
                    calls[w].append({"event_id": a["EventId"], "sector": x["SectorId"], "direction": x["Direction"],
                                     "confidence": x["Confidence"], "excess_pp": by[w][x["SectorId"]], "hit": hit})
            ours = [abs(v) for s, v in by[w].items() if s in linked]
            rest = [abs(v) for s, v in by[w].items() if s not in linked]
            if ours and rest:
                magnitude[w].append((mean(ours), mean(rest)))

    directions = {}
    for w, rows in calls.items():
        if not rows:
            continue
        ups = sum(r["excess_pp"] > 0 for r in rows)
        confident = [r for r in rows if (r["confidence"] or 0) >= 0.7]
        directions[WINDOWS[w]] = {
            "calls": len(rows), "hits": sum(r["hit"] for r in rows),
            "hit_rate": round(sum(r["hit"] for r in rows) / len(rows), 3),
            # What you would score by always guessing the more common outcome in this sample.
            "majority_baseline": round(max(ups, len(rows) - ups) / len(rows), 3),
            "confident_calls": len(confident),
            "confident_hit_rate": round(sum(r["hit"] for r in confident) / len(confident), 3) if confident else None,
            "distinct_events": len({r["event_id"] for r in rows}),
        }
    linked_vs_other = {WINDOWS[w]: {"events": len(p), "linked_pp": round(mean(a for a, _ in p), 3),
                                    "others_pp": round(mean(b for _, b in p), 3),
                                    "linked_bigger": sum(a > b for a, b in p)}
                       for w, p in magnitude.items() if p}
    con.close()
    return {"directions": directions, "linked_vs_other": linked_vs_other,
            "exposures": scored_exposures, "unclear_calls": unclear, "calls_detail": calls}


def labels(db):
    """Create or read evals/labels-<db>.csv. You fill worth_watching (yes/no) and duplicate_of."""
    path = EVALS / f"labels-{Path(db).stem}.csv"
    con = sqlite3.connect(db)
    rows = con.execute("SELECT EventId, FirstAssessedOn, Title FROM Event ORDER BY FirstAssessedOn, EventId").fetchall()
    con.close()
    if not path.exists():
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["event_id", "first_reported", "title", "worth_watching", "duplicate_of", "note"])
            w.writerows([[r[0], r[1], r[2], "", "", ""] for r in rows])
    with path.open(encoding="utf-8") as f:
        filled = [r for r in csv.DictReader(f) if r["worth_watching"].strip().lower() in ("yes", "no")]
    yes = sum(r["worth_watching"].strip().lower() == "yes" for r in filled)
    return {"file": str(path.relative_to(ROOT)), "events": len(rows), "labelled": len(filled),
            "precision": round(yes / len(filled), 3) if filled else None,
            "duplicates_flagged": sum(bool(r["duplicate_of"].strip()) for r in filled)}


@traceable(name="Eval scorecard", run_type="chain")
def scorecard(db, cutoff):
    month = datetime.now(ZoneInfo("UTC")).strftime("%Y-%m")
    cost = events_db.cost_summary(month, db=db)
    cost["budget_usd"] = MONTHLY_BUDGET_USD
    result = {"database": str(Path(db).relative_to(ROOT)), "price_cutoff": str(cutoff),
              "scored_at": datetime.now(ZoneInfo("UTC")).isoformat(),
              **score(db, cutoff), "labels": labels(db), "cost": cost}
    return result


def write_markdown(r, path):
    d, m, lab, cost = r["directions"], r["linked_vs_other"], r["labels"], r["cost"]
    lines = [f"# Scorecard: {r['database']}", "",
             f"Scored {r['scored_at'][:16]} UTC. Prices to {r['price_cutoff']} (Yahoo Finance adjusted close).", "",
             "## Jev direction calls vs sector-minus-SPY", "",
             "| Window | Calls | Hit rate | Always-majority baseline | Calls with confidence >= 0.7 | Their hit rate | Events |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for w, s in d.items():
        conf = f"{s['confident_hit_rate']:.0%}" if s["confident_hit_rate"] is not None else "-"
        lines.append(f"| {w} | {s['calls']} | {s['hit_rate']:.0%} | {s['majority_baseline']:.0%} | "
                     f"{s['confident_calls']} | {conf} | {s['distinct_events']} |")
    lines += ["", f"{r['unclear_calls']} of {r['exposures']} exposures were called unclear and are not scored.", "",
              "## Linked sectors vs the rest (average size of move vs SPY)", "",
              "| Window | Events | Linked | Others | Linked moved more |", "|---|---:|---:|---:|---:|"]
    lines += [f"| {w} | {s['events']} | {s['linked_pp']:.2f} pp | {s['others_pp']:.2f} pp | {s['linked_bigger']} |"
              for w, s in m.items()]
    precision = f"{lab['precision']:.0%}" if lab["precision"] is not None else "not yet (no labels)"
    lines += ["", "## Your labels", "",
              f"{lab['labelled']} of {lab['events']} events labelled in `{lab['file']}`. "
              f"Share worth watching: {precision}. Duplicates flagged: {lab['duplicates_flagged']}.", "",
              "## Cost this month", "",
              f"{cost['runs']} runs, {cost['runs_with_cost_data']} with cost data. Claude spend "
              f"US${cost['claude_cost_usd']:.2f} of the US${cost['budget_usd']:.0f} budget.", "",
              "## Limits", "",
              "- Events overlap in time, so calls on the same day and sector are not independent.",
              "- A hit only means the sign matched. It says nothing about why the sector moved.",
              "- Directions for backfilled days were asked after the fact, from the saved event text only."]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--db", default=str(events_db.DB_PATH.relative_to(ROOT)))
    parser.add_argument("--fill-directions", action="store_true", help="Ask Jev for missing directions first.")
    args = parser.parse_args()
    db = ROOT / args.db
    with events_db.connect(db):
        pass  # applies schema.sql, which adds any newer tables to an older database file
    if args.fill_directions:
        print(f"Jev directions saved: {fill_directions(db)}")
    cutoff = datetime.now(ZoneInfo("America/New_York")).date() - timedelta(days=1)
    result = scorecard(db, cutoff)
    stem = Path(db).stem
    (EVALS / f"scorecard-{stem}.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    write_markdown(result, EVALS / f"scorecard-{stem}.md")
    print(json.dumps({k: result[k] for k in ("directions", "linked_vs_other", "labels")}, indent=2))


if __name__ == "__main__":
    main()
