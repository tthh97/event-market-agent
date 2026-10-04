# triage.py
"""triage node: Jev scores each story's economic severity; only the most severe go to research.

Jev (Typesafe) scores 0 minor .. 3 severe from the story's page title and GDELT metadata, not the
article, so the score ranks leads and is not a fact. Keeps the KEEP highest-scoring stories.
There is no minimum: on 30 Sep 2026 the highest of the top 100 stories scored 1.07, so a floor
at "moderate" (1.0) or "high" (2.0) left nothing to research.
Without TYPESAFE_API_KEY, or if Jev fails, keeps the KEEP most-mentioned stories and says so.
"""

import os

from typesafe_sdk import Score, TypeSafeClient

from state import ScoredStory, State

KEEP = 5

RUBRIC = [
    "minor: little economic consequence beyond the news cycle",
    "moderate: affects a few firms, one product market or one local economy",
    "high: disrupts a whole industry or a national economy",
    "severe: cross-industry or global disruption of supply, prices or policy",
]


def describe(story):
    """What Jev reads about one story."""
    return {"title": story.title,
            "summary": f"Unverified news-coverage lead. "
                       f"Actors: {', '.join(story.actors) or 'unknown'}. Themes: {', '.join(story.themes)}. "
                       f"Places: {'; '.join(story.places) or 'unknown'}. "
                       f"{story.articles} articles on {story.sites} sites."}


def jev_scores(stories):
    """{index: (score, confidence)} from one Jev request."""
    questions = {f"s{i}": Score(instructions=f"Read stories[{i}]. How severe is this story's economic consequence? "
                                              f"Judge from the reported facts, not the tone.", criteria=RUBRIC)
                 for i in range(len(stories))}
    with TypeSafeClient(timeout=90) as client:
        answers = client.system_one(state={"stories": [describe(s) for s in stories]},
                                    questions=questions).model_dump(mode="json")["answers"]
    return {int(key[1:]): (a["score"], a.get("confidence")) for key, a in answers.items()}


def triage(state: State) -> dict:
    """Reads stories. Writes severe and jev_status."""
    unscored = [ScoredStory(**s.model_dump(), severity=None) for s in state.stories[:KEEP]]
    if not os.getenv("TYPESAFE_API_KEY"):
        return {"severe": unscored, "jev_status": "not configured: kept the most-mentioned stories"}
    try:
        scores = jev_scores(state.stories)
    except Exception as error:
        return {"severe": unscored, "jev_status": f"failed ({type(error).__name__}): kept the most-mentioned stories"}
    scored = [ScoredStory(**s.model_dump(), severity=scores[i][0], confidence=scores[i][1])
              for i, s in enumerate(state.stories) if i in scores]
    scored.sort(key=lambda s: -s.severity)
    return {"severe": scored[:KEEP], "jev_status": f"ok: {len(scored)} scored, kept the {KEEP} most severe"}
