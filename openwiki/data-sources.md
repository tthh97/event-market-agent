---
type: Reference
title: Data Sources & Point-in-Time Rules
description: The external data the event-market-agent uses - Tavily dated news, GDELT daily exports, the Caldara-Iacoviello GPR index, and Yahoo Finance prices - and the single point-in-time rule (known_by) that keeps every brief grounded in what was knowable by the requested day.
tags: [data-sources, gdelt, gpr, market-data, point-in-time, grounding]
---

# Data Sources & Point-in-Time Rules

Every brief is grounded by one rule: a brief for day **D** may only use evidence
that existed by the **end of D** in `EVENT_TIMEZONE`, and never anything later
than now. This rule lives in `point_in_time.py` and is imported by the search,
database, market, run, CLI, and eval code.

## The point-in-time rule

| Function | Meaning |
|---|---|
| `today()` | Current day in `EVENT_TIMEZONE` (default `Asia/Singapore`) |
| `known_by(timestamp, as_of)` | True if the timestamp is before the end of `as_of` and not in the future |
| `local_day(timestamp)` | The `EVENT_TIMEZONE` day a timestamp falls on |
| `last_closed_session_day(as_of)` | Latest day whose US session has closed; today's NY session never counts |

Sources with an unknown or unparseable publication date do **not** satisfy
grounding and are excluded.

## Tavily news search

Dated news is retrieved via Tavily (`research_tools.tavily_search`). The first
sweep runs short topic queries (`TOPIC_QUERIES`) restricted to major outlets
(`MAJOR_OUTLETS` in `models.py`: Reuters, AP, Bloomberg, CNBC, FT, WSJ, BBC, NYT,
Guardian, Economist, Al Jazeera, Nikkei, Politico). Each retrieved source carries
a `major_outlet` flag; the agent prefers them and must flag events resting only on
minor sources.

Date handling (`ResearchSession.date_window`):

- A window **ending today** uses Tavily `time_range` (newest articles).
- **Past dates** use an explicit `start_date`/`end_date` range.
- Either way, the publication-date filter (`known_by` + within the window) decides
  what is kept.

## GDELT daily exports

GDELT rows are **unverified research leads**, not confirmed events. Each daily
file mixes event dates and is distinct from ingestion date, so candidate queries
require the requested event date *and* an ingestion date no later than it.

- `refresh-gdelt` downloads `data.gdeltproject.org/events/YYYYMMDD.export.CSV.zip`;
  every brief runs it for its own date first.
- A date's file is published around **07:00 UTC the next day** (~15:00 Singapore),
  so a brief for *today* has no GDELT leads and relies on Tavily; past dates get
  them.
- Imports handle the **58-column** daily export format (not 61-column GDELT 2.0),
  preserve CAMEO strings, and keep only the **300 most-mentioned URLs per event
  date** (all their rows) to stay Git-friendly - about 4,000 rows / 0.8 MB per day
  versus ~110,000 rows / 25 MB raw. The import vacuums afterwards.
- Media attention is a lead-selection heuristic, **not** event significance,
  verified source independence, or market impact. Source publication dates always
  come from the retrieved reporting, never GDELT ingestion dates.

Reference: [GDELT daily-export codebook](https://data.gdeltproject.org/documentation/GDELT-Data_Format_Codebook.pdf).

## GPR geopolitical risk context

The integration downloads the original official daily **GPR** series (not the
separate AI-GPR product). GPR is a newspaper-based *aggregate* geopolitical risk
index - it does not score individual events or apply to every category, and the
agent must not force it onto weather, corporate, or unrelated events. The brief
shows observation and retrieval dates and makes no automatic trading inference. A
snapshot retrieved *after* a requested historical day is excluded from that
brief (vintage exclusion).

Attribution: Dario Caldara and Matteo Iacoviello, *Measuring Geopolitical Risk*
(2022). [Official GPR methodology and downloads](https://www.matteoiacoviello.com/gpr.htm)
(CC BY).

## Market follow-ups

On a follow-up, `market_returns.py` fetches adjusted daily ETF closes and SPY via
`yfinance`. Because event time is only recorded at date precision, the first
strictly later US trading session is used, with the preceding available close as
baseline. The current New York calendar date is always excluded to avoid
incomplete daily prices.

Reported windows end at **D0, D+1, and D+5** when available (`MarketWindow`
rows). Sector return minus SPY return is a simple percentage-point comparison -
**not** a beta-adjusted abnormal return or proof of causation. Prices are fetched
on demand and preserved in report metrics; no versioned price warehouse is built.

See the [Data Model](data-model.md) for how these inputs are stored and the
[Evaluation](evals.md) page for how direction calls are scored against real moves.
