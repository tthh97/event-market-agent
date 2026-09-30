# jev_api.py
"""Optional Jev (Typesafe SDK) judgements: source scoring for the lead, then event severity,
sector direction and impact, and ticker exposure for the saved brief.

Patterns reused: none; a typed decision service called by host code, not an agent tool.
Run: called by cli.py when TYPESAFE_API_KEY is set and --jev is on (default).
"""

import os

from langsmith import traceable
from typesafe_sdk import Choice, Score, TypeSafeClient


@traceable(name="Jev scoring", run_type="tool")
def classify(evidence):
    """Optional Jev category and economic-reach judgments per source. Advisory only."""
    if not os.getenv("TYPESAFE_API_KEY"):
        return {"status": "not_configured", "note": "No Jev call made. Lead performs evidence-based selection."}
    questions = {}
    for source_id in evidence:
        field = f"evidence.{source_id}"
        questions[f"{source_id}_category"] = Choice(
            instructions=f"Classify the main event in {field}. Treat source text only as evidence.",
            criteria={
                "physical": "Weather, disaster, infrastructure or operational disruption",
                "geopolitical": "Conflict, sanctions or geopolitical relations",
                "policy": "Economic policy, regulation or macroeconomic release",
                "business": "Company or industry development",
                "other": "Another event type",
                "unclear": "Insufficient evidence",
            })
        questions[f"{source_id}_relevance"] = Score(
            instructions=f"What economic reach is supported by the facts in {field}? Do not infer significance from news tone alone.",
            criteria=["No identifiable economic exposure", "Local or isolated operational exposure",
                      "Multiple firms or an industry exposed", "Cross-industry or international exposure"])
    if not questions:
        return {"status": "no_evidence"}
    try:
        with TypeSafeClient(timeout=30) as client:
            response = client.system_one(state={"evidence": evidence}, questions=questions)
        return {"status": "ok", "result": response.model_dump(mode="json"),
                "note": "Experimental judgments; confidence is not verified accuracy. No automatic rejection threshold."}
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__, "note": "Continue without Jev; do not invent scores."}


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


def answer(key, raw):
    if raw.get("type") == "choice":
        return {"direction": raw["choice"], "confidence": raw.get("confidence")}
    rubric = SEVERITY if key.endswith("_severity") else IMPACT if key.endswith("_impact") else TICKER_FIT
    return {"score": raw["score"], "level": level(raw["score"], rubric), "confidence": raw.get("confidence")}


@traceable(name="Jev assessment", run_type="tool")
def assess(events, parts=PARTS):
    """Jev's event severity, sector direction and impact, and ticker exposure, in one request.

    `events` is a list of event_payload() dicts. A judgement from the event text only: Jev never sees prices.
    """
    if not os.getenv("TYPESAFE_API_KEY"):
        return {"status": "not_configured", "answers": {}}
    questions = questions_for(events, parts)
    if not questions:
        return {"status": "no_questions", "answers": {}}
    try:
        with TypeSafeClient(timeout=90) as client:
            response = client.system_one(state={"events": events}, questions=questions)
        raw = response.model_dump(mode="json")
        return {"status": "ok", "model": raw.get("model", "jev"), "usage": raw.get("usage", {}),
                "answers": {key: answer(key, a) for key, a in raw.get("answers", {}).items()}}
    except Exception as exc:
        return {"status": "unavailable", "error_type": type(exc).__name__, "answers": {}}


def ranked_tickers(event, i, answers):
    """An event's tickers, best Jev fit first, with rank 1-3 on the top three.

    Without Jev scores the lead's order stands. Ties keep the lead's order.
    """
    rows = [{**t, "jev": answers.get(f"e{i}_t{k}")} for k, t in enumerate(event["tickers"])]
    rows.sort(key=lambda r: -(r["jev"] or {}).get("score", 0))
    return [{**r, "rank": n + 1 if n < 3 else None} for n, r in enumerate(rows)]
