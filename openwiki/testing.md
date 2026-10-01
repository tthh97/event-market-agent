---
type: Testing Guide
title: Testing & Verification
description: The event-market-agent test suite and how it verifies the pipeline end to end. tests/test_pipeline.py drives brief_run with stand-in adapters to check grounding, validation, event tracking, Jev scoring, market windows, reports, cost maths, and the scorecard.
tags: [testing, verification, pytest, adapters, quality]
---

# Testing & Verification

The suite proves the pipeline behaves without any live model, search, price, or
Jev call. It drives `brief_run.run` through the `Adapters` boundary with
**stand-in adapters** (helpers such as `fake_news` and `fake_prices` in
`tests/test_pipeline.py`), so every test is fast and gives the same result every
time. `tests/conftest.py` only turns LangSmith tracing off for every test.

```sh
uv run ruff check .
uv run pytest -q
```

All tests live in `tests/test_pipeline.py`. The `[tool.pytest.ini_options]` in
`pyproject.toml` sets `pythonpath = ["."]` and `testpaths = ["tests"]`, so tests
import the top-level modules directly.

## What the suite covers

The test functions map to the project's core guarantees:

| Area | Representative tests |
|---|---|
| **GDELT ingest** | `test_import_dates_and_idempotency`, `test_gdelt_import_keeps_most_mentioned_urls_per_date`, `test_refresh_gdelt_downloads_unzips_and_skips_known_files` |
| **Point-in-time grounding** | `test_known_by_uses_end_of_day_in_event_timezone_and_never_the_future`, `test_today_uses_time_range_and_past_dates_use_a_date_range` |
| **Code checks (validation)** | `test_citation_and_future_date_rejection`, `test_unlisted_tickers_are_dropped_before_jev`, `test_top_three_tickers_follow_jev_fit_and_benchmarks_are_rejected` |
| **Event tracking** | `test_followup_attaches_to_the_saved_event_and_preserves_date`, `test_brief_attaches_to_saved_event_instead_of_duplicating` |
| **Search budget & sources** | `test_first_sweep_reads_major_outlets_and_flags_every_source`, `test_search_budget_dates_and_tool_invocation` |
| **Jev scoring** | `test_jev_assessment_asks_severity_direction_impact_and_ticker_fit_and_is_saved`, `test_scorecard_asks_jev_for_directions_only` |
| **Market windows** | `test_market_weekend_baseline_and_incomplete_windows`, `test_followup_run_reuses_the_event_and_measures_sector_windows` |
| **GPR vintage** | `test_gpr_future_vintage_excluded`, `test_gpr_prefers_real_date_over_numeric_day` |
| **Reports** | `test_html_report_puts_watch_list_first_and_escapes_text`, `test_html_orders_events_by_jev_severity_and_shows_metrics`, `test_report_lists_saved_sources_and_exposure_type`, `test_digest_ranks_stories_by_peak_severity_and_keeps_each_day` |
| **Weekly briefs** | `test_weekly_question_researches_the_whole_week_and_writes_one_weekly_report` |
| **Read-only SQL** | `test_read_sql_is_read_only_and_uses_chinook_style_tables` |
| **Cost maths** | `test_claude_cost_prices_cache_reads_and_writes_separately` |
| **Scorecard** | `test_scorecard_direction_hits_and_measure_start` |
| **Full run** | `test_runner_saves_validated_artifacts_with_stand_in_adapters` |

## Why stand-in adapters matter

Because `brief_run.run` receives every external service through
`brief_run.Adapters`, tests pass callables that return canned responses instead of
hitting Tavily, Claude, Yahoo, GDELT, or Jev. This is the same seam `cli.py` uses
to wire live adapters (see [Architecture & Run Flow](architecture.md)). The host's
deterministic checks (`validate_brief`, `resolve_tracking`, `drop_unlisted_tickers`)
are therefore fully exercised in isolation from model behavior.

## Live verification

Beyond the unit suite, the README records live-run verification (real briefs,
follow-ups, GDELT refresh, GPR download, and Jev requests). Those runs are
evidence the adapters work against the real services. `output/` is Git-ignored,
so run reports are not part of the repository. For how model output quality is scored against real market moves, see the
[Evaluation](evals.md) page.
