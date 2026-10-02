# prices.py
"""price node: how the sectors named in kept claims moved. Plain Python, no LLM.

Each sector's SPDR ETF and SPY are priced over the last closed session up to the as-of day, from
Yahoo Finance adjusted closes. Today's session is left out because it may still be open. These are
observed moves, not proof that the news caused them.
"""

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from state import State

SECTOR_ETFS = {"energy": "XLE", "materials": "XLB", "industrials": "XLI", "consumer_discretionary": "XLY",
               "consumer_staples": "XLP", "health_care": "XLV", "financials": "XLF",
               "information_technology": "XLK", "communication_services": "XLC", "utilities": "XLU",
               "real_estate": "XLRE"}


def yahoo_close(symbols, start, end):
    """Adjusted daily closes, one column per symbol, end date exclusive."""
    frame = yf.download(symbols, start=start.isoformat(), end=end.isoformat(),
                        auto_adjust=True, progress=False, threads=False)
    return frame["Close"] if not frame.empty else pd.DataFrame()


def sector_moves(sectors, as_of, download=yahoo_close):
    """{sector: (move %, vs SPY pts)} over the last closed session up to as_of, and a line saying
    which closes were used. Sectors without a price are left out."""
    etfs = {SECTOR_ETFS[s]: s for s in sectors}
    try:
        close = download(sorted([*etfs, "SPY"]), as_of - timedelta(days=10), min(as_of + timedelta(days=1), date.today()))
    except Exception as error:
        return {}, f"Prices unavailable: {type(error).__name__}."
    close = close.dropna(how="all")
    if "SPY" not in close or len(close) < 2:
        return {}, "Prices unavailable: not enough closed sessions."
    change = close.iloc[-1] / close.iloc[-2] - 1
    moves = {etfs[etf]: (float(change[etf] * 100), float((change[etf] - change["SPY"]) * 100))
             for etf in etfs if etf in change and not pd.isna(change[etf])}
    window = (f"Close {close.index[-2]:%d %b} to close {close.index[-1]:%d %b}, Yahoo Finance adjusted prices. "
              "Observed moves, not proof that the news caused them.")
    return moves, window


def price(state: State) -> dict:
    """Reads findings and as_of. Writes moves and price_window."""
    sectors = sorted({c.sector for f in state.findings for c in f.claims if c.sector})
    if not sectors:
        return {"moves": {}, "price_window": ""}
    moves, window = sector_moves(sectors, date.fromisoformat(state.as_of))
    return {"moves": moves, "price_window": window}
