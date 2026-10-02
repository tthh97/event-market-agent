---
type: integration-reference
title: "External Services: GDELT, Jev, Tavily, Claude, Yahoo Finance, LangSmith"
description: Reference for every external API the pipeline calls - GDELT GKG, Jev/Typesafe, Tavily, Anthropic Claude, Yahoo Finance, and LangSmith - covering request shape, auth, and fallback behavior.
tags: [integrations, external-services, gdelt, typesafe, jev, tavily, anthropic, claude, yfinance, langsmith, configuration]
verified:
  - by: openwiki/0.6.1
    at: 2026-10-02T09:22:29.186Z
sources:
  - id: openwiki-source-83a5c69723e8f477e9b7dcbd
    resource: repo://docs/HOW_IT_WORKS.md
  - id: openwiki-source-7e751dfb10ab9589b7e0b7b3
    resource: repo://gdelt.py
  - id: openwiki-source-a7a5c23be8139fe4abe0cee6
    resource: repo://prices.py
  - id: openwiki-source-00476aec6b96c0910fec8c00
    resource: repo://research.py
  - id: openwiki-source-c37b4623ada9b4863d71905b
    resource: repo://triage.py
  - id: openwiki-source-84ca0b035f7b064786f53671
    resource: repo://verify.py
generated: { by: "openwiki/0.6.1", at: "2026-10-02T09:22:29.186Z" }
---

## Overview

The pipeline (`graph.py:build`, six nodes: `gdelt -> triage -> research -> verify -> price -> output`) calls six external services. Each has a distinct role, a distinct failure mode, and a distinct auth requirement:

| Service | Called from | Auth | Required? | On failure/absence |
|---|---|---|---|---|
| GDELT GKG 2.1 | `gdelt.py` | none | - | 404 per file means GDELT skipped that update; other HTTP errors stop the run |
| Jev / Typesafe | `triage.py`, `verify.py` | `TYPESAFE_API_KEY` | No | Degrades gracefully; `jev_status`/`verify_status` record it |
| Tavily (news search) | `research.py` (`search_news` tool) | `TAVILY_API_KEY` | Yes | Missing key raises `KeyError` inside the tool call; run stops in research |
| Anthropic Claude | `research.py` (agent + reader) | `ANTHROPIC_API_KEY` | Yes | Import succeeds; the run fails at the first Claude call in research |
| Yahoo Finance (yfinance) | `prices.py` | none | - | Any download error, or fewer than 2 closes, yields no moves and an explanatory `price_window` string |
| LangSmith | env vars only, no direct SDK calls | `LANGSMITH_API_KEY` (+ `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`) | No | No traces; with tracing on and no key, the `langsmith` library logs upload errors and the run continues |

<!-- openwiki: broken internal link [../workflows/research-and-verification.md] file "../workflows/research-and-verification.md" does not exist. Fix the href or restore the target, then delete this comment. -->
See [Configuration and Secrets](../operations/configuration-and-secrets.md) for the full env-var table, [Event Pipeline](../workflows/event-pipeline.md) for how `gdelt.py` and `triage.py` fit the graph, [Research and Verification](../workflows/research-and-verification.md) for the Claude/Tavily/Jev loop, and [Pricing and Reporting](../workflows/pricing-and-reporting.md) for the Yahoo Finance step.

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart LR
    GDELT["gdelt.py"] -->|"HTTP GET, no auth"| GD[("GDELT GKG 2.1")]
    TRIAGE["triage.py"] -->|"one request, 30 stories<br/>TYPESAFE_API_KEY"| JEV1[("Jev / Typesafe")]
    RESEARCH["research.py agent"] -->|"up to 3 calls per topic<br/>TAVILY_API_KEY"| TAV[("Tavily news search")]
    RESEARCH -->|"ANTHROPIC_API_KEY"| CLAUDE[("Claude sonnet agent<br/>+ Haiku reader")]
    VERIFY["verify.py"] -->|"one request, all claims<br/>TYPESAFE_API_KEY"| JEV2[("Jev / Typesafe")]
    PRICE["prices.py"] -->|"no auth"| YF[("Yahoo Finance")]
    MAIN["main.py run"] -.->|"env vars only, no SDK calls"| LS[("LangSmith")]
```
*Which node calls which external service, and what authenticates each call.*

## GDELT GKG 2.1

GDELT 2.0 publishes a Global Knowledge Graph (GKG) file every 15 minutes: one row per article, with that article's page title, site, themes, people, organizations and places. `gdelt.py` is the only caller, and it requires no authentication.

**Endpoints**

- `http://data.gdeltproject.org/gdeltv2/lastupdate.txt` (`gdelt.py:LAST_UPDATE`) - fetched once per run to find the newest 15-minute update. Its third whitespace-separated token is a URL whose last path segment's first 14 characters are the timestamp (`gdelt.py:market_rows`).
- `http://data.gdeltproject.org/gdeltv2/{at}.gkg.csv.zip` (`gdelt.py:GKG`) - one zip per 15-minute timestamp, fetched for the `UPDATES = 24` timestamps (6 hours) ending at that newest update, or at 23:45 UTC on `--date` if that is earlier. Only timestamps not already present in `data/events.db`'s `gkg_file` table are downloaded, 8 at a time via a `ThreadPoolExecutor` (`gdelt.py:market_rows`).

`gdelt.py:http_get` treats HTTP 404 as "GDELT skipped this update" and returns `None`; any other non-2xx status raises via `httpx.Response.raise_for_status()`, which propagates and stops the run. A `--date` later than GDELT's newest update raises `ValueError` before any download.

**Row shape.** Each GKG line is tab-separated; `gdelt.py` reads a fixed set of GKG 2.1 columns (`gdelt.py:RECORD, SITE, URL, THEMES, LOCATIONS, PERSONS, ORGANIZATIONS, EXTRAS = 0, 3, 4, 8, 9, 11, 13, 26`):

- `SITE` / `URL`: the publishing site and article URL.
- `THEMES`: a `;`-separated list of `THEME,offset` pairs; `gdelt.py:themes` keeps only the theme names (before the first comma) that intersect `MARKET_THEMES`, a fixed set of 13 economic GKG themes (e.g. `ECON_OILPRICE`, `ECON_INTEREST_RATES`, `FUELPRICES`).
- `LOCATIONS`: `;`-separated location records; `gdelt.py:record` keeps the field after the second `#` as a place name.
- `PERSONS` / `ORGANIZATIONS`: `;`-separated name lists, merged into one `actors` set.
- `EXTRAS` (column 26): free-form XML-like text; `gdelt.py:record` extracts the `<PAGE_TITLE>...</PAGE_TITLE>` value with a regex. A row is kept only if it has at least one market theme, a page title, and a non-empty `GKGRECORDID`.

Kept rows are appended to `gkg_row` (keyed by `GKGRECORDID`, `ON CONFLICT DO NOTHING`) and the file itself to `gkg_file`, in one transaction per file so a file is marked downloaded only together with its rows; a skipped (404) file is still recorded, with `rows = 0`, so it is never re-requested. The window's rows are then read back from the database with one `SELECT`, which is how a run 15 minutes after a previous one downloads only 1 new file instead of 24.

## Jev / Typesafe (`typesafe_sdk.TypeSafeClient`)

Jev is an external scoring/judgment service reached through the `typesafe_sdk` package. It has exactly two callers in this pipeline, each making one batched request per node run rather than one request per item.

**`triage.py` - severity scoring.** `triage.py:jev_scores` builds one `Score` question per story (`s0`, `s1`, ...), each with the same `RUBRIC` (4 levels, 0 minor to 3 severe) and per-story instructions, and sends all of them plus all stories' `describe()` output (title + a one-line GDELT-derived summary, not article text) in a single `TypeSafeClient(timeout=90).system_one(...)` call. The returned `{score, confidence}` per story is used to sort stories and keep the `KEEP = 3` highest; there is no minimum score threshold (the module docstring records that on 30 Sep 2026 the highest of 100 stories scored 1.07, so any floor at 1.0 or 2.0 would have kept nothing).

**`verify.py` - claim checks.** `verify.py:jev_checks` asks three questions per claim in one request: `supported` (does the matched source state what the claim says, a `Noul` probability), `current` (is the claim about the 7 days ending `as_of`, also `Noul`), and `sector` (a `Choice` among 11 US stock-market sectors plus `"none"`, each with a defining description in `verify.py:SECTORS`). All claims across all findings are sent together as one `state` dict and one `questions` dict to a single `TypeSafeClient(timeout=90).system_one(...)` call.

**Thresholds applied by code, not by Jev** (`verify.py:review`): `supported` or `current` below `DOUBT = 0.4` removes the claim; below `SURE = 0.7` (but at or above `DOUBT`) keeps it with `unverified=True`; the sector is attached only when Jev's sector confidence is at least `SECTOR_SURE = 0.75` and the chosen sector is not `"none"`.

**Auth and degradation.** Both callers read `TYPESAFE_API_KEY` via `os.getenv` before calling (`triage.py:50`, `verify.py:84`); the key is also read internally by `TypeSafeClient`. Both degrade the same way, recording the degraded state in a status string instead of raising:

- Triage: without the key, or if `jev_scores` raises, `triage.py:triage` keeps the first `KEEP` stories from the GDELT ranking (most widely published, not most severe), with `severity=None`, and sets `jev_status` to `"not configured: ..."` or `"failed (<ExceptionType>): ..."`.
- Verify: without the key, or if `jev_checks` raises, only the code-side checks (URL membership, number matching, source-date replacement) run; no claim gets a sector; `verify_status` records the reason. Since no sector means no ETF to price, a degraded verify step causes `prices.py:price` to return empty moves for that run.

## Tavily (news search)

Tavily is the only web-search tool the research agent can call, reached exclusively through the `search_news` tool defined inside `research.py:research_topic`. There is no fallback: `TAVILY_API_KEY` is read with `os.environ["TAVILY_API_KEY"]` (`research.py:107`), not `os.getenv`, so a missing key raises `KeyError` the first time Claude invokes the tool, and LangChain's default tool error handling re-raises non-validation errors, stopping the run inside the research node with no report written.

**Request shape.** Each call is `TavilyClient(...).search(query=query, topic="news", max_results=5, start_date=(as_of - 6 days), end_date=as_of)` — a 7-day window ending on the run's `as_of` day, at most 5 results. A per-topic counter (`searches`, closed over by the tool) enforces `MAX_SEARCHES = 3`: a 4th call on the same topic returns the literal string `"Search limit reached. Answer with what you have."` without calling Tavily. Since each of the (at most 3) severe stories gets its own agent and its own tool-closure counter, a full new-report run makes at most 3 topics x 3 searches = 9 Tavily calls; a follow-up run has a single topic, so at most 3 calls.

<!-- openwiki: broken internal link [../workflows/research-and-verification.md] file "../workflows/research-and-verification.md" does not exist. Fix the href or restore the target, then delete this comment. -->
**Response handling.** Results with a known `published_date` that parses (via `research.py:published_day`, trying RFC-2822 then ISO date) to a day after `as_of` are dropped as published in the future relative to the run. Surviving results become `Source(url, title, published, excerpt)` objects (`excerpt` truncated to the first 1500 characters) and are both returned to the agent (after passing through the Haiku reader subagent, see [Research and Verification](../workflows/research-and-verification.md)) and recorded in a `sources` dict keyed by URL — this dict becomes `state.sources`, the only set of URLs a claim may later cite; `verify.py:review` rejects any claim whose `source_url` is not in it.

## Anthropic Claude

Claude is the only LLM provider in the pipeline, used for two distinct roles inside `research.py`, both authenticated by the single required `ANTHROPIC_API_KEY` environment variable (read implicitly by `langchain_anthropic`/`init_chat_model`, not fetched explicitly in repo code). There is no fallback: a missing key lets the process start and earlier nodes (`gdelt`, `triage`) complete and persist to `data/threads.db`, but the run fails at the first Claude call inside `research`, so no report is written for that invocation.

**Research agent (the main agent).** Model is `RESEARCH_MODEL` env var, default `anthropic:claude-sonnet-5-5`, constructed once at import time with `init_chat_model(..., timeout=120, max_tokens=8000)` (`research.py:27`). A fresh agent is created per topic via `langchain.agents.create_agent(MODEL, tools=[search_news], system_prompt=PROMPT, response_format=Finding)` — up to 3 per new report (one per severe story) and exactly 1 per follow-up question, each starting with empty context. The agent's only tool is `search_news`; its final answer is constrained to the `Finding` Pydantic model (topic, summary, claims) via `response_format`, invoked with `recursion_limit=20`.

**Reader subagent.** A fixed, non-configurable model, `anthropic:claude-haiku-4-5-20251001`, constructed once at import with `timeout=60, max_tokens=4000` (`research.py:30`, no env var). `research.py:read_articles` creates one `create_agent(READER, system_prompt=READER_PROMPT, response_format=Notes)` invocation per `search_news` call that returned at least one article — so at most 9 per new-report run. It has no tools; it copies, per article, the exact sentences relevant to the query into a `Notes`/`Reading` structured response. If the reader call raises for any reason, `search_news` falls back to showing the research agent the full 1500-character excerpts instead of curated quotes (`research.py:120-123`); `verify.py` always checks claim numbers against the full excerpt regardless of which form the agent saw.

Both models are LangChain chat-model objects created once at module import (not per-call), so process startup requires valid Anthropic SDK configuration even before any node runs, though the key itself is only exercised on the first actual completion request.

## Yahoo Finance (yfinance)

`prices.py` is the sole caller, using the `yfinance` package with no authentication. `prices.py:yahoo_close` calls `yf.download(symbols, start=..., end=..., auto_adjust=True, progress=False, threads=False)` and returns the adjusted `"Close"` column (or an empty `DataFrame` if the response is empty).

**Symbols and window.** `prices.py:sector_moves` maps each distinct sector found on kept claims to its SPDR sector ETF via `SECTOR_ETFS` (11 entries, e.g. `energy -> XLE`, `financials -> XLF`, `information_technology -> XLK`) and always adds `SPY` as the market benchmark. It requests the 10 days before `as_of` up to `min(as_of + 1 day, today)`, with the end date exclusive — today's session is deliberately excluded because it may still be open. The last two available closes are compared: `move % = close[-1]/close[-2] - 1`, and `vs SPY pts = (move % for the ETF) - (move % for SPY)`, both expressed as percentage points.

**Fallback.** Wrapped in `try/except Exception`: a download error returns `({}, "Prices unavailable: <ExceptionType>.")`. After a successful download, if `"SPY"` is missing from the columns or fewer than 2 rows remain after dropping all-NaN rows, it returns `({}, "Prices unavailable: not enough closed sessions.")`. On success, the `price_window` string documents the exact two dates compared and a standing caveat: "Observed moves, not proof that the news caused them." If `prices.price` finds no sectors on any kept claim at all (e.g. verify degraded with no `TYPESAFE_API_KEY`, so no claim got a sector), it skips the download entirely and returns empty moves with an empty `price_window`.

## LangSmith

LangSmith is enabled purely through environment variables — `LANGSMITH_TRACING`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` (all declared in `.env.example`) — and no file in this repository calls the `langsmith` SDK directly. Tracing, when on, is driven entirely by LangChain's and LangGraph's built-in instrumentation.

**Trace/span shape**, per `docs/HOW_IT_WORKS.md` section 4:

- One `main.py` invocation produces one trace, named `"Event graph"` (set at `main.py:49`).
- Inside that trace, each of the six graph nodes (`gdelt`, `triage`, `research`, `verify`, `price`, `output`) appears as a child run.
- Inside the `research` node's run, each topic's agent invocation is its own child run, named `research: <first 60 chars of topic>` (set via `config={"run_name": ...}` in `research.py:research_topic`). Under each of those, Claude's completion calls and each `search_news` tool call (with its query and returned text) appear as further children.
- Inside each `search_news` tool call, when the reader subagent runs, it appears as a nested child run named `reader: <first 60 chars of query>` (`research.py:read_articles`), containing the Haiku completion call and its structured `Notes` output.

Jev, the raw Tavily HTTP call, and yfinance are **not** traced as distinct LLM or HTTP runs: Jev and yfinance appear only implicitly as part of their enclosing node's run span, and the Tavily call is visible only as the `search_news` tool-call run (its request/response are not separately instrumented).

**Thread grouping.** `main.py` passes `thread_id` inside the graph's `configurable` config; `langchain_core` copies string `configurable` values into trace metadata, so every trace carries `thread_id` and LangSmith groups an initial run together with its follow-ups as one thread. `main.py` additionally sets `metadata={"thread": thread}`, a duplicate of the same value under a second key (noted as redundant in `docs/HOW_IT_WORKS.md`).

**Scope caveat.** `uv run langgraph dev` serves `graph.py:graph`, compiled without the SQLite checkpointer/store that `main.py` uses; the LangGraph dev server supplies its own checkpointer and store, so Studio threads and Studio's cross-report findings memory (the `recall`/`remember` mechanism in `research.py`) are separate from `data/threads.db` and not exercised by the CLI's persistence path.

## Operational notes

- Required keys for any CLI run: `ANTHROPIC_API_KEY` and `TAVILY_API_KEY`. Without either, the run always fails inside `research`, after `gdelt` and `triage` have already persisted their state.
- `TYPESAFE_API_KEY` is the only optional key with graceful, status-reported degradation at two call sites (`triage.py`, `verify.py`); it never aborts a run.
- `GDELT` and `Yahoo Finance` need no credentials at all; both failure modes (404 per-file skip for GDELT, exception/insufficient-data fallback for Yahoo Finance) are handled inline without affecting unrelated parts of the run.
- `LANGSMITH_*` variables are purely observational: absent or wrong, the pipeline's behavior and output are identical, only tracing visibility changes.
- None of these integrations are tested with live network calls; `tests/test_nodes.py` stubs GDELT, Jev and yfinance and does not exercise the research agent's tool loop (so Tavily and the live Claude/Haiku calls are untested in CI).
