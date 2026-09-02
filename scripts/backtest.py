#!/usr/bin/env python3
"""Backtest the rating strategies against each other.

Walks forward through history one month (21 trading days) at a time. On each
rebalance date every ticker is scored with the same code the site uses
(``strategies.evaluate`` for the composite rating, ``legacy_rating`` for the
old EMA-50/200 + RSI table, and ``component_signals`` for each single strategy)
using only data up to that date, and the *forward* 1-month / 3-month returns are
recorded. That answers two questions per strategy:

  1. Signal quality -- do names rated Buy go on to beat names rated Sell and the
     equal-weight universe?
  2. Portfolio result -- what happens if you hold an equal-weight basket of
     every Buy-or-better name, rebalanced monthly?

Data sources (pick one):
    --csv PATH      long-format CSV with columns date,open,high,low,close,
                    volume,Name (e.g. the public "all_stocks_5yr" S&P 500 set)
    --live          download the site's universe from Yahoo Finance (--years)
    --sample        synthetic random walks (only checks the code runs)

Usage:
    python scripts/backtest.py --csv all_stocks_5yr.csv
    python scripts/backtest.py --live --years 6 --limit 200
    python scripts/backtest.py --csv data.csv --out backtest.json --step 21
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import strategies as S  # noqa: E402

WARMUP = 265            # bars needed before the first score (12m RS + slack)
HORIZONS = {"1m": 21, "3m": 63}
RATINGS = ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"]


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_csv(path):
    """Long-format CSV -> {symbol: {"dates", "close", "high", "low", "volume"}}."""
    raw = defaultdict(list)
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                raw[row["Name"]].append((
                    row["date"], float(row["close"]), float(row["high"]),
                    float(row["low"]), float(row["volume"])))
            except (ValueError, KeyError):
                continue
    out = {}
    for sym, rows in raw.items():
        rows.sort()
        out[sym] = {
            "dates": [r[0] for r in rows], "close": [r[1] for r in rows],
            "high": [r[2] for r in rows], "low": [r[3] for r in rows],
            "volume": [r[4] for r in rows],
        }
    return out


def load_live(years, limit):
    from generate_data import build_universe, download_prices  # noqa: E402
    syms = list(build_universe().keys())
    if limit:
        syms = syms[:limit]
    prices = download_prices(syms, period=f"{years}y")
    out = {}
    for sym, p in prices.items():
        if p.get("close"):
            out[sym] = {"dates": p["dates"], "close": p["close"], "high": p["high"],
                        "low": p["low"], "volume": p["volume"]}
    return out


def load_sample(n_syms=120, n_days=1300, seed=3):
    rng = random.Random(seed)
    out = {}
    dates = [f"D{i:05d}" for i in range(n_days)]
    for k in range(n_syms):
        drift = rng.uniform(-0.0006, 0.0012)
        p, closes, highs, lows, vols = rng.uniform(20, 300), [], [], [], []
        for _ in range(n_days):
            p = max(1.0, p * (1 + drift + rng.gauss(0, 0.017)))
            closes.append(p)
            highs.append(p * (1 + abs(rng.gauss(0, 0.008))))
            lows.append(p * (1 - abs(rng.gauss(0, 0.008))))
            vols.append(rng.uniform(5e5, 5e6) * (3 if rng.random() < 0.05 else 1))
        out[f"S{k:03d}"] = {"dates": dates, "close": closes, "high": highs,
                            "low": lows, "volume": vols}
    return out


# --------------------------------------------------------------------------- #
# Backtest
# --------------------------------------------------------------------------- #
def run(data, step=21):
    # Master calendar = union of all dates; rebalance every `step` bars.
    all_dates = sorted({d for s in data.values() for d in s["dates"]})
    index = {}
    for sym, s in data.items():
        index[sym] = {d: i for i, d in enumerate(s["dates"])}
    ind = {sym: S.compute_indicators(s["close"], s["high"], s["low"], s["volume"])
           for sym, s in data.items()}

    max_h = max(HORIZONS.values())
    rebalance = list(range(WARMUP, len(all_dates) - max_h, step))
    if not rebalance:
        raise SystemExit("Not enough history for a single rebalance "
                         f"(need > {WARMUP + max_h} trading days).")

    # obs[strategy][bucket] -> list of (date_idx, fwd_1m, fwd_3m)
    obs = defaultdict(lambda: defaultdict(list))
    universe_fwd = defaultdict(lambda: defaultdict(list))  # horizon -> date -> returns
    dates_used = []

    for di in rebalance:
        date = all_dates[di]
        # Cross-sectional RS rank as of this date.
        rs_raw = {}
        avail = {}
        for sym, s in data.items():
            i = index[sym].get(date)
            if i is None or i + max_h >= len(s["close"]):
                continue
            avail[sym] = i
            rs_raw[sym] = ind[sym]["rs_raw"][i]
        if len(avail) < 20:
            continue
        ranks = S.percentile_ranks(rs_raw)
        dates_used.append(date)

        for sym, i in avail.items():
            closes = data[sym]["close"]
            fwd = {h: closes[i + n] / closes[i] - 1.0 for h, n in HORIZONS.items()}
            for h in HORIZONS:
                universe_fwd[h][date].append(fwd[h])
            rec = (date, fwd["1m"], fwd["3m"])

            comp = S.evaluate(ind[sym], i, ranks.get(sym))
            if comp["rating"] != "No Data":
                obs["Composite (new)"][comp["rating"]].append(rec)
            leg = S.legacy_rating(ind[sym], i)
            if leg != "No Data":
                obs["Legacy (EMA 50/200 + RSI)"][leg].append(rec)
            for name, on in S.component_signals(ind[sym], i, ranks.get(sym)).items():
                obs[name]["Signal ON" if on else "Signal OFF"].append(rec)

    return summarize(obs, universe_fwd, dates_used, all_dates)


def _stats(recs, universe_by_date, h_idx, h):
    if not recs:
        return None
    rets = [r[h_idx] for r in recs]
    excess = [r[h_idx] - _mean(universe_by_date[h][r[0]]) for r in recs]
    return {
        "n": len(rets),
        "mean_pct": round(100 * _mean(rets), 2),
        "median_pct": round(100 * _median(rets), 2),
        "hit_rate_pct": round(100 * sum(1 for x in rets if x > 0) / len(rets), 1),
        "excess_vs_universe_pct": round(100 * _mean(excess), 2),
    }


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _median(xs):
    if not xs:
        return 0.0
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2.0


def _portfolio(recs_by_date, dates):
    """Equal-weight basket of the bucket's names each month, held one month.
    Returns CAGR / max drawdown / Sharpe / total return (monthly series)."""
    monthly = []
    for d in dates:
        rets = recs_by_date.get(d)
        monthly.append(_mean(rets) if rets else 0.0)
    if not monthly:
        return None
    equity, peak, mdd = 1.0, 1.0, 0.0
    for r in monthly:
        equity *= 1 + r
        peak = max(peak, equity)
        mdd = min(mdd, equity / peak - 1)
    yrs = len(monthly) / 12.0
    cagr = equity ** (1 / yrs) - 1 if yrs > 0 else 0.0
    mu = _mean(monthly)
    sd = math.sqrt(_mean([(x - mu) ** 2 for x in monthly])) if len(monthly) > 1 else 0.0
    sharpe = (mu / sd * math.sqrt(12)) if sd > 0 else 0.0
    return {"total_return_pct": round(100 * (equity - 1), 1),
            "cagr_pct": round(100 * cagr, 2), "max_drawdown_pct": round(100 * mdd, 1),
            "sharpe": round(sharpe, 2), "months": len(monthly)}


def summarize(obs, universe_fwd, dates_used, all_dates):
    report = {"period": [dates_used[0], dates_used[-1]], "rebalances": len(dates_used),
              "strategies": {}}
    # Universe benchmark.
    uni_monthly = {d: universe_fwd["1m"][d] for d in dates_used}
    report["universe"] = {
        "1m": {"mean_pct": round(100 * _mean([_mean(v) for v in uni_monthly.values()]), 2)},
        "portfolio": _portfolio(uni_monthly, dates_used),
    }
    for strat, buckets in obs.items():
        entry = {}
        for bucket, recs in buckets.items():
            by_date = defaultdict(list)
            for r in recs:
                by_date[r[0]].append(r[1])
            entry[bucket] = {
                "1m": _stats(recs, universe_fwd, 1, "1m"),
                "3m": _stats(recs, universe_fwd, 2, "3m"),
                "portfolio": _portfolio(by_date, dates_used),
            }
        # Long basket = Buy-or-better (or Signal ON); spread vs Sell-or-worse.
        longs = [r for b in ("Strong Buy", "Buy", "Signal ON") for r in buckets.get(b, [])]
        shorts = [r for b in ("Sell", "Strong Sell", "Signal OFF") for r in buckets.get(b, [])]
        by_date = defaultdict(list)
        for r in longs:
            by_date[r[0]].append(r[1])
        entry["_long_basket"] = {
            "1m": _stats(longs, universe_fwd, 1, "1m"),
            "3m": _stats(longs, universe_fwd, 2, "3m"),
            "portfolio": _portfolio(by_date, dates_used),
            "spread_1m_pct": (round(100 * (_mean([r[1] for r in longs]) - _mean([r[1] for r in shorts])), 2)
                              if longs and shorts else None),
            "spread_3m_pct": (round(100 * (_mean([r[2] for r in longs]) - _mean([r[2] for r in shorts])), 2)
                              if longs and shorts else None),
        }
        report["strategies"][strat] = entry
    return report


# --------------------------------------------------------------------------- #
# Printing
# --------------------------------------------------------------------------- #
def print_report(rep):
    u = rep["universe"]
    print(f"\nPeriod {rep['period'][0]} -> {rep['period'][1]}  "
          f"({rep['rebalances']} monthly rebalances)")
    print(f"Equal-weight universe: avg 1m return {u['1m']['mean_pct']:+.2f}%, "
          f"CAGR {u['portfolio']['cagr_pct']:+.2f}%, max DD {u['portfolio']['max_drawdown_pct']:.1f}%, "
          f"Sharpe {u['portfolio']['sharpe']:.2f}")

    def row(label, st):
        if not st or not st["1m"]:
            return f"  {label:<14} (no observations)"
        a, b, p = st["1m"], st["3m"], st["portfolio"]
        return (f"  {label:<14} n={a['n']:>6}  1m {a['mean_pct']:+6.2f}% "
                f"(xs {a['excess_vs_universe_pct']:+5.2f}%, hit {a['hit_rate_pct']:4.1f}%)  "
                f"3m {b['mean_pct']:+6.2f}% (xs {b['excess_vs_universe_pct']:+5.2f}%)"
                + (f"  | CAGR {p['cagr_pct']:+6.2f}% DD {p['max_drawdown_pct']:6.1f}% "
                   f"Sharpe {p['sharpe']:.2f}" if p else ""))

    order = ["Composite (new)", "Legacy (EMA 50/200 + RSI)"]
    order += [k for k in rep["strategies"] if k not in order]
    for strat in order:
        e = rep["strategies"][strat]
        lb = e["_long_basket"]
        print(f"\n== {strat}")
        buckets = RATINGS if "Strong Buy" in e or "Buy" in e else ["Signal ON", "Signal OFF"]
        for b in buckets:
            if b in e:
                print(row(b, e[b]))
        print(row("LONG BASKET", lb))
        if lb.get("spread_1m_pct") is not None:
            print(f"  long-minus-short spread: 1m {lb['spread_1m_pct']:+.2f}%  "
                  f"3m {lb['spread_3m_pct']:+.2f}%")
    print("\nxs = excess return vs the equal-weight universe over the same window; "
          "LONG BASKET = all Buy-or-better (or Signal ON) names, equal weight, "
          "rebalanced monthly.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--csv", help="long-format OHLCV CSV (date,open,high,low,close,volume,Name)")
    src.add_argument("--live", action="store_true", help="download from Yahoo Finance")
    src.add_argument("--sample", action="store_true", help="synthetic data (smoke test)")
    ap.add_argument("--years", type=int, default=6, help="history for --live")
    ap.add_argument("--limit", type=int, default=0, help="max tickers for --live")
    ap.add_argument("--step", type=int, default=21, help="bars between rebalances")
    ap.add_argument("--out", help="write the full report as JSON here")
    args = ap.parse_args()

    if args.csv:
        data = load_csv(args.csv)
    elif args.sample:
        data = load_sample()
    else:
        data = load_live(args.years, args.limit)
    print(f"Loaded {len(data)} tickers.")
    rep = run(data, step=args.step)
    print_report(rep)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rep, f, indent=2)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
