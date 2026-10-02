# graph.py
"""Wires the nodes into one graph: gdelt -> triage -> research -> verify -> price -> output.

A new thread runs every node. A later message in the same thread is a follow-up question: it goes
straight to research, which researches the question, and then through verify, price and output again.

`graph` is what `langgraph dev` serves in Studio. The server adds its own persistence, so it is
compiled without a checkpointer. main.py compiles the same builder with a SQLite checkpointer.
"""

from langgraph.graph import END, START, StateGraph

from gdelt import gdelt
from output import output
from prices import price
from research import research
from state import State
from triage import triage
from verify import verify


def start(state: State) -> str:
    """Follow-up questions skip to research once the thread has a report."""
    return "research" if state.report else "gdelt"


def build():
    builder = StateGraph(State)
    builder.add_node("gdelt", gdelt)
    builder.add_node("triage", triage)
    builder.add_node("research", research)
    builder.add_node("verify", verify)
    builder.add_node("price", price)
    builder.add_node("output", output)
    builder.add_conditional_edges(START, start, ["gdelt", "research"])
    builder.add_edge("gdelt", "triage")
    builder.add_edge("triage", "research")
    builder.add_edge("research", "verify")
    builder.add_edge("verify", "price")
    builder.add_edge("price", "output")
    builder.add_edge("output", END)
    return builder


graph = build().compile()
