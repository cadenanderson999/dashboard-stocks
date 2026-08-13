#!/usr/bin/env python3
"""Count (and preview) the daily watchlist candidates.

Selection criteria (tunable below):
  * weekly relative volume >= WEEKLY_RVOL_MIN
        weekly RVOL = mean(volume, last 5 days) / mean(volume, prior 50 days)
  * market cap > MIN_MARKET_CAP
  * price >= MIN_PRICE and a small average-volume floor (so the ratio is real)

Universe: all NYSE + Nasdaq common stocks (Nasdaq Trader directory).

This is a *diagnostic / counting* script -- it prints how many names pass each
filter so the criteria can be tuned. It does not change the site's data.

Usage:
    python scripts/select_watchlist.py
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_rvol_scan import fetch_symbols, _chunks  # noqa: E402

# --- Tunable criteria ----------------------------------------------------- #
WEEKLY_RVOL_MIN = 1.7          # this-week volume vs prior 50-day average
MIN_MARKET_CAP = 1_000_000_000  # $1B
MIN_PRICE = 1.0
MIN_AVG_VOL = 50_000          # basic liquidity floor

SCAN_PERIOD = "4mo"           # enough for 5-day + prior-50-day windows
SCAN_CHUNK = 200
MAX_WORKERS = 4               # gentle on Yahoo so market-cap lookups aren't throttled


def weekly_rvol(volumes):
    """mean(last 5 days) / mean(the 50 days before that). None if too short."""
    if len(volumes) < 55:
        return None
    recent = volumes[-5:]
    baseline = volumes[-55:-5]
    b = sum(baseline) / len(baseline)
    if b <= 0:
        return None
    return (sum(recent) / len(recent)) / b


def market_cap(sym):
    import yfinance as yf
    # fast_info is much lighter than .info; retry once on a transient/rate error.
    for attempt in range(2):
        try:
            fi = yf.Ticker(sym).fast_info
            mc = fi.get("market_cap") if hasattr(fi, "get") else getattr(fi, "market_cap", None)
            if mc:
                return float(mc)
            return None
        except Exception:  # noqa: BLE001
            if attempt == 0:
                time.sleep(1.5)
    return None


def main():
    import yfinance as yf

    symbols_meta = fetch_symbols()
    symbols = list(symbols_meta.keys())
    print(f"Universe: {len(symbols)} NYSE + Nasdaq common stocks.")

    # Stage 1: weekly RVOL + price/liquidity (from batched volume download).
    stage1 = []
    for i, chunk in enumerate(_chunks(symbols, SCAN_CHUNK)):
        try:
            data = yf.download(chunk, period=SCAN_PERIOD, interval="1d",
                               group_by="ticker", auto_adjust=True,
                               threads=True, progress=False)
        except Exception as exc:  # noqa: BLE001
            print(f"  batch {i + 1} failed: {exc}", file=sys.stderr)
            continue
        for sym in chunk:
            try:
                df = data if len(chunk) == 1 else data[sym]
                sub = df[["Close", "Volume"]].dropna()
                closes = sub["Close"].tolist()
                volumes = sub["Volume"].tolist()
            except (KeyError, TypeError):
                continue
            wr = weekly_rvol(volumes)
            if wr is None or not closes:
                continue
            price = closes[-1]
            avg_vol = sum(volumes[-55:-5]) / 50
            if price >= MIN_PRICE and avg_vol >= MIN_AVG_VOL and wr >= WEEKLY_RVOL_MIN:
                stage1.append((sym, wr, price))
        time.sleep(0.5)

    print(f"Passed weekly RVOL >= {WEEKLY_RVOL_MIN} + liquidity: {len(stage1)}")

    # Stage 2: market cap > $1B (only for stage-1 survivors).
    from concurrent.futures import ThreadPoolExecutor, as_completed
    final = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(market_cap, s): (s, wr, p) for (s, wr, p) in stage1}
        for fut in as_completed(futs):
            sym, wr, p = futs[fut]
            try:
                mc = fut.result()
            except Exception:  # noqa: BLE001
                mc = None
            if mc and mc > MIN_MARKET_CAP:
                final.append((sym, wr, p, mc))

    final.sort(key=lambda x: x[1], reverse=True)

    print("\n================ RESULT ================")
    print(f"Weekly RVOL >= {WEEKLY_RVOL_MIN}  AND  market cap > "
          f"${MIN_MARKET_CAP/1e9:.0f}B:  {len(final)} stocks")
    print("Top 25 by weekly RVOL:")
    for sym, wr, p, mc in final[:25]:
        print(f"  {sym:<6} rvol={wr:4.2f}x  ${p:>8.2f}  mktcap=${mc/1e9:6.1f}B")
    print("========================================")
    print("Tune WEEKLY_RVOL_MIN / MIN_MARKET_CAP at the top of this file "
          "to change the count.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
