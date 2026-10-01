---
type: Project Overview
title: Event-First Market Agent Overview
description: What the event-market-agent project does, its guarantees, scope, and boundaries. An AI research agent that, for a given day, picks the world events that matter for US markets and names the exposed US-listed stocks and ETFs.
tags: [overview, ai-agent, markets, events, research]
---

# Event-First Market Agent

`event-market-agent` is an AI research agent that answers one question for a given
day: **which world events matter for US markets, and who is exposed?**

It starts from events, not prices. For a date it reads dated news from major
outlets and GDELT leads, then selects up to five significant developments of any
kind - policy, business, conflict, infrastructure, weather. For each one it
explains the channel to US sectors and names the most directly exposed US-listed
stocks and ETFs, with every claim tied to a cited source. An optional scorer
("Jev", via Typesafe) then scores each event's severity, each sector's impact and
direction versus SPY, and each ticker's exposure.

The product is a short daily watch list you can trust and follow over time.

## What it guarantees

- **Grounded.** Only sources published by the end of the requested day can be
  cited. Host code checks every citation, date, and ticker before anything is
  saved (`brief_run.validate_brief`).
- **Tracked.** The same story keeps one event ID across days. `followup`
  researches what changed, and Python measures how linked sector ETFs moved
  against SPY.
- **Reviewable.** Each run writes an HTML brief and a JSON record. A date-range
  digest shows which stories stayed severe. `evals/scorecard.py` checks the
  scorer's direction calls against real sector moves.
- **Bounded.** Search and model-call budgets are fixed, and every run logs its
  cost against a US$10 monthly budget.

## Scope and boundaries

This is a working first slice, run **on demand from the command line**. It is
explicitly *not*:

- a deployed monitor, scheduler, or notification system,
- a trading signal or buy/sell recommendation,
- a causal estimate of market impact,
- a point-in-time backtest or a versioned price warehouse.

Known limitation recorded by the project: the scorer's direction calls have so
far scored **below a simple always-guess-the-common-outcome baseline** (see the
[Evaluation](evals.md) page). Selection quality is not yet measured against a
labelled set.

## Two run types

| Run | Trigger | Produces |
|---|---|---|
| **Brief** | `cli.py brief "<question>"` | Up to five events for a day (or the week ending on the date), each with exposures and tickers |
| **Follow-up** | `cli.py followup <EVENT_ID>` | Exactly one event, re-researched for what changed, plus a Python sector-ETF-vs-SPY return window |

## Key technology

- **Agent framework:** `deepagents` (`create_deep_agent`) with a lead agent and a
  scoped `event-researcher` subagent, built on LangChain / LangChain Anthropic.
- **Models:** Sonnet lead (`strong_model`), Haiku researcher (`model`),
  configurable in `.env`.
- **News search:** Tavily (dated, publication-filtered).
- **Market data:** `yfinance` adjusted daily closes.
- **Optional scoring:** Typesafe / Jev (`typesafe-sdk`).
- **Storage:** a single SQLite database, `data/events.db`.
- **Tracing:** optional LangSmith (one trace per brief).

See [Architecture & Run Flow](architecture.md) for how these fit together, the
[Data Model](data-model.md) for the database, [CLI & Usage](cli-usage.md) for
commands, [Testing & Verification](testing.md) for the test suite, and the
[Source Map](source-map.md) for where each responsibility lives.
