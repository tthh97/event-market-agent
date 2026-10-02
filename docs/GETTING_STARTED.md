# Getting started

This guide takes you from nothing to your first report, then shows how to ask follow-up questions and see what happened behind the scenes. Every command here was run on 2026-10-02 on macOS with Python 3.12 and uv 0.12.5. The one exception is `git clone`, marked below.

A few words used throughout:

- **Agent**: a program where an AI model decides some of the steps itself, such as what to search for. Here only the research step works that way. Everything else is plain code that always does the same thing.
- **Run**: one execution of the program from start to finish.
- **Thread**: the saved conversation for one run. You can come back to it later and ask follow-up questions.
- **Token**: the unit AI providers bill by. Roughly 4 characters of English text.

## 1. What you'll get

Each run looks at the last 6 hours of world news and finds the market-related stories that the most websites published. It picks the 3 that could matter most economically and searches recent news about each. You get back a short report: a summary per story, a list of facts each linked to the article it came from, and how the affected stock-market sectors moved on the last trading day. Facts that can't be tied to their article are removed and listed at the end.

A real report from 2026-10-02, shortened to one of its three stories:

```markdown
# Events as of 2026-10-02

Triage: Jev ok: 30 scored, kept the 3 most severe. Severity is 0 minor .. 3 severe, judged from GDELT data only.

Verify: ok: Jev checked 25 claims.

## 3. Asian shares mixed as global bond sell-off deepens ahead of US jobs data (2026-10-02)
Jev severity: 2.56 of 3. GDELT: 19 articles on 19 sites, lead https://www.newindianexpress.com/business/2026/Oct/02/asian-shares-mixed-after-global-bond-sell-off-deepens-and-ahead-of-us-jobs-data

Asian stocks were mixed on Friday 2 October 2026. Hong Kong fell sharply, while Korea, Australia and Taiwan gained slightly. A global bond sell-off pushed the US 10-year Treasury yield to 5.34% on Thursday, its highest since 2002. Investors were waiting for the US September jobs report for signals on Federal Reserve policy.

- Hong Kong's Hang Seng lost 2.7% to 23,956.32, hitting the lowest level since July. ([source](https://www.usnews.com/news/business/articles/2026-10-02/asian-shares-mixed-after-global-bond-sell-off-deepens-and-ahead-of-us-jobs-data), 2026-10-02).
- The 10-year U.S. Treasury yield rose to 5.34%, its highest since 2002, before dip buyers stepped in and brought it back to 5.28%. ([source](https://www.dailysabah.com/business/economy/global-sell-off-deepens-as-us-bond-yields-hit-highest-since-2002), 2026-10-01).

## Market reaction

| Sector | ETF | Move | vs SPY |
|---|---|---|---|
| energy | XLE | +2.0% | +1.8 pts |

Close 30 Sep to close 01 Oct, Yahoo Finance adjusted prices. Observed moves, not proof that the news caused them.
```

How to read it:

- **Jev severity** is a 0 to 3 score from an outside scoring service called Jev. It is based on the headline and news metadata only, so treat it as a ranking, not a fact.
- **GDELT** is a free service that lists the world's news articles every 15 minutes. "19 articles on 19 sites" means how widely the story was published.
- **Sector** and **ETF**: an ETF is a fund you can buy like a stock. XLE holds US energy companies, so its price shows how the energy sector moved. "vs SPY" is the difference from the whole US market (SPY tracks the S&P 500).

## 2. Before you start

### Software

| What | Version | Check it with |
|---|---|---|
| Python | 3.11 to 3.14 (from `pyproject.toml`) | `python3 --version` |
| uv, the tool that installs this project's packages | tested with 0.12.5 | `uv --version` |
| git | any | `git --version` |

To install uv, follow https://docs.astral.sh/uv/getting-started/installation/. I didn't test the install itself, because uv was already installed here.

### API keys

An API key is a password that lets the program use a paid online service. You put them in a file called `.env` (section 3).

| Key | Needed? | Used for | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | **Required** | Claude, the AI model that does the research | https://platform.claude.com (Anthropic Console), under API keys |
| `TAVILY_API_KEY` | **Recommended** | News search | https://app.tavily.com (free tier: 1,000 searches a month) |
| `TYPESAFE_API_KEY` | Optional, but the report is much worse without it | Jev, which scores how severe each story is and double-checks facts | https://typesafe.ai |
| `LANGSMITH_API_KEY` | Optional | A website that records every step of every run (section 6) | https://smith.langchain.com, under Settings, API Keys |

**Not used:** OpenAI and FRED. No code in this repo calls them, so you don't need those keys.

What happens without the optional keys (all tested):

- **No `TYPESAFE_API_KEY`:** the run still works. It researches the 3 most widely published stories instead of the 3 most severe, and facts are only checked by plain code. The report says so in its "Triage:" and "Verify:" lines. In my test, the top story became "9 bad money habits to avoid in the lead-up to Christmas", and there was no Market reaction table, because Jev is what tags facts with a sector.
- **`TAVILY_API_KEY=` left empty:** in my test the searches still worked (tavily-python 0.8.4 answered without a key). Tavily doesn't document how long that will last or what the limits are, so get a free key.
- **`TAVILY_API_KEY` line deleted from `.env` entirely:** the run crashes in the research step (`KeyError: 'TAVILY_API_KEY'`). Keep the line, even if it's empty.
- **No `LANGSMITH_API_KEY`:** runs work, but you can't look at them on the LangSmith website.

### Cost per run

These figures are measured from real runs on 2026-10-02, using the token counts LangSmith recorded and Anthropic's prices (Sonnet 5.5: $2 per million input tokens, $10 per million output; Haiku 4.5: $1 and $5).

| Kind of run | Claude cost | Tavily searches |
|---|---|---|
| New report (3 stories) | about $0.12 to $0.14 | up to 9 (1 credit each) |
| Follow-up question | about $0.04 to $0.07 | up to 3 |

- **Tavily:** the free tier covers 1,000 searches a month, which is at least 110 reports. After that it costs $0.008 per search.
- **Jev:** the cost isn't stated anywhere in this repo. Check https://typesafe.ai.
- **LangSmith:** check https://smith.langchain.com for its plans.

## 3. Setup

**Get the code.**

```sh
git clone https://github.com/tthh97/event-market-agent.git
cd event-market-agent
git checkout simplify-graph
```

> Not verified yet: on 2026-10-02 the code this guide describes was only on the maintainer's machine. GitHub had only the older `main` branch. Every step below was tested on an exact copy of the files that will be pushed.

**Install the packages.** This one command creates a private Python environment in a folder called `.venv` and installs everything into it. You don't need to make a virtual environment yourself.

```sh
uv sync
```

**Create your `.env` file and add your keys.**

```sh
cp .env.example .env
```

Open `.env` in any text editor and paste each key after its `=` sign, with no spaces or quotes:

```text
ANTHROPIC_API_KEY=sk-ant-...
TAVILY_API_KEY=tvly-...
TYPESAFE_API_KEY=
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_PROJECT=event-market-agent
```

Never commit `.env` to git. It's already listed in `.gitignore`.

**Check the install.** This runs the project's tests. They don't call any paid service.

```sh
uv run pytest -q
```

You should see `15 passed`.

**First data download.** There is no separate download step. The first run downloads the news it needs, as described in the next section.

## 4. First run

```sh
uv run main.py
```

This took 1 minute 28 seconds from an empty folder. Later runs took between 1 and 2 minutes. While it runs, it prints one block per step:

```text
[gdelt]
  as_of: "2026-10-02"
  stories: 30 items
[triage]
  severe: 3 items
  jev_status: "ok: 30 scored, kept the 3 most severe"
[research]
  findings: 3 items
  sources: 28 items
[verify]
  findings: 3 items
  rejected: 0 items
  verify_status: "ok: Jev checked 25 claims"
[price]
  moves: {"energy": [1.9512207527470737, 1.7728924227764287]}
  price_window: "Close 30 Sep to close 01 Oct, Yahoo Finance adjusted prices. Observed moves, not proof that the news caused them."
[output]
  report: ".../output/2026-10-02-e0b96294-172601.md"
  messages: 1 items

Thread e0b96294. Continue it with: uv run main.py --thread e0b96294 "your question"
```

What each step does:

| Step | What it does |
|---|---|
| `gdelt` | Downloads the last 6 hours of GDELT news lists, keeps market news, and picks the 30 most widely published stories |
| `triage` | Jev scores each story's severity, and the top 3 are kept |
| `research` | Claude searches the news about each of the 3 stories |
| `verify` | Removes facts that don't match their article |
| `price` | Looks up how the affected sectors moved |
| `output` | Writes the report |

**Where things land:**

- `output/<day>-<thread>-<time>.md` is the report, and the `.html` file with the same name is the same report as a page to open in a browser.
- `data/events.db` is a database of every news row downloaded and every run's results. Downloads are reused, so a run 15 minutes after another only fetches the newest 15 minutes of news.
- `data/threads.db` holds your threads, so you can ask follow-ups later.

**A past day instead of now:**

```sh
uv run main.py --date 2026-10-01
```

This reads the last 6 hours of that day, up to 23:45 UTC.

Note: `uv run main.py --help` says the default is "the latest 24 hours". That's wrong. It is the latest 6 hours.

## 5. Talking to the agent

### How it works

You can't ask a question first. Every conversation starts with a report on the latest news. After that, each message you send is a follow-up question, and the agent researches it. The first message you type in a new chat is not read: the program produces the report whatever you wrote.

There are three ways to talk to it. All three were tested.

**1. The command line.** Run a report, then use the thread ID it prints:

```sh
uv run main.py
uv run main.py --thread e0b96294 "Which airlines are most exposed to the diesel price surge?"
```

Check the thread ID carefully. If you mistype it, the program doesn't find your thread, ignores your question, and runs a whole new report. This was tested with `--thread nosuch "hello"`.

**2. Agent Chat UI, a chat window in your browser.** Start the local server and leave it running:

```sh
uv run langgraph dev
```

Then open this link: https://agentchat.vercel.app/?apiUrl=http://localhost:2024&assistantId=event_graph

Your first message returns the report, which takes about 1.5 minutes. Every message after that is a follow-up. Chats here are stored by the local server, separately from the command line's threads.

**3. LangSmith Studio.** This shows each step as a diagram while it runs. With `uv run langgraph dev` running, open https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024. You need to sign in to LangSmith. I checked that the server starts and prints this link, but didn't click through Studio itself.

### Example prompts

Every one of these was run on 2026-10-02. The first starts a thread, and the rest are follow-ups in that thread.

| # | What you type | What happens behind the scenes | What came back |
|---|---|---|---|
| 1 | `uv run main.py`, or any first chat message such as "What major events are happening today?" | The full pipeline: GDELT download, Jev scoring, then for each of 3 stories Claude searches Tavily up to 3 times while a smaller model (Haiku) picks the relevant sentences. Then the fact checks, prices and report | The report in section 1, after 1.5 minutes |
| 2 | "Which of today's events are likely to move markets?" | One research topic: Claude searches the last 7 days of news, Haiku picks sentences, then fact checks and prices | The jobs report, oil and bond yields. 8 facts kept |
| 3 | "What did the Federal Reserve signal this week about interest rates?" | Same as 2 | Officials hinted at a hike by year-end, no rush in October. 6 facts kept, 4 removed |
| 4 | "Follow up on the diesel story: which S&P 500 sectors reacted?" | Same as 2, and the price step shows the sector's move | Energy led (XLE +2.0%), rate-sensitive sectors fell. 3 facts kept, 4 removed as about an earlier period |
| 5 | "Which airlines are most exposed to the diesel price surge?" | Same as 2 | Unhedged US carriers (American, United) most exposed. 6 facts kept |
| 6 | "Which homebuilders are most exposed to the mortgage rate rise?" (thread for 2026-10-01) | Same as 2 | 3 facts kept, 7 removed |
| 7 | "Compare this to past events of the same type: how did energy stocks react to earlier diesel price spikes?" | Same as 2 | Little found. 1 fact kept, 5 removed. See the limits below |
| 8 | "What happened to Apple stock on 15 September 2026?" | Same as 2 | Nothing. 0 facts kept, because that date is outside the search window |

### What it can't answer well

- **Past events or comparisons with history.** Search only covers the 7 days ending on the report's day. The agent also remembers earlier reports' findings, but only for 7 days. Prompts 7 and 8 above show the result.
- **A question as the first message.** The first message always produces the standard report. Ask your question after that.
- **Single stocks.** Prices are only shown for the 11 sector funds (energy, banks and so on) versus the S&P 500, never for one company.
- **Today's price moves.** The price step leaves out today's trading session because it may still be open. Moves are always for the last finished trading day.
- **Cause and effect.** The report shows that a sector moved, not that the news caused it.
- **Anything without news coverage.** Every fact must come from an article that the search returned in this run.

## 6. Seeing what happened

With `LANGSMITH_API_KEY` set, every run is recorded on https://smith.langchain.com.

1. **Open the project.** Go to Projects and open `event-market-agent`. That's the name set by `LANGSMITH_PROJECT` in `.env`.
2. **Find your run.** Command-line runs are named "Event graph". Runs from Agent Chat or Studio are named "event_graph".
3. **Find a thread.** Open the Threads tab. A report and all its follow-ups are grouped under the thread ID the command line printed (for example `e0b96294`). This works because every run carries a `thread_id` label, which I checked for these runs.
4. **Read what the agent did.** Open a run. Its steps are listed in order: `gdelt`, `triage`, `research`, `verify`, `price`, `output`.
   - Inside `research` there is one entry per story, named `research: <story title>`.
   - Inside each one you see Claude's calls and every `search_news` call with the exact search text and the articles it got back.
   - Inside each `search_news` call, `reader: <search text>` shows which sentences Haiku kept.
5. **Spot where it went wrong.** A failed run is marked as an error. Clicking it shows the step that failed and the error text. For example, deleting the Tavily key showed `KeyError('TAVILY_API_KEY')` inside a `tools` step under `research`.

You don't need LangSmith for a first check. The report itself says a lot:

- The **Triage:** line says whether Jev scored the stories.
- The **Verify:** line says how many facts Jev checked.
- **Removed by verify** lists every fact that was thrown out, and why.

## 7. Optional extras

There is no daily tracker, no notification feature, and nothing called "worth a look" in this code. I searched the current code and the previous version in git history. Nothing runs on a schedule. A run happens only when you start one.

## 8. Troubleshooting

| What you see | Cause | Fix |
|---|---|---|
| `TypeError: Anthropic authentication failed: no API key ...` during `research` | `ANTHROPIC_API_KEY` is missing or empty | Add the key to `.env`. The download and triage steps had already run. The next run reuses the download |
| `KeyError: 'TAVILY_API_KEY'` during `research` | The `TAVILY_API_KEY` line is missing from `.env` | Add the line back (`TAVILY_API_KEY=your-key`) |
| `ValueError: The newest GDELT update is ... before 2027-01-01.` | `--date` is a day GDELT hasn't reached yet | Use today or an earlier day, or leave out `--date` |
| `main.py: error: a follow-up question needs --thread` | You gave a question without `--thread` | Run a report first, then use the thread ID it prints |
| You asked a follow-up but got a whole new report | The thread ID was mistyped, so no earlier report was found | Copy the ID exactly from the line `Thread ... Continue it with:` |
| The "Triage:" line says `not configured: kept the most-mentioned stories` | No `TYPESAFE_API_KEY` | Nothing is broken. Add the key if you want severity scores |
| `Triage: Jev failed (...)` or `Verify: failed (...)` | Jev was unreachable or returned an error | The run continues without Jev. Try again later |
| A story says the searches found nothing, with no facts | No news in the 7-day window matched | Normal for niche or older topics. Ask about something more recent |
| `Prices unavailable: ...` under Market reaction | Yahoo Finance didn't answer, or there weren't 2 finished trading days | The rest of the report is fine. Try again later |
| GDELT download fails with an HTTP error | GDELT's server is down or unreachable | Try again later. Files already downloaded are kept, so the next run only fetches what is missing. I couldn't trigger this one on purpose |
| A rate limit error (HTTP 429) from Claude | Too many requests at once | The Anthropic client retries twice by default, then the run stops. Wait a minute and run again. I didn't trigger this one |
| Agent Chat UI says it can't connect | `uv run langgraph dev` isn't running | Start it and keep that terminal open. Use http://localhost:2024 as the deployment URL |
