# cli.py
"""Command line for the event agent: import data, run briefs and follow-ups, list saved events.

Patterns reused: trusted host runner (m4.2). Host code validates, saves and renders results.
Run:
    uv run cli.py --help
"""

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from langchain_core.callbacks import get_usage_metadata_callback
from langsmith import traceable

import events_db
import lead_agent
import research_tools
from brief_checks import validate_brief
from brief_report import render, render_html
from brief_schema import Brief
from jev_api import assess, classify, event_payload
from market_returns import listed, reactions
from models import MONTHLY_BUDGET_USD, ROOT, TIMEZONE, claude_cost_usd

OUTPUT = ROOT / "output"


# One LangSmith trace per brief. The searches, Jev and checks run outside the agent,
# so without this only the agent call appeared in LangSmith.
@traceable(name="Event brief", run_type="chain", process_inputs=lambda inputs: vars(inputs["args"]))
def run_brief(args):
    as_of = args.date
    if as_of > datetime.now(TIMEZONE).date():
        raise ValueError("Cannot research a future as-of date.")
    missing = [k for k in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY") if not os.getenv(k)]
    if missing:
        raise ValueError("Live agent requires " + ", ".join(missing)
                         + " in the environment or project .env. Offline commands remain available.")
    prior = events_db.load_event(args.event_id) if args.command == "followup" else None
    if prior and as_of < date.fromisoformat(prior["as_of"]):
        raise ValueError("Follow-up date precedes the saved assessment. Historical replay is not supported.")

    session = research_tools.SESSION = research_tools.ResearchSession(
        as_of, args.max_searches, since=date.fromisoformat(prior["as_of"]) if prior else as_of)
    if prior:
        session.evidence.update(prior["evidence"])
        question = f"What changed for saved event {prior['event_id']}: {prior['event']['title']}"
        queries = [prior["event"]["title"][:200]]
    else:
        question = args.question
        # Leave at least one search for the researcher.
        queries = research_tools.TOPIC_QUERIES[:max(1, args.max_searches - 1)]
    # The first sweep reads major outlets only. The researcher searches the open web.
    search_results = [session.search(q, major_outlets_only=not prior) for q in queries]
    # Fetch the day's GDELT leads if published. Missing or failed downloads leave the brief to Tavily.
    gdelt_refresh = events_db.refresh_gdelt(as_of)
    jev = classify(session.evidence) if args.jev else {"status": "disabled"}
    packet = {"question": question, "as_of": str(as_of), "timezone": TIMEZONE.key,
              "max_events": 1 if prior else args.limit,
              "gdelt": {**events_db.candidates(as_of), "refresh": gdelt_refresh["status"]},
              "saved_events": events_db.saved_events(as_of),
              "search_results": search_results, "jev": jev, "prior_event": prior}

    # Counts tokens for the lead and the researcher subagent, per model.
    with get_usage_metadata_callback() as usage_callback:
        result = lead_agent.agent.invoke({"messages": [{"role": "user", "content": json.dumps(packet)}]},
                                         config={"recursion_limit": 45, "max_concurrency": 1})
    output = result.get("structured_response")
    if output is None:
        raise ValueError("Agent returned no structured response; nothing was saved.")
    brief = Brief.model_validate(output)
    validate_brief(brief, session.evidence, as_of, packet["max_events"], prior)
    if prior:
        brief.events[0].tracked_event_id = prior["event_id"]
    unlisted = drop_unlisted_tickers(brief, as_of)
    # From the event text only, before any prices: Jev judges severity, each sector's direction vs SPY
    # and size of impact, and how directly each ticker is exposed. The top three tickers are kept in rank.
    jev_judgements = (assess([event_payload(e.model_dump(mode="json"), session.evidence) for e in brief.events])
                      if args.jev else {"status": "disabled", "answers": {}})
    usage = usage_rows(usage_callback.usage_metadata, jev, jev_judgements, session.calls)

    market = None
    if prior:
        original_date = prior["event"]["event_date"]
        tracked = {x["sector"] for x in prior["event"]["exposures"]} | {x.sector for x in brief.events[0].exposures}
        market = reactions(events_db.parse_day(original_date), sorted(tracked), as_of)
    gpr = events_db.gpr_context(as_of)
    run_id, ids = events_db.save_run(
        args.command, question, as_of, brief, session.evidence, session.calls, jev["status"],
        event_id=args.event_id if prior else None, market_metrics=market["metrics"] if market else (),
        judgements=jev_judgements, usage=usage)

    OUTPUT.mkdir(exist_ok=True)
    artifact = {"brief": brief.model_dump(mode="json"), "event_ids": ids, "sources": session.evidence,
                "jev": jev, "jev_judgements": jev_judgements, "unlisted_tickers": unlisted, "usage": usage,
                "gpr": gpr, "market": market, "search_calls": session.calls,
                "as_of": str(as_of), "timezone": TIMEZONE.key, "created_at": datetime.now(TIMEZONE).isoformat()}
    destination = OUTPUT / f"{as_of}-{run_id[:8]}"
    destination.with_suffix(".json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    destination.with_suffix(".md").write_text(
        render(brief, session.evidence, as_of, ids, market, gpr, jev_judgements), encoding="utf-8")
    destination.with_suffix(".html").write_text(
        render_html(brief, session.evidence, as_of, ids, market, gpr, jev_judgements), encoding="utf-8")
    print(f"Saved {destination.with_suffix('.md')} and {destination.with_suffix('.html').name}")
    cost = sum(u["cost_usd"] or 0 for u in usage)
    print(json.dumps({"event_ids": ids, "search_calls": session.calls, "jev": jev["status"],
                      "jev_judgements": jev_judgements["status"], "unlisted_tickers": unlisted,
                      "claude_cost_usd": round(cost, 4)}, indent=2))


def drop_unlisted_tickers(brief, as_of):
    """Remove tickers with no recent Yahoo Finance price and note it in the brief's limitations."""
    found, status = listed([t.symbol for e in brief.events for t in e.tickers], as_of)
    dropped = []
    for event in brief.events:
        dropped += [t.symbol for t in event.tickers if t.symbol not in found]
        event.tickers = [t for t in event.tickers if t.symbol in found]
    if dropped:
        brief.limitations.append(f"Dropped tickers with no recent price, likely not listed: {', '.join(dropped)}.")
    if status != "ok":
        brief.limitations.append(f"Ticker listing check {status}. Tickers are unchecked.")
    return dropped


def usage_rows(claude_usage, jev, jev_judgements, search_calls):
    """RunUsage rows: one per Claude model, one for Jev (both calls), one for Tavily."""
    rows = []
    for model_name, u in claude_usage.items():
        details = u.get("input_token_details") or {}
        rows.append({"service": "anthropic", "model": model_name, "calls": None,
                     "input_tokens": u.get("input_tokens"), "output_tokens": u.get("output_tokens"),
                     "cache_read_tokens": details.get("cache_read") or 0,
                     "cache_write_tokens": sum(details.get(k) or 0 for k in
                                               ("cache_creation", "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")),
                     "cost_usd": claude_cost_usd(model_name, u)})
    jev_usages = [(jev.get("result") or {}).get("usage") or {}, jev_judgements.get("usage") or {}]
    jev_calls = sum(1 for status in (jev.get("status"), jev_judgements.get("status")) if status == "ok")
    if jev_calls:
        rows.append({"service": "jev", "model": (jev.get("result") or {}).get("model") or jev_judgements.get("model", "jev"),
                     "calls": jev_calls, "input_tokens": sum(u.get("input_tokens", 0) for u in jev_usages),
                     "output_tokens": sum(u.get("output_tokens", 0) for u in jev_usages), "cost_usd": None})
    rows.append({"service": "tavily", "model": "search", "calls": search_calls, "cost_usd": None})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Event-first daily briefs and on-demand follow-ups.")
    subs = parser.add_subparsers(dest="command", required=True)
    imp = subs.add_parser("import-gdelt", help="Import a 58-column GDELT daily export into data/events.db.")
    imp.add_argument("path", type=Path)
    scan = subs.add_parser("candidates", help="Inspect coverage-ranked raw leads without an LLM.")
    scan.add_argument("--date", type=date.fromisoformat, required=True)
    scan.add_argument("--limit", type=int, default=12, choices=range(1, 31))
    gdelt = subs.add_parser("refresh-gdelt", help="Download and import the GDELT daily export for a date.")
    gdelt.add_argument("--date", type=date.fromisoformat, default=datetime.now(UTC).date() - timedelta(days=1),
                       help="Event date, default yesterday UTC (published about 07:00 UTC the next day)")
    subs.add_parser("refresh-gpr", help="Download the official daily GPR series into data/events.db.")
    subs.add_parser("list", help="List saved assessed events.")
    subs.add_parser("doctor", help="Check configuration without exposing secrets.")
    cost = subs.add_parser("cost", help="Claude spend for a month against the monthly budget.")
    cost.add_argument("--month", default=datetime.now(UTC).strftime("%Y-%m"), help="YYYY-MM, UTC")
    for command in ("brief", "followup"):
        cmd = subs.add_parser(command)
        cmd.add_argument("question" if command == "brief" else "event_id")
        cmd.add_argument("--date", type=date.fromisoformat, default=datetime.now(TIMEZONE).date())
        cmd.add_argument("--max-searches", type=int, choices=range(1, 11), default=8)
        cmd.add_argument("--jev", action=argparse.BooleanOptionalAction, default=True)
        if command == "brief":
            cmd.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    args = parser.parse_args(argv)
    today = datetime.now(TIMEZONE).date()
    try:
        if args.command == "import-gdelt":
            result = events_db.import_gdelt(args.path)
        elif args.command == "candidates":
            result = events_db.candidates(args.date, args.limit)
        elif args.command == "refresh-gdelt":
            result = events_db.refresh_gdelt(args.date)
        elif args.command == "refresh-gpr":
            result = events_db.refresh_gpr()
        elif args.command == "cost":
            result = events_db.cost_summary(args.month)
            result["budget_usd"] = MONTHLY_BUDGET_USD
            result["budget_used_pct"] = round(100 * result["claude_cost_usd"] / MONTHLY_BUDGET_USD, 1)
        elif args.command == "list":
            result = events_db.list_events()
        elif args.command == "doctor":
            keys = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "TYPESAFE_API_KEY")
            result = {"keys_configured": {k: bool(os.getenv(k)) for k in keys}, "timezone": TIMEZONE.key,
                      "database": str(events_db.DB_PATH), "gdelt": events_db.candidates(today, 1), "gpr": events_db.gpr_context(today)}
        else:
            run_brief(args)
            return
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
