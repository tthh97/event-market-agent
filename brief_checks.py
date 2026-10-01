"""Code checks on the lead's Brief before anything is saved: counts, dates, citations, event tracking.

Patterns reused: none; replaces a live LLM verifier with deterministic checks.
Run: called by brief_run.py.
"""

from langsmith import traceable

import events_db
from point_in_time import known_by, parse_timestamp

# S&P 500 trackers are the benchmark, not an exposed name.
BENCHMARK_TICKERS = {"SPY", "VOO", "IVV", "SPLG"}


def resolve_tracking(brief, followup_id=None, db=None):
    """Settle which saved event each brief event continues, and enforce the tracking rules.

    A follow-up is exactly one event, attached to followup_id. A brief event attaches only when the
    lead set tracked_event_id. Either way the saved event must exist, keep its onset date, and be
    used by at most one event. After this, tracked_event_id is the only link save_run needs.
    """
    if followup_id:
        if len(brief.events) != 1:
            raise ValueError("Follow-up must contain exactly the tracked event.")
        if brief.events[0].tracked_event_id not in (None, followup_id):
            raise ValueError("Follow-up returned a different saved event.")
        brief.events[0].tracked_event_id = followup_id
    tracked = [e.tracked_event_id for e in brief.events if e.tracked_event_id]
    if len(tracked) != len(set(tracked)):
        raise ValueError("Two events in one brief point at the same saved event.")
    for event in brief.events:
        if not event.tracked_event_id:
            continue
        saved = events_db.get_event(event.tracked_event_id, db)
        if saved is None:
            raise ValueError(f"Unknown tracked_event_id: {event.tracked_event_id}")
        if saved["EventDate"] and str(event.event_date) != saved["EventDate"]:
            raise ValueError("Tracked event changed the original event date.")


@traceable(name="Code checks", run_type="chain",
           process_inputs=lambda i: {"as_of": str(i["as_of"]), "limit": i["limit"], "events": len(i["brief"].events)})
def validate_brief(brief, evidence, as_of, limit, followup_id=None, db=None):
    if len(brief.events) > limit:
        raise ValueError("Agent exceeded the requested event count.")
    for event in brief.events:
        if event.event_date and event.event_date > as_of:
            raise ValueError("Event date is after the requested as-of date.")
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
            timestamp = parse_timestamp(evidence[source_id].get("published_at"))
            if timestamp is None or not known_by(timestamp, as_of):
                raise ValueError(f"Invalid publication date for {source_id}")
    resolve_tracking(brief, followup_id, db)
