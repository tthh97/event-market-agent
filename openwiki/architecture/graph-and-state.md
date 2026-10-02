---
type: architecture
title: Graph Wiring and Shared State
description: How graph.py wires the six-node LangGraph pipeline (gdelt, triage, research, verify, price, output) and its follow-up routing branch, and how state.py's single typed State object is divided into fields each node owns.
tags: [graph, langgraph, state, architecture, routing, checkpointer]
verified:
  - by: openwiki/0.6.1
    at: 2026-10-02T09:22:29.186Z
sources:
  - id: openwiki-source-b9a2305b0f09bb933a03007f
    resource: repo://graph.py
  - id: openwiki-source-833e692518af9eeaf8564cc6
    resource: repo://main.py
  - id: openwiki-source-0dc6ea1593405ef6df2682c0
    resource: repo://state.py
generated: { by: "openwiki/0.6.1", at: "2026-10-02T09:22:29.186Z" }
---

## Overview

The whole run is one [LangGraph](https://github.com/langchain-ai/langgraph) `StateGraph` built in `graph.py` and driven by `main.py`. There is no supervisor or router LLM: the graph's shape is fixed code, a straight line of six nodes with one conditional branch at the start. Every node reads and writes fields of a single Pydantic model, `state.py:State`, which LangGraph merges field-by-field after each node returns. For the end-to-end business meaning of each step (what GDELT, Jev, Tavily, and Yahoo Finance actually do), see [Event Pipeline](../workflows/event-pipeline.md); this page is about how the graph is wired and how state flows between nodes, not what each node computes.

## Building the graph: `graph.py:build`

`graph.py:build` constructs a `StateGraph(State)`, registers the six node functions, and wires them in sequence:

```python
builder.add_conditional_edges(START, start, ["gdelt", "research"])
builder.add_edge("gdelt", "triage")
builder.add_edge("triage", "research")
builder.add_edge("research", "verify")
builder.add_edge("verify", "price")
builder.add_edge("price", "output")
builder.add_edge("output", END)
```

`build()` returns the uncompiled `StateGraph` builder rather than a compiled graph, so callers choose their own compilation options (a checkpointer and store, or none). `graph.py` itself calls `build().compile()` with no arguments to produce the module-level `graph` object, and `main.py` calls `build().compile(checkpointer=saver, store=store)` to produce the object it actually runs. These two compiled graphs share the same node wiring but differ in persistence (see [Compiled variants](#compiled-variants-graph-vs-app) below).

### The conditional START edge: `graph.py:start`

```python
def start(state: State) -> str:
    """Follow-up questions skip to research once the thread has a report."""
    return "research" if state.report else "gdelt"
```

`start` is the condition function LangGraph calls immediately after `START`. It inspects exactly one field, `state.report`:

- **New run**: `report` is `None` (the field's default), so `start` returns `"gdelt"` and the full pipeline runs from GDELT discovery through output.
- **Follow-up question**: the thread already has a `report` from an earlier completed run in the same thread, so `start` returns `"research"`, skipping `gdelt` and `triage` entirely. Research, verify, price, and output then run again, this time driven by the follow-up question instead of the severe stories list.

Because `report` is only ever set by the `output` node, this single field is the whole mechanism that distinguishes a new run from a follow-up — there is no separate "mode" flag. `main.py` sets this up by choosing the graph's input: a new run supplies `{"as_of": date_or_none}`, while a follow-up supplies `{"messages": [{"role": "user", "content": question}]}` under an existing `--thread` id, so that the checkpointer loads the prior `report` value for that thread before `start` runs. The routing mechanism and its downstream effects (what research, verify, price, and output do differently on a follow-up) are detailed in [Follow-up Questions](../workflows/follow-up-questions.md).

## The node sequence and follow-up branch

<!-- openwiki: mermaid parse failed and this diagram was converted to a text fence so it does not break rendering. Fix the diagram source and restore the mermaid fence. Parser error: Heuristic: an unescaped angle bracket inside a label breaks rendering; rephrase the label. -->
```text
flowchart TD
    STARTNODE{{"START: graph.py:start<br/>checks state.report"}}
    STARTNODE -- "report is None: new run" --> GDELT["gdelt<br/>writes as_of, stories"]
    STARTNODE -- "report is set: follow-up" --> RESEARCH
    GDELT --> TRIAGE["triage<br/>writes severe, jev_status"]
    TRIAGE --> RESEARCH["research<br/>writes findings, sources"]
    RESEARCH --> VERIFY["verify<br/>writes rejected, verify_status"]
    VERIFY --> PRICE["price<br/>writes moves, price_window"]
    PRICE --> OUTPUT["output<br/>writes report, messages"]
    OUTPUT --> ENDNODE(["END"])
```
*Node sequence through `graph.py:build`, the conditional START edge, and the state fields each node writes.*

A new run therefore executes all six nodes in order; a follow-up executes four (`research`, `verify`, `price`, `output`), reusing the `severe` and `jev_status` fields left over from the thread's original run rather than recomputing them.

## Shared state: `state.py:State`

All nodes take and return the same model, `State`, defined with Pydantic. A node function returns a plain dict of only the fields it changed; LangGraph merges that dict into the running state (for the one field with a custom reducer, `messages`, see below). This return-a-partial-dict convention is why `main.py:describe` can print one line per changed field after every node step.

### Field-ownership convention

Each field in `State` is documented as belonging to exactly one node, both by a trailing comment in `state.py` and by convention enforced in each node's code — a node is expected to read only the fields it needs and write only its own fields:

| Field(s) | Owner node | Meaning |
|---|---|---|
| `as_of` | `gdelt` | Input (`YYYY-MM-DD`) or, if not given, filled in by `gdelt` with the resolved GDELT window end date |
| `stories` | `gdelt` | Up to 30 ranked `Story` objects grouped from the GDELT window |
| `severe` | `triage` | The 3 highest-severity `ScoredStory` objects, kept from `stories` |
| `jev_status` | `triage` | Human-readable note on whether Jev scoring succeeded or fell back |
| `findings` | `research` | One `Finding` per researched topic (per severe story, or one for a follow-up question) |
| `sources` | `research` | Every `Source` article the research tool retrieved in this run; the only articles a claim may cite |
| `rejected` | `verify` | Each claim removed during verification, and why |
| `verify_status` | `verify` | Human-readable note on whether Jev's claim checks succeeded or fell back |
| `moves` | `price` | `{sector: (move %, vs SPY points)}` for sectors referenced by kept claims |
| `price_window` | `price` | Which market closes were used, or why prices are unavailable |
| `report` | `output` | Path of the written Markdown report; `None` until `output` runs, and the signal `start` reads to detect a follow-up |
| `messages` | `output` (and the CLI, for follow-up input) | Chat history: the follow-up question goes in, the AI reply (the report body) comes out |

`messages` is the one field with a custom merge: it is annotated `Annotated[list[AnyMessage], add_messages]`, LangGraph's standard message-list reducer, so new messages are appended (and messages with matching ids are replaced) instead of the whole list being overwritten the way every other field is. Every other field uses plain last-write-wins replacement, which is why only the owning node is expected to ever set it — two nodes writing the same field would race.

This ownership split means a node can safely assume the fields it does not own were populated by an earlier node (or, on a follow-up, left over from the thread's original run) and need not be recomputed. It also means a reviewer auditing one node only needs to check that node's documented fields to see the whole surface it can affect.

### Data models

`state.py` also defines the Pydantic models that populate `State`'s list and dict fields:

- **`Story`** — one GDELT story: all GKG articles in the window sharing a page title, with `url`, `title`, `articles` and `sites` counts, and collected `actors`, `themes` (GDELT market themes, e.g. `ECON_OILPRICE`), and `places`. Produced by `gdelt`.
- **`ScoredStory`** — a `Story` plus `severity` (Jev's 0–3 scale, `None` if Jev is not configured) and `confidence`. Produced by `triage`.
- **`Claim`** — one factual sentence with `source_url`, `source_date`, a `sector` (set by `verify`, empty until then), and an `unverified` flag (set by `verify`, `False` until then). Produced by `research`, mutated in place by `verify`.
- **`Finding`** — what research found about one severe story or one follow-up question: a `topic`, a `summary`, and a list of `Claim`. Produced by `research`; its `claims` list is filtered by `verify`.
- **`Source`** — one article the research tool returned in the current run (`url`, `title`, `published`, `excerpt`); only URLs present here may be cited by a `Claim`. Produced by `research`.

### `CHECKPOINT_TYPES`: the checkpoint allow-list

```python
CHECKPOINT_TYPES = [("state", name) for name in ("Story", "ScoredStory", "Claim", "Finding", "Source")]
```

`main.py` passes this list to `JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES)` when constructing the `SqliteSaver`. LangGraph's msgpack-based checkpoint serializer refuses to deserialize arbitrary Python objects from a saved checkpoint unless their `(module, class name)` pair is explicitly allow-listed; `CHECKPOINT_TYPES` lists exactly the five `State` models from the `state` module that appear nested inside list/dict fields (`Story`, `ScoredStory`, `Claim`, `Finding`, `Source`), so a saved thread can be loaded back into these types without the serializer rejecting them. `State` itself does not need to be listed because LangGraph reconstructs the top-level state model directly from its own schema. This allow-list is a security and forward-compatibility boundary: adding a new Pydantic model to a list/dict field of `State` without adding it to `CHECKPOINT_TYPES` means checkpoints containing it cannot be safely reloaded. See [Persistence](persistence.md) for how `data/threads.db` stores these checkpoints and the companion LangGraph store.

## Compiled variants: `graph` vs. `app`

`graph.py` compiles two different things depending on how it is used, and they are not interchangeable:

- **`graph`** (the module-level object in `graph.py`, built with `build().compile()`, no arguments) is what `langgraph.json` points `langgraph dev` / LangGraph Studio at. It is compiled **without a checkpointer or store** because the dev server supplies its own persistence layer. Studio threads and Studio's findings memory are therefore separate from `data/threads.db` — running the graph in Studio does not read or write the same checkpoint/store file that `main.py` uses.
- **The app `main.py` builds** (`build().compile(checkpointer=saver, store=store)`) is what actually runs in the CLI. `main.py` opens `data/threads.db` once as both a `SqliteSaver` (the checkpointer, holding per-thread `State` snapshots after every node) and a `SqliteStore` (holding findings remembered across reports for `research.py`'s recall), and compiles the same `builder` from `graph.py:build` with both attached.

Because both variants come from the same `build()` function, the node wiring and the `start` routing are identical in Studio and on the command line; only persistence differs. This distinction matters when testing or debugging: a graph invoked via `graph.py:graph` directly (e.g., in a unit test) will not persist state across invocations or support thread-based follow-ups unless a checkpointer is supplied separately. The project's node-level tests stub nodes individually rather than exercising routing persistence; see [Test Strategy](../testing/test-strategy.md).

## Related pages

- [Persistence](persistence.md) — how `data/threads.db` (checkpointer + store) and `data/events.db` (append-only run log) are structured and written.
- [Event Pipeline](../workflows/event-pipeline.md) — what each node actually computes: GDELT download, Jev triage, Claude/Tavily research, verification, and pricing.
- [Follow-up Questions](../workflows/follow-up-questions.md) — the follow-up branch's effect on research's topic selection and on which fields are replaced versus retained.
- [Test Strategy](../testing/test-strategy.md) — how nodes and routing are tested without network access.
