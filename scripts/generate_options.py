#!/usr/bin/env python3
"""Options open-interest summary per stock (near-term expirations).

For the Robinhood 100 plus the current Volume Screener hits, this pulls option
chains from Yahoo Finance (via yfinance) for expirations within ~60 days and
computes, per ticker:

  * total call open interest, total put open interest, put/call OI ratio
  * the highest-open-interest call and put contracts

Output: ``data/options.json`` (keyed by symbol), read by the detail page.

Options are the heaviest pull (one request per expiration), so this is scoped to
the most-traded names and capped in expiration depth to stay bounded.

Usage:
    python scripts/generate_options.py            # live
    python scripts/generate_options.py --sample   # deterministic sample data
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tickers import ROBINHOOD_TOP_100  # noqa: E402

OPTIONS_MAX_DAYS = 60          # only expirations within this horizon
OPTIONS_MAX_EXPIRATIONS = 6    # cap expirations fetched per ticker
TOP_CONTRACTS = 5              # highest-OI calls / puts to keep
MAX_WORKERS = 6

OUTPUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "options.json"
)
SCAN_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "data", "rvol_scan.json"
)


def target_symbols():
    """Robinhood 100 ∪ current Volume Screener hits."""
    syms = set(ROBINHOOD_TOP_100)
    try:
        with open(SCAN_PATH) as f:
            scan = json.load(f)
        for s in scan.get("stocks", []):
            if s.get("symbol"):
                syms.add(s["symbol"])
    except (OSError, ValueError):
        print("No rvol_scan.json found; using Robinhood 100 only.", file=sys.stderr)
    return sorted(syms)


def _num(v):
    try:
        f = float(v)
        return f if f == f else None  # filter NaN
    except (TypeError, ValueError):
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else 0


def fetch_one(sym):
    """Aggregate near-term option OI for one ticker. Returns dict or None."""
    import yfinance as yf

    tk = yf.Ticker(sym)
    try:
        expirations = list(tk.options or ())
    except Exception:  # noqa: BLE001
        return None
    if not expirations:
        return None

    today = date.today()
    chosen = []
    for e in expirations:
        try:
            d = datetime.strptime(e, "%Y-%m-%d").date()
        except ValueError:
            continue
        if today <= d and (d - today).days <= OPTIONS_MAX_DAYS:
            chosen.append(e)
    if not chosen:
        chosen = expirations[:1]  # fall back to the nearest expiration
    chosen = chosen[:OPTIONS_MAX_EXPIRATIONS]

    call_oi = put_oi = 0
    calls, puts = [], []
    for e in chosen:
        try:
            chain = tk.option_chain(e)
        except Exception:  # noqa: BLE001
            continue
        for kind, df, bucket in (("call", chain.calls, calls), ("put", chain.puts, puts)):
            if df is None or getattr(df, "empty", True):
                continue
            for _, row in df.iterrows():
                oi = _int(row.get("openInterest"))
                if kind == "call":
                    call_oi += oi
                else:
                    put_oi += oi
                if oi > 0:
                    bucket.append({
                        "exp": e,
                        "strike": _num(row.get("strike")),
                        "oi": oi,
                        "volume": _int(row.get("volume")),
                        "last": _num(row.get("lastPrice")),
                    })

    if call_oi == 0 and put_oi == 0:
        return None

    top = lambda b: sorted(b, key=lambda c: c["oi"], reverse=True)[:TOP_CONTRACTS]
    return {
        "call_oi": call_oi,
        "put_oi": put_oi,
        "put_call_ratio": round(put_oi / call_oi, 2) if call_oi else None,
        "horizon_days": OPTIONS_MAX_DAYS,
        "expirations_used": len(chosen),
        "top_calls": top(calls),
        "top_puts": top(puts),
    }


def fetch_live():
    from concurrent.futures import ThreadPoolExecutor, as_completed

    symbols = target_symbols()
    print(f"Fetching options for {len(symbols)} tickers "
          f"(<= {OPTIONS_MAX_DAYS}d, <= {OPTIONS_MAX_EXPIRATIONS} expirations)...")

    out = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_one, s): s for s in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                data = fut.result()
            except Exception:  # noqa: BLE001
                data = None
            if data:
                out[sym] = data
    print(f"Got options data for {len(out)} tickers.")
    return out


def generate_sample():
    print("Generating SAMPLE options data (not real).")
    rng = random.Random(11)
    out = {}
    exps = ["2026-07-17", "2026-07-24", "2026-08-21"]
    for sym in ROBINHOOD_TOP_100:
        call_oi = rng.randint(50_000, 3_000_000)
        put_oi = int(call_oi * rng.uniform(0.4, 1.6))
        base = rng.uniform(20, 400)

        def contracts(kind):
            rows = []
            for _ in range(TOP_CONTRACTS):
                rows.append({
                    "exp": rng.choice(exps),
                    "strike": round(base * rng.uniform(0.7, 1.3), 1),
                    "oi": rng.randint(5_000, 250_000),
                    "volume": rng.randint(500, 60_000),
                    "last": round(rng.uniform(0.2, 30), 2),
                })
            return sorted(rows, key=lambda c: c["oi"], reverse=True)

        out[sym] = {
            "call_oi": call_oi,
            "put_oi": put_oi,
            "put_call_ratio": round(put_oi / call_oi, 2),
            "horizon_days": OPTIONS_MAX_DAYS,
            "expirations_used": len(exps),
            "top_calls": contracts("call"),
            "top_puts": contracts("put"),
        }
    return out


def write_output(stocks, is_sample):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "params": {
            "max_days": OPTIONS_MAX_DAYS,
            "max_expirations": OPTIONS_MAX_EXPIRATIONS,
            "top_contracts": TOP_CONTRACTS,
        },
        "count": len(stocks),
        "stocks": stocks,
    }
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote options for {len(stocks)} tickers to {os.path.relpath(OUTPUT_PATH)} "
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
        stocks = fetch_live()
    except Exception as exc:  # noqa: BLE001
        print(f"Options fetch failed: {exc}", file=sys.stderr)
        stocks = {}

    if not stocks:
        print("No live options data — writing sample.", file=sys.stderr)
        write_output(generate_sample(), is_sample=True)
        return 1

    write_output(stocks, is_sample=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
