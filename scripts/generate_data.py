#!/usr/bin/env python3
"""Fetch daily prices for the Robinhood Top 100 and compute trading signals.

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
from tickers import ROBINHOOD_TOP_100  # noqa: E402

# Indicator parameters.
EMA_FAST = 50
EMA_SLOW = 200
RSI_PERIOD = 14
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


def build_record(symbol, closes, name=None):
    """Compute indicators + rating for one ticker from its close series."""
    ema_fast_series = ema(closes, EMA_FAST) if len(closes) >= EMA_FAST else None
    ema_slow_series = ema(closes, EMA_SLOW) if len(closes) >= EMA_SLOW else None
    ema_fast = ema_fast_series[-1] if ema_fast_series else None
    ema_slow = ema_slow_series[-1] if ema_slow_series else None
    rsi_value = rsi(closes)

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
        **rating,
    }


# --------------------------------------------------------------------------- #
# Data acquisition
# --------------------------------------------------------------------------- #
def fetch_live():
    """Fetch real data via yfinance. Returns list of records (may be empty)."""
    import yfinance as yf  # imported lazily so --sample works without it

    records = []
    symbols = ROBINHOOD_TOP_100
    print(f"Downloading {len(symbols)} tickers from Yahoo Finance ({LOOKBACK})...")

    # Batch download is far faster and gentler on the API than one-at-a-time.
    data = yf.download(
        tickers=symbols,
        period=LOOKBACK,
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        threads=True,
        progress=False,
    )

    for symbol in symbols:
        try:
            if len(symbols) == 1:
                closes = data["Close"].dropna().tolist()
            else:
                closes = data[symbol]["Close"].dropna().tolist()
        except (KeyError, TypeError):
            closes = []

        if len(closes) < EMA_SLOW:
            print(f"  ! {symbol}: only {len(closes)} closes — limited indicators")

        if not closes:
            print(f"  x {symbol}: no data, skipping")
            continue

        records.append(build_record(symbol, closes))

    return records


def generate_sample():
    """Deterministic, clearly-fake data so the UI renders without network."""
    print("Generating SAMPLE data (not real market prices).")
    rng = random.Random(42)
    records = []
    for symbol in ROBINHOOD_TOP_100:
        base = rng.uniform(15, 500)
        # Build a synthetic but plausible close series with a drift + noise.
        drift = rng.uniform(-0.0015, 0.0020)
        closes = []
        price = base
        for _ in range(260):
            price *= 1.0 + drift + rng.uniform(-0.02, 0.02)
            price = max(price, 1.0)
            closes.append(price)
        records.append(build_record(symbol, closes))
    return records


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def write_output(records, is_sample):
    # Sort: strongest buys first, then by RSI.
    records.sort(key=lambda r: (-(r.get("score") or 0), r.get("rsi") or 50))

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "params": {
            "ema_fast": EMA_FAST,
            "ema_slow": EMA_SLOW,
            "rsi_period": RSI_PERIOD,
            "timeframe": "1d",
        },
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
