"""Optional Jev (Typesafe SDK) judgements on the saved brief: event severity, sector direction
and impact, and ticker exposure.

Callers get Judgements aligned with the events they passed in. The question keys, rubrics and
ticker ranking stay inside this module.
Patterns reused: none; a typed decision service called by host code, not an agent tool.
Run: called by brief_run.py when TYPESAFE_API_KEY is set and --jev is on (default).
"""

from dataclasses import dataclass, field

from langsmith import traceable
from typesafe_sdk import Choice, Score, TypeSafeClient


def typesafe_call(state, questions, timeout=90):
    """The live adapter: one Typesafe request, its response as a plain dict."""
    with TypeSafeClient(timeout=timeout) as client:
        return client.system_one(state=state, questions=questions).model_dump(mode="json")


DIRECTION_CRITERIA = {
    "up": "Likely to outperform the S&P 500 because of this event",
    "down": "Likely to underperform the S&P 500 because of this event",
    "unclear": "The evidence does not support a direction",
}

# Score rubrics, level 0 first. The level names are what the report and database show.
SEVERITY = {
    "minor": "Little economic consequence beyond the news cycle",
    "moderate": "Affects a few firms, one product market or one local economy",
    "high": "Disrupts a whole industry or a national economy",
    "severe": "Cross-industry or global disruption of supply, prices or policy",
}
IMPACT = {
    "negligible": "No measurable effect on the sector's revenue, costs or prices",
    "small": "Affects a few firms in the sector",
    "material": "Affects a sizeable share of the sector's revenue, costs or prices",
    "large": "Changes the earnings or pricing outlook of the sector as a whole",
}
TICKER_FIT = {
    "none": "The business has no clear link to the event",
    "indirect": "The link runs through customers, suppliers or the wider economy",
    "direct": "Part of the business is directly affected",
    "core": "The event hits the core business or the whole fund",
}
PARTS = ("severity", "direction", "impact", "tickers")


def level(score, rubric):
    """Name of the rubric level nearest a Jev expected score."""
    names = list(rubric)
    return names[min(len(names) - 1, max(0, round(score)))]


def event_payload(event, evidence):
    """What Jev sees for one event: its summary, exposures, tickers and the cited source excerpts."""
    cited = dict.fromkeys(event["source_ids"] + [s for x in event["exposures"] + event.get("tickers", [])
                                                 for s in x["source_ids"]])
    return {"title": event["title"], "summary": event["summary"],
            "exposures": [{"sector": x["sector"], "channel": x["channel"], "reasoning": x["reasoning"]}
                          for x in event["exposures"]],
            "tickers": [{"symbol": t["symbol"], "kind": t["kind"], "name": t["name"], "reason": t["reason"]}
                        for t in event.get("tickers", [])],
            "sources": {s: (evidence.get(s) or {}).get("excerpt", "")[:1500] for s in cited}}


def questions_for(events, parts):
    """Jev questions keyed e{i}_severity, e{i}_x{j} (direction), e{i}_x{j}_impact, e{i}_t{k}."""
    questions = {}
    for i, event in enumerate(events):
        if "severity" in parts:
            questions[f"e{i}_severity"] = Score(
                instructions=f"Read events[{i}]. How severe is this event's economic consequence? "
                             f"Judge from the reported facts, not the tone.",
                criteria=list(SEVERITY.values()))
        for j, x in enumerate(event["exposures"]):
            sector = x["sector"].replace("_", " ")
            if "direction" in parts:
                questions[f"e{i}_x{j}"] = Choice(
                    instructions=f"Read events[{i}]. Through the channel in events[{i}].exposures[{j}], which way is "
                                 f"the {sector} sector most likely to move relative to the S&P 500 "
                                 f"over the next five trading sessions? Judge from the reported facts, not the tone.",
                    criteria=DIRECTION_CRITERIA)
            if "impact" in parts:
                questions[f"e{i}_x{j}_impact"] = Score(
                    instructions=f"Read events[{i}]. Through the channel in events[{i}].exposures[{j}], how large is "
                                 f"the effect on the US {sector} sector? Judge from the reported facts, not the tone.",
                    criteria=list(IMPACT.values()))
        if "tickers" in parts:
            for k, t in enumerate(event.get("tickers", [])):
                questions[f"e{i}_t{k}"] = Score(
                    instructions=f"Read events[{i}]. How directly is the business of events[{i}].tickers[{k}] "
                                 f"({t['symbol']}, {t['name']}) exposed to this event?",
                    criteria=list(TICKER_FIT.values()))
    return questions


@dataclass(frozen=True)
class Level:
    """A rubric score from Jev: 0 to 3, the nearest level name, and Jev's confidence."""
    score: float
    level: str
    confidence: float | None = None


@dataclass(frozen=True)
class Direction:
    direction: str  # up, down or unclear, relative to the S&P 500
    confidence: float | None = None


@dataclass
class ExposureJudgement:
    direction: Direction | None = None
    impact: Level | None = None


@dataclass
class TickerJudgement:
    index: int  # position in the event's tickers list
    fit: Level | None = None
    rank: int | None = None  # 1-3 for the top three, else None


@dataclass
class EventJudgement:
    severity: Level | None
    exposures: list[ExposureJudgement]
    tickers: list[TickerJudgement]  # best fit first; without Jev, the lead's order


@dataclass
class Judgements:
    """One EventJudgement per event passed in, in the same order."""
    status: str
    events: list[EventJudgement]
    model: str = "jev"
    usage: dict = field(default_factory=dict)
    error_type: str | None = None


def parse(key, raw):
    if raw.get("type") == "choice":
        return Direction(raw["choice"], raw.get("confidence"))
    rubric = SEVERITY if key.endswith("_severity") else IMPACT if key.endswith("_impact") else TICKER_FIT
    return Level(raw["score"], level(raw["score"], rubric), raw.get("confidence"))


def judgements_from(events, answers, status, **extra):
    """Attach parsed answers (keyed e{i}_...) to each event. Missing answers stay None."""
    result = []
    for i, event in enumerate(events):
        tickers = [TickerJudgement(k, answers.get(f"e{i}_t{k}")) for k in range(len(event.get("tickers", [])))]
        # Best Jev fit first. Ties and unscored tickers keep the lead's order.
        tickers.sort(key=lambda t: -(t.fit.score if t.fit else 0))
        for n, t in enumerate(tickers[:3]):
            t.rank = n + 1
        result.append(EventJudgement(
            severity=answers.get(f"e{i}_severity"),
            exposures=[ExposureJudgement(answers.get(f"e{i}_x{j}"), answers.get(f"e{i}_x{j}_impact"))
                       for j in range(len(event["exposures"]))],
            tickers=tickers))
    return Judgements(status, result, **extra)


def unscored(events, status="disabled"):
    """Judgements with no Jev answers: the lead's ticker order decides the top three."""
    return judgements_from(events, {}, status)


@traceable(name="Jev assessment", run_type="tool")
def assess(events, call, parts=PARTS):
    """Jev's event severity, sector direction and impact, and ticker exposure, in one request.

    `events` is a list of event_payload() dicts. A judgement from the event text only: Jev never sees prices.
    call is typesafe_call or a stand-in. None means Jev is not configured.
    """
    if call is None:
        return unscored(events, "not_configured")
    questions = questions_for(events, parts)
    if not questions:
        return unscored(events, "no_questions")
    try:
        raw = call({"events": events}, questions)
    except Exception as exc:
        return judgements_from(events, {}, "unavailable", error_type=type(exc).__name__)
    answers = {key: parse(key, a) for key, a in raw.get("answers", {}).items()}
    return judgements_from(events, answers, "ok", model=raw.get("model", "jev"), usage=raw.get("usage", {}))
