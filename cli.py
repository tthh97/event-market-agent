"""Command line for the event agent: import data, run briefs and follow-ups, list saved events.

Patterns reused: trusted host runner (m4.2). brief_run.py runs the brief; this file wires the live
adapters, writes the reports and prints.
Run:
    uv run cli.py --help
"""

import argparse
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import brief_run
import events_db
import lead_agent
from brief_report import render_digest_html, save_reports
from jev_api import typesafe_call
from market_returns import yahoo_close
from models import ROOT, TIMEZONE
from point_in_time import today
from research_tools import tavily_search

OUTPUT = ROOT / "output"


def live_adapters():
    """Real Tavily, Claude, Yahoo, Jev, GDELT and the configured database. Jev only with its key."""
    missing = [k for k in ("ANTHROPIC_API_KEY", "TAVILY_API_KEY") if not os.getenv(k)]
    if missing:
        raise ValueError("Live agent requires " + ", ".join(missing)
                         + " in the environment or project .env. Offline commands remain available.")
    return brief_run.Adapters(news=tavily_search, agent=lead_agent.build, prices=yahoo_close,
                              jev=typesafe_call if os.getenv("TYPESAFE_API_KEY") else None,
                              gdelt=events_db.download_gdelt, db=events_db.DB_PATH)


def run_and_report(request, adapters):
    result = brief_run.run(request, adapters)
    report = save_reports(result, OUTPUT, adapters.db)
    print(f"Saved {report} and {report.with_suffix('.json').name}")
    cost = sum(u["cost_usd"] or 0 for u in result.usage)
    print(json.dumps({"event_ids": result.event_ids, "search_calls": result.search_calls,
                      "jev": result.judgements.status,
                      "unlisted_tickers": result.unlisted, "claude_cost_usd": round(cost, 4)}, indent=2))


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
    dig = subs.add_parser("digest", help="HTML page of the stories to be aware of over a date range.")
    dig.add_argument("--start", type=date.fromisoformat, required=True)
    dig.add_argument("--end", type=date.fromisoformat, required=True)
    subs.add_parser("doctor", help="Check configuration without exposing secrets.")
    cost = subs.add_parser("cost", help="Claude spend for a month against the monthly budget.")
    cost.add_argument("--month", default=datetime.now(UTC).strftime("%Y-%m"), help="YYYY-MM, UTC")
    for command in ("brief", "followup"):
        cmd = subs.add_parser(command)
        cmd.add_argument("question" if command == "brief" else "event_id")
        cmd.add_argument("--date", type=date.fromisoformat, default=today())
        cmd.add_argument("--max-searches", type=int, choices=range(1, 11), default=8)
        cmd.add_argument("--jev", action=argparse.BooleanOptionalAction, default=True)
        if command == "brief":
            cmd.add_argument("--limit", type=int, choices=range(1, 6), default=5)
    args = parser.parse_args(argv)
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
        elif args.command == "digest":
            OUTPUT.mkdir(exist_ok=True)
            path = OUTPUT / f"digest-{args.start}-to-{args.end}.html"
            path.write_text(render_digest_html(events_db.digest(args.start, args.end), args.start, args.end),
                            encoding="utf-8")
            result = {"saved": str(path)}
        elif args.command == "list":
            result = events_db.saved_events()
        elif args.command == "doctor":
            keys = ("ANTHROPIC_API_KEY", "TAVILY_API_KEY", "TYPESAFE_API_KEY")
            result = {"keys_configured": {k: bool(os.getenv(k)) for k in keys}, "timezone": TIMEZONE.key,
                      "database": str(events_db.DB_PATH), "gdelt": events_db.candidates(today(), 1), "gpr": events_db.gpr_context(today())}
        else:
            request = brief_run.Request(
                as_of=args.date, max_searches=args.max_searches, jev=args.jev,
                **({"question": args.question, "limit": args.limit} if args.command == "brief"
                   else {"event_id": args.event_id}))
            run_and_report(request, live_adapters())
            return
        print(json.dumps(result, indent=2, default=str))
    except (ValueError, FileNotFoundError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
