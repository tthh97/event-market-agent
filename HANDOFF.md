# Handoff: event-market-agent

Updated 2 October 2026, Singapore time.

## What it does

You ask "which events should I watch?" for a day or a week. The agent returns up to five events. Each has cited sources, a link to US sectors (event -> channel -> sector), and three to five exposed US tickers. Jev scores severity and direction vs SPY. Everything is saved, so the same story keeps one ID across days, and `followup` measures how the linked sector ETFs moved against SPY.

## How a brief runs

```text
cli.py brief "question" --date D
  1. brief_run.run          period = D, or the 7 days ending D if the question says "week"
  2. research_tools         up to 5 Tavily topic searches on major outlets.
     events_db              GDELT daily file for each day (top 300 URLs kept), top 12 leads to the agent
                            Articles published outside the period are dropped (point_in_time.known_by)
  3. lead_agent.build       Sonnet lead + Haiku researcher. Must return a Brief (Pydantic).
                            Tools: research_news, read_sources, read_sql (read-only)
  4. brief_run.validate     event count, event dates, citation IDs, publication dates,
                            duplicate and S&P 500 tickers, tracked_event_id rules. Any failure saves nothing
  5. drop_unlisted_tickers  tickers with no Yahoo Finance close in 10 days are removed
  6. jev_api.assess         one request: severity, sector impact and direction, ticker exposure.
                            Top three tickers ranked
  7. events_db.save_run     Run, Source, Event, Assessment, Exposure, WatchTicker, Jev scores, RunUsage
  8. brief_report           HTML and JSON in output/, rendered from the saved rows, by severity

cli.py followup EVENT_ID
  same research and checks for one event, then market_returns.reactions computes
  sector ETF vs SPY at D0, D+1 and D+5 into MarketWindow
```

| File | Job |
|---|---|
| `cli.py` | Commands: brief, followup, list, digest, doctor, cost, refresh-gdelt, import-gdelt, candidates. Wires the live services |
| `brief_run.py` | One brief or follow-up, from request to saved rows. Outside services come in as `Adapters` |
| `point_in_time.py` | The "known by the requested day" rule for sources and market sessions |
| `research_tools.py` | Search budget, dated source registry, the three agent tools |
| `lead_agent.py` | Brief schema, prompts, lead and researcher setup |
| `jev_api.py` | Jev questions, rubrics and ticker ranking |
| `events_db.py` + `schema.sql` | All database reads and writes, GDELT download and import |
| `market_returns.py` | Yahoo prices, ticker listing check, ETF vs SPY windows |
| `brief_report.py` | Brief and digest HTML |
| `models.py` | Models, prices, sectors, major outlets, timezone |
| `evals/scorecard.py` | Scores Jev direction calls against real sector moves |

## Where things stand

- Live brief, follow-up and Jev all work. A brief costs about US$0.11 to US$0.14 of Claude.
- `data/events.db` is tracked and holds the main history. `data/backfill-2026-09-17-to-30.db` (tracked) and `data/backfill-2026-09-01-to-30.db` (untracked) are separate backfills. Use `EVENTS_DB=...` to point a command at one.
- Weekly briefs for 11-17, 18-24 and 24-30 Sep are in `events.db`.
- Ruff passes. `tests/` is deleted in the working tree but the deletion isn't committed. There are no tests right now.

## Settled decisions

1. Event-first. Never pick an event because its price moved.
2. On-demand only. No scheduler, alerts or monitoring.
3. One subagent (Haiku researcher).
4. All numbers come from Python. Sector minus SPY is a plain percentage-point difference, not causation.
5. No live LLM verifier. Code checks citations and dates.
6. Zero events is a valid answer.
7. Jev is advisory. It does not establish facts.
8. **GPR removed (2 Oct).** It only filled a context box at the end of the report. The agent never saw it.

## Open problems

1. **Jev's role.** It scored generic commentary 2.99/3 and its direction calls hit 46% vs a 57% always-majority baseline (D+1, 17-30 Sep). Decide whether it stays in live briefs or moves to evals only.
2. **No selection eval.** Fill `evals/labels-backfill-2026-09-17-to-30.csv` (worth_watching, duplicate_of) so "worth watching" can be scored.
3. **No tests.** Restore or rewrite `tests/` before the next behaviour change.
4. **Period is 1 or 7 days.** It comes from a regex on the question. "Past 14 days" silently becomes 1 day. A `--days N` flag would fix it.
5. **Weak sources get through.** The RTX event rests on one Zacks summary. Consider requiring two independent sources or a primary one.
6. **Commentary saved as an event.** 9694dbcb7ea2 (US import rules) has no dated development.
7. **Running themes take event slots.** "Fed hike / yields backdrop" was picked on 5 of 7 days.
8. **Free-text categories.** 10 spellings. Make `category` a fixed list.
9. **Lead rarely researches.** It often makes one model call with no researcher and no `read_sql`.
10. **Hormuz split in the backfill.** 01b78fe9a612 and 648ec9e12ab5 are the same story. Run backfills in date order.

## Don't retry

- Choosing events from big market moves.
- Building the catalog from big market days.
- A live LLM verifier subagent.
- ACLED on a personal email (no event-level data).
- Scraping the Wikipedia Current Events Portal.

## Commands

```sh
cd /Users/thiha.th/event-market-agent
uv sync
uv run cli.py doctor
uv run cli.py brief "What events happened today that I should watch?" --limit 3
uv run cli.py brief "What were the most important events this week?" --date 2026-09-30
uv run cli.py list
uv run cli.py followup EVENT_ID
uv run cli.py digest --start 2026-09-24 --end 2026-09-30
uv run cli.py cost
uv run ruff check .
```

Just after midnight Singapore time, "today" usually returns zero sources. Use `--date` for yesterday.

## Pickup prompt

> Continue the event-first market agent at `/Users/thiha.th/event-market-agent`. Read `HANDOFF.md` and `README.md` first, and follow the `deep-agent` skill. Pick the next open problem with the user, likely 3 (restore tests) or 1 (Jev's role).
