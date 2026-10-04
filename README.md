# Event-first market agent

A LangGraph graph that answers: **which of today's news stories could matter economically, and what do the sources say about them?**

It takes the market stories GDELT saw most widely published in the last 6 hours, has Jev score their economic severity, researches the 3 most severe with Claude and Tavily, keeps only the claims code can tie to a source, shows how the affected US sectors moved, and writes the report as Markdown and as a standalone HTML page. You can then ask follow-up questions in the same thread. Every step is a node in LangSmith Studio and a span in the LangSmith trace.

## Why it exists

In short, it filters global news down to the few stories that could move markets and only reports facts it can prove from a source.

The main value is trust. AI news summaries can invent facts or numbers. This agent checks every claim against its source article, removes what it cannot confirm and records why. The result is a short, sourced brief instead of a pile of headlines.

New to the project? Start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). For the design in detail, see [docs/HOW_IT_WORKS.md](docs/HOW_IT_WORKS.md).

## Quick start

Requires Python 3.11-3.14 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
cp .env.example .env                 # fill in the keys below
uv run main.py                       # latest 6 hours of news
uv run main.py --date 2026-09-30     # 6 hours ending 23:45 UTC that day
uv run main.py --thread <id> "Which airlines could be affected by the flydubai incident?"
```

| Key | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | research (Claude) |
| `TAVILY_API_KEY` | research (news search) |
| `TYPESAFE_API_KEY` | triage and verify (Jev). Optional: without it triage keeps the most widely published stories and says so |
| `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | traces in LangSmith |
| `RESEARCH_MODEL` | optional, default `anthropic:claude-sonnet-5-5` |

## How one run works

`graph.py` wires six nodes in a line. Each reads and writes named fields of one typed state (`state.py`).

```
gdelt -> triage -> research -> verify -> price -> output
```

| Node | File | LLM | What it does |
|---|---|---|---|
| gdelt | `gdelt.py` | no | Reads GDELT's Global Knowledge Graph for the 6 hours ending at the newest 15-minute update. Keeps articles with a market theme such as `ECON_OILPRICE`, groups them by page title, and keeps the 30 published on the most sites |
| triage | `triage.py` | Jev | Scores each story's severity (0 minor .. 3 severe) and keeps the 3 highest |
| research | `research.py` | Claude | One `create_agent` per story with one tool, `search_news` (Tavily, last 7 days only). Returns a summary and claims, each with a source URL and date |
| verify | `verify.py` | Jev | Code drops claims whose URL the search never returned or whose numbers are not in the article. Jev then checks each claim's meaning and freshness and picks its US sector. Unsure claims are kept and marked unverified |
| price | `prices.py` | no | Each sector's ETF move over the last closed session, and its difference from SPY (Yahoo Finance) |
| output | `output.py` | no | Writes `output/<day>-<thread>-<time>.md` and a matching `.html` page, and returns the Markdown as the reply |

Where the numbers come from: severity from Jev, dates from the search tool (verify overwrites the model's), and every number in a claim must appear in its article's text.

## Threads and follow-ups

Every run prints its thread ID. A later message in the same thread skips gdelt and triage, researches the question, then runs verify, price and output again.

```sh
uv run main.py --date 2026-09-30
# ... Thread b630ce9c. Continue it with: uv run main.py --thread b630ce9c "your question"
```

## What is stored

| Path | Contents |
|---|---|
| `data/events.db` | Every market GDELT row fetched, and every run's stories, claims, removed claims and price moves (`db.py`). Rows are only added, never duplicated, so a run 15 minutes after another downloads 1 new file instead of 24 |
| `data/threads.db` | LangGraph checkpoints (threads) and store. The store keeps each report's verified findings, and research shows the last 7 days of them to the agent as context it may not cite |
| `output/` | One Markdown report and one HTML page per run |

All three are gitignored.

## LangSmith Studio

```sh
uv run langgraph dev
```

Serves `event_graph` from `langgraph.json`. Studio adds its own persistence, so its threads are separate from `data/threads.db`.

## Limits

- GKG's market themes let some politics and lifestyle stories through. Jev's score in triage filters them, keeping the 3 highest with no minimum.
- A run on a window with nothing stored downloads about 80 MB of GKG (24 files), about 10-35 seconds.
- Jev judges severity from the page title and GDELT metadata, not the article, so its score ranks leads and is not a fact.
- Jev's freshness check passes standing facts (a company's revenue mix, an analyst estimate) and removes older events reported as news. On 38 claims the old wording had removed, it removes 6, all older events. Jev's scores vary between runs, so a borderline claim can land either side.
- The finding's summary is written by the research model and is not checked by verify.
- price shows observed sector moves, not proof that the news caused them.

## Tests

```sh
uv run pytest -q
uv run ruff check .
```

The tests cover gdelt, triage, verify, price, output and the follow-up routing, with Jev, Tavily and GDELT stubbed.
