# Scorecard: data/backfill-2026-09-17-to-30.db

Scored 2026-09-30T19:30 UTC. Prices to 2026-09-29 (Yahoo Finance adjusted close).

## Jev direction calls vs sector-minus-SPY

| Window | Calls | Hit rate | Always-majority baseline | Calls with confidence >= 0.7 | Their hit rate | Events |
|---|---:|---:|---:|---:|---:|---:|
| D+1 | 28 | 46% | 57% | 15 | 40% | 20 |
| D+5 | 11 | 46% | 73% | 7 | 43% | 9 |

25 of 56 exposures were called unclear and are not scored.

## Linked sectors vs the rest (average size of move vs SPY)

| Window | Events | Linked | Others | Linked moved more |
|---|---:|---:|---:|---:|
| D+1 | 29 | 1.19 pp | 1.08 pp | 15 |
| D+5 | 15 | 2.63 pp | 1.92 pp | 13 |

## Your labels

0 of 33 events labelled in `evals/labels-backfill-2026-09-17-to-30.csv`. Share worth watching: not yet (no labels). Duplicates flagged: 0.

## Cost this month

14 runs, 0 with cost data. Claude spend US$0.00 of the US$10 budget.

## Limits

- Events overlap in time, so calls on the same day and sector are not independent.
- A hit only means the sign matched. It says nothing about why the sector moved.
- Directions for backfilled days were asked after the fact, from the saved event text only.
