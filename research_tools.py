# research_tools.py
"""Tools the agents call: dated Tavily news search, source lookup, read-only SQL.

Patterns reused: custom @tool functions (m1.5).
Run: imported by lead_agent.py and researcher_subagent.py. cli.py sets SESSION per run.
"""

import hashlib
import os
from datetime import UTC, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from threading import Lock

from langchain_core.tools import tool
from langsmith import traceable
from tavily import TavilyClient

import events_db
from models import MAJOR_OUTLETS, TIMEZONE, is_major_outlet

# Research session: one per run, shared by the lead and the researcher.


def parse_published(value):
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            result = parsedate_to_datetime(str(value))
        except (ValueError, TypeError):
            return None
    return result.replace(tzinfo=UTC) if result.tzinfo is None else result


class ResearchSession:
    """Search budget and dated source registry. Only sources in `evidence` may be cited."""

    def __init__(self, as_of, max_calls=6, since=None):
        self.as_of = as_of
        self.since = since or as_of
        self.max_calls = max_calls
        self.calls = 0
        self.evidence = {}
        self.excluded = 0
        self.lock = Lock()

    def date_window(self):
        """Tavily date parameters. The date filter below still decides what is kept.

        A date range returns mostly older articles, so a window ending today uses
        time_range, which returns the newest. Past dates keep the date range.
        """
        today = datetime.now(TIMEZONE).date()
        if self.as_of == today:
            days = (today - self.since).days
            for name, span in (("day", 1), ("week", 7), ("month", 31)):
                if days < span:
                    return {"time_range": name}
        return {"start_date": (self.since - timedelta(days=1)).isoformat(),
                "end_date": (self.as_of + timedelta(days=1)).isoformat()}

    @traceable(name="Tavily search", run_type="tool",
               process_inputs=lambda i: {k: v for k, v in i.items() if k != "self"})
    def search(self, query, major_outlets_only=False):
        if not query.strip() or len(query) > 600:
            return {"error": "Provide a non-empty query of at most 600 characters."}
        if not os.getenv("TAVILY_API_KEY"):
            return {"error": "TAVILY_API_KEY is not configured."}
        with self.lock:
            if self.calls >= self.max_calls:
                return {"error": "Search-call limit reached; use existing evidence or report uncertainty."}
            self.calls += 1
        end_of_day = datetime.combine(self.as_of + timedelta(days=1), time.min, TIMEZONE)
        cutoff = min(datetime.now(UTC), end_of_day.astimezone(UTC))
        try:
            result = TavilyClient(api_key=os.environ["TAVILY_API_KEY"]).search(
                query=query, topic="news", search_depth="basic", max_results=10,
                include_answer=False, include_raw_content=False, timeout=30, **self.date_window(),
                **({"include_domains": MAJOR_OUTLETS} if major_outlets_only else {}))
        except Exception as exc:
            return {"error": f"Search unavailable ({type(exc).__name__}); do not infer missing facts."}
        found = []
        for item in result.get("results", []):
            timestamp = parse_published(item.get("published_date"))
            # Unknown publication dates do not satisfy temporal grounding.
            if timestamp is None or timestamp >= cutoff or timestamp.astimezone(TIMEZONE).date() < self.since:
                self.excluded += 1
                continue
            url = item.get("url", "")
            if not url.startswith(("https://", "http://")):
                continue
            source_id = "s_" + hashlib.sha256(url.encode()).hexdigest()[:12]
            record = {"source_id": source_id, "url": url, "title": item.get("title", ""),
                      "major_outlet": is_major_outlet(url),
                      "published_at": timestamp.isoformat(), "retrieved_at": datetime.now(UTC).isoformat(),
                      "excerpt": item.get("content", "")[:4500]}
            with self.lock:
                self.evidence[source_id] = record
            found.append(record)
        return {"sources": found, "calls_used": self.calls, "calls_limit": self.max_calls,
                "excluded_unknown_or_outside_dates": self.excluded,
                "note": "Search excerpts are evidence leads. Publication filtering does not prove an unrevised historical webpage."}


SESSION = None  # set by cli.py before each invoke

# Short topic queries for the first sweep. One long keyword query returned 0 of 6
# usable results in a live test; these returned 3 to 10 each.
TOPIC_QUERIES = [
    "economy and markets news",
    "government policy and regulation news",
    "war conflict and geopolitics news",
    "company and industry news",
    "disaster weather and outage news",
]


@tool
def research_news(query: str) -> dict:
    """Search dated news evidence for this run. Use to verify an event or sector exposure; cite returned source IDs.

    Use short, specific queries of under 10 words, such as "Raytheon AMRAAM contract". Search calls are capped.
    """
    if SESSION is None:
        return {"error": "Research session is not initialized by the runner."}
    return SESSION.search(query)


@tool
def read_sources(source_ids: list[str]) -> dict:
    """Read exact retrieved source excerpts by ID before citing them. Unknown IDs are reported, never fabricated."""
    if SESSION is None:
        return {"error": "Research session is not initialized."}
    if not source_ids or len(source_ids) > 20:
        return {"error": "Provide between 1 and 20 source IDs."}
    return {key: SESSION.evidence.get(key, {"error": "Unknown source ID"}) for key in source_ids}


@tool
def read_sql(query: str) -> str:
    """Run one read-only SELECT against data/events.db and return up to 50 rows.

    Use it to see earlier assessments of a story (Event, Assessment, Exposure), extra GDELT
    leads (GdeltEvent) or GPR history (GprDaily). Tables are PascalCase and singular.
    """
    if not query.lstrip().lower().startswith(("select", "with")):
        return "Error: only SELECT queries are allowed."
    try:
        columns, rows, truncated = events_db.read_only_query(query)
    except Exception as exc:
        return f"Error: {exc}"
    # Long text cells (article excerpts) are cut so one query cannot flood the context.
    lines = [" | ".join(columns)]
    lines += [" | ".join(str(v)[:300] for v in row) for row in rows]
    if truncated:
        lines.append("(more rows exist; add a WHERE clause or LIMIT)")
    return "\n".join(lines)
