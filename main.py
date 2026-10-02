# main.py
"""Command line: run the graph once, or ask a follow-up in an earlier run's thread.

    uv run main.py                          latest 24 hours of GDELT, new thread
    uv run main.py --date 2026-09-29        one GDELT day, new thread
    uv run main.py --thread ID "question"   follow-up in that thread

Threads are saved in data/threads.db, so a thread can be continued later. The same file holds the
LangGraph store, where each report's findings are kept for later reports to recall.
"""

import argparse
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.store.sqlite import SqliteStore

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

from graph import build  # noqa: E402  (after .env, so LangSmith and API keys are set)
from state import CHECKPOINT_TYPES  # noqa: E402


def describe(update):
    """One line per changed field, short enough for a terminal."""
    for field, value in (update or {}).items():
        if isinstance(value, list):
            yield f"{field}: {len(value)} items"
        else:
            yield f"{field}: {json.dumps(value, default=str)[:200]}"


def main():
    parser = argparse.ArgumentParser(description="Event research graph: GDELT, Jev triage, research, report.")
    parser.add_argument("question", nargs="?", help="A follow-up question. Needs --thread.")
    parser.add_argument("--date", help="GDELT day YYYY-MM-DD. Default: the latest 24 hours.")
    parser.add_argument("--thread", help="Continue this thread instead of starting a new one.")
    args = parser.parse_args()
    if args.question and not args.thread:
        parser.error("a follow-up question needs --thread")
    thread = args.thread or uuid4().hex[:8]
    inputs = {"messages": [{"role": "user", "content": args.question}]} if args.question else {"as_of": args.date}
    config = {"configurable": {"thread_id": thread}, "run_name": "Event graph", "metadata": {"thread": thread}}
    (ROOT / "data").mkdir(exist_ok=True)
    threads = str(ROOT / "data" / "threads.db")
    with sqlite3.connect(threads, check_same_thread=False) as connection, SqliteStore.from_conn_string(threads) as store:
        saver = SqliteSaver(connection, serde=JsonPlusSerializer(allowed_msgpack_modules=CHECKPOINT_TYPES))
        store.setup()
        app = build().compile(checkpointer=saver, store=store)
        for step in app.stream(inputs, config, stream_mode="updates"):
            for node, update in step.items():
                print(f"[{node}]")
                for line in describe(update):
                    print(f"  {line}")
    print(f"\nThread {thread}. Continue it with: uv run main.py --thread {thread} \"your question\"")


if __name__ == "__main__":
    main()
