# market_returns.py
"""Observed ETF returns, never causal estimates. Patterns reused: tools. Run: via followup."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from models import SECTORS


def calculate_windows(close, event_date, as_of):
    """Date-only events use the first strictly later session to avoid invented intraday timing."""
    close = close.sort_index().copy()
    close.index = pd.to_datetime(close.index).date
    close = close.loc[close.index <= as_of]
    rows = []
    for ticker in close.columns:
        if ticker == 'SPY':
            continue
        aligned = close[[ticker, 'SPY']].dropna()
        later = [d for d in aligned.index if d > event_date]
        if not later:
            continue
        start = later[0]
        before = [d for d in aligned.index if d < start]
        if not before:
            continue
        base = before[-1]
        for offset in (0, 1, 5):
            if len(later) <= offset:
                continue
            end = later[offset]
            sector = float(aligned.loc[end, ticker] / aligned.loc[base, ticker] - 1)
            benchmark = float(aligned.loc[end, 'SPY'] / aligned.loc[base, 'SPY'] - 1)
            rows.append({'ticker': ticker, 'window': f'D0_to_D{offset}', 'baseline_date': str(base),
                         'start_session': str(start), 'end_session': str(end), 'return_pct': sector * 100,
                         'spy_return_pct': benchmark * 100, 'excess_percentage_points': (sector - benchmark) * 100})
    return rows


def reactions(event_date, sectors, as_of):
    if not event_date:
        return {'status': 'unknown_event_date', 'metrics': []}
    unknown = set(sectors) - set(SECTORS)
    if unknown:
        raise ValueError(f'Unknown sectors: {sorted(unknown)}')
    today_ny = datetime.now(ZoneInfo('America/New_York')).date()
    # Conservative: never mix partial current-session prices into daily returns.
    cutoff = min(as_of, today_ny - timedelta(days=1))
    if cutoff <= event_date or not sectors:
        return {'status': 'not_yet_observable', 'metrics': []}
    tickers = sorted({SECTORS[s] for s in sectors} | {'SPY'})
    try:
        frame = yf.download(tickers, start=(event_date - timedelta(days=10)).isoformat(),
                            end=(min(cutoff, event_date + timedelta(days=30)) + timedelta(days=1)).isoformat(),
                            auto_adjust=True, progress=False, threads=False)
        if frame.empty:
            return {'status': 'no_price_data', 'metrics': []}
        metrics = calculate_windows(frame['Close'], event_date, cutoff)
        return {'status': 'ok' if metrics else 'no_complete_window', 'metrics': metrics,
                'source': 'Yahoo Finance via yfinance', 'retrieved_at': datetime.now(ZoneInfo('UTC')).isoformat(),
                'timing': 'Date-only event: first strictly later trading session; current NY date excluded. Adjusted close returns. D0_to_D5 includes six sessions.',
                'limitation': 'Observed returns and differences versus SPY are not causal attribution or beta-adjusted abnormal returns.'}
    except Exception as exc:
        return {'status': 'unavailable', 'error_type': type(exc).__name__, 'metrics': []}


def listed(symbols, as_of):
    """Which symbols have a Yahoo Finance close in the ten days to as_of. Catches invented tickers.

    Returns (found, status). On a download failure nothing can be checked and every symbol is kept.
    """
    symbols = sorted(set(symbols))
    if not symbols:
        return set(), 'ok'
    try:
        frame = yf.download(symbols, start=(as_of - timedelta(days=10)).isoformat(),
                            end=(as_of + timedelta(days=1)).isoformat(),
                            auto_adjust=True, progress=False, threads=False)
    except Exception as exc:
        return set(symbols), f'unavailable: {type(exc).__name__}'
    if frame.empty:
        return set(), 'ok'
    close = frame['Close']
    if isinstance(close, pd.Series):
        close = close.to_frame(symbols[0])
    return {s for s in symbols if s in close.columns and close[s].notna().any()}, 'ok'
