# verify.py
"""verify node: keep only claims that code and Jev can tie to a source. No LLM agent.

Code removes a claim whose URL search_news did not return, or whose numbers are not in that article.
Jev answers three questions per claim in one request: does the article state it (supported), is it
about the 7 days ending on the as-of day (current), and which sector's costs, prices or demand it
changes. Below DOUBT on supported or current removes the claim. Below SURE keeps it marked unverified.
Without TYPESAFE_API_KEY, or if Jev fails, only the code checks run, and verify_status says so.
Dates come from the tool that recorded them, never from a model.
"""

import os
import re

from typesafe_sdk import Choice, Noul, TypeSafeClient

from state import State

NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
SURE, DOUBT, SECTOR_SURE = 0.7, 0.4, 0.75  # Jev probabilities
SECTORS = {
    "energy": "oil, gas, refining, fuel producers and oilfield services",
    "materials": "chemicals, metals, mining, packaging",
    "industrials": "airlines, freight, trucking, rail, machinery, defense, construction",
    "consumer_discretionary": "autos, retail, travel, restaurants, homebuilders",
    "consumer_staples": "food, beverages, farming inputs, household products, grocery",
    "health_care": "drugs, biotech, devices, insurers, hospitals",
    "financials": "banks, insurers, brokers, asset managers",
    "information_technology": "software, semiconductors, hardware",
    "communication_services": "telecoms, media, internet platforms",
    "utilities": "electric, gas and water utilities",
    "real_estate": "REITs and property",
    "none": "no listed companies' costs, prices or demand are directly changed by this",
}


def numbers(text):
    """Numbers in text, without thousands separators: "US$1,250.5m" -> {"1250.5"}."""
    return {n.replace(",", "") for n in NUMBER.findall(text)}


def jev_checks(claims, as_of):
    """[(supported, current, sector, sector_confidence)] per (claim, source), from one Jev request."""
    state = {"as_of": as_of, "sectors": SECTORS,
             "claims": [{"text": c.text, "source": s.model_dump(exclude={"url"}) if s else {}} for c, s in claims]}
    questions = {}
    for i in range(len(claims)):
        text = f"`claims[{i}].text`"
        questions |= {
            f"supported{i}": Noul(instructions=f"Does `claims[{i}].source` state what {text} says, with the same meaning?"),
            f"current{i}": Noul(instructions=f"Is {text} about events in the 7 days ending `as_of`, rather than an earlier period?"),
            f"sector{i}": Choice(instructions=f"Whose costs, prices or demand does {text} most directly change? "
                                              f"Pick the US stock-market sector from `sectors`.", criteria=SECTORS)}
    with TypeSafeClient(timeout=90) as client:
        a = client.system_one(state=state, questions=questions).model_dump(mode="json")["answers"]
    return [(a[f"supported{i}"]["noul"], a[f"current{i}"]["noul"], a[f"sector{i}"]["choice"], a[f"sector{i}"]["confidence"])
            for i in range(len(claims))]


def review(claim, source, answer):
    """The claim as kept, or the reason it is removed. answer is Jev's, or None when Jev did not run."""
    if source is None:
        return "its URL is not an article search_news returned"
    missing = numbers(claim.text) - numbers(f"{source.title} {source.excerpt}")
    if missing:
        return f"number(s) {', '.join(sorted(missing))} not in the source"
    update = {"source_date": source.published}
    if answer:
        supported, current, sector, confidence = answer
        if supported < DOUBT:
            return "Jev: the source does not state it"
        if current < DOUBT:
            return "Jev: it is about an earlier period"
        update |= {"unverified": min(supported, current) < SURE,
                   "sector": sector if sector != "none" and confidence >= SECTOR_SURE else None}
    return claim.model_copy(update=update)


def verify(state: State) -> dict:
    """Reads findings, sources and as_of. Writes findings (kept claims only), rejected and verify_status."""
    by_url = {s.url: s for s in state.sources}
    claims = [(i, c, by_url.get(c.source_url)) for i, f in enumerate(state.findings) for c in f.claims]
    answers, status = [None] * len(claims), "not configured: URLs and numbers checked only"
    if os.getenv("TYPESAFE_API_KEY") and claims:
        try:
            answers = jev_checks([(c, s) for _, c, s in claims], state.as_of)
            status = f"ok: Jev checked {len(claims)} claims"
        except Exception as error:
            status = f"failed ({type(error).__name__}): URLs and numbers checked only"
    kept, rejected = [[] for _ in state.findings], []
    for (i, claim, source), answer in zip(claims, answers):
        result = review(claim, source, answer)
        if isinstance(result, str):
            rejected.append(f"{state.findings[i].topic}: \"{claim.text}\" ({result})")
        else:
            kept[i].append(result)
    return {"findings": [f.model_copy(update={"claims": k}) for f, k in zip(state.findings, kept)],
            "rejected": rejected, "verify_status": status}
