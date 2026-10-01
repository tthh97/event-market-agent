---
type: Project Status
title: Project Status & Current State
description: Where the event-market-agent stands today - what works, what has been consolidated, the known limitations and open problems, and how this differs from the stale HANDOFF.md. A grounded snapshot for anyone picking the project up.
tags: [status, current-state, limitations, roadmap, handoff]
---

# Project Status & Current State

A grounded snapshot of where `event-market-agent` stands, read from the current
source and tests rather than from the repository's `HANDOFF.md` (which predates
the module consolidation - see the note at the end).

## What works today

- **Live briefs, follow-ups, and Jev scoring** run end to end. The README's
  verification section records real runs: live `refresh-gdelt`, live briefs for
  several dates, a live follow-up that preserved the event ID and stored a market
  window, live GPR download, and live Jev requests returning `ok`.
- **Deterministic host checks** gate every save: citation IDs, publication dates,
  event count, benchmark/duplicate ticker rejection, unlisted-ticker dropping, and
  event-tracking rules (`brief_run.validate_brief`, `resolve_tracking`).
- **Event tracking across days** attaches repeat stories to a saved `EventId`
  through `tracked_event_id`; `followup EVENT_ID` is the guaranteed link.
- **Market follow-ups** compute sector-ETF-vs-SPY return windows (D0/D+1/D+5) in
  Python and save them to `MarketWindow`.
- **Cost logging** writes `RunUsage` per run; `uv run cli.py cost` reports the
  month against the US$10 budget.
- **Tests and linting pass.** The suite is now 27 tests in
  `tests/test_pipeline.py`, driven entirely through stand-in adapters - see
  [Testing & Verification](testing.md).

## Recent consolidation

The codebase was refactored from the file layout the handoff describes into the
current, smaller set of modules:

| Handoff file (no longer present) | Now lives in |
|---|---|
| `researcher_subagent.py` | `lead_agent.py` (the `event-researcher` subagent spec) |
| `brief_schema.py` | `lead_agent.py` (the `Brief`/`Event`/`Exposure`/`Ticker` models) |
| `brief_checks.py` | `brief_run.py` (`validate_brief`, `resolve_tracking`, ...) |

The [Source Map](source-map.md) reflects the current layout.

## Known limitations

These are inherent to the current slice and are stated across the wiki and README:

- **Scorer not validated.** Jev direction calls scored **below** an
  always-guess-the-common-outcome baseline on the first backfill (46% vs 57% at
  D+1; 46% vs 73% at D+5). Jev does not clearly separate events from commentary.
  See [Evaluation & Scorecard](evals.md).
- **Selection quality is unmeasured.** There is no labelled "worth watching" eval
  set yet, and no LangSmith evaluation scorecard.
- **On-demand only.** No scheduler, alerts, autonomous follow-ups, trading
  signals, or causal attribution - by design.
- **Grounding is temporal, not semantic.** Date/citation checks catch invented IDs
  and future sources; whether a citation *supports* a claim still depends on model
  quality and review.

## Open problems (next steps)

Carried forward from the project's own backlog, limited to those still open:

1. **Decide Jev's role** - advisory scorer vs eval-only, given it does not
   separate events from commentary.
2. **Label past days** - fill `evals/labels-backfill-2026-09-17-to-30.csv`
   (`worth_watching`, `duplicate_of`) so selection quality can be scored.
3. **Weak sources** - a single mid-tier source can carry an event; consider
   requiring two independent sources or a primary one.
4. **Fixed category list** - `category` is free text today (many spellings);
   make it an enum.
5. **Commentary saved as events** - add a check that an "event" has a dated
   development, not just ongoing commentary.
6. **Score ticker picks vs SPY** in `evals/scorecard.py`.

## Note on `HANDOFF.md`

The repository's `HANDOFF.md` (dated 1 October 2026) is **stale**: it lists the
pre-consolidation files above, states the project "has no Git repo of its own"
(it now has its own `.git/`), and reports an older test count. It remains useful
for the *decision history and rationale* ("Settled decisions", "Don't retry"),
but for the current file layout and counts, trust the source and this wiki.
`HANDOFF.md` is a source file, so the wiki does not edit it.

See the [Overview](overview.md) for scope and the [Evaluation & Scorecard](evals.md)
page for the measured results behind these caveats.
