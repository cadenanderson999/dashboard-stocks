#!/usr/bin/env python3
"""Market-wide Relative Volume (RVOL) scanner.

Pulls the full NYSE + Nasdaq common-stock list from the (free, official) Nasdaq
Trader symbol directory, downloads ~3 months of daily volume per ticker via
yfinance, and keeps the **liquid** names whose **today's RVOL exceeds 2.0** --
i.e. stocks trading at more than twice their normal volume.

  today's RVOL = today's volume / trailing 50-day average volume

Output is written to ``data/rvol_scan.json`` for the Scanner page.

Usage:
    python scripts/generate_rvol_scan.py            # live scan
    python scripts/generate_rvol_scan.py --sample   # deterministic sample data
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_data import (  # noqa: E402
    rvol_stats,
    RVOL_AVG_WINDOW,
    build_universe,
    build_record,
    download_prices,
    fetch_fundamentals,
)

# Scan parameters.
SCAN_PERIOD = "3mo"          # enough history for a 50-day average + today
SCAN_CHUNK = 250             # tickers per batched download
RVOL_THRESHOLD = 2.0         # surface names trading above this RVOL
MIN_PRICE = 1.0              # liquidity filter: ignore sub-$1 names
MIN_AVG_VOL = 50_000         # liquidity filter: >= 50k avg daily shares
# Cap how many hits we enrich with full indicators/fundamentals, so the daily
# job stays bounded. We keep the highest-RVOL names.
MAX_RESULTS = 300

NASDAQ_DIR = "https://ftp.nasdaqtrader.com/SymbolDirectory/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dashboard-stocks/1.0"

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "rvol_scan.json"
)


# --------------------------------------------------------------------------- #
# Symbol universe (Nasdaq Trader directory)
# --------------------------------------------------------------------------- #
def _normalize(symbol):
    """Yahoo-compatible ticker, or None if it's not a plain common share."""
    s = symbol.strip().upper()
    # Skip preferreds / warrants / units / when-issued and other non-common.
    if not s or any(c in s for c in "$ ="):
        return None
    return s.replace(".", "-")  # BRK.B -> BRK-B


def fetch_symbols():
    """Return ``{symbol: {"name", "exchange"}}`` for NYSE + Nasdaq common stocks.

    Returns ``{}`` on failure so the caller can fall back to the site's universe.
    """
    import urllib.request

    def get(filename):
        req = urllib.request.Request(NASDAQ_DIR + filename, headers={"User-Agent": UA})
        return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")

    out = {}

    # nasdaqlisted.txt: Symbol|Name|MarketCategory|TestIssue|FinStatus|Lot|ETF|NextShares
    try:
        for line in get("nasdaqlisted.txt").splitlines()[1:]:
            if line.startswith("File Creation Time"):
                continue
            f = line.split("|")
            if len(f) < 8:
                continue
            sym, name, _mkt, test, _fin, _lot, etf = f[0], f[1], f[2], f[3], f[4], f[5], f[6]
            if test == "Y" or etf == "Y":
                continue
            s = _normalize(sym)
            if s:
                out[s] = {"name": name.strip(), "exchange": "Nasdaq"}
    except Exception as exc:  # noqa: BLE001
        print(f"nasdaqlisted fetch failed: {exc}", file=sys.stderr)

    # otherlisted.txt: ACTSymbol|Name|Exchange|CQS|ETF|Lot|TestIssue|NASDAQSymbol
    exch_name = {"N": "NYSE", "A": "NYSE American"}
    try:
        for line in get("otherlisted.txt").splitlines()[1:]:
            if line.startswith("File Creation Time"):
                continue
            f = line.split("|")
            if len(f) < 8:
                continue
            act, name, exch, _cqs, etf, _lot, test = f[0], f[1], f[2], f[3], f[4], f[5], f[6]
            if test == "Y" or etf == "Y" or exch not in exch_name:
                continue
            s = _normalize(act)
            if s and s not in out:
                out[s] = {"name": name.strip(), "exchange": exch_name[exch]}
    except Exception as exc:  # noqa: BLE001
        print(f"otherlisted fetch failed: {exc}", file=sys.stderr)

    return out


# --------------------------------------------------------------------------- #
# Scan
# --------------------------------------------------------------------------- #
def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def scan_candidates(symbols_meta):
    """First pass: find liquid tickers whose today's RVOL exceeds the threshold.

    Light + fast (3 months of volume only). Returns ``[{symbol, rvol}, ...]``.
    """
    import yfinance as yf

    symbols = list(symbols_meta.keys())
    print(f"Scanning {len(symbols)} symbols ({SCAN_PERIOD} daily volume)...")

    candidates = []
    for idx, chunk in enumerate(_chunks(symbols, SCAN_CHUNK)):
        print(f"  batch {idx + 1}: {chunk[0]}..{chunk[-1]}")
        try:
            data = yf.download(
                tickers=chunk, period=SCAN_PERIOD, interval="1d",
                group_by="ticker", auto_adjust=True, threads=True, progress=False,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"    batch failed: {exc}", file=sys.stderr)
            continue

        for sym in chunk:
            try:
                df = data if len(chunk) == 1 else data[sym]
                sub = df[["Close", "Volume"]].dropna()
                closes = sub["Close"].tolist()
                volumes = sub["Volume"].tolist()
            except (KeyError, TypeError):
                continue

            if len(volumes) < RVOL_AVG_WINDOW + 1:
                continue

            price = closes[-1]
            avg_vol = sum(volumes[-(RVOL_AVG_WINDOW + 1):-1]) / RVOL_AVG_WINDOW
            # Liquidity filters keep RVOL meaningful (microcaps are noisy).
            if price < MIN_PRICE or avg_vol < MIN_AVG_VOL:
                continue

            rvol = rvol_stats(volumes)["rvol_today"]
            if rvol is None or rvol < RVOL_THRESHOLD:
                continue

            candidates.append({"symbol": sym, "rvol": rvol})

        time.sleep(1)  # be gentle on Yahoo between batches

    candidates.sort(key=lambda c: c["rvol"], reverse=True)
    print(f"Found {len(candidates)} liquid names with RVOL > {RVOL_THRESHOLD}; "
          f"enriching top {min(len(candidates), MAX_RESULTS)}.")
    return candidates[:MAX_RESULTS]


def enrich(candidates, symbols_meta):
    """Second pass: compute the full signal set for the RVOL hits.

    Re-fetches longer history (for EMA200/RSI) plus fundamentals + sector, and
    builds records in the same shape as the Signals page via ``build_record``.
    """
    universe = build_universe(live=False)  # GICS sectors / list tags we already have
    syms = [c["symbol"] for c in candidates]
    if not syms:
        return []

    prices = download_prices(syms)            # default 2y history
    fundamentals = fetch_fundamentals(syms)   # pe, market_cap, sector

    records = []
    for sym in syms:
        closes, volumes = prices.get(sym, ([], []))
        if not closes:
            continue
        f = fundamentals.get(sym, {})
        u = universe.get(sym, {})
        meta = symbols_meta.get(sym, {})
        sector = u.get("sector") or f.get("sector") or "Other"
        rec = build_record(
            sym, closes, volumes,
            name=u.get("name") or meta.get("name") or sym,
            pe=f.get("pe"), market_cap=f.get("market_cap"),
            sector=sector, lists=u.get("lists"),
        )
        rec["exchange"] = meta.get("exchange") or ""
        records.append(rec)

    # Keep the strongest volume surges first.
    records.sort(key=lambda r: (r.get("rvol_today") or 0), reverse=True)
    print(f"Enriched {len(records)} records with full signal data.")
    return records


def generate_sample():
    """Deterministic, clearly-fake scanner data so the page renders offline.

    Produces full-shape records (same fields as the Signals page) with a forced
    volume spike on the last day so today's RVOL clears the threshold.
    """
    print("Generating SAMPLE scanner data (not real market prices).")
    rng = random.Random(7)
    universe = build_universe(live=False)
    syms = list(universe.keys())
    rng.shuffle(syms)

    records = []
    for sym in syms[:45]:
        base = rng.uniform(15, 500)
        base_vol = rng.uniform(1e5, 3e7)
        drift = rng.uniform(-0.0015, 0.0020)
        closes, volumes = [], []
        price = base
        for _ in range(260):
            price *= 1.0 + drift + rng.uniform(-0.02, 0.02)
            price = max(price, 1.0)
            closes.append(price)
            volumes.append(base_vol * rng.uniform(0.6, 1.4))
        # Force the most recent day to be a volume surge (RVOL ~2-8x).
        volumes[-1] = base_vol * rng.uniform(2.0, 8.0)

        u = universe[sym]
        pe = None if rng.random() < 0.15 else rng.uniform(8, 70)
        market_cap = closes[-1] * rng.uniform(1e7, 6e9)
        rec = build_record(
            sym, closes, volumes, name=u.get("name") or sym,
            pe=pe, market_cap=market_cap,
            sector=u.get("sector"), lists=u.get("lists"),
        )
        rec["exchange"] = rng.choice(["NYSE", "Nasdaq"])
        records.append(rec)

    records.sort(key=lambda r: (r.get("rvol_today") or 0), reverse=True)
    return records


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def write_output(records, is_sample):
    records.sort(key=lambda r: (r.get("rvol_today") or 0), reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "params": {
            "rvol_threshold": RVOL_THRESHOLD,
            "avg_window": RVOL_AVG_WINDOW,
            "min_price": MIN_PRICE,
            "min_avg_volume": MIN_AVG_VOL,
            "max_results": MAX_RESULTS,
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

    symbols_meta = fetch_symbols()
    if not symbols_meta:
        print("Symbol directory unavailable; falling back to site universe.",
              file=sys.stderr)
        symbols_meta = {
            s: {"name": m.get("name") or s, "exchange": ""}
            for s, m in build_universe(live=False).items()
        }

    try:
        candidates = scan_candidates(symbols_meta)
        records = enrich(candidates, symbols_meta)
    except Exception as exc:  # noqa: BLE001
        print(f"Scan failed: {exc}", file=sys.stderr)
        records = []

    if not records:
        print("No live results — writing sample so the page still renders.",
              file=sys.stderr)
        write_output(generate_sample(), is_sample=True)
        return 1

    write_output(records, is_sample=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
