# output.py
"""output node: write the run as Markdown, and reply with the Markdown. Plain Python, no LLM.

It also appends the run to data/events.db (db.py) and, for a new report, stores its findings in the
LangGraph store so later reports can recall them (research.py).
Severity numbers come from triage (Jev), dates from the research tool, and claims only from verify.
"""

from contextlib import closing
from datetime import datetime
from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

import db
from prices import SECTOR_ETFS
from research import remember
from state import State

FOLDER = Path(__file__).resolve().parent / "output"


def markdown(state, followup):
    lines = [f"# {'Follow-up' if followup else 'Events'} as of {state.as_of}", ""]
    if not followup:
        lines += [f"Triage: Jev {state.jev_status}. Severity is 0 minor .. 3 severe, judged from GDELT data only.", ""]
    lines += [f"Verify: {state.verify_status}.", ""]
    for i, finding in enumerate(state.findings):
        lines.append(f"## {i + 1}. {finding.topic}")
        if not followup and i < len(state.severe):
            story = state.severe[i]
            score = "not scored" if story.severity is None else f"{story.severity:.2f} of 3"
            lines.append(f"Jev severity: {score}. GDELT: {story.articles} articles on {story.sites} sites, lead {story.url}")
        lines += ["", finding.summary, ""]
        for claim in finding.claims:
            sector = f" Sector: {claim.sector}." if claim.sector else ""
            flag = " Unverified: Jev was unsure the source supports it for this week." if claim.unverified else ""
            lines.append(f"- {claim.text} ([source]({claim.source_url}), {claim.source_date}).{sector}{flag}")
        lines.append("")
    if state.price_window:
        lines += ["## Market reaction", ""]
        if state.moves:
            lines += ["| Sector | ETF | Move | vs SPY |", "|---|---|---|---|"]
            lines += [f"| {s} | {SECTOR_ETFS[s]} | {m:+.1f}% | {v:+.1f} pts |" for s, (m, v) in state.moves.items()] + [""]
        lines += [state.price_window, ""]
    if state.rejected:
        lines += ["## Removed by verify", ""] + [f"- {r}" for r in state.rejected] + [""]
    return "\n".join(lines)


def log(run_id, thread_id, state, followup):
    """Append the run, its stories, kept and removed claims, and price moves to events.db."""
    with closing(db.connect()) as connection, connection:
        connection.execute("INSERT INTO run (run_id, thread_id, as_of, kind, jev_status, verify_status, price_window) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                           (run_id, thread_id, state.as_of, "followup" if followup else "events", state.jev_status,
                            state.verify_status, state.price_window))
        if not followup:
            connection.executemany("INSERT INTO story VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                                   [(run_id, i + 1, s.title, s.url, s.severity, s.articles, s.sites)
                                    for i, s in enumerate(state.severe)])
        connection.executemany("INSERT INTO claim VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                               [(run_id, f.topic, c.text, c.source_url, c.source_date, c.sector, c.unverified)
                                for f in state.findings for c in f.claims])
        connection.executemany("INSERT INTO rejection VALUES (?, ?) ON CONFLICT DO NOTHING",
                               [(run_id, line) for line in state.rejected])
        connection.executemany("INSERT INTO price_move VALUES (?, ?, ?, ?) ON CONFLICT DO NOTHING",
                               [(run_id, sector, move, vs_spy) for sector, (move, vs_spy) in state.moves.items()])


def output(state: State, config: RunnableConfig, runtime: Runtime) -> dict:
    """Reads everything above. Writes report, adds the reply to messages, and logs the run."""
    followup = state.report is not None
    text = markdown(state, followup)
    FOLDER.mkdir(exist_ok=True)
    thread = config["configurable"].get("thread_id", "run")
    name = f"{state.as_of}-{thread}-{datetime.now():%H%M%S}"
    path = FOLDER / f"{name}.md"
    path.write_text(text, encoding="utf-8")
    log(name, thread, state, followup)
    if runtime.store is not None and not followup:
        remember(runtime.store, state.as_of, name, state.findings)
    return {"report": str(path), "messages": [AIMessage(text)]}
