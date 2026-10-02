# Files

- [Graph Wiring and Shared State](graph-and-state.md) - How graph.py wires the six-node LangGraph pipeline (gdelt, triage, research, verify, price, output) and its follow-up routing branch, and how state.py's single typed State object is divided into fields each node owns.
- [Persistence: events.db, threads.db, and the LangGraph Store](persistence.md) - Explains the two SQLite databases the system uses, data/events.db for append-only GDELT and run history and data/threads.db for LangGraph checkpoints and the findings-memory store, and how each is written and read.
