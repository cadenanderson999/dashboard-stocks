#!/usr/bin/env python3
"""Fetch daily prices for the stock universe and compute trading signals.

The universe is the union of the Robinhood Top 100 and the S&P 500. Each ticker
is tagged with the list(s) it belongs to and its GICS sector.

For every ticker this computes, on the **daily** timeframe, the multi-factor
composite rating from ``strategies.py``:

  * Trend     -> Minervini Trend Template (50/150/200 SMA stack, rising 200 SMA,
                 52-week range position)
  * Momentum  -> cross-sectional relative-strength rank (1-99) + 12-1 momentum
  * Timing    -> pullback-in-uptrend entries (RSI 14 / RSI 2), bear bounces
  * Volume    -> accumulation vs distribution (up/down volume, OBV)

plus the supporting indicators (EMAs, RSI, MACD, ADX, ATR, realised vol,
relative volume) and writes everything to ``data/stocks.json``, which the
static front-end loads. ``scripts/backtest.py`` compares the rating against
the previous EMA-50/200 + RSI table.

Data source: Yahoo Finance via the ``yfinance`` library (free, no API key).

Usage:
    python scripts/generate_data.py             # fetch live data
    python scripts/generate_data.py --sample    # write deterministic sample data
                                                # (used when no network access)

Live failures retain cached data with freshness metadata. Sample data is only
written when --sample is explicitly selected.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from datetime import datetime, timezone

# tickers.py lives next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tickers import (  # noqa: E402
    ROBINHOOD_TOP_100,
    RH_SECTORS,
    sp500_fallback_map,
)
import strategies as strat  # noqa: E402
import market_data as md

# Universe list labels.
RH_LIST = "Robinhood 100"
SP_LIST = "S&P 500"

# Browser-like UA so Wikipedia doesn't 403 the fallback fetch.
WIKI_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dashboard-stocks/1.0"
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Indicator parameters (kept for the EMA/RSI columns; the rating itself is
# parameterised in strategies.py).
EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14

# Relative-volume (RVOL) parameters.
RVOL_AVG_WINDOW = 50   # trailing average volume window (days)
RVOL_LOOKBACK = 30     # aggregate RVOL over this many recent trading days
RVOL_THRESHOLD = 2.0   # a day is a "surge" when RVOL exceeds this multiple

# Need at least EMA_SLOW points for a meaningful 200 EMA; ask for ~2 years.
LOOKBACK = "2y"

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "stocks.json"
)
DETAILS_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "details.json"
)


# --------------------------------------------------------------------------- #
# Indicator math (pure Python so it works with or without pandas)
# --------------------------------------------------------------------------- #
def ema(values, period):
    """Exponential moving average. Returns a list aligned with ``values``."""
    return strat.ema_series(values, period)


def rsi(values, period=RSI_PERIOD):
    """Wilder's RSI. Returns the latest RSI value (0-100) or None."""
    series = strat.rsi_series(values, period)
    return series[-1] if series else None


def rvol_stats(volumes, avg_window=RVOL_AVG_WINDOW, lookback=RVOL_LOOKBACK,
               threshold=RVOL_THRESHOLD):
    """Relative-volume statistics over the last ``lookback`` trading days.

    For each day, RVOL = that day's volume / the trailing ``avg_window``-day
    average volume (the average uses the days *before* the current one). We then
    aggregate the most recent ``lookback`` daily RVOLs into:

      * rvol_mean      -- mean RVOL over the window (≈ how busy vs. normal)
      * rvol_high_days -- count of days with RVOL > ``threshold`` (volume surges)
      * rvol_today     -- the most recent day's RVOL

    Returns a dict; values are None when there isn't enough history.
    """
    none = {"rvol_mean": None, "rvol_high_days": None,
            "rvol_today": None, "rvol_days_counted": 0}
    if not volumes or len(volumes) < avg_window + 1:
        return none

    daily_rvol = []
    for i in range(avg_window, len(volumes)):
        trailing_avg = sum(volumes[i - avg_window:i]) / avg_window
        if trailing_avg > 0:
            daily_rvol.append(volumes[i] / trailing_avg)

    if not daily_rvol:
        return none

    window = daily_rvol[-lookback:]
    return {
        "rvol_mean": sum(window) / len(window),
        "rvol_high_days": sum(1 for r in window if r > threshold),
        "rvol_today": daily_rvol[-1],
        "rvol_days_counted": len(window),
    }


# --------------------------------------------------------------------------- #
# Rating logic (see strategies.py)
# --------------------------------------------------------------------------- #
def make_rating(ind, rs_rank=None):
    """Composite multi-factor rating for the latest bar of an indicator bundle."""
    return strat.evaluate(ind, -1, rs_rank)


def compute_rs_ranks(price_map):
    """Cross-sectional RS rank (1-99) for ``{symbol: {"close": [...]}}``."""
    raw = {}
    for sym, p in price_map.items():
        closes = p.get("close") or []
        if len(closes) > strat.YEAR and not p.get("quality", {}).get("stale"):
            raw[sym] = strat.weighted_rs_series(closes)[-1]
    return strat.percentile_ranks(raw)


def fetch_sp500():
    """Fetch the current S&P 500 from Wikipedia: ``{symbol: {sector, name}}``.

    Returns an empty dict on any failure (caller falls back to the static list).
    """
    import urllib.request
    from io import StringIO
    import pandas as pd

    try:
        req = urllib.request.Request(SP500_WIKI_URL, headers={"User-Agent": WIKI_UA})
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")
        df = pd.read_html(StringIO(html))[0]
    except Exception as exc:  # noqa: BLE001
        print(f"S&P 500 live fetch failed ({exc}); using static fallback.",
              file=sys.stderr)
        return {}

    out = {}
    for _, row in df.iterrows():
        # Yahoo uses dashes, Wikipedia uses dots (e.g. BRK.B -> BRK-B).
        sym = str(row["Symbol"]).strip().replace(".", "-")
        out[sym] = {
            "sector": str(row["GICS Sector"]).strip(),
            "name": str(row["Security"]).strip(),
        }
    print(f"Fetched {len(out)} S&P 500 constituents from Wikipedia.")
    return out


def build_universe(live=True):
    """Build the merged universe: ``{symbol: {sector, name, lists}}``.

    Union of the Robinhood Top 100 and the S&P 500. When ``live`` is True the S&P
    list is fetched from Wikipedia (with static fallback); otherwise the static
    fallback is used directly (used by --sample to avoid a network call).
    """
    sp = (fetch_sp500() if live else {}) or sp500_fallback_map()

    universe = {}

    def slot(sym):
        return universe.setdefault(
            sym, {"sector": None, "name": None, "lists": set()}
        )

    for sym in ROBINHOOD_TOP_100:
        u = slot(sym)
        u["lists"].add(RH_LIST)
        # Seed sector from the RH map; S&P may overwrite with the official one.
        u["sector"] = u["sector"] or RH_SECTORS.get(sym)

    for sym, meta in sp.items():
        u = slot(sym)
        u["lists"].add(SP_LIST)
        if meta.get("sector"):
            u["sector"] = meta["sector"]
        if meta.get("name"):
            u["name"] = meta["name"]

    if live:
        from refresh_extras import retention_end
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo('America/New_York')).date()
        for e in md.read_json(md.ROOT / 'data/earnings_calendar.json').get('events', []):
            if e['retain_until'] >= today.isoformat():
                u = slot(e['symbol'])
                u['lists'].add('Earnings watch')
                u['name'] = u['name'] or e.get('name')
                if not u.get('earnings_date') or e['date'] > u['earnings_date']:
                    u['earnings_date'] = e['date']
                    u['earnings_retain_until'] = e['retain_until']
    # Guarantee every ticker has a sector label.
    for u in universe.values():
        u["sector"] = u["sector"] or "Other"

    print(f"Universe: {len(universe)} unique tickers "
          f"({sum(RH_LIST in u['lists'] for u in universe.values())} Robinhood, "
          f"{sum(SP_LIST in u['lists'] for u in universe.values())} S&P 500).")
    return universe


# Extra .info fields kept for the per-stock detail page.
INFO_KEYS = [
    "longName", "shortName", "industry", "website", "country",
    "previousClose", "open", "dayLow", "dayHigh",
    "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
    "forwardPE", "priceToBook", "trailingEps", "beta",
    "dividendRate", "dividendYield",
    "averageVolume", "averageVolume10days", "sharesOutstanding",
    # Analyst price targets + recommendation.
    "targetMeanPrice", "targetHighPrice", "targetLowPrice", "targetMedianPrice",
    "numberOfAnalystOpinions", "recommendationKey", "recommendationMean",
    # Next earnings date (unix timestamps).
    "earningsTimestamp", "earningsTimestampStart",
]

# How many recent quarters of earnings history to store.
EARNINGS_QUARTERS = 6


def extract_earnings(ticker):
    """Recent quarterly revenue / net income / EPS from the income statement.

    Returns a list (newest first) of ``{period, revenue, net_income, eps}``.
    Empty statements return ``[]``; acquisition exceptions reach the retry gateway.
    """
    df = ticker.quarterly_income_stmt
    if df is None or getattr(df, "empty", True):
        return []

    def cell(col, *fields):
        for field in fields:
            try:
                v = df.loc[field, col]
            except Exception:  # noqa: BLE001
                continue
            if v is not None and v == v:  # not None, not NaN
                return float(v)
        return None

    rows = []
    for col in list(df.columns)[:EARNINGS_QUARTERS]:
        period = col.strftime("%Y-%m-%d") if hasattr(col, "strftime") else str(col)
        rows.append({
            "period": period,
            "revenue": cell(col, "Total Revenue", "Operating Revenue"),
            "net_income": cell(col, "Net Income", "Net Income Common Stockholders"),
            "eps": cell(col, "Diluted EPS", "Basic EPS"),
        })
    return rows


def fetch_fundamentals(symbols, include_earnings=True):
    """Refresh independently cached profiles, metrics, analysts and statements."""
    yf = md.yahoo()
    cache = md.store()
    profile_keys = {'longName', 'shortName', 'industry', 'website', 'country', 'sector'}
    analyst_keys = {k for k in INFO_KEYS if k.startswith('target') or
                    k.startswith('recommendation') or k == 'numberOfAnalystOpinions'}
    out = {}
    for sym in symbols:
        tk = yf.Ticker(sym)
        # .info bundles profiles, quotes and analysts in one operation. Cache
        # that response once, then refresh individual groups on their own TTL.
        def info_response():
            value = tk.info or {}
            if not any(value.get(k) is not None for k in
                       ('marketCap', 'currentPrice', 'regularMarketPrice', 'longName')):
                raise md.FetchError('empty_response')
            return value
        groups = [('profile', profile_keys, md.staggered_ttl(sym, 28)),
                  ('metrics', (set(INFO_KEYS) | {'trailingPE', 'marketCap'}) - profile_keys - analyst_keys, md.staggered_ttl(sym, 7)),
                  ('analysts', analyst_keys, md.staggered_ttl(sym, 3))]
        combined, quality = {}, {}
        for group, keys, ttl in groups:
            def get_group(keys=keys):
                response, meta = cache.fetch(f'info:{sym}', info_response, 86400)
                if meta['status'] in ('stale', 'missing'):
                    raise md.FetchError(meta.get('reason', 'provider_error'))
                return {k: response.get(k) for k in keys}
            # The wrapper uses the same gateway; no additional Yahoo call is
            # necessary if the shared .info response is already cached.
            value, meta = cache.fetch(f'{group}:{sym}', get_group, ttl, network=False, preserve_none=True)
            combined.update(value or {})
            quality[group] = meta
        earnings = []
        if include_earnings:
            force_earnings = False
            ttl = md.staggered_ttl(sym, 7)
            next_date = _ts_to_date(combined.get('earningsTimestampStart') or combined.get('earningsTimestamp'))
            if next_date:
                delta = abs((datetime.fromisoformat(next_date).date() - datetime.now(timezone.utc).date()).days)
                if delta <= 7:
                    ttl = 86400
                    cached_date = cache.get(f'earnings:{sym}').get('updated_at')
                    force_earnings = bool(cached_date and
                        cache.clock() - datetime.fromisoformat(cached_date).timestamp() >= 86400)
            earnings, quality['earnings'] = cache.fetch(
                f'earnings:{sym}', lambda: extract_earnings(tk), ttl, force=force_earnings)
        missing = {k: ('unavailable' if quality[g]['status'] in ('fresh', 'cached')
                        else quality[g].get('reason', 'missing'))
                   for g, keys, _ in groups for k in keys if combined.get(k) is None}
        out[sym] = {'pe': combined.get('trailingPE'),
                    'market_cap': combined.get('marketCap'),
                    'sector': combined.get('sector'), 'info': combined,
                    'earnings': earnings or [], 'quality': quality,
                    'missing_fields': missing}
    return out


def build_record(symbol, closes, volumes=None, highs=None, lows=None,
                 rs_rank=None, name=None, pe=None, market_cap=None,
                 sector=None, lists=None):
    """Compute indicators + rating for one ticker from its OHLCV series.

    ``highs``/``lows`` are optional (ADX/ATR degrade to close-only estimates);
    ``rs_rank`` is the ticker's cross-sectional relative-strength rank (1-99),
    computed by the caller across the whole universe.
    """
    volumes = volumes or []
    ind = strat.compute_indicators(closes, highs, lows, volumes)
    last = lambda key: ind[key][-1] if ind.get(key) else None  # noqa: E731

    rv = rvol_stats(volumes)
    price = closes[-1] if closes else None
    prev = closes[-2] if len(closes) >= 2 else None
    change_pct = ((price - prev) / prev * 100.0) if (price and prev) else None

    rating = make_rating(ind, rs_rank) if closes else strat.no_data_rating()

    def r(x, n=2):
        return round(x, n) if isinstance(x, (int, float)) and not math.isnan(x) else None

    atr = last("atr")
    high52, low52 = last("high52"), last("low52")
    rec = {
        "symbol": symbol,
        "name": name or symbol,
        "price": r(price),
        "change_pct": r(change_pct),
        # Moving averages.
        "ema50": r(last("ema50")) if len(closes) >= EMA_FAST else None,
        "ema200": r(last("ema200")) if len(closes) >= EMA_SLOW else None,
        "sma50": r(last("sma50")),
        "sma150": r(last("sma150")),
        "sma200": r(last("sma200")),
        "sma200_rising": rating.get("sma200_rising"),
        # Oscillators / trend strength.
        "rsi": r(last("rsi14"), 1),
        "rsi2": r(last("rsi2"), 1),
        "macd": r(last("macd"), 3),
        "macd_signal": r(last("macd_signal"), 3),
        "macd_hist": r(last("macd_hist"), 3),
        "adx": r(last("adx"), 1),
        "atr": r(atr),
        "atr_pct": r(atr / price * 100.0) if (atr and price) else None,
        "stop_2atr": r(price - 2.0 * atr) if (atr and price) else None,
        "hv60": r(last("hv"), 1),
        # 52-week range + returns.
        "high52": r(high52),
        "low52": r(low52),
        "pct_off_high": r((price / high52 - 1.0) * 100.0) if (price and high52) else None,
        "pct_above_low": r((price / low52 - 1.0) * 100.0) if (price and low52) else None,
        "ret_1m": r((last("ret_1m") or 0) * 100.0, 1) if last("ret_1m") is not None else None,
        "ret_3m": r((last("ret_3m") or 0) * 100.0, 1) if last("ret_3m") is not None else None,
        "ret_6m": r((last("ret_6m") or 0) * 100.0, 1) if last("ret_6m") is not None else None,
        "mom_12_1": r((last("mom_12_1") or 0) * 100.0, 1) if last("mom_12_1") is not None else None,
        # Relative strength.
        "rs_rank": rs_rank,
        "rs_raw": r(last("rs_raw"), 4),
        # Volume.
        "udv_ratio": r(last("udv")),
        "rvol_mean": r(rv["rvol_mean"]),
        "rvol_high_days": rv["rvol_high_days"],
        "rvol_today": r(rv["rvol_today"]),
        # Fundamentals / tags.
        "pe": r(pe, 1),
        "market_cap": int(market_cap) if isinstance(market_cap, (int, float)) else None,
        "sector": sector or "Other",
        "lists": sorted(lists) if lists else [],
    }
    rec.update(rating)
    return rec


def _ts_to_date(ts):
    """Unix timestamp -> 'YYYY-MM-DD', or None."""
    if not isinstance(ts, (int, float)) or math.isnan(ts):
        return None
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return None


def build_detail(info, earnings=None):
    """Extended per-stock fundamentals for the detail page."""
    info = info or {}

    def num(key, n=2):
        v = info.get(key)
        return round(v, n) if isinstance(v, (int, float)) and not math.isnan(v) else None

    def ival(key):
        v = info.get(key)
        return int(v) if isinstance(v, (int, float)) and not math.isnan(v) else None

    next_earnings = _ts_to_date(
        info.get("earningsTimestampStart") or info.get("earningsTimestamp")
    )

    return {
        "industry": info.get("industry"),
        "website": info.get("website"),
        "country": info.get("country"),
        "previous_close": num("previousClose"),
        "open": num("open"),
        "day_low": num("dayLow"),
        "day_high": num("dayHigh"),
        "week52_low": num("fiftyTwoWeekLow"),
        "week52_high": num("fiftyTwoWeekHigh"),
        "forward_pe": num("forwardPE", 1),
        "price_to_book": num("priceToBook", 2),
        "eps": num("trailingEps", 2),
        "beta": num("beta", 2),
        "dividend_rate": num("dividendRate", 2),
        "avg_volume": ival("averageVolume"),
        "avg_volume_10d": ival("averageVolume10days"),
        "shares_outstanding": ival("sharesOutstanding"),
        "next_earnings": next_earnings,
        "analyst": {
            "target_mean": num("targetMeanPrice"),
            "target_high": num("targetHighPrice"),
            "target_low": num("targetLowPrice"),
            "target_median": num("targetMedianPrice"),
            "num_analysts": ival("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey"),
            "recommendation_mean": num("recommendationMean", 2),
        },
        "earnings": earnings or [],
    }


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #
EMPTY_PRICES = {"dates": [], "close": [], "high": [], "low": [], "volume": []}


def download_prices(symbols, chunk=100, period=LOOKBACK):
    """Shared per-symbol cache. chunk retained for callers; no burst downloads."""
    return {sym: md.price_history(sym, period) for sym in symbols}


def fetch_live():
    """Fetch real data via yfinance. Returns list of records (may be empty)."""
    universe = build_universe()
    symbols = list(universe.keys())
    print(f"Downloading {len(symbols)} tickers from Yahoo Finance ({LOOKBACK})...")

    prices = download_prices(symbols)
    price_only = os.getenv('PRICE_ONLY') == '1'
    previous_snapshot = md.read_json(OUTPUT_PATH)
    old_rows = {r['symbol']:r for r in previous_snapshot.get('stocks', [])} if not previous_snapshot.get('is_sample') else {}
    old_details = md.read_json(DETAILS_PATH).get('stocks', {})
    fundamentals = ({sym: {'pe':r.get('pe'), 'market_cap':r.get('market_cap'),
                     'quality':r.get('data_quality', {}).get('fundamentals', {}),
                     'missing_fields':r.get('data_quality', {}).get('missing_fields', {})}
                     for sym,r in old_rows.items()} if price_only else fetch_fundamentals(symbols))
    rs_ranks = compute_rs_ranks(prices)

    previous_doc = md.read_json(OUTPUT_PATH)
    previous = {} if previous_doc.get('is_sample') else {r['symbol']: r for r in previous_doc.get('stocks', [])}
    records = []
    details = {}
    skipped = 0
    for symbol in symbols:
        p = prices.get(symbol) or EMPTY_PRICES
        if not p["close"]:
            skipped += 1

        meta = universe[symbol]
        f = fundamentals.get(symbol, {})
        records.append(build_record(
            symbol, p["close"], p["volume"], highs=p["high"], lows=p["low"],
            rs_rank=rs_ranks.get(symbol),
            name=meta.get("name"),
            pe=f.get("pe"), market_cap=f.get("market_cap"),
            sector=meta.get("sector"), lists=meta.get("lists"),
        ))
        if not p['close'] and symbol in previous:
            records[-1] = dict(previous[symbol])
        rec = records[-1]
        rec['data_quality'] = {'prices': p.get('quality', {}),
                               'fundamentals': f.get('quality', {}),
                               'missing_fields': f.get('missing_fields', {})}
        rec['price_as_of'] = p.get('quality', {}).get('as_of') or rec.get('price_as_of')
        rec['price_valid_until'] = md.price_valid_until()
        if p.get('quality', {}).get('stale'):
            rec.update(rating='Stale' if rec.get('price') is not None else 'No Data', score=None,
                       rs_rank=None, setups=[], reason='Current price data unavailable; showing last known values.')
        details[symbol] = dict(old_details.get(symbol, {})) if price_only else build_detail(f.get("info"), f.get("earnings"))
        details[symbol]['data_quality'] = rec['data_quality']
        rec['next_earnings'] = meta.get('earnings_date') or details[symbol].get('next_earnings')
        rec['earnings_retain_until'] = meta.get('earnings_retain_until')
        details[symbol]['next_earnings'] = rec['next_earnings']
        if previous.get(symbol, {}).get('quote'):
            rec['quote'] = previous[symbol]['quote']
        old = previous.get(symbol, {})
        if (old.get('score') is not None and rec.get('score') is not None
                and old.get('price_as_of') and rec.get('price_as_of')
                and old['price_as_of'] < rec['price_as_of']):
            rec['previous_signal'] = {k: old.get(k) for k in ('score', 'rating', 'price_as_of')}
        elif old.get('price_as_of') == rec.get('price_as_of'):
            rec['previous_signal'] = old.get('previous_signal')

    print(f"Built {len(records)} records ({skipped} tickers had no usable data).")
    md.store().report('stocks')
    if not any(r.get('price') is not None for r in records):
        return [], {}
    return records, details


def generate_sample():
    """Deterministic, clearly-fake data so the UI renders without network."""
    print("Generating SAMPLE data (not real market prices).")
    rng = random.Random(42)
    # Use the static universe so sample data mirrors the real shape/size.
    universe = build_universe(live=False)
    records = []
    details = {}
    # First pass: synthetic OHLCV for everyone (so RS ranks can be computed
    # cross-sectionally, exactly like the live path).
    prices = {}
    for symbol in universe:
        base = rng.uniform(15, 500)
        base_vol = rng.uniform(1e6, 5e7)
        drift = rng.uniform(-0.0015, 0.0020)
        closes, highs, lows, volumes = [], [], [], []
        price = base
        for _ in range(300):
            price *= 1.0 + drift + rng.uniform(-0.02, 0.02)
            price = max(price, 1.0)
            closes.append(price)
            highs.append(price * (1.0 + rng.uniform(0.0, 0.02)))
            lows.append(price * (1.0 - rng.uniform(0.0, 0.02)))
            # Normal-ish volume with occasional surges.
            vol = base_vol * rng.uniform(0.6, 1.4)
            if rng.random() < 0.08:
                vol *= rng.uniform(2.0, 4.0)
            volumes.append(vol)
        prices[symbol] = {"close": closes, "high": highs, "low": lows,
                          "volume": volumes, "base_vol": base_vol}
    rs_ranks = compute_rs_ranks(prices)

    for symbol, meta in universe.items():
        p = prices[symbol]
        closes, volumes, base_vol = p["close"], p["volume"], p["base_vol"]
        # Plausible fundamentals: most have a P/E, some (no earnings) don't.
        pe = None if rng.random() < 0.15 else rng.uniform(8, 70)
        market_cap = closes[-1] * rng.uniform(1e7, 6e9)
        records.append(build_record(
            symbol, closes, volumes, highs=p["high"], lows=p["low"],
            rs_rank=rs_ranks.get(symbol), name=meta.get("name"),
            pe=pe, market_cap=market_cap,
            sector=meta.get("sector"), lists=meta.get("lists"),
        ))
        last = closes[-1]
        info = {
            "industry": meta.get("sector"),
            "website": None, "country": "United States",
            "previousClose": closes[-2], "open": closes[-2] * rng.uniform(0.99, 1.01),
            "dayLow": last * 0.98, "dayHigh": last * 1.02,
            "fiftyTwoWeekLow": min(closes[-252:]), "fiftyTwoWeekHigh": max(closes[-252:]),
            "forwardPE": None if pe is None else pe * rng.uniform(0.8, 1.1),
            "priceToBook": rng.uniform(1, 12), "trailingEps": last / (pe or 20),
            "beta": rng.uniform(0.5, 2.0), "dividendRate": rng.choice([0, rng.uniform(0.5, 4)]),
            "averageVolume": int(base_vol), "averageVolume10days": int(base_vol * rng.uniform(0.8, 1.2)),
            "sharesOutstanding": int(market_cap / last),
            # Analyst targets clustered around the current price.
            "targetMeanPrice": last * rng.uniform(0.9, 1.3),
            "targetHighPrice": last * rng.uniform(1.3, 1.7),
            "targetLowPrice": last * rng.uniform(0.6, 0.9),
            "targetMedianPrice": last * rng.uniform(0.95, 1.25),
            "numberOfAnalystOpinions": rng.randint(4, 45),
            "recommendationKey": rng.choice(["buy", "hold", "strong_buy", "sell"]),
            "recommendationMean": round(rng.uniform(1.5, 3.5), 2),
            "earningsTimestampStart": 1786000000 + rng.randint(0, 90) * 86400,
        }
        # Synthetic quarterly earnings history (newest first).
        shares = info["sharesOutstanding"]
        earnings = []
        rev = market_cap * rng.uniform(0.15, 0.5)
        for q in range(EARNINGS_QUARTERS):
            month = 3 * ((q + 1))
            ni = rev * rng.uniform(0.05, 0.25)
            earnings.append({
                "period": f"2025-{max(1, 12 - month % 12):02d}-28",
                "revenue": rev,
                "net_income": ni,
                "eps": round(ni / shares, 2) if shares else None,
            })
            rev *= rng.uniform(0.92, 0.99)  # older quarters slightly smaller
        details[symbol] = build_detail(info, earnings)
    return records, details


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def write_output(records, is_sample):
    # Sort: strongest buys first, then by RSI.
    records.sort(key=lambda r: (-(r.get("score") or 0), r.get("rsi") or 50))

    # Distinct lists + sectors present, for building the front-end filters.
    lists = sorted({l for r in records for l in r.get("lists", [])})
    sectors = sorted({r.get("sector") for r in records if r.get("sector")})

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "params": {
            "strategy": "composite-v2",
            "sma": [strat.SMA_FAST, strat.SMA_MID, strat.SMA_SLOW],
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "rsi_period": RSI_PERIOD,
            "thresholds": {
                "strong_buy": strat.STRONG_BUY, "buy": strat.BUY,
                "sell": strat.SELL, "strong_sell": strat.STRONG_SELL,
            },
            "rvol_avg_window": RVOL_AVG_WINDOW,
            "rvol_lookback": RVOL_LOOKBACK,
            "rvol_threshold": RVOL_THRESHOLD,
            "timeframe": "1d",
        },
        "lists": lists,
        "sectors": sectors,
        "count": len(records),
        "stocks": records,
    }

    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    md.atomic_json(OUTPUT_PATH, payload)
    print(f"Wrote {len(records)} records to {os.path.relpath(OUTPUT_PATH)} "
          f"(sample={is_sample})")


def write_details(details, is_sample):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "count": len(details),
        "stocks": details,
    }
    os.makedirs(os.path.dirname(DETAILS_PATH), exist_ok=True)
    md.atomic_json(DETAILS_PATH, payload)
    print(f"Wrote {len(details)} detail entries to {os.path.relpath(DETAILS_PATH)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true",
                        help="write deterministic sample data (no network)")
    args = parser.parse_args()

    if args.sample:
        records, details = generate_sample()
        write_output(records, is_sample=True)
        write_details(details, is_sample=True)
        return 0

    try:
        records, details = fetch_live()
    except Exception as exc:  # noqa: BLE001
        print(f"Live fetch raised: {exc}", file=sys.stderr)
        records, details = [], {}

    if not records:
        print("Live fetch produced no data; retaining previous output.", file=sys.stderr)
        md.store().report('stocks')
        return 1

    write_output(records, is_sample=False)
    write_details(details, is_sample=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
