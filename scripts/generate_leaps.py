#!/usr/bin/env python3
"""LEAP call screen: which stocks suit a long-dated call, and which contracts.

Runs *after* ``generate_data.py`` (it reads ``data/stocks.json`` and
``data/details.json``). Every ticker gets a LEAP score (0-100, see
``strategies.leap_score``) built from:

  * trend        -- rising 200-day SMA + Minervini Trend Template
  * momentum     -- relative-strength rank
  * fundamentals -- analyst upside, revenue growth, profitability
  * volatility   -- lower realised vol = cheaper, steadier LEAPs
  * liquidity    -- market cap (deep, tight LEAP markets)
  * timing       -- unextended entry (pullback preferred)

Names scoring >= 75 that pass the hard gates (rising 200 SMA, Trend Template
>= 7/8, RS >= 70, Buy-or-better rating, cap >= $2B, vol <= 70%) are eligible
for "LEAP Buy"; >= 60 in an uptrend for "Watch". The list is then *ranked*
and capped (``LEAP_BUY_MAX`` / ``WATCH_MAX``) so it stays a shortlist even in
a broad bull market. For the shortlist, the option chain is fetched from
Yahoo and up to three calls per LEAP expiry (>= 365 days out) are suggested
by Black-Scholes delta:

  * Stock replacement  delta ~0.80  (deep ITM, least decay)
  * Balanced           delta ~0.65
  * Aggressive         delta ~0.50  (at-the-money, max leverage)

with bid/ask, IV, open interest, breakeven, leverage and time-value cost.

Usage:
    python scripts/generate_leaps.py            # live option chains
    python scripts/generate_leaps.py --sample   # synthetic chains (offline)
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategies as strat  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
STOCKS_PATH = os.path.join(DATA_DIR, "stocks.json")
DETAILS_PATH = os.path.join(DATA_DIR, "details.json")
OUTPUT_PATH = os.path.join(DATA_DIR, "leaps.json")

# Screen / chain parameters.
LEAP_MIN_DTE = 365        # a LEAP is an option with >= 1 year to expiry
MAX_EXPIRIES = 2          # nearest LEAP expiry + the farthest one
LEAP_BUY_MAX = 40         # at most this many "LEAP Buy" names (best scores)
WATCH_MAX = 60            # at most this many "Watch" names
MAX_CHAINS = LEAP_BUY_MAX + WATCH_MAX   # every listed name gets its chain
RISK_FREE = 0.04          # annual risk-free rate used for delta
MIN_OI = 25               # open-interest floor for "liquid" contracts
MAX_SPREAD = 0.20         # bid/ask spread as a fraction of mid
MAX_WORKERS = 3           # gentle on Yahoo
RETRY_DELAY = 3.0         # seconds before retrying a rate-limited chain fetch
CANDIDATE_FIELDS = [
    "symbol", "name", "sector", "lists", "price", "change_pct", "market_cap",
    "pe", "rating", "score", "rs_rank", "tt_pass", "hv60", "sma200",
    "sma200_rising", "timing", "trend", "setups", "pct_off_high", "ret_6m",
    "mom_12_1", "rsi",
]


def load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Screen
# --------------------------------------------------------------------------- #
def screen(stocks, details):
    """Score every stock; return the sorted LEAP Buy / Watch candidates."""
    out = []
    for s in stocks:
        d = (details or {}).get(s["symbol"]) or {}
        ls = strat.leap_score(s, d)
        if not ls or not ls["leap_rating"]:
            continue
        cand = {k: s.get(k) for k in CANDIDATE_FIELDS}
        cand.update(ls)
        cand["next_earnings"] = d.get("next_earnings")
        cand["dividend_yield"] = (
            round(d["dividend_rate"] / s["price"] * 100.0, 2)
            if d.get("dividend_rate") and s.get("price") else 0.0)
        cand["analyst_target"] = (d.get("analyst") or {}).get("target_mean")
        cand["contracts"] = []
        cand["expiries"] = []
        cand["chain_status"] = "not_fetched"
        out.append(cand)
    out.sort(key=lambda c: (c["leap_rating"] != "LEAP Buy", -c["leap_score"]))
    # Rank + cap: the best LEAP_BUY_MAX eligible names keep "LEAP Buy", the
    # rest of the eligible names become "Watch", and Watch is capped too.
    total_eligible = sum(1 for c in out if c["leap_rating"] == "LEAP Buy")
    for i, c in enumerate(out):
        if c["leap_rating"] == "LEAP Buy" and i >= LEAP_BUY_MAX:
            c["leap_rating"] = "Watch"
    out = out[:LEAP_BUY_MAX + WATCH_MAX]
    print(f"  {total_eligible} names met the LEAP Buy gates; keeping the top "
          f"{min(total_eligible, LEAP_BUY_MAX)} as LEAP Buy and {len(out) - min(total_eligible, LEAP_BUY_MAX)} as Watch.")
    return out


# --------------------------------------------------------------------------- #
# Option chains
# --------------------------------------------------------------------------- #
def _dte(expiry, today):
    try:
        exp = datetime.strptime(expiry, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (exp - today).days


def fetch_chain(symbol, today):
    """Fetch LEAP call chains for one ticker via yfinance.

    Returns ``(status, chains)`` where chains is
    ``[{"expiry", "dte", "calls": [...]}, ...]``.
    """
    import yfinance as yf

    expiries = None
    for attempt in range(2):
        try:
            tk = yf.Ticker(symbol)
            expiries = list(tk.options or [])
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 0 and "rate" in str(exc).lower():
                time.sleep(RETRY_DELAY)
                continue
            return "error", []
    if expiries is None:
        return "error", []
    leaps = [(e, _dte(e, today)) for e in expiries]
    leaps = [(e, d) for e, d in leaps if d is not None and d >= LEAP_MIN_DTE]
    if not leaps:
        return "no_leaps", []
    leaps.sort(key=lambda x: x[1])
    chosen = [leaps[0]] + ([leaps[-1]] if len(leaps) > 1 and MAX_EXPIRIES > 1 else [])

    chains = []
    for expiry, dte in chosen:
        calls = None
        for attempt in range(2):
            try:
                calls = tk.option_chain(expiry).calls
                break
            except Exception as exc:  # noqa: BLE001
                if attempt == 0 and "rate" in str(exc).lower():
                    time.sleep(RETRY_DELAY)
                    continue
        if calls is None:
            continue
        rows = []
        for _, c in calls.iterrows():
            def num(key):
                v = c.get(key)
                return float(v) if v is not None and v == v else None
            rows.append({
                "strike": num("strike"), "bid": num("bid"), "ask": num("ask"),
                "last": num("lastPrice"), "iv": num("impliedVolatility"),
                "oi": int(num("openInterest") or 0), "volume": int(num("volume") or 0),
                "contract": str(c.get("contractSymbol") or ""),
            })
        if rows:
            chains.append({"expiry": expiry, "dte": dte, "calls": rows})
        time.sleep(0.3)
    return ("ok" if chains else "error"), chains


def synthetic_chain(cand, today, rng):
    """Black-Scholes-priced fake chains so the page renders offline."""
    price = cand["price"]
    hv = cand.get("hv60") or 30.0
    base_iv = max(0.15, hv / 100.0 * rng.uniform(0.9, 1.3))
    year = today.year
    chains = []
    for yrs in (1, 2, 3):
        if len(chains) >= MAX_EXPIRIES:
            break
        # Third Friday of January, `yrs` years out (the classic LEAP cycle).
        jan = datetime(year + yrs, 1, 1).date()
        third_fri = jan.replace(day=15 + (4 - jan.weekday()) % 7)
        dte = (third_fri - today).days
        if dte < LEAP_MIN_DTE:
            continue
        T = dte / 365.0
        rows = []
        step = _strike_step(price)
        k = max(step, math.floor(price * 0.5 / step) * step)
        while k <= price * 1.6:
            iv = base_iv * (1.0 + 0.15 * max(0.0, (price - k) / price))  # skew
            mid, _ = strat.bs_call(price, k, T, iv, RISK_FREE, cand.get("dividend_yield", 0) / 100.0)
            spread = max(0.05, mid * rng.uniform(0.03, 0.12))
            rows.append({
                "strike": round(k, 2), "bid": round(max(mid - spread / 2, 0.01), 2),
                "ask": round(mid + spread / 2, 2), "last": round(mid, 2), "iv": round(iv, 4),
                "oi": int(rng.uniform(20, 4000) * (2 if abs(k - price) / price < 0.15 else 1)),
                "volume": int(rng.uniform(0, 400)),
                "contract": f"{cand['symbol']}{third_fri.strftime('%y%m%d')}C{int(k * 1000):08d}",
            })
            k += step
        chains.append({"expiry": third_fri.isoformat(), "dte": dte, "calls": rows})
    return chains


def _strike_step(price):
    return 1.0 if price < 25 else 2.5 if price < 100 else 5.0 if price < 250 else 10.0


def attach_contracts(cand, chains, status):
    cand["chain_status"] = status
    cand["expiries"] = [{"expiry": c["expiry"], "dte": c["dte"]} for c in chains]
    if not chains:
        return
    cand["contracts"] = strat.pick_leap_contracts(
        cand["price"], chains, hv_pct=cand.get("hv60"), r=RISK_FREE,
        div_yield=(cand.get("dividend_yield") or 0) / 100.0,
        min_oi=MIN_OI, max_spread=MAX_SPREAD,
    )
    # ATM implied vol from the nearest LEAP expiry, vs realised vol.
    near = chains[0]
    atm = min(near["calls"], key=lambda c: abs((c.get("strike") or 0) - cand["price"]))
    iv = atm.get("iv")
    if iv and 0.02 < iv < 5:
        cand["iv_atm"] = round(iv * 100.0, 1)
        if cand.get("hv60"):
            cand["iv_hv_ratio"] = round(iv * 100.0 / cand["hv60"], 2)


def fill_contracts(candidates, sample, today):
    rng = random.Random(11)
    if sample:
        for c in candidates:
            attach_contracts(c, synthetic_chain(c, today, rng), "ok")
        return True

    from concurrent.futures import ThreadPoolExecutor, as_completed
    targets = candidates[:MAX_CHAINS]
    print(f"Fetching LEAP option chains for {len(targets)} candidates...")
    ok = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_chain, c["symbol"], today): c for c in targets}
        for fut in as_completed(futs):
            c = futs[fut]
            try:
                status, chains = fut.result()
            except Exception:  # noqa: BLE001
                status, chains = "error", []
            attach_contracts(c, chains, status)
            ok += status == "ok"
    print(f"  {ok}/{len(targets)} chains fetched.")
    return ok > 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def write_output(candidates, is_sample, chains_ok):
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "is_sample": is_sample,
        "chains_available": chains_ok,
        "params": {
            "leap_min_dte": LEAP_MIN_DTE, "risk_free": RISK_FREE,
            "thresholds": {"leap_buy": strat.LEAP_BUY, "watch": strat.LEAP_WATCH},
            "roles": [{"role": r, "target_delta": d, "why": w} for r, d, w in strat.LEAP_ROLES],
            "min_open_interest": MIN_OI, "max_spread_pct": MAX_SPREAD * 100,
        },
        "count": len(candidates),
        "buy_count": sum(1 for c in candidates if c["leap_rating"] == "LEAP Buy"),
        "candidates": candidates,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(candidates)} LEAP candidates "
          f"({payload['buy_count']} LEAP Buy) to {os.path.relpath(OUTPUT_PATH)} "
          f"(sample={is_sample})")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", action="store_true",
                    help="synthetic option chains (no network)")
    args = ap.parse_args()

    stocks_doc = load_json(STOCKS_PATH)
    if not stocks_doc:
        print("data/stocks.json missing — run generate_data.py first.", file=sys.stderr)
        return 1
    details_doc = load_json(DETAILS_PATH) or {}
    is_sample = bool(args.sample or stocks_doc.get("is_sample"))
    if is_sample and not args.sample:
        print("stocks.json is sample data — generating synthetic chains too.")

    candidates = screen(stocks_doc.get("stocks", []), details_doc.get("stocks", {}))
    print(f"Screened {len(stocks_doc.get('stocks', []))} stocks -> "
          f"{len(candidates)} LEAP candidates.")

    today = datetime.now(timezone.utc).date()
    chains_ok = fill_contracts(candidates, is_sample, today)
    write_output(candidates, is_sample, chains_ok)
    return 0 if (chains_ok or not candidates) else 1


if __name__ == "__main__":
    raise SystemExit(main())
