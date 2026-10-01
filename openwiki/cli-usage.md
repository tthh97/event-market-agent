---
type: Usage Guide
title: CLI & Usage
description: Setup, configuration, and command reference for the event-market-agent CLI (cli.py), covering doctor, brief, followup, refresh-gdelt, import-gdelt, candidates, refresh-gpr, list, digest, and cost, plus outputs and cost boundaries.
tags: [cli, usage, setup, commands, configuration]
---

# CLI & Usage

All commands run through `cli.py` with `uv`. The CLI parses the command, wires the
**live adapters** (real Tavily, Claude, Yahoo, Jev, GDELT, and the configured
database), runs the work, and writes reports.

## Setup

Requires Python 3.11–3.14 and `uv`. From the project folder:

```sh
uv sync
cp .env.example .env
# Edit .env to add your keys. Never commit it.
uv run cli.py doctor
uv run cli.py brief "What events happened today that I should watch?" --limit 5
```

### Configuration

Settings live in `.env` (loaded by `models.py`; existing environment variables
take precedence). This project never reads secret *values* into the wiki - only
that the following keys exist:

| Key | Required? | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes (live briefs) | Lead + researcher models |
| `TAVILY_API_KEY` | Yes (live briefs) | Dated news search |
| `TYPESAFE_API_KEY` | Optional | Enables Jev scoring; if absent, output records it was not used |
| `MODEL` / `STRONG_MODEL` | Optional | Override Haiku researcher / Sonnet lead |
| `EVENT_TIMEZONE` | Optional | Day boundaries, default `Asia/Singapore` |

Defaults: Sonnet lead, Haiku researcher, `Asia/Singapore` day boundaries. A brief
may contain fewer than five events (including zero) rather than forcing a quota.

## Command reference

| Command | What it does |
|---|---|
| `doctor` | Checks configuration without exposing secrets |
| `brief "<question>"` | Research a day (or week) and save up to `--limit` events |
| `followup <EVENT_ID>` | Re-research one saved event and measure sector-vs-SPY returns |
| `list` | List saved assessed events |
| `cost [--month YYYY-MM]` | Claude spend for a month against the US$10 budget |
| `candidates --date <d>` | Inspect coverage-ranked raw GDELT leads, no model/search calls |
| `refresh-gdelt [--date <d>]` | Download + import the GDELT daily export (default yesterday UTC) |
| `import-gdelt <path>` | Import a local 58-column GDELT daily export |
| `refresh-gpr` | Download the official daily GPR series |
| `digest --start <d> --end <d>` | HTML page of stories to be aware of over a date range |

### Common examples

```sh
# Inspect raw leads with no model or search calls
uv run cli.py candidates --date 2026-09-29 --limit 12

# Historical-date research (publication dates filtered; not a point-in-time backtest)
uv run cli.py brief "What significant events developed on this date?" --date 2026-09-29 --limit 3

# A week ending on --date, written as one "Weekly brief"
uv run cli.py brief "What were the most important events this week?" --date 2026-09-30

# Save then follow a story
uv run cli.py list
uv run cli.py followup SAVED_EVENT_ID

# Optional controls
uv run cli.py brief "What happened today?" --max-searches 4 --no-jev
```

### Brief options

- `--date` - requested day (default today in `EVENT_TIMEZONE`).
- `--limit` - max events, 1–5 (default 5).
- `--max-searches` - total Tavily searches, 1–10 (default 8).
- `--jev` / `--no-jev` - enable/disable Jev scoring (default on).
- `--period day|week` - override the week/day auto-detection. By default a
  question mentioning the week (week, weekly, 7 days) is treated as a 7-day brief.

## Outputs

Each run writes an HTML brief and a JSON record into `output/` (Git-ignored). The
HTML opens with a **"Keep an eye on"** list ordered by Jev severity, highest
first. Each card shows severity (0–3 with a bar), each linked sector's Jev impact
and direction vs SPY, and the top three tickers by Jev exposure. Without Jev, the
lead's order stands. These are judgements from the news text, **not** measured
price impact.

> Note: a window ending **today** has no GDELT leads yet (the daily file is
> published ~07:00 UTC the next day), so it relies on Tavily. Past dates get GDELT
> leads. A missing or failed GDELT download never stops a brief.

## Cost and boundaries

- **Searches:** default 8 Tavily searches per run - up to 5 topic searches first
  (major outlets only), the rest for the researcher on the open web. Configurable
  1–10.
- **Model calls:** lead ≤ 10; each researcher invocation ≤ 5; bounded token
  outputs and recursion limit 45.
- **Jev:** one request per run, after the code checks, judging events not sources.
- **Budget:** every run saves usage in `RunUsage`; `uv run cli.py cost` shows the
  month against the **US$10** budget. Tavily and Jev are counted but not priced.
- **Tracing:** optional LangSmith, one trace per brief ("Event brief").

## Running the checks

```sh
uv run ruff check .
uv run pytest -q
```

See the [Evaluation](evals.md) page for the scorecard, and the
[Source Map](source-map.md) for where each command's logic lives.
