# models.py
"""Shared model configuration and project settings.

Patterns reused: course models.py convention.
Run: imported by the other modules; no model calls occur here.
"""

import os
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)

# Day boundaries for "today" and publication-date filtering.
TIMEZONE = ZoneInfo(os.getenv("EVENT_TIMEZONE", "Asia/Singapore"))

# US sector -> SPDR sector ETF used for follow-up price observations.
SECTORS = {
    "energy": "XLE",
    "materials": "XLB",
    "industrials": "XLI",
    "consumer_discretionary": "XLY",
    "consumer_staples": "XLP",
    "health_care": "XLV",
    "financials": "XLF",
    "information_technology": "XLK",
    "communication_services": "XLC",
    "utilities": "XLU",
    "real_estate": "XLRE",
}

# Major news outlets for the first sweep. Measured on 30 Sep 2026: about the same number of
# same-day results as the open web (27 vs 29), but from Reuters, CNBC, WSJ, FT and Politico
# instead of mostly small sites.
MAJOR_OUTLETS = [
    "reuters.com", "apnews.com", "bloomberg.com", "cnbc.com", "ft.com", "wsj.com", "bbc.com",
    "nytimes.com", "theguardian.com", "economist.com", "aljazeera.com", "nikkei.com", "politico.com",
]


def is_major_outlet(url):
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in MAJOR_OUTLETS)


# US$ per million tokens, Anthropic first-party rates (claude-api reference, cached 25 Sep 2026).
# Cache writes: 1.25x input for the 5-minute TTL, 2x for 1 hour. Cache reads: 0.1x input.
PRICES_PER_MTOK = {
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
MONTHLY_BUDGET_USD = 10.0


def claude_cost_usd(model_name, usage):
    """Cost of one model's LangChain usage_metadata. None if the model has no known price.

    LangChain's input_tokens already includes cache reads and cache writes, so they are
    subtracted before pricing the uncached remainder.
    """
    price = next((p for name, p in PRICES_PER_MTOK.items() if model_name.startswith(name)), None)
    if price is None:
        return None
    details = usage.get("input_token_details") or {}
    read = details.get("cache_read") or 0
    write_5m = (details.get("ephemeral_5m_input_tokens") or 0) + (details.get("cache_creation") or 0)
    write_1h = details.get("ephemeral_1h_input_tokens") or 0
    uncached = usage.get("input_tokens", 0) - read - write_5m - write_1h
    dollars = (uncached * price["input"] + read * price["input"] * 0.1 + write_5m * price["input"] * 1.25
               + write_1h * price["input"] * 2 + usage.get("output_tokens", 0) * price["output"])
    return dollars / 1_000_000


model = init_chat_model(
    os.getenv("MODEL", "anthropic:claude-haiku-4-5"),
    timeout=60,
    max_retries=1,
    max_tokens=2500,
)
strong_model = init_chat_model(
    os.getenv("STRONG_MODEL", "anthropic:claude-sonnet-5-5"),
    timeout=240,
    max_retries=1,
    # A five-event Brief hit 5000 tokens mid-JSON on 25 Sep 2026 and failed to parse.
    max_tokens=12000,
)
