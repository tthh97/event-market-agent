# digest.py
"""Combine the stored runs in a date range into one HTML page: daily theme dots, threads, market moves
and a daily log.

    uv run digest.py --from 2026-10-02 --to 2026-10-04

Reads data/events.db only: no GDELT, Jev, Tavily or price downloads. Uses the newest 'events' run per
day. One Claude call writes the headline, the themes, each story's theme and the threads, from the
stored summaries and claims. The page says that text is not checked by verify. Everything else
(stories, severity, claims, sources, tags, market moves) is copied from the database.
Writes output/market-events-<from>-to-<to>.html from templates/digest.html.
"""

import argparse
import json
import os
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

import db  # noqa: E402

TEMPLATE = ROOT / "templates" / "digest.html"
FOLDER = ROOT / "output"


class Theme(BaseModel):
    key: str = Field(description="Short lowercase id, e.g. 'oil'")
    name: str = Field(description="2-5 words, e.g. 'Oil reserves and diesel'")


class StoryTheme(BaseModel):
    story: str = Field(description="The story id as given, '<day>#<rank>'")
    theme: str = Field(description="One theme key")


class Thread(BaseModel):
    theme: str = Field(description="One theme key")
    days: str = Field(description="When it ran, e.g. '2 Oct to 4 Oct'")
    title: str
    paragraphs: list[str] = Field(description="1-2 short paragraphs, only facts from the summaries and claims given")


class Writing(BaseModel):
    headline: str = Field(description="Page title, 3-7 words")
    lead: str = Field(description="1-2 sentences: what the period was about")
    themes: list[Theme] = Field(description="2-6 themes. The last one has key 'other' and name 'Elsewhere'")
    story_themes: list[StoryTheme] = Field(description="One entry per story id")
    threads: list[Thread] = Field(description="One thread per theme that has stories")


PROMPT = """You group a period's news stories into themes and write short threads about them, for a page
that lists the stories day by day. Use only what the summaries and claims below say. Do not add facts,
numbers, causes or predictions. Give every story id exactly one theme. Write plain, short sentences.

Stories, by id '<day>#<rank>':
"""


def load(first, last):
    """[day] for the newest events run of each day in [first, last], in the template's shape."""
    with closing(db.connect()) as connection:
        connection.row_factory = lambda cursor, row: dict(zip([c[0] for c in cursor.description], row))
        runs = connection.execute(
            "SELECT * FROM run r WHERE kind = 'events' AND as_of BETWEEN ? AND ? AND created_at = "
            "(SELECT max(created_at) FROM run WHERE kind = 'events' AND as_of = r.as_of) ORDER BY as_of",
            (first, last)).fetchall()
        days = []
        for run in runs:
            key = (run["run_id"],)
            stories = connection.execute("SELECT * FROM story WHERE run_id = ? ORDER BY rank", key).fetchall()
            moves = connection.execute("SELECT * FROM market_move WHERE run_id = ? ORDER BY sector, country", key).fetchall()
            removed = connection.execute("SELECT count(*) AS n FROM rejection WHERE run_id = ?", key).fetchone()["n"]
            days.append({
                "day": run["as_of"], "window": run["price_window"], "removed": removed,
                "stories": [{
                    "id": f"{run['as_of']}#{s['rank']}", "title": s["title"], "sev": s["severity"], "sites": s["sites"],
                    "lead": s["url"], "summary": s["summary"] or "",
                    "claims": [{"text": c["text"], "url": c["source_url"], "date": c["source_date"],
                                "unverified": bool(c["unverified"]), "sector": c["sector"], "country": c["country"]}
                               for c in connection.execute("SELECT * FROM claim WHERE run_id = ? AND topic = ? "
                                                           "ORDER BY rowid", (run["run_id"], s["topic"]))
                               if c["source_url"].startswith(("https://", "http://"))]}
                    for s in stories],
                "market": [{"sector": m["sector"], "country": m["country"], "etf": m["etf"],
                            "benchmark": m["benchmark"], "move": f"{m['move']:+.1f}%",
                            "vs": f"{m['vs_benchmark']:+.1f} pts"} for m in moves]})
    return days


def write(days):
    """The model-written parts of the page."""
    from langchain.chat_models import init_chat_model

    brief = {s["id"]: {"title": s["title"], "summary": s["summary"], "claims": [c["text"] for c in s["claims"]]}
             for d in days for s in d["stories"]}
    model = init_chat_model(os.getenv("RESEARCH_MODEL", "anthropic:claude-sonnet-5-5"), timeout=180, max_tokens=8000)
    return model.with_structured_output(Writing, method="json_schema").invoke(PROMPT + json.dumps(brief, ensure_ascii=False, indent=1))


def page(first, last, days, writing):
    """The template filled with the stored days and the model's writing. A story or thread whose theme the
    model did not define goes under 'other'."""
    themes = [t for t in writing.themes if t.key != "other"] + [Theme(key="other", name="Elsewhere")]
    keys = {t.key for t in themes}
    story_theme = {st.story: st.theme for st in writing.story_themes}
    for day in days:
        for story in day["stories"]:
            story["theme"] = story_theme.get(story["id"]) if story_theme.get(story["id"]) in keys else "other"
    data = {"from": first, "to": last, "headline": writing.headline, "lead": writing.lead,
            "themes": [t.model_dump() for t in themes], "days": days,
            "threads": [{"t": th.theme if th.theme in keys else "other", "days": th.days, "h": th.title,
                         "p": th.paragraphs} for th in writing.threads]}
    script = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return TEMPLATE.read_text(encoding="utf-8").replace("/*DATA*/null", script)


def main():
    parser = argparse.ArgumentParser(description="One HTML page for the stored runs in a date range.")
    parser.add_argument("--from", dest="first", required=True, help="First day, YYYY-MM-DD")
    parser.add_argument("--to", dest="last", required=True, help="Last day, YYYY-MM-DD")
    args = parser.parse_args()
    days = load(args.first, args.last)
    if not days:
        raise SystemExit(f"No events runs stored from {args.first} to {args.last}.")
    path = FOLDER / f"market-events-{args.first}-to-{args.last}.html"
    path.write_text(page(args.first, args.last, days, write(days)), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
