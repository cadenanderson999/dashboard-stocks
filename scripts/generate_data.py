#!/usr/bin/env python3
"""Fetch daily prices for the stock universe and compute trading signals.

The universe is the union of the Robinhood Top 100 and the S&P 500. Each ticker
is tagged with the list(s) it belongs to and its GICS sector.

For every ticker this computes, on the **daily** timeframe:

  * EMA(50) and EMA(200)            -> trend (golden cross / death cross)
  * RSI(14)                         -> momentum (overbought / oversold)

It then combines the two into a single Buy/Sell rating and writes everything to
``data/stocks.json``, which the static front-end loads.

Data source: Yahoo Finance via the ``yfinance`` library (free, no API key).

Usage:
    python scripts/generate_data.py             # fetch live data
    python scripts/generate_data.py --sample    # write deterministic sample data
                                                # (used when no network access)

If a live fetch is attempted but fails for every ticker (e.g. the network is
blocked), the script automatically falls back to writing sample data so the
site still renders, and exits non-zero so CI surfaces the problem.
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

# Universe list labels.
RH_LIST = "Robinhood 100"
SP_LIST = "S&P 500"

# Browser-like UA so Wikipedia doesn't 403 the fallback fetch.
WIKI_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dashboard-stocks/1.0"
SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Indicator parameters.
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


# --------------------------------------------------------------------------- #
# Indicator math (pure Python so it works with or without pandas)
# --------------------------------------------------------------------------- #
def ema(values, period):
    """Exponential moving average. Returns a list aligned with ``values``."""
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for price in values[1:]:
        out.append(price * k + out[-1] * (1.0 - k))
    return out


def rsi(values, period=RSI_PERIOD):
    """Wilder's RSI. Returns the latest RSI value (0-100) or None."""
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    # Seed with a simple average of the first `period` changes...
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    # ...then smooth (Wilder) over the rest.
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


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
# Rating logic
# --------------------------------------------------------------------------- #
def make_rating(ema_fast, ema_slow, rsi_value):
    """Combine trend (EMA) and momentum (RSI) into a single rating.

    Returns a dict with: rating, score (-2..2), trend, momentum, reason.
    """
    if ema_fast is None or ema_slow is None or rsi_value is None:
        return {
            "rating": "No Data",
            "score": 0,
            "trend": "Unknown",
            "momentum": "Unknown",
            "reason": "Insufficient price history to compute indicators.",
        }

    bullish_trend = ema_fast > ema_slow
    trend = "Bullish" if bullish_trend else "Bearish"

    if rsi_value < 30:
        momentum = "Oversold"
    elif rsi_value > 70:
        momentum = "Overbought"
    else:
        momentum = "Neutral"

    # Decision table: trend (EMA50 vs EMA200) x momentum (RSI).
    if bullish_trend:
        if momentum == "Oversold":
            rating, score = "Strong Buy", 2
            reason = "Uptrend (EMA50 > EMA200) with an oversold RSI pullback."
        elif momentum == "Overbought":
            rating, score = "Hold", 0
            reason = "Uptrend, but RSI is overbought — may be extended."
        else:
            rating, score = "Buy", 1
            reason = "Uptrend (EMA50 > EMA200) with healthy momentum."
    else:
        if momentum == "Overbought":
            rating, score = "Strong Sell", -2
            reason = "Downtrend (EMA50 < EMA200) with an overbought RSI bounce."
        elif momentum == "Oversold":
            rating, score = "Hold", 0
            reason = "Downtrend, but RSI is oversold — possible relief bounce."
        else:
            rating, score = "Sell", -1
            reason = "Downtrend (EMA50 < EMA200) with weak momentum."

    return {
        "rating": rating,
        "score": score,
        "trend": trend,
        "momentum": momentum,
        "reason": reason,
    }


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

    # Guarantee every ticker has a sector label.
    for u in universe.values():
        u["sector"] = u["sector"] or "Other"

    print(f"Universe: {len(universe)} unique tickers "
          f"({sum(RH_LIST in u['lists'] for u in universe.values())} Robinhood, "
          f"{sum(SP_LIST in u['lists'] for u in universe.values())} S&P 500).")
    return universe


def fetch_fundamentals(symbols):
    """Best-effort P/E + market cap per ticker via yfinance (threaded).

    Returns ``{symbol: {"pe": float|None, "market_cap": int|None}}``. Any ticker
    that fails simply gets ``None`` values -- fundamentals never block the
    technical ratings, which come from the (more reliable) price download.
    """
    import yfinance as yf  # lazy import
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"Fetching fundamentals (P/E, market cap) for {len(symbols)} tickers...")

    def one(sym):
        try:
            info = yf.Ticker(sym).info or {}
            pe = info.get("trailingPE")
            mcap = info.get("marketCap")
            return sym, {"pe": pe, "market_cap": mcap}
        except Exception:  # noqa: BLE001
            return sym, {"pe": None, "market_cap": None}

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(one, s) for s in symbols]
        for fut in as_completed(futures):
            sym, d = fut.result()
            out[sym] = d
    return out


def build_record(symbol, closes, volumes=None, name=None, pe=None,
                 market_cap=None, sector=None, lists=None):
    """Compute indicators + rating for one ticker from its price/volume series."""
    ema_fast_series = ema(closes, EMA_FAST) if len(closes) >= EMA_FAST else None
    ema_slow_series = ema(closes, EMA_SLOW) if len(closes) >= EMA_SLOW else None
    ema_fast = ema_fast_series[-1] if ema_fast_series else None
    ema_slow = ema_slow_series[-1] if ema_slow_series else None
    rsi_value = rsi(closes)
    rv = rvol_stats(volumes or [])

    price = closes[-1] if closes else None
    prev = closes[-2] if len(closes) >= 2 else None
    change_pct = ((price - prev) / prev * 100.0) if (price and prev) else None

    rating = make_rating(ema_fast, ema_slow, rsi_value)

    def r(x, n=2):
        return round(x, n) if isinstance(x, (int, float)) and not math.isnan(x) else None

    return {
        "symbol": symbol,
        "name": name or symbol,
        "price": r(price),
        "change_pct": r(change_pct),
        "ema50": r(ema_fast),
        "ema200": r(ema_slow),
        "rsi": r(rsi_value, 1),
        "rvol_mean": r(rv["rvol_mean"]),
        "rvol_high_days": rv["rvol_high_days"],
        "rvol_today": r(rv["rvol_today"]),
        "pe": r(pe, 1),
        "market_cap": int(market_cap) if isinstance(market_cap, (int, float)) else None,
        "sector": sector or "Other",
        "lists": sorted(lists) if lists else [],
        **rating,
    }


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #
def download_prices(symbols, chunk=100):
    """Download daily close+volume for many symbols, chunked to be API-friendly.

    Returns ``{symbol: (closes, volumes)}`` (empty tuples for failures).
    """
    import yfinance as yf  # imported lazily so --sample works without it

    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        print(f"  downloading {i + 1}-{i + len(part)} of {len(symbols)}...")
        data = yf.download(
            tickers=part,
            period=LOOKBACK,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        for sym in part:
            try:
                df = data if len(part) == 1 else data[sym]
                # Keep close + volume aligned by dropping rows missing either.
                sub = df[["Close", "Volume"]].dropna()
                out[sym] = (sub["Close"].tolist(), sub["Volume"].tolist())
            except (KeyError, TypeError):
                out[sym] = ([], [])
    return out


def fetch_live():
    """Fetch real data via yfinance. Returns list of records (may be empty)."""
    universe = build_universe()
    symbols = list(universe.keys())
    print(f"Downloading {len(symbols)} tickers from Yahoo Finance ({LOOKBACK})...")

    prices = download_prices(symbols)
    fundamentals = fetch_fundamentals(symbols)

    records = []
    skipped = 0
    for symbol in symbols:
        closes, volumes = prices.get(symbol, ([], []))
        if not closes:
            skipped += 1
            continue

        meta = universe[symbol]
        f = fundamentals.get(symbol, {})
        records.append(build_record(
            symbol, closes, volumes,
            name=meta.get("name"),
            pe=f.get("pe"), market_cap=f.get("market_cap"),
            sector=meta.get("sector"), lists=meta.get("lists"),
        ))

    print(f"Built {len(records)} records ({skipped} tickers had no usable data).")
    return records


def generate_sample():
    """Deterministic, clearly-fake data so the UI renders without network."""
    print("Generating SAMPLE data (not real market prices).")
    rng = random.Random(42)
    # Use the static universe so sample data mirrors the real shape/size.
    universe = build_universe(live=False)
    records = []
    for symbol, meta in universe.items():
        base = rng.uniform(15, 500)
        base_vol = rng.uniform(1e6, 5e7)
        # Build a synthetic but plausible close + volume series.
        drift = rng.uniform(-0.0015, 0.0020)
        closes, volumes = [], []
        price = base
        for _ in range(260):
            price *= 1.0 + drift + rng.uniform(-0.02, 0.02)
            price = max(price, 1.0)
            closes.append(price)
            # Normal-ish volume with occasional surges.
            vol = base_vol * rng.uniform(0.6, 1.4)
            if rng.random() < 0.08:
                vol *= rng.uniform(2.0, 4.0)
            volumes.append(vol)
        # Plausible fundamentals: most have a P/E, some (no earnings) don't.
        pe = None if rng.random() < 0.15 else rng.uniform(8, 70)
        market_cap = closes[-1] * rng.uniform(1e7, 6e9)
        records.append(build_record(
            symbol, closes, volumes, name=meta.get("name"),
            pe=pe, market_cap=market_cap,
            sector=meta.get("sector"), lists=meta.get("lists"),
        ))
    return records


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
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "rsi_period": RSI_PERIOD,
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
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(records)} records to {os.path.relpath(OUTPUT_PATH)} "
          f"(sample={is_sample})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true",
                        help="write deterministic sample data (no network)")
    args = parser.parse_args()

    if args.sample:
        write_output(generate_sample(), is_sample=True)
        return 0

    try:
        records = fetch_live()
    except Exception as exc:  # noqa: BLE001
        print(f"Live fetch raised: {exc}", file=sys.stderr)
        records = []

    if not records:
        print("Live fetch produced no data — falling back to sample.",
              file=sys.stderr)
        write_output(generate_sample(), is_sample=True)
        return 1

    write_output(records, is_sample=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
