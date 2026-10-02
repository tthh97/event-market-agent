---
type: architecture
title: "Persistence: events.db, threads.db, and the LangGraph Store"
description: Explains the two SQLite databases the system uses, data/events.db for append-only GDELT and run history and data/threads.db for LangGraph checkpoints and the findings-memory store, and how each is written and read.
tags: [persistence, sqlite, events-db, threads-db, langgraph-store, checkpointer, findings-memory]
verified:
  - by: openwiki/0.6.1
    at: 2026-10-02T09:22:29.186Z
sources:
  - id: openwiki-source-22a47a76b5740406c171500a
    resource: repo://db.py
  - id: openwiki-source-7e751dfb10ab9589b7e0b7b3
    resource: repo://gdelt.py
  - id: openwiki-source-b9a2305b0f09bb933a03007f
    resource: repo://graph.py
  - id: openwiki-source-833e692518af9eeaf8564cc6
    resource: repo://main.py
  - id: openwiki-source-ac1481dec664fdfda1573933
    resource: repo://output.py
  - id: openwiki-source-00476aec6b96c0910fec8c00
    resource: repo://research.py
  - id: openwiki-source-0dc6ea1593405ef6df2682c0
    resource: repo://state.py
generated: { by: "openwiki/0.6.1", at: "2026-10-02T09:22:29.186Z" }
---

The system keeps two separate SQLite databases under `data/`, each owned by a different layer of
the code:

- **`data/events.db`** ([db.py](../../db.py)) is the application's own append-only history: every
  GDELT article row the [event pipeline](../workflows/event-pipeline.md) has ever fetched, and a
  record of every graph run's results.
- **`data/threads.db`** is LangGraph's own storage: the checkpointer's saved conversation state and
  the key-value store used as findings memory.

Nothing is shared between the two files, and neither module imports the other's connection logic.
`db.py`'s module docstring makes the boundary explicit: "LangGraph's own data (checkpoints and the
findings memory) is in `data/threads.db`, not here."

## `data/events.db`: GDELT rows and run results

`db.py` defines the schema and a `connect()` helper that opens the file (creating `data/` if
needed) and runs the schema script, which uses `CREATE TABLE IF NOT EXISTS`, so calling it is always
safe. Every table is append-only: rows are inserted with `INSERT ... ON CONFLICT DO NOTHING` against
a natural key, so re-running the same fetch or the same graph run is idempotent and never rewrites
or deletes history.

### `gkg_file` / `gkg_row`: written by `gdelt.py`

```
CREATE TABLE gkg_file (at TEXT PRIMARY KEY, rows INTEGER NOT NULL, fetched_at TEXT ...)
CREATE TABLE gkg_row  (record_id TEXT PRIMARY KEY, at TEXT REFERENCES gkg_file, site, url, title,
                       themes, actors, places TEXT NOT NULL)
```

- `gkg_file` has one row per 15-minute GDELT GKG file already downloaded, keyed by `at`
  (`YYYYMMDDHHMMSS`, UTC) — the file's own timestamp. `rows` is how many market-themed rows it
  contained (0 when GDELT skipped that update, i.e. returned 404).
- `gkg_row` has one row per market-themed article, keyed by `record_id` — GDELT's own
  `GKGRECORDID` — so the same article is never stored twice even if later fetches overlap an
  earlier window. `gkg_row.at` references the file it came from, and is indexed so reading a
  6-hour window back out is fast.

`gdelt.py`'s `market_rows()` is the only writer: for each of the 24 fifteen-minute timestamps in
the requested 6-hour window, it checks which are already in `gkg_file`, downloads only the missing
ones in parallel, and inserts each file's rows and its `gkg_file` marker **in one transaction**, so
a file is only ever marked stored together with its rows — if the process dies mid-insert, the file
is retried on the next run instead of being silently treated as fetched-but-empty. Because files are
fetched once and kept forever, a GDELT day or window that overlaps an earlier run's window downloads
only the files that are not already present. See
[Event Pipeline](../workflows/event-pipeline.md) for how the `gdelt` node turns these rows into
ranked stories.

### `run` / `story` / `claim` / `rejection` / `price_move`: written by `output.py`'s `log`

```
CREATE TABLE run        (run_id TEXT PRIMARY KEY, thread_id, as_of, kind CHECK (IN events, followup),
                         jev_status, verify_status, price_window, created_at)
CREATE TABLE story      (run_id REFERENCES run, rank, title, url, severity, articles, sites,
                         PRIMARY KEY (run_id, rank))
CREATE TABLE claim      (run_id REFERENCES run, topic, text, source_url, source_date, sector,
                         unverified, PRIMARY KEY (run_id, text))
CREATE TABLE rejection  (run_id REFERENCES run, line, PRIMARY KEY (run_id, line))
CREATE TABLE price_move (run_id REFERENCES run, sector, move, vs_spy, PRIMARY KEY (run_id, sector))
```

After every graph run, the `output` node's `log(run_id, thread_id, state, followup)` opens a
connection and, in one transaction, appends:

- one `run` row, keyed by `run_id` (the report's file name, e.g. `2026-09-29-a1b2c3d4-143022`),
  recording the thread it belongs to, the as-of day, whether it was a new `events` report or a
  `followup`, and the triage/verify status strings and price window description shown in the
  report;
- `story` rows (new reports only) for each [triaged](../workflows/pricing-and-reporting.md) severe
  story kept, with its rank, title, URL, Jev severity (`NULL` if Jev did not score it), and GDELT
  article/site counts;
- `claim` rows for every claim [verify](../workflows/follow-up-questions.md) kept, keyed by
  `(run_id, text)`, with its source URL, source date, assigned sector, and whether it was flagged
  `unverified`;
- `rejection` rows for each claim-rejection line verify produced, and why;
- `price_move` rows for each sector ETF's move and its difference from SPY in the run's price
  window.

Because every insert uses `ON CONFLICT DO NOTHING` on these natural keys, logging a run is safe to
retry. This table set is the durable audit trail behind the
[Pricing and Reporting](../workflows/pricing-and-reporting.md) workflow: it lets you query, for
example, which claims were kept or rejected for a given run, or how sector ETFs moved around past
reports, directly with `sqlite3 data/events.db`.

## `data/threads.db`: the LangGraph checkpointer and store

`main.py` is the only place that opens `data/threads.db` for the command-line entry point. It wires
up two independent LangGraph persistence components against the same file:

```python
threads = str(ROOT / "data" / "threads.db")
with sqlite3.connect(threads, check_same_thread=False) as connection, SqliteStore.from_conn_string(threads) as store:
    saver = SqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))
    store.setup()
    app = build().compile(checkpointer=saver, store=store)
```

- **`SqliteSaver`** (`langgraph.checkpoint.sqlite`) is the **checkpointer**: it persists the full
  graph [`State`](../architecture/graph-and-state.md) after every node, keyed by `thread_id`, so a
  thread can be resumed later. It is built with a `JsonPlusSerializer` restricted to
  `state.CHECKPOINT_TYPES` — the `(module, class)` pairs (`Story`, `ScoredStory`, `Claim`,
  `Finding`, `Source`) a checkpoint is allowed to rebuild — so a checkpoint file cannot make the
  process load arbitrary Python objects.
- **`SqliteStore`** (`langgraph.store.sqlite`) is the **store**: a key-value store LangGraph nodes
  reach through `runtime.store`, independent of any one thread. `store.setup()` creates its tables
  if missing, mirroring how `db.connect()` runs `db.py`'s schema script.

Both are compiled into the same graph (`build().compile(checkpointer=saver, store=store)`), and
`main.py` is explicit that this is a different database from `events.db`: `--thread ID "question"`
continues a thread by loading its checkpoint from `threads.db`, while every run's logged results
still go to `events.db` via `output.py:log`. See [Graph and State](graph-and-state.md) for how
`State` and the conditional entry edge (new report vs. follow-up) relate to checkpointed threads.

## The store as findings memory

The store's second role, beyond backing `runtime.store`, is **findings memory** across reports —
the mechanism behind "yesterday's events inform today's report" continuity described in
[Event Pipeline](../workflows/event-pipeline.md) and
[Follow-up Questions](../workflows/follow-up-questions.md).

- **Write** — `output.py:remember(store, as_of, run_id, findings)` is called from the `output` node
  only for a **new report** (`not followup`) and only when a store is present
  (`runtime.store is not None`). For each finding that kept at least one claim, it calls
  `store.put(("findings", as_of), f"{run_id}-{i}", {...}, index=False)`, storing the finding's
  topic, summary, and claim texts under a namespace keyed by the report's as-of day.
- **Read** — `research.py:recall(store, as_of)` is called from the `research` node, but only for a
  **new report** (not a follow-up, which instead carries forward only the current thread's own
  findings). It builds the list of the `MEMORY_DAYS` (7) days ending on `as_of`, and for each day
  calls `store.search(("findings", day), limit=50)`, turning every stored finding into one summary
  line, newest day first, with duplicate lines (the same story reported twice in a day) collapsed.

These recalled lines are injected into the research agent's prompt as
"Earlier reports (context only, do not cite)": the agent may use them to judge how a story has
developed and to steer new searches, but the system prompt and `recall`'s framing both forbid
citing them as claims — every claim a report keeps must still come from a fresh `search_news` call
verified against its own source. This keeps the findings-memory store a memory aid rather than a
second, unverified source of truth.

## `langgraph dev` / Studio: separate persistence

`graph.py` compiles the exported `graph` object — the one `langgraph dev` serves to LangGraph
Studio — **without** a checkpointer or store: `graph = build().compile()`. The module docstring
explains why: "The server adds its own persistence, so it is compiled without a checkpointer."
`main.py` compiles the same `build()` builder but supplies the `SqliteSaver`/`SqliteStore` pair
against `data/threads.db`.

The practical consequence is that **Studio threads and Studio's findings memory are entirely
separate from `data/threads.db`**: a thread started in Studio does not appear when continuing with
`uv run main.py --thread ID`, and findings written during a Studio run are not recalled by a
command-line run (and vice versa), because each persistence layer uses its own storage. Anyone
needing continuity across both entry points must run through the same one.
