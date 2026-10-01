# Event-first market agent

Ask what significant events happened on a date, investigate potential US sector exposure, and save the assessment for an on-demand follow-up. General across event types: policy, business, conflict, infrastructure, weather, and others. This is a working first slice, not a deployed or continuously running monitor.

## Setup

Requires Python 3.11–3.14 and uv. From this folder:

```sh
uv sync
cp .env.example .env
# Edit .env to add your keys. Never commit it.
uv run cli.py doctor
uv run cli.py brief "What events happened today that I should watch?" --limit 5
```

`ANTHROPIC_API_KEY` and `TAVILY_API_KEY` are required for live briefs. `TYPESAFE_API_KEY` enables Jev; if absent, the output explicitly records that it was not used. Keys already in your environment take precedence over `.env`. Model choices and the timezone are configured in `.env`; no credentials are copied from the course setup.

Default models: Sonnet lead, Haiku researcher. Default day boundaries: Asia/Singapore. The report can contain fewer than five events, including zero, rather than forcing a quota.

## Commands

```sh
# Download and import the official GDELT daily export for a date (default yesterday UTC).
# Every brief also does this for its own date. A day's file appears around 07:00 UTC the next day.
uv run cli.py refresh-gdelt --date 2026-09-29

# Import a local 58-column GDELT daily event export (not the 61-column GDELT 2.0 format).
uv run cli.py import-gdelt /path/to/20260929.export.CSV

# Inspect raw leads without any model or search calls.
uv run cli.py candidates --date 2026-09-29 --limit 12

# Download the official GPR daily series and record retrieval time.
uv run cli.py refresh-gpr

# Historical-date research: publication dates are filtered, but this is not a point-in-time backtest.
uv run cli.py brief "What significant events developed on this date?" --date 2026-09-29 --limit 3

# List saved assessments and use one ID to follow the same event.
uv run cli.py list
uv run cli.py cost
uv run cli.py followup SAVED_EVENT_ID

# Optional controls.
uv run cli.py brief "What happened today?" --max-searches 4 --no-jev
```

Reports are HTML and JSON in `output/`. The HTML page opens with a "Keep an eye on" list ordered by Jev severity, highest first. Each card shows severity (0-3 with a bar), each linked sector's Jev impact and direction vs SPY, and the top three tickers by Jev exposure. These are Jev judgements from the news text, not measured price impact. Without Jev the lead's order stands. Everything else is stored in one SQLite database, `data/events.db` (see Database). `output/` is ignored by Git. `data/events.db` is tracked so the database ships with the project. Host code saves validated output. The agent can read the database but never write to it. Copy the project without `.venv/` to move it and run `uv sync` again.

## Architecture

```text
Question + requested date
  -> short Tavily topic searches (economy, policy, conflict, business, disasters)
  -> GDELT refresh for the date (download if published) + candidate lookup
  -> lead deep agent
       -> event-researcher subagent for missing evidence and exposure links
  -> Pydantic output + citation-ID/date/ticker validation
  -> drop tickers with no recent Yahoo Finance price
  -> Jev severity, sector impact and direction, ticker exposure (top three ranked)
  -> host saves run, events, sources, exposures and tickers to data/events.db
     and renders JSON and HTML from the saved rows (watch list ordered by severity)

Saved event + follow-up date
  -> retrieve prior evidence and original event date
  -> research developments since the prior assessment
  -> append assessment under the same event ID
  -> Python calculates available sector ETF returns versus SPY, saved to MarketWindow
```

The lead merges same-story reporting within a brief. GDELT grouping by URL only reduces repeated rows; it is not semantic event identification. The packet lists saved events. When the lead judges a story to be one of them, it sets `tracked_event_id` and the brief appends a new assessment to that event instead of creating a duplicate. Code checks that the ID exists, that no two events in a brief share it, and that a known onset date is unchanged (an unknown one may be filled once). The match itself is model judgement. `followup EVENT_ID` is the guaranteed link.

Jev is not shown the sources before the lead decides. After the code checks, one Jev request judges every event from its text only, never prices: severity (minor, moderate, high, severe), each exposure's impact on its sector (negligible, small, material, large) and direction vs SPY, and how directly each ticker is exposed (none, indirect, direct, core). Scores are Jev's expected level on a 0-3 scale.

Tickers: the lead names three to five US-listed stocks or ETFs per event, each with a reason and citations. Code rejects S&P 500 trackers (SPY, VOO, IVV, SPLG) and duplicates, and drops symbols with no Yahoo Finance close in the last ten days. Jev ranks the rest; the top three get Rank 1-3.

Potential sector exposure is a sourced narrative hypothesis. There is no universal asset or company exposure database in this version. If evidence does not establish a connection, an empty exposure list is valid. Sector ETFs are broad proxies and may miss an industry-specific reaction.

## Database

`data/events.db` follows the layout of the course `chinook.db`: PascalCase singular tables, `<Table>Id` keys, foreign keys, ISO text dates. The full schema with comments is in `schema.sql`.

| Table | One row per |
|---|---|
| `Sector` | US sector and its SPDR ETF (11 rows) |
| `DataImport` | Imported GDELT file or GPR download |
| `GdeltEvent` | GDELT export row (unverified research lead) |
| `GprDaily` | Daily GPR index value |
| `Run` | Brief or follow-up |
| `Source` | Dated news article retrieved by Tavily (`RunSource` links it to runs) |
| `Event` | Tracked development, with its original onset date |
| `Assessment` | What one run concluded about one event (`AssessmentSource` holds its citations) |
| `Exposure` | Event -> channel -> sector link (`ExposureSource` holds its citations) |
| `WatchTicker` | Stock or ETF named as exposed, with Jev exposure score and Rank 1-3 for the top three (`WatchTickerSource` holds its citations) |
| `AssessmentSeverity`, `ExposureImpact`, `ExposureDirection` | Jev severity per assessment, impact and direction per exposure |
| `MarketWindow` | Python-computed sector ETF vs SPY return window |

The lead agent has a `read_sql` tool: one `SELECT` on a read-only connection, at most 50 rows, long text cut to 300 characters. It uses it to check whether a story is already tracked. Writes fail at the SQLite level, not only by prompt.

Example queries:

```sh
sqlite3 -header data/events.db "SELECT EventId, EventDate, Title FROM Event"
sqlite3 -header data/events.db "
  SELECT e.Title, x.SectorId, x.Status, x.Channel
  FROM Event e JOIN Assessment a USING (EventId) JOIN Exposure x USING (AssessmentId)"
```

## Evals

`evals/scorecard.py` scores the saved briefs in a database file. It needs no model calls, except Jev when `--fill-directions` is given.

```sh
uv run evals/scorecard.py --db data/backfill-2026-09-17-to-30.db --fill-directions
```

It measures:

1. **Jev direction calls.** After the checks pass, Jev predicts for each exposure whether the sector will beat or trail SPY over the next five sessions, from the event text only (`ExposureDirection` table). The scorecard compares each call with the Python-computed sector-minus-SPY move at D+1 and D+5, next to an always-guess-the-common-outcome baseline.
2. **Linked vs other sectors.** Whether the linked sectors moved more than the rest after each event.
3. **Your labels.** It creates `evals/labels-<db>.csv`. Fill `worth_watching` (yes/no) and `duplicate_of`, then rerun to get the share of events worth watching.
4. **Cost this month** from `RunUsage`.

Output: `evals/scorecard-<db>.md` and `.json`, plus an "Eval scorecard" run in LangSmith.

First result, 17-30 Sep backfill (scored 1 Oct 2026, prices to 29 Sep):

| Window | Jev calls | Jev right | Always-majority baseline |
|---|---:|---:|---:|
| D+1 | 28 | 46% | 57% |
| D+5 | 11 | 45% | 73% |

Jev's direction calls did worse than guessing the common outcome, including its confident ones (40% at D+1, 15 calls). 25 of 56 exposures were called unclear. The samples are small and events overlap in time. Linked sectors moved more than the rest in 15 of 29 events at D+1 and 13 of 15 at D+5.

## GDELT

The first export, imported by hand on 30 Sep, had **113,066 records, 58 tab-delimited columns**. All rows had ingestion date September 29, 2026, and **110,827** had that event date. Other rows referred to September 28, September 22, August 30, September 2025, or October 2016. Each daily file mixes event dates like this.

The filename and ingestion date do not establish when every event happened. Candidate queries require the requested event date and an ingestion date no later than it. Import preserves CAMEO strings and distinguishes both dates. URL groups are ranked by maximum mentions; counts are not summed across duplicate rows. Media attention is a lead-selection heuristic, not event significance, verified source independence, or market impact.

Live refresh (added 1 Oct 2026): `refresh-gdelt` downloads `data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip`, and every brief runs it for its own date first. The file for a date is published around 07:00 UTC the next day (15:00 Singapore time), so a brief for today has no GDELT leads and relies on Tavily. Past dates get them. A missing or failed download never stops a brief.

Only the 300 most-mentioned URLs per event date are kept, with all their rows. A full day is about 110,000 rows and 25MB, too much for a Git-tracked database. Kept, it is about 4,000 rows and 0.8MB. Briefs read at most 30 candidates, so nothing they see changes. The import vacuums the file afterwards. Source publication dates must come from the retrieved reporting, never from GDELT ingestion dates. Missing-date results are excluded.

[GDELT daily-export codebook](https://data.gdeltproject.org/documentation/GDELT-Data_Format_Codebook.pdf)

## GPR context

The integration downloads the original official daily GPR series, not the separate AI-GPR product. GPR is newspaper-based aggregate geopolitical risk context. It does not score individual events or apply to every event category. The brief displays observation and retrieval dates; it makes no automatic trading inference.

The verified snapshot's latest observation was **September 28, 2026**. GPR daily data are released on a periodic schedule, and recent values may be revised. A snapshot retrieved after a requested historical day is excluded from that historical brief. This does not implement a vintage archive or historical point-in-time GPR backtest.

Attribution: Dario Caldara and Matteo Iacoviello, *Measuring Geopolitical Risk* (2022). Data downloaded October 1, 2026, Singapore time. The source page states CC BY licensing.

[Official GPR methodology and downloads](https://www.matteoiacoviello.com/gpr.htm)

## Market follow-ups

Python fetches adjusted daily ETF closes and SPY. Since event time is only recorded at date precision, the first strictly later US trading session is used, with the preceding available close as baseline. The current New York calendar date is always excluded to avoid incomplete daily prices.

Reported windows end at D0, D+1, and D+5 when available. D0 through D+5 includes six sessions. The output shows exact baseline and end dates. Sector return minus SPY return is a simple percentage-point comparison, not a beta-adjusted abnormal return or proof of causation. Prices are fetched on demand and preserved in report metrics; a full versioned price warehouse is not built.

## Cost and boundaries

- Default eight Tavily searches per run: up to five topic searches first, limited to major outlets (`MAJOR_OUTLETS` in `models.py`), the rest for the researcher on the open web. Every source carries a `major_outlet` flag. Configurable from one to ten. A window ending today uses Tavily `time_range` (newest articles). Past dates use a date range. The publication-date filter applies either way.
- Lead limited to ten model calls; each researcher invocation limited to five, with bounded token outputs and recursion.
- One Jev request per run, after the code checks. It judges events, not individual sources.
- Every run saves its usage in `RunUsage`: tokens and cost per Claude model, Jev tokens, Tavily searches. `uv run cli.py cost` shows the month against the US$10 budget. A live brief on 30 Sep cost US$0.14 in Claude tokens (35k in, 5k out), which matches LangSmith. Tavily and Jev are counted but not priced.
- Optional LangSmith tracing: each brief is one trace, "Event brief", with the Tavily searches, Jev scoring, the agent and the code checks as steps. No LangSmith evaluation scorecard exists yet.
- No scheduler, notifications, autonomous follow-ups, five-year catalog, or buy/sell signals.
- Source/date validation catches invented IDs and future sources; semantic claim support still depends on model quality and review.
- Historical web results may have been edited after their publication date. This is exploratory historical research, not hindsight-free evaluation.
- Runtime or credential failures stop the run without claiming a successful brief. GPR and market outages are separately visible.

## Verification performed

As of 1 October 2026 (Singapore time).

| Check | Result |
|---|---|
| Ruff | Passed |
| Tests | 26 passed: GDELT download, top-URL pruning and skip of known files; Jev severity, impact, direction and ticker questions saved; top-three ranking and benchmark/duplicate ticker rejection; unlisted tickers dropped; HTML ordered by severity; cost maths incl. cache, Jev directions saved per exposure, scorecard hit rules, the first sweep reads major outlets and flags every source, HTML report puts the watch list first and escapes text, today uses time_range and past dates use a date range, repeat stories attach to saved events, date filtering, duplicate imports, citations, follow-ups keep the event ID and date, MarketWindow rows, read-only `read_sql`, weekend returns, GPR vintage exclusion |
| Import supplied GDELT file | Passed, 113,066 records |
| Live `refresh-gdelt` (1 Oct) | 27 Sep: 61,485 rows, 3,245 kept. 28 Sep: 97,242 rows, 4,048 kept. 29 Sep: skipped, already imported. 30 Sep: not published yet. `events.db` 30MB before pruning, 4.3MB after three days |
| Live brief, 26 Sep, on a scratch copy of the database | The brief downloaded and imported the 26 Sep file itself (69,342 rows), then saved 2 events. US$0.09 |
| Official GPR download and date parsing | Passed, latest observation September 28 |
| Live brief, 29 Sep | 3 events saved (run before the database rebuild, migrated) |
| Live brief, 30 Sep | 2 events. The agent used `read_sql` and named the already-saved Hormuz event |
| Live brief, 30 Sep, rerun after the duplicate fix | Hormuz and RTX attached to 648ec9e12ab5 and 060f8ec1034d. No new events created |
| Live brief, 30 Sep NY, after the retrieval fix | 28 sources and 4 events (Hormuz update, ADP payrolls, AI accord, Choice Hotels deal), up from 3 sources and 2 events with the old single long query |
| Live brief, 30 Sep NY, major-outlet sweep | 33 of 33 sources from major outlets (Reuters, AP, Politico and others). 5 events: Hormuz update, PCE and GDP, Bank of England risk warning, Korean LNG investment, Boeing Navy fighter |
| Live follow-up, RTX event | Same event ID, onset date 28 Sep kept, XLI vs SPY D0 window stored |
| Live Jev request | Returned `ok` on all three live runs. Output not yet reviewed |
| Live brief, 30 Sep, Jev severity and tickers (1 Oct) | 4 events attached to saved IDs. Hormuz severe 2.99, Treasury yields high 2.28, AI IPO high 1.52, AI accord moderate 1.37. 3-4 tickers each, all listed (e.g. Hormuz: USO, XOP, VLO). US$0.11 |
| Selection quality | Not measured. No labelled eval set yet |

The current-day search also completed but returned no eligible sources in the very early Singapore-day window. Zero results are preserved rather than replaced with stale news.

Run checks:

```sh
uv run ruff check .
uv run pytest -q
```

Smoke-test JSON files in `output/` are integration evidence, not completed AI-generated briefs.

## Main files

| File | Job |
|---|---|
| `cli.py` | Command line: import, brief, followup, list, digest, doctor, cost. Wires the live adapters |
| `brief_run.py` | One brief or follow-up from request to saved rows. Outside services come in as adapters |
| `point_in_time.py` | The "known by the requested day" rule for sources, GPR and market sessions |
| `lead_agent.py` | Lead agent, its prompt and the database guide. Built per run |
| `researcher_subagent.py` | The event-researcher subagent and its prompt |
| `research_tools.py` | Per-run research session (search budget, sources) and the agent tools |
| `brief_schema.py` | Structured output: Brief, Event, Exposure |
| `brief_checks.py` | Code checks on a Brief before saving, including which saved event it continues |
| `brief_report.py` | HTML brief and digest pages, rendered from saved assessments |
| `jev_api.py` | Optional Jev (Typesafe) scoring, returned as judgements per event |
| `events_db.py` | All reads and writes of `data/events.db`, including the assessment view reports use |
| `schema.sql` | Database tables, commented |
| `market_returns.py` | Sector ETF vs SPY return windows |
| `models.py` | Models, `.env`, timezone, sector-to-ETF map |

[TypeSafe primitives](https://docs.typesafe.ai/primitives) · [Tavily Python reference](https://docs.tavily.com/sdk/python/reference) · [yfinance download reference](https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html)
