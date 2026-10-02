# output.py
"""output node: write the run as Markdown and as a standalone HTML page, and reply with the Markdown.
Plain Python, no LLM.

It also appends the run to data/events.db (db.py) and, for a new report, stores its findings in the
LangGraph store so later reports can recall them (research.py).
Severity numbers come from triage (Jev), dates from the research tool, and claims only from verify.
"""

from contextlib import closing
from datetime import datetime
from html import escape
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


STYLE = """
:root { --bg: #f7f6f2; --card: #fff; --text: #1d1d1b; --muted: #6b6a66; --line: #e3e1da; --accent: #1f5f8b;
        --warn: #9a6a00; --up: #2f7d4f; --down: #b3261e; --track: #ebe9e2; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151514; --card: #1f1f1d; --text: #ecebe6; --muted: #a3a29c; --line: #34332f; --accent: #7db4dc;
          --warn: #e0b453; --up: #74c393; --down: #f2877e; --track: #2c2b28; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.55 -apple-system, "Segoe UI", sans-serif; }
main { max-width: 820px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; } h2 { font-size: 20px; margin: 32px 0 10px; } h3 { font-size: 18px; margin: 0 0 6px; }
p, li { overflow-wrap: anywhere; } a { color: var(--accent); } .muted { color: var(--muted); font-size: 14px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; margin: 12px 0; }
.bar { height: 6px; border-radius: 3px; background: var(--track); margin: 6px 0 10px; max-width: 240px; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--accent); }
ul { padding-left: 20px; } li { margin: 6px 0; }
.tag { display: inline-block; border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; font-size: 12px;
       color: var(--muted); margin-left: 4px; }
.tag.warn { border-color: var(--warn); color: var(--warn); }
table { width: 100%; border-collapse: collapse; font-size: 15px; font-variant-numeric: tabular-nums; }
th, td { text-align: left; padding: 6px 8px; border-bottom: 1px solid var(--line); }
.up { color: var(--up); } .down { color: var(--down); }
details summary { cursor: pointer; color: var(--muted); }
"""


def link(url, label):
    """An <a> for http(s) URLs only, so a model-written URL cannot run script."""
    if url.startswith(("https://", "http://")):
        return f'<a href="{escape(url)}">{escape(label)}</a>'
    return escape(label)


def signed(value, unit):
    css = "up" if value > 0 else "down" if value < 0 else ""
    return f'<span class="{css}">{value:+.1f}{unit}</span>'


def html(state, followup):
    """The same content as markdown(), as one standalone page with no external files."""
    title = f"{'Follow-up' if followup else 'Events'} as of {state.as_of}"
    body = [f"<h1>{escape(title)}</h1>"]
    if not followup:
        body.append(f'<p class="muted">Triage: Jev {escape(state.jev_status)}. '
                    "Severity is 0 minor .. 3 severe, judged from GDELT data only.</p>")
    body.append(f'<p class="muted">Verify: {escape(state.verify_status)}.</p>')
    for i, finding in enumerate(state.findings):
        card = [f"<h3>{i + 1}. {escape(finding.topic)}</h3>"]
        if not followup and i < len(state.severe):
            story = state.severe[i]
            if story.severity is None:
                card.append('<p class="muted">Jev severity: not scored.</p>')
            else:
                card.append(f'<p class="muted">Jev severity: {story.severity:.2f} of 3</p>'
                            f'<div class="bar"><span style="width:{story.severity / 3:.0%}"></span></div>')
            card.append(f'<p class="muted">GDELT: {story.articles} articles on {story.sites} sites, '
                        f'{link(story.url, "lead article")}</p>')
        card.append(f"<p>{escape(finding.summary)}</p>")
        items = []
        for claim in finding.claims:
            tags = f'<span class="tag">{escape(claim.sector)}</span>' if claim.sector else ""
            if claim.unverified:
                tags += '<span class="tag warn" title="Jev was unsure the source supports it for this week">Unverified</span>'
            items.append(f"<li>{escape(claim.text)} ({link(claim.source_url, 'source')}, "
                         f"{escape(claim.source_date)}){tags}</li>")
        if items:
            card.append("<ul>" + "".join(items) + "</ul>")
        body.append('<section class="card">' + "".join(card) + "</section>")
    if state.price_window:
        body.append("<h2>Market reaction</h2>")
        if state.moves:
            rows = "".join(f"<tr><td>{escape(s)}</td><td>{SECTOR_ETFS[s]}</td><td>{signed(m, '%')}</td>"
                           f"<td>{signed(v, ' pts')}</td></tr>" for s, (m, v) in state.moves.items())
            body.append('<section class="card"><table><thead><tr><th>Sector</th><th>ETF</th><th>Move</th>'
                        f"<th>vs SPY</th></tr></thead><tbody>{rows}</tbody></table></section>")
        body.append(f'<p class="muted">{escape(state.price_window)}</p>')
    if state.rejected:
        removed = "".join(f"<li>{escape(r)}</li>" for r in state.rejected)
        body.append(f"<h2>Removed by verify</h2><details><summary>{len(state.rejected)} claims removed, and why"
                    f"</summary><ul>{removed}</ul></details>")
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1"><link rel="icon" href="data:,">'
            f"<title>{escape(title)}</title><style>{STYLE}</style></head>"
            f"<body><main>{''.join(body)}</main></body></html>\n")


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
    path.with_suffix(".html").write_text(html(state, followup), encoding="utf-8")
    log(name, thread, state, followup)
    if runtime.store is not None and not followup:
        remember(runtime.store, state.as_of, name, state.findings)
    return {"report": str(path), "messages": [AIMessage(text)]}
