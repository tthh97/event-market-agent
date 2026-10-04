# prices.py
"""price node: how the markets named in kept claims moved. Plain Python, no LLM.

A claim's tag is a sector and a country. A US tag is priced with the sector's SPDR ETF against SPY, a
global tag with the sector's iShares global ETF against ACWI, and any other country with that
country's iShares ETF against ACWI. Few US-listed ETFs hold one sector of one country, so a
non-US row is the whole country's market, and the report says so.

Prices are taken over the last closed session up to the as-of day, from Yahoo Finance adjusted
closes. Today's session is left out because it may still be open. These are observed moves, not
proof that the news caused them.
"""

from datetime import date, timedelta

import pandas as pd
import yfinance as yf

from state import Move, State

SECTOR_ETFS = {"energy": "XLE", "materials": "XLB", "industrials": "XLI", "consumer_discretionary": "XLY",
               "consumer_staples": "XLP", "health_care": "XLV", "financials": "XLF",
               "information_technology": "XLK", "communication_services": "XLC", "utilities": "XLU",
               "real_estate": "XLRE"}
GLOBAL_SECTOR_ETFS = {"energy": "IXC", "materials": "MXI", "industrials": "EXI", "consumer_discretionary": "RXI",
                      "consumer_staples": "KXI", "health_care": "IXJ", "financials": "IXG",
                      "information_technology": "IXN", "communication_services": "IXP", "utilities": "JXI",
                      "real_estate": "REET"}
COUNTRY_ETFS = {"china": "MCHI", "japan": "EWJ", "india": "INDA", "uk": "EWU", "eurozone": "EZU", "germany": "EWG",
                "france": "EWQ", "canada": "EWC", "australia": "EWA", "south_korea": "EWY", "taiwan": "EWT",
                "brazil": "EWZ", "mexico": "EWW", "saudi_arabia": "KSA", "singapore": "EWS"}


def market(sector, country):
    """(ETF, benchmark) that prices one tag."""
    if country == "us":
        return SECTOR_ETFS[sector], "SPY"
    if country == "global":
        return GLOBAL_SECTOR_ETFS[sector], "ACWI"
    return COUNTRY_ETFS[country], "ACWI"


def yahoo_close(symbols, start, end):
    """Adjusted daily closes, one column per symbol, end date exclusive."""
    frame = yf.download(symbols, start=start.isoformat(), end=end.isoformat(),
                        auto_adjust=True, progress=False, threads=False)
    return frame["Close"] if not frame.empty else pd.DataFrame()


def market_moves(tags, as_of, download=yahoo_close):
    """[Move] over the last closed session up to as_of for each (sector, country) tag, and a line
    saying which closes were used. Tags without a price are left out."""
    markets = {tag: market(*tag) for tag in tags}
    symbols = sorted({symbol for pair in markets.values() for symbol in pair})
    try:
        close = download(symbols, as_of - timedelta(days=10), min(as_of + timedelta(days=1), date.today()))
    except Exception as error:
        return [], f"Prices unavailable: {type(error).__name__}."
    close = close.dropna(how="all")
    if len(close) < 2:
        return [], "Prices unavailable: not enough closed sessions."
    change = close.iloc[-1] / close.iloc[-2] - 1
    change = change[change.notna()]
    moves = [Move(sector=s, country=c, etf=etf, benchmark=bench, move=float(change[etf] * 100),
                  vs_benchmark=float((change[etf] - change[bench]) * 100))
             for (s, c), (etf, bench) in sorted(markets.items()) if etf in change and bench in change]
    window = (f"Close {close.index[-2]:%d %b} to close {close.index[-1]:%d %b}, Yahoo Finance adjusted prices. "
              "Observed moves, not proof that the news caused them.")
    return moves, window


def price(state: State) -> dict:
    """Reads findings and as_of. Writes moves and price_window."""
    tags = sorted({(c.sector, c.country) for f in state.findings for c in f.claims if c.sector and c.country})
    if not tags:
        return {"moves": [], "price_window": ""}
    moves, window = market_moves(tags, date.fromisoformat(state.as_of))
    return {"moves": moves, "price_window": window}
