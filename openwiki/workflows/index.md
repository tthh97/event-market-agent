# Files

- [Event Pipeline: GDELT Ingestion and Triage](event-pipeline.md) - How the graph's first two nodes turn GDELT's 15-minute GKG files into a deduplicated, ranked list of market stories and then use Jev's severity score to pick the 3 stories that get researched.
- [Continuing a Thread with a Follow-up Question](follow-up-questions.md) - How a later message in the same LangGraph thread re-runs research, verify, price, and output against the question itself instead of the day's GDELT stories, skipping gdelt/triage entirely.
- [Sector Pricing and Report Output](pricing-and-reporting.md) - How the price node prices kept claims' sectors against SPY via Yahoo Finance, and how the output node assembles the Markdown/JSON report, writes it to output/, logs it to events.db, and returns it as the chat reply.
