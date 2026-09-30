# brief_schema.py
"""Structured output the lead agent must return: Brief -> Event -> Exposure.

Patterns reused: structured response_format (m1).
Run: imported by lead_agent.py and cli.py. The host validates it again in brief_checks.py.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from models import SECTORS


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Exposure(StrictModel):
    sector: Literal[tuple(SECTORS)]
    channel: str = Field(min_length=1)
    reasoning: str = Field(min_length=1)
    status: Literal["reported_exposure", "hypothesis"]
    source_ids: list[str] = Field(min_length=1)


class Ticker(StrictModel):
    # A US-listed stock or ETF directly exposed to the event. Jev ranks these; the top three are shown.
    symbol: str = Field(pattern=r"^[A-Z][A-Z0-9.-]{0,9}$")
    kind: Literal["stock", "etf"]
    name: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)


class Event(StrictModel):
    # EventId of a saved event this is the same development as, else null for a new event.
    tracked_event_id: str | None
    title: str = Field(min_length=1)
    event_date: date | None
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1)
    why_watch: str
    source_ids: list[str] = Field(min_length=1)
    exposures: list[Exposure]
    tickers: list[Ticker] = Field(max_length=5)
    uncertainty: str
    watch_next: str
    status: Literal["developing", "ongoing", "resolved", "unclear"]


class Brief(StrictModel):
    events: list[Event] = Field(max_length=5)
    limitations: list[str]
