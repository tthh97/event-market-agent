# brief_checks.py
"""Code checks on the lead's Brief before anything is saved: counts, dates, citations, event IDs.

Patterns reused: none; replaces a live LLM verifier with deterministic checks.
Run: called by cli.py.
"""

from langsmith import traceable

import events_db
from models import TIMEZONE
from research_tools import parse_published

# S&P 500 trackers are the benchmark, not an exposed name.
BENCHMARK_TICKERS = {"SPY", "VOO", "IVV", "SPLG"}


@traceable(name="Code checks", run_type="chain",
           process_inputs=lambda i: {"as_of": str(i["as_of"]), "limit": i["limit"], "events": len(i["brief"].events)})
def validate_brief(brief, evidence, as_of, limit, prior=None):
    if len(brief.events) > limit:
        raise ValueError("Agent exceeded the requested event count.")
    if prior and len(brief.events) != 1:
        raise ValueError("Follow-up must contain exactly the tracked event.")
    tracked = []
    for event in brief.events:
        if event.event_date and event.event_date > as_of:
            raise ValueError("Event date is after the requested as-of date.")
        if prior:
            if event.tracked_event_id not in (None, prior["event_id"]):
                raise ValueError("Follow-up returned a different saved event.")
            saved_date = prior["event"]["event_date"]
        elif event.tracked_event_id:
            saved = events_db.get_event(event.tracked_event_id)
            if saved is None:
                raise ValueError(f"Unknown tracked_event_id: {event.tracked_event_id}")
            tracked.append(event.tracked_event_id)
            saved_date = saved["EventDate"]
        else:
            saved_date = None
        if saved_date and str(event.event_date) != str(saved_date):
            raise ValueError("Tracked event changed the original event date.")
        symbols = [t.symbol for t in event.tickers]
        if len(symbols) != len(set(symbols)):
            raise ValueError(f"Duplicate ticker in event: {event.title}")
        if BENCHMARK_TICKERS & set(symbols):
            raise ValueError(f"Benchmark ticker listed as exposed: {sorted(BENCHMARK_TICKERS & set(symbols))}")
        used = set(event.source_ids)
        for item in [*event.exposures, *event.tickers]:
            used.update(item.source_ids)
        for source_id in used:
            if source_id not in evidence:
                raise ValueError(f"Unknown citation ID: {source_id}")
            timestamp = parse_published(evidence[source_id].get("published_at"))
            if timestamp is None or timestamp.astimezone(TIMEZONE).date() > as_of:
                raise ValueError(f"Invalid publication date for {source_id}")
    if len(tracked) != len(set(tracked)):
        raise ValueError("Two events in one brief point at the same saved event.")
