# state.py
"""The graph state: one typed object. Each node reads and writes only the fields marked for it."""

from typing import Annotated, Literal

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

Sector = Literal["energy", "materials", "industrials", "consumer_discretionary", "consumer_staples",
                 "health_care", "financials", "information_technology", "communication_services",
                 "utilities", "real_estate"]


class Story(BaseModel):
    """One GDELT story: every GKG article in the window that shares a page title."""
    url: str
    title: str = Field(description="The article's page title")
    articles: int
    sites: int
    actors: list[str]
    themes: list[str] = Field(description="GDELT market themes, e.g. ECON_OILPRICE")
    places: list[str]


class ScoredStory(Story):
    severity: float | None = Field(description="Jev's 0 minor .. 3 severe, None when Jev is not configured")
    confidence: float | None = None


class Claim(BaseModel):
    text: str = Field(description="One factual sentence, numbers copied exactly from the source")
    source_url: str = Field(description="URL of the article the sentence comes from")
    source_date: str = Field(description="The article's publication date YYYY-MM-DD, or unknown")
    sector: Sector | None = Field(default=None, description="Set by verify. Leave empty")
    unverified: bool = Field(default=False, description="Set by verify. Leave false")


class Finding(BaseModel):
    """What research found about one severe story, or about a follow-up question."""
    topic: str
    summary: str
    claims: list[Claim]


class Source(BaseModel):
    """An article the research tool returned in this run. Only these can be cited."""
    url: str
    title: str
    published: str  # YYYY-MM-DD or unknown
    excerpt: str


class State(BaseModel):
    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list)  # chat in, answer out
    as_of: str | None = None  # input, or set by gdelt: the GDELT day, YYYY-MM-DD
    stories: list[Story] = Field(default_factory=list)  # gdelt
    severe: list[ScoredStory] = Field(default_factory=list)  # triage
    jev_status: str = ""  # triage
    findings: list[Finding] = Field(default_factory=list)  # research
    sources: list[Source] = Field(default_factory=list)  # research
    rejected: list[str] = Field(default_factory=list)  # verify: claims removed, and why
    verify_status: str = ""  # verify
    moves: dict[str, tuple[float, float]] = Field(default_factory=dict)  # price: {sector: (move %, vs SPY pts)}
    price_window: str = ""  # price: which closes were used, or why there are no prices
    report: str | None = None  # output: path of the Markdown report


# The types above that a checkpoint may load back. Anything else in a checkpoint is refused.
CHECKPOINT_TYPES = [("state", name) for name in ("Story", "ScoredStory", "Claim", "Finding", "Source")]
