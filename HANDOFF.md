# Handoff: event-market-agent

Updated 1 October 2026, Singapore time. Replaces the earlier handoff, which said no live run had happened. That is no longer true.

## What it does

You ask "what happened today that I should watch?". The agent returns up to five sourced events. Each event links to US sectors through a stated channel (event -> channel -> sector). You can later follow up a saved event ID to see what changed and how the sector ETFs moved against SPY.

## Where things stand

- Project: `/Users/thiha.th/event-market-agent`. It has no Git repo of its own. It sits inside the home-directory repo.
- Live brief, live follow-up and live Jev calls all work (see README, Verification).
- All data lives in `data/events.db`, a Chinook-style SQLite database. `schema.sql` defines it. It is tracked, not git-ignored.
- The lead agent can query the database through `read_sql` (read-only).
- `data/events_legacy.db` is the pre-rebuild database, kept as a backup. Delete it once you are happy with the new one.
- Ruff passes. 22 tests pass.

## How it works

```text
brief:    question + date
          -> GDELT leads (database) + 2 Tavily searches
          -> optional Jev scoring
          -> Sonnet lead (read_sql, read_sources) -> Haiku researcher (Tavily)
          -> code checks: schema, citation IDs, publication dates
          -> saved to events.db, report in output/

followup: saved event ID
          -> previous assessment and sources from events.db
          -> new reporting since then
          -> new Assessment row under the same Event, onset date unchanged
          -> Python computes ETF vs SPY windows -> MarketWindow
```

| File | Job |
|---|---|
| `cli.py` | Command line, saving, reports |
| `lead_agent.py` | Lead agent and prompt |
| `researcher_subagent.py` | Researcher subagent and prompt |
| `research_tools.py` | Tavily search, `read_sources`, `read_sql` |
| `brief_schema.py` | Brief / Event / Exposure output model |
| `brief_checks.py` | Code checks before saving |
| `brief_report.py` | Markdown report |
| `jev_api.py` | Optional Jev scoring |
| `events_db.py` + `schema.sql` | The database |
| `market_returns.py` | ETF vs SPY windows |
| `models.py` | Models and settings |

## Settled decisions

1. Event-first. Never pick an event because its price moved.
2. On-demand only. No scheduler, alerts or monitoring.
3. One subagent (Haiku researcher).
4. All numbers come from Python. Sector minus SPY is a plain percentage-point difference, not causation.
5. No live LLM verifier. Code checks citations and dates.
6. Zero events is a valid answer.
7. GPR is background context only.
8. Jev is advisory. It does not establish facts.

## Open problems

1. **Same story, new ID: fixed 1 Oct.** Briefs now attach repeat stories to saved events through `tracked_event_id`. The old duplicate 342e033fb6fb was merged into 648ec9e12ab5. The match is model judgement, so an unmatched repeat is still possible.
2. **Jev's role.** Scores read on 30 Sep: 2.99/3 for generic commentary, 2.93/3 for an opinion essay. It does not separate events from commentary, which argues for using it in evals rather than as a live scorer. Still to decide.
3. **No evals.** "Worth watching" is not measured. Next step: label 20 to 30 past days.
4. **Weak sources get through.** The RTX event rests on one Zacks summary. Consider requiring two independent sources, or a primary one.
5. **LangSmith: fixed 1 Oct.** New key works. Each brief is one trace, "Event brief", holding the 5 Tavily searches, Jev scoring, the agent and the code checks. `TYPESAFE_API_KEY` in `.env` still ends in a stray newline.
6. **Cost not logged.** The US$10/month target is unchecked.
7. **GDELT is one old file (29 Sep).** Current discovery depends on Tavily.
8. **Retrieval: fixed 1 Oct.** One long keyword query returned 0 of 6 usable results. Now short topic queries, with `time_range` for today. The same run went from 3 to 28 sources.
9. **Commentary saved as an event.** 9694dbcb7ea2 (US import rules) has no dated development. Add a check, and decide whether to delete it or keep it as an eval example.
10. **Source quality: fixed 1 Oct.** The first sweep now reads major outlets only (33 of 33 sources in the test run). Before: The 28 sources in the fixed run came from 26 mostly mid-tier sites (Anadolu, Asia News Network, an opinion column). No Reuters, CNBC or Bloomberg was retrieved. Options: Tavily `include_domains` for a trusted-outlet pass, or a source-quality rule in the prompt.

11. **Hormuz split by backfill.** Briefs for 21-27 Sep were run after 29-30 Sep, so Hormuz exists as 01b78fe9a612 and 648ec9e12ab5. Merge them. Run backfills in date order.
12. **Running themes take event slots.** "Fed hike / yields backdrop" (1da6f7dd3870) was picked on 5 of 7 days.
13. **Free-text categories.** 10 spellings. Make `category` a fixed list.
14. **Lead rarely researches.** On the 25 Sep rerun it made one model call, with no researcher and no `read_sql`.
15. **Lead output limit: fixed 1 Oct.** 5,000 tokens cut a five-event Brief mid-JSON. Now 12,000, timeout 240s.

16. **Two-week backfill done 1 Oct.** 17-30 Sep run in date order into `data/backfill-2026-09-17-to-30.db` (set `EVENTS_DB` to use it). 33 events, 11 followed across days, Hormuz on one ID for all 14 days. Market check (`output/market-impact-2026-09-17-to-30.json`): linked sectors moved more than the rest in about half of events at D0/D+1, and in 12 of 15 at D+5. Same-day events share price windows, so this is weak evidence.
17. **Exposures have no direction.** Add expected up/down per exposure so a real hit-rate can be measured.

18. **Direction, evals and cost: built 1 Oct.** Jev predicts each exposure's direction vs SPY (`ExposureDirection`). `evals/scorecard.py` scores it. First result on 17-30 Sep: Jev right 46% at D+1 vs a 57% always-majority baseline, 45% at D+5 vs 73%. Jev's direction calls are not useful yet. Every run logs `RunUsage`. One live brief cost US$0.14 in Claude tokens. `uv run cli.py cost` shows the month.
19. **Labels needed.** Fill `evals/labels-backfill-2026-09-17-to-30.csv` (worth_watching, duplicate_of) so selection quality can be scored.
20. **Severity, sector impact and top tickers: built 1 Oct.** One Jev request per brief now scores severity per event, impact and direction per exposure, and exposure per ticker. The lead names 3-5 cited tickers per event. Code drops unlisted ones via Yahoo Finance and rejects S&P 500 trackers. Top three by Jev are ranked. HTML watch list is ordered by Jev severity and shows all three metrics. Tables: `AssessmentSeverity`, `ExposureImpact`, `WatchTicker`. None of these scores is validated against prices yet. Next: score ticker picks vs SPY in `evals/scorecard.py`.

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
uv run cli.py brief "What events happened today that I should watch?" --limit 3 --max-searches 4
uv run cli.py list
uv run cli.py followup EVENT_ID --max-searches 4
sqlite3 -header data/events.db "SELECT EventId, EventDate, Title FROM Event"
uv run ruff check . && uv run pytest -q
```

Just after midnight Singapore time, "today" usually returns zero sources. Use `--date` for yesterday.

## Pickup prompt

> Continue the event-first market agent at `/Users/thiha.th/event-market-agent`. Read `HANDOFF.md` and `README.md` first, and follow the `deep-agent` skill. Live brief and follow-up work. Data is in the Chinook-style `data/events.db` (`schema.sql`). Open problem 1 is fixed. Pick the next open problem with the user, likely 2 (read the Jev scores) or 3 (label past days for evals).
