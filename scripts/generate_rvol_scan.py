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
from generate_data import rvol_stats, RVOL_AVG_WINDOW, build_universe  # noqa: E402

# Scan parameters.
SCAN_PERIOD = "3mo"          # enough history for a 50-day average + today
SCAN_CHUNK = 250             # tickers per batched download
RVOL_THRESHOLD = 2.0         # surface names trading above this RVOL
MIN_PRICE = 1.0              # liquidity filter: ignore sub-$1 names
MIN_AVG_VOL = 50_000         # liquidity filter: >= 50k avg daily shares

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


def scan(symbols_meta):
    """Download volume, compute RVOL, keep liquid names above the threshold."""
    import yfinance as yf

    symbols = list(symbols_meta.keys())
    print(f"Scanning {len(symbols)} symbols ({SCAN_PERIOD} daily volume)...")

    records = []
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
            prev = closes[-2] if len(closes) >= 2 else price
            avg_vol = sum(volumes[-(RVOL_AVG_WINDOW + 1):-1]) / RVOL_AVG_WINDOW

            # Liquidity filters keep RVOL meaningful (microcaps are noisy).
            if price < MIN_PRICE or avg_vol < MIN_AVG_VOL:
                continue

            rvol = rvol_stats(volumes)["rvol_today"]
            if rvol is None or rvol < RVOL_THRESHOLD:
                continue

            meta = symbols_meta[sym]
            records.append({
                "symbol": sym,
                "name": meta.get("name") or sym,
                "exchange": meta.get("exchange") or "",
                "price": round(price, 2),
                "change_pct": round((price - prev) / prev * 100.0, 2) if prev else None,
                "volume": int(volumes[-1]),
                "avg_volume": int(avg_vol),
                "rvol": round(rvol, 2),
            })

        time.sleep(1)  # be gentle on Yahoo between batches

    print(f"Found {len(records)} liquid names with RVOL > {RVOL_THRESHOLD}.")
    return records


def generate_sample():
    """Deterministic, clearly-fake scanner data so the page renders offline."""
    print("Generating SAMPLE scanner data (not real market prices).")
    rng = random.Random(7)
    universe = build_universe(live=False)
    syms = list(universe.keys())
    rng.shuffle(syms)
    records = []
    for sym in syms[:45]:
        avg_vol = rng.uniform(1e6, 5e7)
        rvol = round(rng.uniform(2.0, 8.5), 2)
        price = round(rng.uniform(5, 400), 2)
        records.append({
            "symbol": sym,
            "name": universe[sym].get("name") or sym,
            "exchange": rng.choice(["NYSE", "Nasdaq"]),
            "price": price,
            "change_pct": round(rng.uniform(-12, 18), 2),
            "volume": int(avg_vol * rvol),
            "avg_volume": int(avg_vol),
            "rvol": rvol,
        })
    return records


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def write_output(records, is_sample):
    records.sort(key=lambda r: r["rvol"], reverse=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "params": {
            "rvol_threshold": RVOL_THRESHOLD,
            "avg_window": RVOL_AVG_WINDOW,
            "min_price": MIN_PRICE,
            "min_avg_volume": MIN_AVG_VOL,
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
        records = scan(symbols_meta)
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
