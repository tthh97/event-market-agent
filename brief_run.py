"""One brief or follow-up run, from request to saved database rows.

Everything outside this process comes in through Adapters: news search, the lead agent, prices,
Jev, GDELT downloads and the database file. cli.py wires the live ones; tests pass stand-ins.
Patterns reused: trusted host runner (m4.2). Host code validates and saves; the agent only decides.
validate_brief replaces a live LLM verifier with deterministic checks: counts, dates, citations, event tracking.
Run: brief_run.run(Request(...), adapters), called by cli.py.
"""

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback
from langsmith import traceable

import events_db
import jev_api
from lead_agent import Brief
from market_returns import listed, reactions
from models import TIMEZONE, cache_tokens, claude_cost_usd
from point_in_time import known_by, parse_timestamp, today
from research_tools import TOPIC_QUERIES, ResearchSession, make_tools

# S&P 500 trackers are the benchmark, not an exposed name.
BENCHMARK_TICKERS = {"SPY", "VOO", "IVV", "SPLG"}

# A question about the week asks for the seven days ending on the requested date.
WEEK_QUESTION = re.compile(r"\b(weeks?|weekly|7 days|seven days)\b", re.IGNORECASE)


def days_asked(question):
    """Days a brief question covers: 7 when it asks about the week, else 1."""
    return 7 if WEEK_QUESTION.search(question or "") else 1


@dataclass
class Adapters:
    news: Callable | None  # research_tools.tavily_search: (query, **params) -> Tavily response dict
    agent: Callable  # lead_agent.build: tools -> agent with .invoke
    prices: Callable  # market_returns.yahoo_close: (tickers, start, end) -> close-price DataFrame
    jev: Callable | None  # jev_api.typesafe_call; None when Jev is not configured
    gdelt: Callable  # events_db.download_gdelt: day -> zipped export bytes, or None if not published
    db: Path


@dataclass
class Request:
    """A brief (question set) or a follow-up of a saved event (event_id set).

    A brief covers the `days` days ending on as_of. A follow-up covers the time since its saved assessment.
    """
    as_of: date
    question: str | None = None
    event_id: str | None = None
    days: int = 1
    limit: int = 5
    max_searches: int = 8
    jev: bool = True


@dataclass
class Result:
    run_id: str
    event_ids: list[str]
    as_of: date
    brief: Brief
    evidence: dict
    judgements: jev_api.Judgements
    unlisted: list[str]
    usage: list[dict]
    gpr: dict
    market: dict | None
    search_calls: int
    start: date  # first day of a brief's period; as_of for a one-day brief or a follow-up


@traceable(name="Event brief", run_type="chain",
           process_inputs=lambda i: {k: str(v) for k, v in vars(i["request"]).items()})
def run(request, adapters):
    """Research, decide, check and save one run. Raises ValueError and saves nothing if a check fails."""
    as_of, db = request.as_of, adapters.db
    if as_of > today():
        raise ValueError("Cannot research a future as-of date.")
    prior = events_db.load_event(request.event_id, db) if request.event_id else None
    if prior and as_of < date.fromisoformat(prior["as_of"]):
        raise ValueError("Follow-up date precedes the saved assessment. Historical replay is not supported.")

    start = as_of if prior else as_of - timedelta(days=request.days - 1)
    session = ResearchSession(as_of, request.max_searches, news=adapters.news,
                              since=date.fromisoformat(prior["as_of"]) if prior else start)
    if prior:
        session.evidence.update(prior["evidence"])
        question = f"What changed for saved event {prior['event_id']}: {prior['event']['title']}"
        queries = [prior["event"]["title"][:200]]
    else:
        question = request.question
        # Leave at least one search for the researcher.
        queries = TOPIC_QUERIES[:max(1, request.max_searches - 1)]
    # The first sweep reads major outlets only. The researcher searches the open web.
    search_results = [session.search(q, major_outlets_only=not prior) for q in queries]
    # Fetch each day's GDELT leads if published. Missing or failed downloads leave the brief to Tavily.
    days = [start + timedelta(days=i) for i in range((as_of - start).days + 1)]
    refresh = {str(day): events_db.refresh_gdelt(day, db, adapters.gdelt)["status"] for day in days}
    max_events = 1 if prior else request.limit
    packet = {"question": question, "as_of": str(as_of), "timezone": TIMEZONE.key, "max_events": max_events,
              "period": {"start": str(start), "end": str(as_of), "days": len(days)},
              "gdelt": {**events_db.candidates(as_of, db=db, since=start),
                        "refresh": refresh[str(as_of)] if len(days) == 1 else refresh},
              "saved_events": events_db.saved_events(as_of, limit=20, db=db),
              "search_results": search_results, "prior_event": prior}

    agent = adapters.agent(make_tools(session, db))
    # Counts tokens for the lead and the researcher subagent, per model.
    with get_usage_metadata_callback() as usage_callback:
        result = agent.invoke({"messages": [{"role": "user", "content": json.dumps(packet)}]},
                              config={"recursion_limit": 45, "max_concurrency": 1})
    output = result.get("structured_response")
    if output is None:
        raise ValueError("Agent returned no structured response; nothing was saved.")
    brief = Brief.model_validate(output)
    validate_brief(brief, session.evidence, as_of, max_events, request.event_id, db)
    unlisted = drop_unlisted_tickers(brief, as_of, adapters.prices)
    # From the event text only, before any prices: Jev judges severity, each sector's direction vs SPY
    # and size of impact, and how directly each ticker is exposed. The top three tickers are kept in rank.
    payloads = [jev_api.event_payload(e.model_dump(mode="json"), session.evidence) for e in brief.events]
    judgements = jev_api.assess(payloads, adapters.jev) if request.jev else jev_api.unscored(payloads)
    usage = usage_rows(usage_callback.usage_metadata, judgements, session.calls)

    market = None
    if prior:
        tracked = {x["sector"] for x in prior["event"]["exposures"]} | {x.sector for x in brief.events[0].exposures}
        market = reactions(events_db.parse_day(prior["event"]["event_date"]), sorted(tracked), as_of, adapters.prices)
    gpr = events_db.gpr_context(as_of, db)
    run_id, ids = events_db.save_run(
        "followup" if prior else "brief", question, as_of, brief, session.evidence, session.calls, judgements.status,
        judgements, market_metrics=market["metrics"] if market else (), usage=usage, db=db)
    return Result(run_id, ids, as_of, brief, session.evidence, judgements, unlisted, usage, gpr, market,
                  session.calls, start)


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


def drop_unlisted_tickers(brief, as_of, download):
    """Remove tickers with no recent price and note it in the brief's limitations."""
    found, status = listed([t.symbol for e in brief.events for t in e.tickers], as_of, download)
    dropped = []
    for event in brief.events:
        dropped += [t.symbol for t in event.tickers if t.symbol not in found]
        event.tickers = [t for t in event.tickers if t.symbol in found]
    if dropped:
        brief.limitations.append(f"Dropped tickers with no recent price, likely not listed: {', '.join(dropped)}.")
    if status != "ok":
        brief.limitations.append(f"Ticker listing check {status}. Tickers are unchecked.")
    return dropped


def usage_rows(claude_usage, judgements, search_calls):
    """RunUsage rows: one per Claude model, one for Jev, one for Tavily."""
    rows = []
    for model_name, u in claude_usage.items():
        read, write_5m, write_1h = cache_tokens(u)
        rows.append({"service": "anthropic", "model": model_name, "calls": None,
                     "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                     "cache_read_tokens": read, "cache_write_tokens": write_5m + write_1h,
                     "cost_usd": claude_cost_usd(model_name, u)})
    if judgements.status == "ok":
        rows.append({"service": "jev", "model": judgements.model, "calls": 1,
                     "input_tokens": judgements.usage.get("input_tokens", 0),
                     "output_tokens": judgements.usage.get("output_tokens", 0), "cost_usd": None})
    rows.append({"service": "tavily", "model": "search", "calls": search_calls, "cost_usd": None})
    return rows
