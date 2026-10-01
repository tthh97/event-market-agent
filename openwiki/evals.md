---
type: Evaluation
title: Evaluation & Scorecard
description: How the event-market-agent is evaluated. evals/scorecard.py scores Jev direction calls against real sector-minus-SPY moves, compares linked vs other sectors, supports human labels, and reports monthly cost, along with the first backfill results and their limitations.
tags: [evaluation, scorecard, metrics, backtest, limitations]
---

# Evaluation & Scorecard

`evals/scorecard.py` scores the saved briefs in a database file. It needs no model
calls, except when `--fill-directions` asks Jev to supply missing direction calls
for backfilled events.

```sh
uv run evals/scorecard.py --db data/backfill-2026-09-17-to-30.db --fill-directions
```

Output: `evals/scorecard-<db>.md` and `.json`, plus an "Eval scorecard" run in
LangSmith.

## What it measures

1. **Jev direction calls.** After the code checks pass, Jev predicts for each
   exposure whether the sector will *beat or trail SPY* over the next five
   sessions, from the event text only (`ExposureDirection`). The scorecard
   compares each call with the Python-computed sector-minus-SPY move at D+1 and
   D+5, next to an **always-guess-the-common-outcome baseline**.
2. **Linked vs other sectors.** Whether the linked sectors moved more than the
   rest after each event.
3. **Your labels.** It creates `evals/labels-<db>.csv`. Fill `worth_watching`
   (yes/no) and `duplicate_of`, then rerun to get the share of events worth
   watching.
4. **Cost this month** from `RunUsage`.

## First result (17–30 Sep backfill)

Scored 30 Sep 2026, prices to 29 Sep (Yahoo Finance adjusted close):

| Window | Jev calls | Jev hit rate | Always-majority baseline | High-confidence (≥0.7) hit rate |
|---|---:|---:|---:|---:|
| D+1 | 28 | 46% | 57% | 40% (15 calls) |
| D+5 | 11 | 46% | 73% | 43% (7 calls) |

**Jev's direction calls did worse than guessing the common outcome**, including
its confident ones. 25 of 56 exposures were called unclear and are not scored.
Linked sectors moved more than the rest in 15 of 29 events at D+1 and 13 of 15 at
D+5.

## Limitations (recorded in the scorecard)

- Events overlap in time, so calls on the same day and sector are not independent.
- A hit only means the **sign matched** - it says nothing about *why* a sector
  moved (not a beta-adjusted abnormal return or causal estimate).
- Directions for backfilled days were asked after the fact, from saved event text.
- Samples are small. Selection quality is not yet measured - there is no labelled
  eval set and no LangSmith evaluation scorecard yet.
- Historical web results may have been edited after their publication date, so this
  is exploratory historical research, not hindsight-free evaluation.

See [Overview](overview.md) for how these caveats frame the project's claims and
[Data Sources & Point-in-Time Rules](data-sources.md) for how market windows are
computed.
