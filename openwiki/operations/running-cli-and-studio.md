---
type: operations-guide
title: "Running the Pipeline: CLI and LangGraph Studio"
description: How to invoke the event research graph from the command line (new runs, dated windows, follow-ups) or via `uv run langgraph dev` in LangGraph Studio, and where each run's artifacts land on disk.
tags: [operations, cli, langgraph-studio, gdelt, persistence, main.py]
verified:
  - by: openwiki/0.6.1
    at: 2026-10-02T09:22:29.186Z
sources:
  - id: openwiki-source-7e751dfb10ab9589b7e0b7b3
    resource: repo://gdelt.py
  - id: openwiki-source-b9a2305b0f09bb933a03007f
    resource: repo://graph.py
  - id: openwiki-source-5bbba7b2a8ea8360ff233d63
    resource: repo://langgraph.json
  - id: openwiki-source-833e692518af9eeaf8564cc6
    resource: repo://main.py
  - id: openwiki-source-ac1481dec664fdfda1573933
    resource: repo://output.py
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
generated: { by: "openwiki/0.6.1", at: "2026-10-02T09:22:29.186Z" }
---

## Two ways to run the graph

The graph defined in [`graph.py`](../../graph.py) (`gdelt -> triage -> research -> verify -> price -> output`,
with follow-ups skipping straight to `research`) can be invoked in two ways that share the same
node code but differ in how state is persisted and observed:

- **`uv run main.py`** — a one-shot CLI run with console output, backed by a SQLite checkpointer
  and store at `data/threads.db`.
- **`uv run langgraph dev`** — a local dev server that serves the same graph object for interactive
  use in LangGraph Studio.

Both ultimately compile the `StateGraph` returned by `graph.build()`, but they compile it
differently: `main.py` compiles it with an explicit `SqliteSaver`/`SqliteStore` pair, while
`graph.py`'s module-level `graph = build().compile()` (the object `langgraph dev` serves) is
compiled with **no** checkpointer at all, because the Studio server supplies its own persistence
layer. See [Persistence](../architecture/persistence.md) for how checkpoints and the long-term
store are structured in each case.

## CLI: `main.py`

### The three invocation shapes

`main.py` accepts three argument shapes, all mutually exclusive in intent:

| Invocation | Thread | GDELT window | Behavior |
|---|---|---|---|
| `uv run main.py` | new (`uuid4().hex[:8]`) | latest GDELT window | Runs all six nodes: fetch GDELT, triage, research, verify, price, output. |
| `uv run main.py --date 2026-09-30` | new | the window ending at 23:45 UTC that day (or the newest update if the day isn't over yet) | Same six nodes, but anchored to a specific GDELT day instead of "now". |
| `uv run main.py --thread ID "question"` | continues `ID` | not re-fetched | A follow-up: skips `gdelt` and `triage`, feeds the question into `research`, then runs `verify`, `price`, `output` again. |

A bare question without `--thread` is rejected by the argument parser (`a follow-up question needs
--thread`), since a follow-up only makes sense against an existing thread's checkpointed state.

Internally, `main.py` picks the input shape based on whether a question was supplied:
`{"messages": [...]}` for a follow-up, or `{"as_of": args.date}` (possibly `None`) for a fresh run.
The graph's own conditional start edge (`start()` in `graph.py`) then decides the entry node by
checking whether `state.report` is already set — i.e. whether this thread has produced a report
before — not by inspecting the CLI arguments directly. This is detailed further in
[Follow-up Questions](../workflows/follow-up-questions.md).

### The GDELT window: a documented mismatch

Both the README and `main.py --help` describe the no-`--date` case as "the latest 24 hours" of
GDELT data. In practice, `gdelt.py` fetches the latest 6 hours: it downloads `UPDATES = 24`
GDELT GKG files, but each file is a *15-minute* update, so 24 files cover 6 hours, not 24. The
module's own docstring and inline comment (`UPDATES = 24  # 6 hours of 15-minute files`) are
correct; it is the CLI help text and README prose that say "24 hours". When documenting or
operating this command, treat **6 hours** as the actual behavior and note the "24 hours" wording
in help/README output as a known doc/code mismatch rather than a second, larger window.

### Streamed per-node output

`main.py` runs the compiled graph with `app.stream(inputs, config, stream_mode="updates")`, so each
yielded `step` is a single node's state update rather than the full accumulated state. For every
node the CLI prints a header line `[node_name]` followed by one line per field the node changed,
via the local `describe()` helper:

- list-valued fields print as `field: N items` (their length, not their content), which keeps
  `stories`, `findings`, `sources`, etc. readable in a terminal;
- scalar fields print as `field: <json-encoded value, truncated to 200 chars>`.

This gives a compact, node-by-node trace of a run in the console — for example `[gdelt]` followed
by `as_of: ...` and `stories: 30 items`, then `[triage]` with `severe: 3 items` and `jev_status: ...`,
and so on through `output`.

After the stream is exhausted, `main.py` prints the run's thread ID and a ready-to-paste
continuation command:

```
Thread b630ce9c. Continue it with: uv run main.py --thread b630ce9c "your question"
```

This is the only place a thread ID is surfaced to the operator; it must be copied from this line
(or recovered from `data/threads.db`) to continue the conversation later, since the CLI does not
otherwise list existing threads.

## LangGraph Studio: `uv run langgraph dev`

`langgraph.json` declares a single graph, `event_graph`, pointing at `./graph.py:graph`:

```json
{
  "dependencies": ["."],
  "graphs": {
    "event_graph": "./graph.py:graph"
  },
  "env": ".env"
}
```

Running `uv run langgraph dev` starts a local LangGraph API server that loads this module, reads
`.env` for credentials, and serves `event_graph` for Studio, which opens automatically in the
browser. Every node and the state fields it reads and writes appear live as the run progresses,
which is useful for inspecting intermediate state (e.g. `severe`, `findings`, `rejected`) that the
CLI only summarizes as item counts.

Because `graph.py`'s `graph` object is compiled **without** a checkpointer (`build().compile()`,
no `checkpointer=`/`store=` arguments), the dev server relies entirely on its own
built-in persistence to track Studio threads, runs, and the long-term store. This means:

- Threads created or continued inside Studio are **not** rows in `data/threads.db` — they live in
  whatever storage `langgraph dev` manages for its own server process.
- Conversely, threads created with `uv run main.py` (and recorded in `data/threads.db`) are not
  visible to Studio as threads to resume, since Studio and the CLI use disjoint storage.
- The two entrypoints run identical node logic and the identical `State` schema, so a run's
  behavior and output shape are the same either way; only where the checkpoints/store rows end up
  differs.

See [Persistence](../architecture/persistence.md) for the full checkpointer/store architecture,
and [Configuration and Secrets](configuration-and-secrets.md) for the `.env` keys `langgraph dev`
and `main.py` both load (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `TYPESAFE_API_KEY`, the
`LANGSMITH_*` tracing variables, and `RESEARCH_MODEL`).

## Where artifacts land on disk

Regardless of entrypoint, a successful run's `output` node writes to three places:

- **`output/<as_of>-<thread>-<time>.md`** — the human-readable Markdown report (also the content of
  the graph's reply `AIMessage`), named from the run's `as_of` date, thread ID, and the time of day
  the report was written (`HHMMSS`). For example:
  `output/2026-09-30-7b69d527-021701.md`.
- **`output/<as_of>-<thread>-<time>.json`** — the same run's state (`as_of`, `jev_status`, `severe`,
  `findings`, `rejected`, `verify_status`, `moves`, `price_window`) serialized as JSON, for
  programmatic consumption alongside the prose report.
- **`data/events.db`** — the durable history database (`db.py`): every market-tagged GDELT row ever
  fetched, plus each run's stories, kept claims, rejected claims, and price moves, each insert
  guarded with `ON CONFLICT DO NOTHING` so re-running an overlapping window or re-logging a run is
  idempotent.

In addition, **`data/threads.db`** (used only by the `main.py` CLI path, not by `langgraph dev`)
holds the LangGraph checkpointer's per-thread state snapshots and the LangGraph store's long-term
memory of verified findings that later follow-up research can recall. See
[Persistence](../architecture/persistence.md) for the schema and lifecycle of both SQLite files,
and [Follow-up Questions](../workflows/follow-up-questions.md) for how a continued thread actually
reuses this stored state.
