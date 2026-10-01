---
type: Source Map
title: Source Map
description: File-by-file guide to the event-market-agent source tree, mapping each module and directory to its responsibility, plus key dependencies and entry points, so humans and agents can find the right place to read or change behavior.
tags: [source-map, files, navigation, modules]
---

# Source Map

Where each responsibility lives in `event-market-agent/`. The design keeps all
outside services behind the `brief_run.Adapters` boundary so the core run logic can be
tested offline with stand-ins.

## Modules

| File | Responsibility |
|---|---|
| `cli.py` | Command line (`import-gdelt`, `brief`, `followup`, `list`, `digest`, `doctor`, `cost`, `candidates`, `refresh-gdelt`, `refresh-gpr`). Wires the live adapters, writes reports, prints results. Entry point. |
| `brief_run.py` | One brief or follow-up from request to saved rows. Holds `Request`, `Adapters`, `Result`, the orchestration (`run`), and the deterministic checks (`validate_brief`, `resolve_tracking`, `drop_unlisted_tickers`, `usage_rows`). |
| `lead_agent.py` | The lead deep agent, its `event-researcher` subagent, their prompts, the database guide, and the `Brief`/`Event`/`Exposure`/`Ticker` Pydantic output shapes. Built per run via `build(tools)`. |
| `research_tools.py` | Per-run `ResearchSession` (search budget, dated source registry) and the agent tools: `research_news`, `read_sources`, `read_sql`. Live Tavily adapter and `TOPIC_QUERIES`. |
| `point_in_time.py` | The "known by the requested day" rule for sources, GPR, and market sessions (`today`, `known_by`, `local_day`, `last_closed_session_day`, `parse_timestamp`). |
| `events_db.py` | All reads and writes of `data/events.db`: GDELT/GPR ingest, candidate ranking, saved-event lookups, `save_run`, read-only query, cost summary, the assessment view reports use. |
| `schema.sql` | Database tables, commented. |
| `market_returns.py` | Sector-ETF-vs-SPY return windows (`reactions`), ticker listing checks (`listed`), and the live `yahoo_close` price adapter. |
| `jev_api.py` | Optional Jev (Typesafe) scoring: builds per-event payloads and returns severity/impact/direction/exposure `Judgements`. |
| `brief_report.py` | Renders the HTML brief and digest pages from saved assessments. |
| `models.py` | Shared settings: models (`model`, `strong_model`), `.env` loading, `TIMEZONE`, `SECTORS` sector→ETF map, `MAJOR_OUTLETS`, Claude pricing, and the monthly budget. |

## Module dependencies

Each module imports only from modules below it. `models` and `jev_api` import nothing
from the project.

```text
cli ─┬─> brief_run ─┬─> research_tools ─> events_db ─> point_in_time ─> models
     │              ├─> lead_agent ─────────────────────────────────> models
     │              ├─> market_returns ─> point_in_time
     │              ├─> jev_api
     │              └─> events_db
     └─> brief_report ─> events_db, jev_api
evals/scorecard ─> events_db, jev_api, market_returns
```

## Directories

| Path | Contents |
|---|---|
| `data/` | `events.db` (tracked in Git, ships with the project). |
| `output/` | Generated HTML + JSON reports (Git-ignored). |
| `evals/` | `scorecard.py` and generated scorecards/labels - see [Evaluation](evals.md). |
| `tests/` | Pytest suite exercising the run with stand-in adapters - see [Testing & Verification](testing.md). |

## Entry points & tooling

- **Run:** `uv run cli.py <command>` (see [CLI & Usage](cli-usage.md)).
- **Checks:** `uv run ruff check .` and `uv run pytest -q`.
- **Packaging:** `pyproject.toml` (deps include `deepagents`, `langchain-anthropic`,
  `tavily-python`, `typesafe-sdk`, `yfinance`, `pydantic`, `langsmith`); `uv.lock`
  pins versions.

## Where to make common changes

| To change… | Edit |
|---|---|
| Agent behavior / prompts / output schema | `lead_agent.py` |
| What counts as valid output (counts, dates, citations, tickers, tracking) | `brief_run.py` (`validate_brief`, `resolve_tracking`) |
| Search budget, queries, source filtering | `research_tools.py` |
| Grounding / timezone / "known by" logic | `point_in_time.py`, `models.py` (`EVENT_TIMEZONE`) |
| Tables or stored fields | `schema.sql` + `events_db.py` |
| Sector→ETF mapping, models, pricing, major outlets | `models.py` |
| Market return windows | `market_returns.py` |
| Report layout | `brief_report.py` |

See [Architecture & Run Flow](architecture.md) for how these modules interact at
runtime.
