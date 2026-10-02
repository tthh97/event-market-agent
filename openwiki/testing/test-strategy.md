---
type: testing-guide
title: "Test Strategy: Stubbed Nodes, No Network"
description: How tests/test_nodes.py verifies each plain and LLM-backed graph node in isolation, using monkeypatched Jev/create_agent calls, a fake GDELT HTTP server, and tmp_path/InMemoryStore fixtures instead of real network or model calls.
tags: [testing, pytest, nodes, jev, typesafe, gdelt, monkeypatch, stubbing]
verified:
  - by: openwiki/0.6.1
    at: 2026-10-02T09:22:29.186Z
sources:
  - id: openwiki-source-7e751dfb10ab9589b7e0b7b3
    resource: repo://gdelt.py
  - id: openwiki-source-b9a2305b0f09bb933a03007f
    resource: repo://graph.py
  - id: openwiki-source-ac1481dec664fdfda1573933
    resource: repo://output.py
  - id: openwiki-source-a7a5c23be8139fe4abe0cee6
    resource: repo://prices.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-00476aec6b96c0910fec8c00
    resource: repo://research.py
  - id: openwiki-source-0dc6ea1593405ef6df2682c0
    resource: repo://state.py
  - id: openwiki-source-ba8c498e9a3e8775c04aac2e
    resource: repo://tests/test_nodes.py
  - id: openwiki-source-c37b4623ada9b4863d71905b
    resource: repo://triage.py
  - id: openwiki-source-84ca0b035f7b064786f53671
    resource: repo://verify.py
generated: { by: "openwiki/0.6.1", at: "2026-10-02T09:22:29.186Z" }
---

## Why this exists

The graph's nodes (`gdelt`, `triage`, `research`, `verify`, `price`, `output`) call out to GDELT over
HTTP, to Jev (Typesafe) for severity scoring and claim checking, to Claude models for research and
reading, and to Yahoo Finance for prices. `tests/test_nodes.py` exercises every node's own logic —
routing, filtering, ranking, persistence — without ever making a real network or model call. Each
node is tested by substituting the one external call it makes with a small, explicit stand-in, so
the test asserts the node's control flow and data shaping rather than the behavior of GDELT, Jev,
Anthropic, Tavily or Yahoo Finance.

This is the single test module for the project; there is no per-node test file. Related system
behavior is described in [Graph and State](../architecture/graph-and-state.md), and the nodes under
test are the ones described in [Event Pipeline](../workflows/event-pipeline.md),
[Pricing and Reporting](../workflows/pricing-and-reporting.md) and
<!-- openwiki: broken internal link [../workflows/research-and-verification.md] file "../workflows/research-and-verification.md" does not exist. Fix the href or restore the target, then delete this comment. -->
[Research and Verification](../workflows/research-and-verification.md).

## Running the suite

```
uv run pytest -q
uv run ruff check .
```

`pyproject.toml` sets `[tool.pytest.ini_options]` with `pythonpath = ["."]` and
`testpaths = ["tests"]`, so `db`, `gdelt`, `output`, `prices`, `research`, `triage`, `verify`,
`graph` and `state` import as top-level modules straight from the repository root — no package
installation or `src` layout is needed for tests to find them.

## Stubbing Jev: monkeypatching the scoring/check functions, not the HTTP layer

`triage.py` and `verify.py` each call Jev (the Typesafe SDK) through one small function —
`triage.jev_scores(stories)` and `verify.jev_checks(claims, as_of)` — that wraps a single
`TypeSafeClient.system_one` request and reshapes its answers. Tests never construct a
`TypeSafeClient`; instead they monkeypatch that one wrapper function directly:

```python
monkeypatch.setattr(triage, "jev_scores", lambda stories: {i: (float(i), 0.5) for i in range(len(stories))})
monkeypatch.setattr(verify, "jev_checks", lambda claims, as_of: [
    (0.95, 0.9, "energy", 0.9), (0.6, 0.9, "energy", 0.5), (0.9, 0.2, "none", 0.9)])
```

This keeps the test focused on what `triage()`/`verify()` do with Jev's answers — sorting by
severity and keeping the top `KEEP` stories, or applying the `supported`/`current`/`sector`
thresholds to decide whether to keep, mark unverified, or reject a claim — rather than on how the
SDK call is made.

Both nodes also behave differently depending on whether `TYPESAFE_API_KEY` is set, so tests toggle
it with `monkeypatch.setenv`/`monkeypatch.delenv`:

- `monkeypatch.setenv("TYPESAFE_API_KEY", "test")` together with a stubbed `jev_scores`/`jev_checks`
  exercises the Jev path: `test_triage_keeps_the_most_severe` checks that stories are ranked by the
  stubbed severity scores; `test_verify_routes_claims_on_jev_confidence` checks the
  supported/current/sector thresholds (`SURE`, `DOUBT`, `SECTOR_SURE`) that decide keep vs.
  unverified vs. reject, and that `verify_status` reports `"ok: Jev checked N claims"`.
- `monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)` exercises the no-Jev fallback: without the
  key, `triage.triage` never calls `jev_scores` at all — it keeps the most-mentioned stories with
  `severity=None` and a `jev_status` starting with `"not configured"`
  (`test_triage_without_jev_says_so`); `verify.verify` falls back to only its own code checks (an
  unsourced URL, or a number in the claim that is not in the source excerpt) and reports
  `verify_status` as `"not configured: URLs and numbers checked only"`
  (`test_verify_removes_unsourced_claims_and_invented_numbers`).

Because the env var and the stubbed function are independent, tests can also exercise a configured
key whose request fails, which both nodes degrade to the same style of fallback and status message
(`"failed (<ExceptionType>): ..."`) — though the current suite focuses on the "not configured" and
"ok" branches directly.

## `output.py`: end-to-end against `tmp_path` and an `InMemoryStore`

`output.output(state, config, runtime)` is plain Python with four externally visible effects: it
writes a Markdown report, writes a matching JSON payload, appends rows to `data/events.db` (via
`db.py`), and — for a new (non-follow-up) report — stores findings in the LangGraph store for later
recall. `test_output_writes_json_and_markdown_and_replies` exercises all four in one test, without
touching the real `output/` folder or `data/events.db`:

```python
monkeypatch.setattr(output, "FOLDER", tmp_path)
monkeypatch.setattr(db, "PATH", tmp_path / "events.db")
store = InMemoryStore()
...
result = output.output(state, {"configurable": {"thread_id": "t1"}}, Runtime(store=store))
```

Redirecting `output.FOLDER` and `db.PATH` to `tmp_path` means the report, its `.json` sibling, and
`events.db` are all written under pytest's temporary directory and cleaned up automatically. The
test then asserts on each effect directly:

- **Markdown text** — reads `result["report"]` from disk and checks that it contains the expected
  severity line (`"Jev severity: 2.50 of 3"`), the claim's sector annotation (`"Sector: energy."`),
  the price-move table row (`"| energy | XLE | +2.0% | +1.8 pts |"`), and the price window sentence.
- **JSON payload** — loads the `.json` sibling of the report and checks a representative field
  (`findings[0]["topic"] == "Oil"`).
- **Reply message** — asserts `result["messages"][0].content` equals the same Markdown text, i.e.
  the node's `AIMessage` reply is exactly the report it wrote.
- **`events.db` rows** — opens a connection via `db.connect()` (pointed at the monkeypatched path)
  and checks the `run`, `story`, `claim` and `price_move` tables each got the expected row(s),
  confirming `output.log` wrote a consistent, denormalized record of the run.
- **Store item for recall** — calls `store.search(("findings", "2026-09-30"))` on the same
  `InMemoryStore` passed into the node and checks the stored item's `topic`, confirming
  `research.remember` was invoked with the day's findings so a later report can recall them.

## `prices.sector_moves`: injecting `download` and guarding against numpy scalars

`prices.sector_moves(sectors, as_of, download=yahoo_close)` takes its data-fetching dependency as a
parameter, defaulting to `yahoo_close` (which calls `yf.download`). Tests never call Yahoo Finance;
they pass a `download` callable that ignores its arguments (beyond recording them) and returns a
canned `pandas.DataFrame` of closes:

```python
frame = pd.DataFrame({"XLE": [100.0, 102.0], "SPY": [500.0, 501.0]},
                     index=pd.to_datetime(["2026-09-29", "2026-09-30"]))
asked = []
moves, window = prices.sector_moves(["energy"], date(2026, 9, 30),
                                    lambda symbols, start, end: asked.append(symbols) or frame)
```

`test_sector_moves_measure_the_last_closed_session_against_spy` asserts the symbols requested
(`["SPY", "XLE"]`, sorted), the computed move and vs.-SPY numbers via `pytest.approx`, and the
price-window sentence. It also asserts `all(type(value) is float for value in moves["energy"])` —
an explicit guard that `sector_moves` converts pandas/numpy scalars (`numpy.float64`) to plain
Python `float` before they land in `moves`. This matters because `State.moves` flows into
`output.output`'s JSON dump and into the LangGraph checkpoint via `State`; a `numpy.float64` is not
one of the whitelisted checkpoint types (`CHECKPOINT_TYPES` in `state.py` only covers the
`state`-module Pydantic models) and would be incompatible with checkpoint serialization, so this
assertion protects an invariant that would otherwise fail far away from `prices.py`, inside
checkpointing. `test_price_without_sectors_asks_for_no_prices` covers the short-circuit: with no
sector on any kept claim, `prices.price` returns `{"moves": {}, "price_window": ""}` without
calling `sector_moves` at all.

## The research reader subagent: faking `research.create_agent`

`research.read_articles(query, articles)` builds a `create_agent(READER, ...)` and calls
`.invoke(...)["structured_response"]` to get back `Notes` (a list of `Reading`, each an article
index and the sentences copied from it). `test_reader_keeps_only_articles_it_quotes` replaces
`research.create_agent` itself with a factory that returns a fake object exposing only `.invoke`:

```python
class Reader:
    def invoke(self, inputs, config):
        assert "[0] Fed holds" in inputs["messages"][0]["content"] and "[1] Oil jumps" in inputs["messages"][0]["content"]
        return {"structured_response": notes}
monkeypatch.setattr(research, "create_agent", lambda *args, **kwargs: Reader())
```

This verifies two things at once: that `read_articles` formats the articles into the prompt
correctly (numbered `[i] title` blocks the fake asserts on), and that it filters the agent's
`Notes` down to `{article index: sentences}`, dropping readings with an out-of-range article index
or no sentences (the `article=0` reading with empty `sentences` and the `article=7` reading that is
out of range for a 2-article list are both excluded from the expected result).

**What is explicitly not tested**: the research agent's real tool-calling loop. `research_topic`
builds a `create_agent(MODEL, tools=[search_news], ...)` and calls `agent.invoke(...)`, where
`search_news` itself calls `TavilyClient(...).search(...)` and then `read_articles`. None of
`test_nodes.py` stubs `TavilyClient`, the `MODEL` chat model, or `agent.invoke` for this full loop —
doing so would require either live calls to Claude and Tavily, or a much heavier harness
simulating multi-turn tool calling. The suite only reaches `research.py`'s pure helpers:
`read_articles` (via the faked reader above), `recall`/`remember` (via a real `InMemoryStore`, see
below), and `published_day`. The `research` graph node's end-to-end behavior against a live model
and search API is intentionally left outside this unit-test layer.

`test_research_recalls_the_last_7_days_of_findings` exercises `recall`/`remember` against a real
`InMemoryStore` (no model involved): it writes findings for several as-of days via
`research.remember`, then asserts `research.recall(store, date(2026, 9, 30))` returns only the
findings from the `MEMORY_DAYS` (7) days ending on that date, newest first, deduplicated when a
topic was stored twice (a rerun of the same day), and that `recall(None, ...)` returns `[]` when no
store is configured (e.g. when running outside LangGraph Platform/Studio persistence).

## GDELT fixtures: a fake HTTP server, no real downloads

`gdelt.py` reads GDELT's Global Knowledge Graph (GKG) files over HTTP via `httpx`, but every
GDELT-related test injects its own `get` callable instead of `gdelt.http_get`, so no test makes a
real HTTP request. Three helpers in `test_nodes.py` build this fake server:

- **`gkg_row(url, site, title, themes, persons, orgs, places)`** builds one raw GKG 2.1 line: a
  27-tab-separated-column row with the specific columns `gdelt.py` reads (`SITE`, `URL`, `THEMES`,
  `LOCATIONS`, `PERSONS`, `ORGANIZATIONS`, `EXTRAS`) filled in, leaving the rest as empty strings.
  This lets a test describe just the fields that matter for a scenario (e.g. a story's themes and
  places) without fabricating an entire real GKG record.
- **`zipped(text)`** wraps the tab-separated text in an in-memory ZIP archive (`io.BytesIO` +
  `zipfile.ZipFile`), mirroring how GDELT actually ships each 15-minute export as a `.csv.zip`.
- **`gkg_files(rows_at, latest=...)`** returns a `(get, asked)` pair: `get` is a callable matching
  the signature `gdelt.market_rows` expects (`get(url) -> bytes | None`), and `asked` is the list
  of every URL it was asked for, so a test can assert exactly which files `gdelt` downloaded. `get`
  answers `lastupdate.txt` with a canned "latest update" line, and answers any `*.gkg.csv.zip` URL
  by calling `rows_at(at)` (the caller-supplied row generator for that 15-minute timestamp) and
  zipping the result — or returning `None` to simulate GDELT having skipped that update, which
  `gdelt.market_rows` must tolerate without erroring.

Each GDELT test is given its own `tmp_path / "events.db"` and passes it as `gdelt.top_stories`'s
`path` argument, so tests never share or pollute the real `data/events.db`, and each test starts
from an empty, schema-only database (created on first connect via `db.connect`). The tests built on
these fixtures check: ranking stories by distinct site count over the 24-file (6-hour) window and
that a second identical call reads back from the database instead of re-downloading
(`test_top_stories_rank_market_headlines_by_sites_over_the_last_6_hours`); that requesting a past
`as_of` day reads that day's last 6 hours ending 23:45 UTC
(`test_top_stories_for_a_day_read_its_last_6_hours`); that requesting a day GDELT has not reached
yet raises `ValueError` with a message naming the newest update
(`test_top_stories_for_a_day_gdelt_has_not_reached_says_so`); and that a window shifted 15 minutes
later only downloads the one new file, while `gkg_file`/row counts in `events.db` reflect the
cumulative set of files ever fetched (`test_a_window_15_minutes_later_downloads_only_the_new_file`).

## Routing is tested directly against `graph.start`

`test_follow_ups_skip_to_research` calls `graph.start(State(...))` directly — the conditional-edge
function LangGraph uses to pick the first node — and asserts it returns `"gdelt"` for a fresh
`State()` and `"research"` once `state.report` is set (a follow-up message in an existing thread).
This is a pure function test: it does not build or invoke the compiled graph at all, only the
routing predicate.

## Patterns to follow when adding a node

- If a node calls exactly one external API through a small wrapper function (like
  `triage.jev_scores`, `verify.jev_checks`, or `prices.sector_moves`'s `download` parameter),
  monkeypatch that wrapper (or pass a stub via a parameter) rather than mocking the underlying
  client/library. This keeps the test about the node's own branching and data shaping.
  Config-gated behavior (e.g. an API key's presence) should be exercised both ways via
  `monkeypatch.setenv`/`delenv`.
- If a node writes to shared state (a file, `events.db`, the LangGraph store), redirect its path/
  module-level constants to `tmp_path` and a fresh `InMemoryStore` via `monkeypatch.setattr`, then
  assert on every externally observable artifact the node produces, not just its return value.
- If a node runs a `create_agent`/model-backed subagent with a narrow, single-shot responsibility
  (like the reader), fake `create_agent` itself to return an object with a canned `.invoke`. Reserve
  real model/tool-calling integration (the full agent loop with live tools) for manual or separate
  integration testing — it is explicitly out of scope for `tests/test_nodes.py`.
- If a node depends on an HTTP data source with its own incremental/caching behavior (like GDELT's
  file-by-file fetch-and-store), build a small fake server fixture that returns canned payloads and
  records the URLs requested, and give each test its own `tmp_path` database so caching behavior
  (what gets re-downloaded vs. read back) can be asserted precisely.
