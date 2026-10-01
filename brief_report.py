"""Render saved assessments as a standalone HTML brief page and as a date-range digest.

Every report renders from events_db assessment views, so a page shows exactly what was saved.
Patterns reused: host-side output (m4.2).
Run: save_reports is called by cli.py after a run; render_digest_html by the digest command.
"""

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta
from html import escape

import events_db
import jev_api
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



def score_text(judgement):
    """e.g. "high 2.1/3", or "" without a Jev answer."""
    return f"{judgement['level']} {judgement['score']:.1f}/3" if judgement else ""


def direction_text(direction):
    if not direction:
        return ""
    conf = f", {direction['confidence']:.2f}" if direction.get("confidence") is not None else ""
    return f"{direction['direction']}{conf}"


def top(tickers):
    return [t for t in tickers if t["rank"]]


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


def level_span(judgement):
    if not judgement:
        return '<span class="muted">no Jev score</span>'
    return (f'<span class="lvl-{escape(judgement["level"])}">{escape(judgement["level"])}</span> '
            f'<span class="muted">{judgement["score"]:.1f}/3</span>')


def sector_list(exposures):
    items = []
    for x in exposures:
        arrow = {"up": " &#9650; vs SPY", "down": " &#9660; vs SPY"}.get((x["direction"] or {}).get("direction"), "")
        hyp = ' <span class="muted">(hypothesis)</span>' if x["status"] == "hypothesis" else ""
        items.append(f'<li>{escape(x["sector"].replace("_", " ").title())}{hyp}: '
                     f'{level_span(x["impact"])}<span class="muted dir">{arrow}</span></li>')
    return f'<ul class="pills">{"".join(items)}</ul>' if items else '<p class="muted">None established</p>'


def ticker_list(tickers):
    items = [f'<li><span class="ticker">{escape(t["symbol"])}</span> <span class="muted">{escape(t["kind"])}</span> '
             f'{level_span(t["fit"])}</li>' for t in top(tickers)]
    return f'<ul class="pills">{"".join(items)}</ul>' if items else '<p class="muted">None named</p>'


def render_html(events, as_of, limitations, market=None, start=None):
    """One run's assessment views as a self-contained HTML page, scored watch list first.

    start before as_of makes it a weekly brief covering start to as_of.
    """
    e = escape
    period = f"Weekly brief: {start} to {as_of}" if start and start != as_of else f"Event brief: {as_of}"

    def kind_tag(x):
        return '<span class="tag update">Update</span>' if x["update"] else '<span class="tag new">New</span>'

    watch = []
    for x in events:
        severity = x["severity"]
        bar = (f'<div class="bar"><span class="lvl-{e(severity["level"])}" '
               f'style="width:{max(4, severity["score"] / 3 * 100):.0f}%"></span></div>') if severity else ""
        watch.append(
            f'<li class="card"><h3><a href="#{e(x["event_id"])}">{e(x["title"])}</a></h3>'
            f'<div class="tags">{kind_tag(x)}<span class="tag">{e(x["status"])}</span>'
            f'<span class="tag">{e(x["category"])}</span></div>'
            f'<div class="metrics"><div class="metric"><div class="label">Severity</div>'
            f'<div class="value">{level_span(severity)}</div>{bar}</div>'
            f'<div class="metric"><div class="label">Sector impact</div>{sector_list(x["exposures"])}</div>'
            f'<div class="metric"><div class="label">Top tickers</div>{ticker_list(x["tickers"])}</div></div>'
            f'<p>{e(x["why_watch"])}</p><p class="muted"><b>Check next:</b> {e(x["watch_next"])}</p></li>')
    watch = "".join(watch) or '<div class="card">No sufficiently supported events were selected.</div>'

    details = []
    for x in events:
        rows = "".join(
            f"<tr><td>{e(s['sector'].replace('_', ' ').title())}</td><td>{e(s['channel'])}</td>"
            f"<td>{'reported' if s['status'] == 'reported_exposure' else 'hypothesis'}</td>"
            f"<td>{e(score_text(s['impact'])) or '-'}</td>"
            f"<td>{e(direction_text(s['direction'])) or '-'}</td><td>{e(s['reasoning'])}</td></tr>"
            for s in x["exposures"]) or '<tr><td colspan="6">No sector exposure established.</td></tr>'
        tickers = "".join(
            f"<tr><td>{t['rank'] or '-'}</td><td><span class=\"ticker\">{e(t['symbol'])}</span></td><td>{e(t['name'])}</td>"
            f"<td>{e(t['kind'])}</td><td>{e(score_text(t['fit'])) or '-'}</td><td>{e(t['reason'])}</td></tr>"
            for t in x["tickers"]
        ) or '<tr><td colspan="6">No directly exposed stock or ETF named.</td></tr>'
        sources = "".join(
            f'<li><a href="{e(s["url"])}" target="_blank" rel="noopener">{e(s["title"] or s["url"])}</a>'
            f'{MAJOR_TAG if s["major_outlet"] else ""}'
            f' <span class="muted">published {e(s["published_at"][:16].replace("T", " "))} UTC</span></li>'
            for s in x["sources"])
        details.append(
            f'<section class="card" id="{e(x["event_id"])}"><h3>{e(x["title"])}</h3>'
            f'<div class="tags">{kind_tag(x)}<span class="tag"><code>{e(x["event_id"])}</code></span>'
            f'<span class="tag">started {e(str(x["event_date"] or "unknown"))}</span><span class="tag">{e(x["category"])}</span></div>'
            f'<p>{e(x["summary"])}</p>'
            f'<div class="table-wrap"><table><thead><tr><th>Sector</th><th>Channel</th><th>Type</th><th>Jev impact</th>'
            f'<th>Jev vs SPY</th><th>Reasoning</th></tr></thead><tbody>{rows}</tbody></table></div>'
            f'<div class="table-wrap"><table><thead><tr><th>Rank</th><th>Ticker</th><th>Name</th><th>Kind</th>'
            f'<th>Jev exposure</th><th>Reason</th></tr></thead><tbody>{tickers}</tbody></table></div>'
            f'<p><b>Uncertainty:</b> {e(x["uncertainty"])}</p><p class="muted">Sources</p><ul>{sources}</ul></section>')

    extra = ""
    if market and market.get("metrics"):
        rows = "".join(
            f"<tr><td>{e(r['ticker'])}</td><td>{e(r['window'])}</td><td>{e(r['baseline_date'])}</td><td>{e(r['end_session'])}</td>"
            f"<td>{r['return_pct']:.2f}%</td><td>{r['spy_return_pct']:.2f}%</td><td>{r['excess_percentage_points']:.2f} pp</td></tr>"
            for r in market["metrics"])
        extra += (f'<h2>Observed market reaction</h2><div class="card table-wrap"><table><thead><tr><th>ETF</th><th>Window</th>'
                  f'<th>Baseline</th><th>End</th><th>Return</th><th>SPY</th><th>Difference</th></tr></thead><tbody>{rows}</tbody></table>'
                  f'<p class="muted">{e(market.get("timing", ""))} {e(market.get("limitation", ""))}</p></div>')
    limits = "".join(f"<li>{e(x)}</li>" for x in [*limitations, *FIXED_LIMITS])

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(period.replace("brief", "Brief"))}</title><style>{HTML_STYLE}</style></head>
<body><main>
<h1>{e(period)}</h1>
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


def save_reports(result, output_dir, db=None):
    """Write one run's .html report and .json record next to each other. Returns the .html path."""
    events = events_db.run_view(result.run_id, db)
    output_dir.mkdir(exist_ok=True)
    period = f"{result.start}-to-{result.as_of}" if result.start != result.as_of else str(result.as_of)
    destination = output_dir / f"{period}-{result.run_id[:8]}"
    artifact = {"brief": result.brief.model_dump(mode="json"), "event_ids": result.event_ids,
                "sources": result.evidence, "jev_judgements": asdict(result.judgements),
                "unlisted_tickers": result.unlisted, "usage": result.usage,
                "market": result.market, "search_calls": result.search_calls, "period_start": str(result.start), "as_of": str(result.as_of),
                "timezone": TIMEZONE.key, "created_at": datetime.now(TIMEZONE).isoformat()}
    destination.with_suffix(".json").write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    destination.with_suffix(".html").write_text(
        render_html(events, result.as_of, result.brief.limitations, result.market, result.start),
        encoding="utf-8")
    return destination.with_suffix(".html")


DIGEST_STYLE = """
:root { --sev-minor: #86b6ef; --sev-moderate: #3987e5; --sev-high: #1c5cab; --sev-severe: #0d366b; }
@media (prefers-color-scheme: dark) {
  :root { --sev-minor: #184f95; --sev-moderate: #2a78d6; --sev-high: #6da7ec; --sev-severe: #b7d3f6; } }
.strip { display: grid; grid-template-columns: repeat(var(--days), 1fr); gap: 2px; margin: 10px 0 2px; }
.strip span { height: 14px; border-radius: 3px; background: var(--track); }
.strip .s-minor { background: var(--sev-minor); } .strip .s-moderate { background: var(--sev-moderate); }
.strip .s-high { background: var(--sev-high); } .strip .s-severe { background: var(--sev-severe); }
.strip .s-none { background: var(--muted); }
.strip-axis { display: flex; justify-content: space-between; font-size: 12px; color: var(--muted); }
.legend { display: flex; flex-wrap: wrap; gap: 12px; font-size: 13px; color: var(--muted); margin: 8px 0 0; }
.legend i { display: inline-block; width: 12px; height: 12px; border-radius: 3px; vertical-align: -1px; margin-right: 4px; }
.nowrap { white-space: nowrap; }
.swatch { display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 6px; }
.summary { display: flex; flex-wrap: wrap; gap: 24px; margin: 8px 0 4px; }
.summary b { display: block; font-size: 26px; font-variant-numeric: tabular-nums; }
"""

SEVERITY_LEVELS = tuple(jev_api.SEVERITY)


def render_digest_html(events, start, end):
    """One page for a date range: stories at high or severe first, each with its day-by-day severity."""
    e = escape
    first, last = date.fromisoformat(str(start)), date.fromisoformat(str(end))
    days = [str(first + timedelta(n)) for n in range((last - first).days + 1)]
    aware = [x for x in events if (x["max_severity"] or 0) >= 1.5]
    rest = [x for x in events if (x["max_severity"] or 0) < 1.5]

    def peak(x):
        """Level in ink with a swatch from the strip's ramp, so it reads with the strip."""
        if x["max_severity"] is None:
            return '<span class="muted">no Jev score</span>'
        lvl = jev_api.level(x["max_severity"], jev_api.SEVERITY)
        return (f'<span class="nowrap"><i class="swatch" style="background:var(--sev-{lvl})"></i>{lvl} '
                f'<span class="muted">{x["max_severity"]:.1f}/3</span></span>')

    def strip(x):
        cells = []
        for d in days:
            day = x["days"].get(d)
            if not day:
                cells.append(f'<span title="{d}: not reported"></span>')
            elif day["level"]:
                cells.append(f'<span class="s-{e(day["level"])}" title="{d}: {e(day["level"])} {day["score"]:.1f}/3"></span>')
            else:
                cells.append(f'<span class="s-none" title="{d}: reported, no Jev score"></span>')
        return (f'<div class="strip" style="--days:{len(days)}" role="img" '
                f'aria-label="Severity by day, {len(x["days"])} days reported">{"".join(cells)}</div>'
                f'<div class="strip-axis"><span>{e(days[0][5:])}</span><span>{e(days[-1][5:])}</span></div>')

    cards = "".join(
        f'<li class="card"><h3>{e(x["title"])}</h3>'
        f'<div class="tags"><span class="tag">{e(x["category"])}</span><span class="tag">{e(x["status"])}</span>'
        f'<span class="tag">reported {len(x["days"])} day{"s" if len(x["days"]) != 1 else ""}, '
        f'{e(x["first_seen"][5:])} to {e(x["last_seen"][5:])}</span>'
        f'<span class="tag"><code>{e(x["event_id"])}</code></span></div>'
        f'{strip(x)}'
        f'<div class="metrics"><div class="metric"><div class="label">Peak severity</div><div class="value">{peak(x)}</div></div>'
        f'<div class="metric"><div class="label">Sector impact (latest)</div>{sector_list(x["exposures"])}</div>'
        f'<div class="metric"><div class="label">Top tickers (latest)</div>{ticker_list(x["tickers"])}</div></div>'
        f'<p>{e(x["why_watch"])}</p><p class="muted"><b>Check next:</b> {e(x["watch_next"])}</p></li>'
        for x in aware) or '<div class="card">No story reached high severity in this range.</div>'
    rows = "".join(
        f'<tr><td>{e(x["title"])}</td><td>{peak(x)}</td><td>{len(x["days"])}</td><td class="nowrap">{e(x["last_seen"])}</td>'
        f'<td>{", ".join(e(t["symbol"]) for t in top(x["tickers"])) or "-"}</td></tr>' for x in rest)
    legend = "".join(f'<span><i style="background:var(--sev-{lvl})"></i>{lvl}</span>' for lvl in SEVERITY_LEVELS)
    legend += ('<span><i style="background:var(--muted)"></i>reported, no score</span>'
               '<span><i style="background:var(--track)"></i>not reported</span>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event Digest {e(days[0])} to {e(days[-1])}</title><style>{HTML_STYLE}{DIGEST_STYLE}</style></head>
<body><main>
<h1>What to be aware of</h1>
<p class="muted">{e(days[0])} to {e(days[-1])}. One brief per day, up to three events each, day boundaries {e(TIMEZONE.key)}.</p>
<div class="summary"><div><b>{len(events)}</b><span class="muted">stories tracked</span></div>
<div><b>{len(aware)}</b><span class="muted">reached high or severe</span></div>
<div><b>{sum(len(x["days"]) > 1 for x in events)}</b><span class="muted">reported on more than one day</span></div></div>
<h2>Be aware of</h2>
<p class="muted">Stories whose Jev severity reached high (1.5/3) or more on any day, highest peak first.
Each strip has one cell per day, coloured by that day's severity. Sectors and tickers are from the latest assessment.</p>
<div class="legend">{legend}</div>
<ol class="watch">{cards}</ol>
<h2>Also tracked</h2>
<div class="card table-wrap"><table><thead><tr><th>Story</th><th>Peak severity</th><th>Days</th><th>Last seen</th><th>Top tickers</th></tr></thead>
<tbody>{rows or '<tr><td colspan="5">None.</td></tr>'}</tbody></table></div>
<h2>Limits</h2><div class="card"><ul>
<li>Severity, impact and ticker ranks are Jev judgements from news text, not measured market impact.</li>
<li>Backfilled days were researched after the fact. Web pages may have been edited since publication, so this is not a point-in-time record.</li>
<li>At most three stories per day were selected. Anything the lead agent did not pick is missing.</li>
</ul></div>
</main></body></html>
"""
