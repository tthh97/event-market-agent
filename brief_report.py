# brief_report.py
"""Render a validated Brief, market windows and GPR context as Markdown and as a standalone HTML page.

Patterns reused: host-side output (m4.2).
Run: called by cli.py.
"""

from html import escape

import events_db
from jev_api import ranked_tickers
from models import TIMEZONE

# Printed under every report, after the agent's own limitations.
FIXED_LIMITS = [
    "Source-ID and date validation does not establish that every narrative claim is correct.",
    "Historical publication filtering cannot reconstruct revised web pages or provide a full point-in-time backtest.",
    "A repeat story is attached to its saved event only when the lead matches it. "
    "Matching is judged by the model and not guaranteed. Use followup with an event ID for a guaranteed link.",
    "Severity, sector impact, direction and ticker ranks are Jev judgements from the event text, not measured "
    "market impact. On 17-30 Sep Jev's direction calls scored below an always-majority baseline.",
    "Tickers are named by the lead agent from the sources. They are not a screen of every exposed company, "
    "and not buy or sell signals.",
]


def answers_of(judgements):
    return (judgements or {}).get("answers", {})


def direction_text(judgements, i, j):
    a = answers_of(judgements).get(f"e{i}_x{j}")
    if not a:
        return ""
    conf = f", {a['confidence']:.2f}" if a.get("confidence") is not None else ""
    return f"{a['direction']}{conf}"


def score_text(judgement):
    """e.g. "high 2.1/3", or "" without a Jev answer."""
    return f"{judgement['level']} {judgement['score']:.1f}/3" if judgement else ""


def watch_order(brief, judgements):
    """Event indexes by Jev severity, highest first. Without severity the lead's order stands."""
    answers = answers_of(judgements)
    return sorted(range(len(brief.events)),
                  key=lambda i: -(answers.get(f"e{i}_severity") or {}).get("score", 0))


def top_tickers(event, i, judgements):
    return [t for t in ranked_tickers(event.model_dump(mode="json"), i, answers_of(judgements)) if t["rank"]]


def render(brief, evidence, as_of, ids, market=None, gpr=None, judgements=None):
    lines = [f"# Event brief: {as_of}", "",
             f"Day boundaries: {TIMEZONE.key}. Sector exposures are assessments, not price predictions.", ""]
    if not brief.events:
        lines += ["No sufficiently supported events were selected.", ""]
    else:
        lines += ["Events are ordered by Jev severity, highest first.", ""]
    for i in watch_order(brief, judgements):
        event, event_id = brief.events[i], ids[i]
        label = "Update to saved event" if event.tracked_event_id else "New saved event"
        lines += [f"## {event.title}", "",
                  f"{label}: `{event_id}` | Status: {event.status} | Event date: {event.event_date or 'unknown'}", "",
                  event.summary, "", f"**Why watch:** {event.why_watch}", ""]
        severity = score_text(answers_of(judgements).get(f"e{i}_severity"))
        if severity:
            lines += [f"**Jev severity:** {severity}", ""]
        lines += ["**Potential sector exposure**", ""]
        for j, exposure in enumerate(event.exposures):
            citations = ", ".join(f"[{s}]({evidence[s]['url']})" for s in exposure.source_ids)
            jev = direction_text(judgements, i, j)
            impact = score_text(answers_of(judgements).get(f"e{i}_x{j}_impact"))
            lines.append(f"- **{exposure.sector.replace('_', ' ')}** ({exposure.status}): "
                         f"{exposure.channel}. {exposure.reasoning} {citations}"
                         + (f" Jev impact: {impact}." if impact else "")
                         + (f" Jev expects vs SPY: {jev}." if jev else ""))
        if not event.exposures:
            lines.append("No sector exposure established from the available evidence.")
        lines += ["", "**Top tickers to watch**", ""]
        for t in top_tickers(event, i, judgements):
            fit = score_text(t["jev"])
            lines.append(f"{t['rank']}. **{t['symbol']}** ({t['kind']}, {t['name']}): {t['reason']}"
                         + (f" Jev exposure: {fit}." if fit else ""))
        if not event.tickers:
            lines.append("No directly exposed stock or ETF named.")
        lines += ["", f"**Uncertainty:** {event.uncertainty}", "", f"**Watch next:** {event.watch_next}", "",
                  "**Sources**", ""]
        for sid in cited_sources(event):
            src = evidence[sid]
            lines.append(f"- [{src['title']}]({src['url']}) - published {src['published_at']} ({sid})")
        lines.append("")
    if market:
        lines += ["## Observed market reaction", "", f"Status: {market['status']}", ""]
        if market.get("metrics"):
            lines += ["| ETF | Window | Baseline | End | Return | SPY | Difference |",
                      "|---|---|---|---|---:|---:|---:|"]
            for row in market["metrics"]:
                lines.append(f"| {row['ticker']} | {row['window']} | {row['baseline_date']} | {row['end_session']} | "
                             f"{row['return_pct']:.2f}% | {row['spy_return_pct']:.2f}% | {row['excess_percentage_points']:.2f} pp |")
        lines += ["", market.get("timing", ""), "", market.get("limitation", ""), "",
                  "Price source: Yahoo Finance via yfinance. Raw metrics and retrieval time are saved in the companion JSON.", ""]
    if gpr:
        lines += ["## GPR context", "", f"Status: {gpr['status']}", "", gpr.get("note", ""), ""]
        if gpr.get("latest"):
            lines += [f"Latest eligible observation: {gpr['latest']['date']}; GPRD: {gpr['latest']['gprd']:.2f}.", ""]
        lines += [f"[Official GPR source]({events_db.GPR_SOURCE}) - Caldara and Iacoviello.", ""]
    lines += ["## Limits", ""] + [f"- {x}" for x in brief.limitations]
    lines += [f"- {x}" for x in FIXED_LIMITS]
    return "\n".join(lines) + "\n"


def cited_sources(event):
    """Source IDs cited by an event, its exposures and its tickers, in first-cited order."""
    return list(dict.fromkeys(event.source_ids + [s for x in [*event.exposures, *event.tickers] for s in x.source_ids]))


HTML_STYLE = """
:root { --bg: #f7f6f2; --card: #ffffff; --text: #1d1d1b; --muted: #6b6a66; --line: #e3e1da;
        --accent: #1f5f8b; --new: #2f7d4f; --update: #1f5f8b; --warn: #9a6a00; --severe: #b3261e; --track: #ebe9e2; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #151514; --card: #1f1f1d; --text: #ecebe6; --muted: #a3a29c; --line: #34332f;
          --accent: #7db4dc; --new: #74c393; --update: #7db4dc; --warn: #e0b453; --severe: #f2877e; --track: #2c2b28; } }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 16px/1.55 -apple-system, "Segoe UI", sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 28px; margin: 0 0 4px; } h2 { font-size: 20px; margin: 36px 0 12px; }
h3 { font-size: 18px; margin: 4px 0 8px; } p { margin: 6px 0; overflow-wrap: anywhere; }
a { color: var(--accent); } .muted { color: var(--muted); font-size: 14px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 18px 20px; margin: 12px 0; }
.watch { counter-reset: n; list-style: none; padding: 0; margin: 0; }
.watch > li { counter-increment: n; position: relative; padding: 14px 16px 14px 56px; }
.watch > li::before { content: counter(n); position: absolute; left: 16px; top: 14px; width: 28px; height: 28px;
  border-radius: 50%; background: var(--accent); color: var(--card); font-weight: 700; text-align: center; line-height: 28px; }
.tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 4px 0; }
.tag { border: 1px solid var(--line); border-radius: 999px; padding: 1px 10px; font-size: 13px; color: var(--muted); }
.tag.new { border-color: var(--new); color: var(--new); } .tag.update { border-color: var(--update); color: var(--update); }
.tag.hyp { border-color: var(--warn); color: var(--warn); }
table { width: 100%; border-collapse: collapse; font-size: 14px; } th, td { text-align: left; padding: 6px 8px;
  border-bottom: 1px solid var(--line); vertical-align: top; overflow-wrap: break-word; }
.table-wrap { overflow-x: auto; } .table-wrap table { min-width: 640px; } ul { padding-left: 20px; } li { margin: 3px 0; }
code { font: 13px ui-monospace, Menlo, monospace; }
.metrics { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin: 10px 0 8px; }
@media (max-width: 640px) { .metrics { grid-template-columns: 1fr; } }
.metric { border-top: 1px solid var(--line); padding-top: 8px; min-width: 0; }
.metric .label { font-size: 12px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); }
.metric .value { font-weight: 600; font-variant-numeric: tabular-nums; }
.bar { height: 6px; border-radius: 3px; background: var(--track); margin-top: 6px; overflow: hidden; }
.bar span { display: block; height: 100%; border-radius: 3px; }
.lvl-minor, .lvl-negligible, .lvl-none { color: var(--muted); } .bar .lvl-minor { background: var(--muted); }
.lvl-moderate, .lvl-small, .lvl-indirect { color: var(--accent); } .bar .lvl-moderate { background: var(--accent); }
.lvl-high, .lvl-material, .lvl-direct { color: var(--warn); } .bar .lvl-high { background: var(--warn); }
.lvl-severe, .lvl-large, .lvl-core { color: var(--severe); } .bar .lvl-severe { background: var(--severe); }
.pills { list-style: none; padding: 0; margin: 4px 0 0; } .pills li { margin: 2px 0; font-size: 14px; }
.dir { white-space: nowrap; }
.ticker { font: 600 13px ui-monospace, Menlo, monospace; }
"""


MAJOR_TAG = ' <span class="tag update">major outlet</span>'


def render_html(brief, evidence, as_of, ids, market=None, gpr=None, judgements=None):
    """The same report as render(), as one self-contained HTML page with a scored watch list first."""
    e = escape
    answers = answers_of(judgements)
    order = watch_order(brief, judgements)

    def level_span(judgement):
        if not judgement:
            return '<span class="muted">no Jev score</span>'
        return (f'<span class="lvl-{e(judgement["level"])}">{e(judgement["level"])}</span> '
                f'<span class="muted">{judgement["score"]:.1f}/3</span>')

    def sector_list(i, event):
        items = []
        for j, x in enumerate(event.exposures):
            direction = (answers.get(f"e{i}_x{j}") or {}).get("direction")
            arrow = {"up": " &#9650; vs SPY", "down": " &#9660; vs SPY"}.get(direction, "")
            hyp = ' <span class="muted">(hypothesis)</span>' if x.status == "hypothesis" else ""
            items.append(f'<li>{e(x.sector.replace("_", " ").title())}{hyp}: '
                         f'{level_span(answers.get(f"e{i}_x{j}_impact"))}<span class="muted dir">{arrow}</span></li>')
        return f'<ul class="pills">{"".join(items)}</ul>' if items else '<p class="muted">None established</p>'

    def ticker_list(i, event):
        items = [f'<li><span class="ticker">{e(t["symbol"])}</span> <span class="muted">{e(t["kind"])}</span> '
                 f'{level_span(t["jev"])}</li>' for t in top_tickers(event, i, judgements)]
        return f'<ul class="pills">{"".join(items)}</ul>' if items else '<p class="muted">None named</p>'

    def kind_tag(event):
        return '<span class="tag update">Update</span>' if event.tracked_event_id else '<span class="tag new">New</span>'

    watch = []
    for i in order:
        event, event_id = brief.events[i], ids[i]
        severity = answers.get(f"e{i}_severity")
        bar = (f'<div class="bar"><span class="lvl-{e(severity["level"])}" '
               f'style="width:{max(4, severity["score"] / 3 * 100):.0f}%"></span></div>') if severity else ""
        watch.append(
            f'<li class="card"><h3><a href="#{e(event_id)}">{e(event.title)}</a></h3>'
            f'<div class="tags">{kind_tag(event)}<span class="tag">{e(event.status)}</span>'
            f'<span class="tag">{e(event.category)}</span></div>'
            f'<div class="metrics"><div class="metric"><div class="label">Severity</div>'
            f'<div class="value">{level_span(severity)}</div>{bar}</div>'
            f'<div class="metric"><div class="label">Sector impact</div>{sector_list(i, event)}</div>'
            f'<div class="metric"><div class="label">Top tickers</div>{ticker_list(i, event)}</div></div>'
            f'<p>{e(event.why_watch)}</p><p class="muted"><b>Check next:</b> {e(event.watch_next)}</p></li>')
    watch = "".join(watch) or '<div class="card">No sufficiently supported events were selected.</div>'

    details = []
    for i in order:
        event, event_id = brief.events[i], ids[i]
        rows = "".join(
            f"<tr><td>{e(x.sector.replace('_', ' ').title())}</td><td>{e(x.channel)}</td>"
            f"<td>{'reported' if x.status == 'reported_exposure' else 'hypothesis'}</td>"
            f"<td>{e(score_text(answers.get(f'e{i}_x{j}_impact'))) or '-'}</td>"
            f"<td>{e(direction_text(judgements, i, j)) or '-'}</td><td>{e(x.reasoning)}</td></tr>"
            for j, x in enumerate(event.exposures)) or '<tr><td colspan="6">No sector exposure established.</td></tr>'
        tickers = "".join(
            f"<tr><td>{t['rank'] or '-'}</td><td><span class=\"ticker\">{e(t['symbol'])}</span></td><td>{e(t['name'])}</td>"
            f"<td>{e(t['kind'])}</td><td>{e(score_text(t['jev'])) or '-'}</td><td>{e(t['reason'])}</td></tr>"
            for t in ranked_tickers(event.model_dump(mode="json"), i, answers)
        ) or '<tr><td colspan="6">No directly exposed stock or ETF named.</td></tr>'
        sources = "".join(
            f'<li><a href="{e(evidence[s]["url"])}" target="_blank" rel="noopener">{e(evidence[s]["title"] or evidence[s]["url"])}</a>'
            f'{MAJOR_TAG if evidence[s].get("major_outlet") else ""}'
            f' <span class="muted">published {e(evidence[s]["published_at"][:16].replace("T", " "))} UTC</span></li>'
            for s in cited_sources(event))
        details.append(
            f'<section class="card" id="{e(event_id)}"><h3>{e(event.title)}</h3>'
            f'<div class="tags">{kind_tag(event)}<span class="tag"><code>{e(event_id)}</code></span>'
            f'<span class="tag">started {e(str(event.event_date or "unknown"))}</span><span class="tag">{e(event.category)}</span></div>'
            f'<p>{e(event.summary)}</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Sector</th><th>Channel</th><th>Type</th><th>Jev impact</th>'
            f'<th>Jev vs SPY</th><th>Reasoning</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Ticker</th><th>Name</th><th>Kind</th>'
            f'<th>Jev exposure</th><th>Reason</th></tr></thead><tbody>{tickers}</tbody></table></div>'
            f'<p><b>Uncertainty:</b> {e(event.uncertainty)}</p><p class="muted">Sources</p><ul>{sources}</ul></section>')

    extra = ""
    if market and market.get("metrics"):
        rows = "".join(
            f"<tr><td>{e(r['ticker'])}</td><td>{e(r['window'])}</td><td>{e(r['baseline_date'])}</td><td>{e(r['end_session'])}</td>"
            f"<td>{r['return_pct']:.2f}%</td><td>{r['spy_return_pct']:.2f}%</td><td>{r['excess_percentage_points']:.2f} pp</td></tr>"
            for r in market["metrics"])
        extra += (f'<h2>Observed market reaction</h2><div class="card table-wrap"><table><thead><tr><th>ETF</th><th>Window</th>'
                  f'<th>Baseline</th><th>End</th><th>Return</th><th>SPY</th><th>Difference</th></tr></thead><tbody>{rows}</tbody></table>'
                  f'<p class="muted">{e(market.get("timing", ""))} {e(market.get("limitation", ""))}</p></div>')
    if gpr:
        latest = gpr.get("latest")
        value = f"Latest eligible GPRD: {latest['gprd']:.2f} on {latest['date']}. " if latest else ""
        extra += (f'<h2>Geopolitical risk context</h2><div class="card"><p>{e(value)}{e(gpr.get("note", ""))}</p>'
                  f'<p class="muted">Status: {e(gpr["status"])}. <a href="{events_db.GPR_SOURCE}">Official GPR source</a>, Caldara and Iacoviello.</p></div>')
    limits = "".join(f"<li>{e(x)}</li>" for x in [*brief.limitations, *FIXED_LIMITS])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event Brief {e(str(as_of))}</title><style>{HTML_STYLE}</style></head>
<body><main>
<h1>Event brief: {e(str(as_of))}</h1>
<p class="muted">Day boundaries: {e(TIMEZONE.key)}. Sector exposures are assessments, not price predictions or trading signals.</p>
<h2>Keep an eye on</h2>
<p class="muted">Ordered by Jev severity, highest first. Scores run 0 to 3 and are Jev judgements from the news text,
not measured price impact. Selection is the lead agent's judgement.</p>
<ol class="watch">{watch}</ol>
<h2>Details</h2>{"".join(details)}
{extra}
<h2>Limits</h2><div class="card"><ul>{limits}</ul></div>
</main></body></html>
"""
