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
import time
from datetime import date, datetime, timedelta, timezone

# tickers.py lives next to this file.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tickers import (  # noqa: E402
    ROBINHOOD_TOP_100,
    RH_SECTORS,
    sp500_fallback_map,
)
import strategies as strat  # noqa: E402

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
        if len(closes) > strat.YEAR:
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

# Retries for Yahoo rate limiting (HTTP 429) inside the fundamentals fetch.
# Without these a burst of 429s silently blanks out P/E, analyst targets and
# earnings for a chunk of tickers on that day's run.
FETCH_RETRIES = 3
FETCH_BACKOFF = 2.0  # seconds; doubles each retry


class _NeverRaised(Exception):
    """Placeholder so retries degrade to a plain call if yfinance moves the
    rate-limit exception; better than failing every ticker on an import."""


def _rate_limit_error():
    """yfinance's rate-limit exception class, resolved once and cached."""
    global _RATE_LIMIT_EXC
    if _RATE_LIMIT_EXC is None:
        try:
            from yfinance.exceptions import YFRateLimitError
            _RATE_LIMIT_EXC = YFRateLimitError
        except ImportError:
            print("yfinance.exceptions.YFRateLimitError not found; "
                  "rate-limit retries disabled.", file=sys.stderr)
            _RATE_LIMIT_EXC = _NeverRaised
    return _RATE_LIMIT_EXC


_RATE_LIMIT_EXC = None


def _with_retry(fn):
    """Call ``fn()``; on a Yahoo rate-limit error back off and retry."""
    exc = _rate_limit_error()
    for attempt in range(FETCH_RETRIES):
        try:
            return fn()
        except exc:
            if attempt == FETCH_RETRIES - 1:
                raise
            time.sleep(FETCH_BACKOFF * (2 ** attempt) + random.random())


def extract_earnings(ticker):
    """Recent quarterly revenue / net income / EPS from the income statement.

    Returns a list (newest first) of ``{period, revenue, net_income, eps}``.
    Best-effort: returns ``[]`` on any failure.

    Note: Yahoo fills the income statement from the *filed* financials, which
    typically land days to weeks after the earnings press release. The
    ``earningsHistory`` module (see :func:`fetch_earnings_events`) carries the
    headline EPS the same day, so the two are merged in :func:`merge_earnings`.
    """
    try:
        df = _with_retry(lambda: ticker.quarterly_income_stmt)
    except Exception:  # noqa: BLE001
        return []
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


def _raw(v):
    """Unwrap Yahoo's ``{"raw": x, "fmt": "..."}`` cells; pass plain values.

    Always returns a plain Python number so the result stays JSON-serialisable
    (the pandas fallback path in :func:`fetch_earnings_events` yields numpy
    scalars, which ``json.dump`` refuses).
    """
    if isinstance(v, dict):
        v = v.get("raw")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def fetch_earnings_events(ticker):
    """Same-day earnings data from Yahoo's ``earningsHistory`` + ``calendarEvents``.

    Unlike the income statement these are updated within hours of the
    earnings release (they drive the "EPS vs estimate" panel on Yahoo's quote
    page), so a company that reported last night shows up on this morning's
    refresh.

    Returns ``{"reported": [...], "dates": [...], "date_is_estimate": bool,
    "eps_estimate": float|None, "revenue_estimate": float|None}`` where
    ``reported`` is newest-first ``{period, eps, eps_estimate, surprise_pct}``
    and ``dates`` are ISO dates of the upcoming report window. Best-effort:
    returns an empty structure on any failure.
    """
    empty = {"reported": [], "dates": [], "date_is_estimate": False,
             "eps_estimate": None, "revenue_estimate": None}
    modules = "earningsHistory,calendarEvents"

    def fetch():
        # One quoteSummary call for both modules (the public ``earnings_history``
        # and ``calendar`` properties would each cost a request). Fall back to
        # those properties if this internal path ever changes shape.
        try:
            from yfinance.scrapers.quote import _QUOTE_SUMMARY_URL_
            params = {"modules": modules, "corsDomain": "finance.yahoo.com",
                      "formatted": "false", "symbol": ticker.ticker}
            res = ticker._data.get_raw_json(  # noqa: SLF001
                f"{_QUOTE_SUMMARY_URL_}/{ticker.ticker}", params=params)
            return (res or {}).get("quoteSummary", {}).get("result") or [{}]
        except (ImportError, AttributeError):
            hist = ticker.earnings_history
            cal = ticker.calendar or {}
            history = [] if hist is None or hist.empty else [
                {"quarter": {"fmt": idx.strftime("%Y-%m-%d")},
                 "epsActual": {"raw": row.get("epsActual")},
                 "epsEstimate": {"raw": row.get("epsEstimate")},
                 "surprisePercent": {"raw": row.get("surprisePercent")}}
                for idx, row in hist.iterrows()]
            earnings = {
                "earningsDate": [int(datetime.combine(d, datetime.min.time(),
                                                      tzinfo=timezone.utc).timestamp())
                                 for d in cal.get("Earnings Date", [])],
                "earningsAverage": cal.get("Earnings Average"),
                "revenueAverage": cal.get("Revenue Average"),
            }
            return [{"earningsHistory": {"history": history},
                     "calendarEvents": {"earnings": earnings}}]

    try:
        result = _with_retry(fetch) or [{}]
        node = result[0] or {}
    except Exception:  # noqa: BLE001
        return empty

    reported = []
    for item in (node.get("earningsHistory") or {}).get("history") or []:
        q = item.get("quarter")
        period = q.get("fmt") if isinstance(q, dict) else None
        if not period and isinstance(q, dict) and _raw(q) is not None:
            period = _ts_to_date(_raw(q))
        eps = _raw(item.get("epsActual"))
        if not period or eps is None:
            continue  # future / unreported quarter
        reported.append({
            "period": period,
            "eps": eps,
            "eps_estimate": _raw(item.get("epsEstimate")),
            "surprise_pct": _raw(item.get("surprisePercent")),
        })
    reported.sort(key=lambda r: r["period"], reverse=True)

    earnings = (node.get("calendarEvents") or {}).get("earnings") or {}
    dates = []
    for d in earnings.get("earningsDate") or []:
        iso = _ts_to_date(_raw(d))
        if iso:
            dates.append(iso)
    return {
        "reported": reported,
        "dates": sorted(dates),
        "date_is_estimate": bool(earnings.get("isEarningsDateEstimate")),
        "eps_estimate": _raw(earnings.get("earningsAverage")),
        "revenue_estimate": _raw(earnings.get("revenueAverage")),
    }


def _same_quarter(a, b, tol_days=10):
    """True if two ISO period-end dates refer to the same fiscal quarter."""
    try:
        da = date.fromisoformat(a)
        db = date.fromisoformat(b)
    except (TypeError, ValueError):
        return a == b
    return abs((da - db).days) <= tol_days


def merge_earnings(statement_rows, reported_rows):
    """Merge income-statement quarters with same-day reported EPS.

    ``statement_rows`` come from :func:`extract_earnings` (revenue, net income,
    diluted EPS -- authoritative but slow to appear); ``reported_rows`` from
    :func:`fetch_earnings_events` (headline EPS vs. estimate -- available the
    day of the report). A quarter that has been reported but not yet filed
    appears with revenue / net income ``None`` so the newest quarter is never
    missing just because the 10-Q hasn't landed. Newest first, capped at
    ``EARNINGS_QUARTERS``.
    """
    rows = []
    for r in statement_rows or []:
        rows.append({**r, "eps_estimate": None, "surprise_pct": None})

    for rep in reported_rows or []:
        match = next((r for r in rows if _same_quarter(r["period"], rep["period"])), None)
        if match is None:
            rows.append({"period": rep["period"], "revenue": None, "net_income": None,
                         "eps": rep["eps"], "eps_estimate": rep["eps_estimate"],
                         "surprise_pct": rep["surprise_pct"]})
            continue
        # Keep the statement's diluted EPS when present; the headline number is
        # usually "adjusted" and the estimate/surprise pair belongs with it.
        if match.get("eps") is None:
            match["eps"] = rep["eps"]
        match["eps_reported"] = rep["eps"]
        match["eps_estimate"] = rep["eps_estimate"]
        match["surprise_pct"] = rep["surprise_pct"]

    rows.sort(key=lambda r: r["period"], reverse=True)
    return rows[:EARNINGS_QUARTERS]


def load_previous_details(path):
    """Read a previously published ``details.json``; ``{}`` if unusable.

    Used to carry earnings forward when a run gets rate-limited by Yahoo --
    without it a throttled run silently replaces good earnings with blanks.
    Sample payloads are ignored so fake data never leaks into a live build.
    """
    if not path:
        return {}
    try:
        with open(path) as f:
            payload = json.load(f)
    except (OSError, ValueError) as exc:
        print(f"Previous details unusable ({exc}); no backfill.", file=sys.stderr)
        return {}
    if payload.get("is_sample"):
        print("Previous details are sample data; no backfill.", file=sys.stderr)
        return {}
    return payload.get("stocks") or {}


def backfill_details(details, previous, today=None):
    """Fill gaps in a fresh run from the last published data (in place).

    A ticker whose fundamentals call was rate-limited comes back with no
    earnings and no report date. Rather than publish that hole, reuse what the
    site was already serving: quarters the new run is missing are merged back
    in (fresh rows always win for the same quarter) and a still-future report
    date is kept. Returns ``(quarters_restored, dates_restored)``.
    """
    if not previous:
        return 0, 0
    today = today or datetime.now(timezone.utc).date().isoformat()
    quarters = dates = 0

    for symbol, new in details.items():
        old = previous.get(symbol)
        if not old:
            continue

        old_rows = old.get("earnings") or []
        if old_rows:
            have = {r["period"] for r in new.get("earnings") or []}
            missing = [r for r in old_rows if r.get("period") not in have]
            if missing:
                merged = (new.get("earnings") or []) + missing
                merged.sort(key=lambda r: r.get("period") or "", reverse=True)
                merged = merged[:EARNINGS_QUARTERS]
                # Count only the carried-over rows that survived the cap.
                kept = {id(r) for r in merged}
                quarters += sum(1 for r in missing if id(r) in kept)
                new["earnings"] = merged

        if not new.get("next_earnings"):
            old_next = old.get("next_earnings")
            if old_next and old_next >= today:
                new["next_earnings"] = old_next
                new["next_earnings_is_estimate"] = old.get("next_earnings_is_estimate", False)
                if new.get("next_eps_estimate") is None:
                    new["next_eps_estimate"] = old.get("next_eps_estimate")
                if new.get("next_revenue_estimate") is None:
                    new["next_revenue_estimate"] = old.get("next_revenue_estimate")
                dates += 1

    if quarters or dates:
        print(f"Backfilled {quarters} quarters and {dates} report dates "
              f"from the previously published data.")
    return quarters, dates


def fetch_fundamentals(symbols, earnings=True):
    """Best-effort fundamentals per ticker via yfinance (threaded).

    Returns ``{symbol: {"pe", "market_cap", "sector", "info": {...},
    "earnings": [...], "events": {...}}}`` where ``info`` is a subset of
    yfinance's ``.info`` used by the detail page. Any ticker that fails simply
    gets ``None`` values -- fundamentals never block the technical ratings,
    which come from the (more reliable) price download.

    ``earnings=False`` skips the income statement and earnings-calendar calls
    (two of the four requests per ticker) for callers that only need P/E,
    market cap and sector.
    """
    import yfinance as yf  # lazy import
    from concurrent.futures import ThreadPoolExecutor, as_completed

    print(f"Fetching fundamentals for {len(symbols)} tickers...")
    blank = {"pe": None, "market_cap": None, "sector": None,
             "info": {}, "earnings": [], "events": {}}

    def one(sym):
        try:
            tk = yf.Ticker(sym)
            info = _with_retry(lambda: tk.info) or {}
            d = {
                "pe": info.get("trailingPE"),
                "market_cap": info.get("marketCap"),
                "sector": info.get("sector"),
                "info": {k: info.get(k) for k in INFO_KEYS},
                "earnings": [], "events": {},
            }
            if earnings:
                events = fetch_earnings_events(tk)
                d["earnings"] = merge_earnings(extract_earnings(tk), events["reported"])
                d["events"] = events
            return sym, d
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym}: fundamentals failed ({type(exc).__name__}: {exc})",
                  file=sys.stderr)
            return sym, dict(blank)

    out = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(one, s) for s in symbols]
        for fut in as_completed(futures):
            sym, d = fut.result()
            out[sym] = d

    # Surface silent gaps in the Action log: a spike here means Yahoo was
    # rate-limiting us, not that the companies have no earnings.
    no_info = sum(1 for d in out.values() if not d["info"])
    print(f"Fundamentals: {len(out) - no_info}/{len(out)} tickers with info"
          f", {no_info} without.")
    if earnings:
        no_earn = sum(1 for d in out.values() if not d["earnings"])
        no_date = sum(1 for d in out.values() if not d["events"].get("dates"))
        print(f"Earnings: {no_earn} tickers without quarterly history, "
              f"{no_date} without a calendar date.")
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


def next_earnings_date(info, events=None, today=None):
    """Pick the upcoming report date: calendar first, ``.info`` as fallback.

    Yahoo's ``calendarEvents`` rolls forward to the next quarter within a day
    of a report; the ``earningsTimestamp*`` fields in ``.info`` can lag for a
    while. Dates already in the past (the company just reported and Yahoo
    hasn't moved the date yet) are dropped rather than shown as "next".
    """
    info = info or {}
    events = events or {}
    today = today or datetime.now(timezone.utc).date().isoformat()

    def first_future(dates):
        return min((d for d in dates if d and d >= today), default=None)

    # calendarEvents is authoritative here: it rolls forward within a day of a
    # report, so trust it whenever it offers a future date.
    from_calendar = first_future(events.get("dates") or [])
    if from_calendar:
        return from_calendar
    # ``.info``'s earningsTimestamp* fields can lag by weeks -- fallback only.
    return first_future([_ts_to_date(info.get(k))
                         for k in ("earningsTimestampStart", "earningsTimestamp")])


def build_detail(info, earnings=None, events=None, today=None):
    """Extended per-stock fundamentals for the detail page."""
    info = info or {}
    events = events or {}

    def num(key, n=2):
        v = info.get(key)
        return round(v, n) if isinstance(v, (int, float)) and not math.isnan(v) else None

    def ival(key):
        v = info.get(key)
        return int(v) if isinstance(v, (int, float)) and not math.isnan(v) else None

    next_earnings = next_earnings_date(info, events, today=today)

    def fnum(v, n=2):
        return round(v, n) if isinstance(v, (int, float)) and not math.isnan(v) else None

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
        # True when Yahoo flags the date as a projection rather than confirmed.
        "next_earnings_is_estimate": bool(next_earnings and events.get("date_is_estimate")),
        "next_eps_estimate": fnum(events.get("eps_estimate")) if next_earnings else None,
        "next_revenue_estimate": fnum(events.get("revenue_estimate"), 0) if next_earnings else None,
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
    """Download daily OHLCV for many symbols, chunked to be API-friendly.

    Returns ``{symbol: {"dates", "close", "high", "low", "volume"}}`` (empty
    lists for failures).
    """
    import yfinance as yf  # imported lazily so --sample works without it

    out = {}
    for i in range(0, len(symbols), chunk):
        part = symbols[i:i + chunk]
        print(f"  downloading {i + 1}-{i + len(part)} of {len(symbols)}...")
        data = yf.download(
            tickers=part,
            period=period,
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            threads=True,
            progress=False,
        )
        for sym in part:
            try:
                df = data if len(part) == 1 else data[sym]
                # Keep the columns aligned by dropping rows missing any of them.
                sub = df[["Close", "High", "Low", "Volume"]].dropna()
                out[sym] = {
                    "dates": [d.strftime("%Y-%m-%d") for d in sub.index],
                    "close": sub["Close"].tolist(),
                    "high": sub["High"].tolist(),
                    "low": sub["Low"].tolist(),
                    "volume": sub["Volume"].tolist(),
                }
            except (KeyError, TypeError):
                out[sym] = dict(EMPTY_PRICES)
    return out


def fetch_live(previous_path=None):
    """Fetch real data via yfinance. Returns list of records (may be empty)."""
    universe = build_universe()
    symbols = list(universe.keys())
    print(f"Downloading {len(symbols)} tickers from Yahoo Finance ({LOOKBACK})...")

    prices = download_prices(symbols)
    fundamentals = fetch_fundamentals(symbols)
    rs_ranks = compute_rs_ranks(prices)

    records = []
    details = {}
    skipped = 0
    for symbol in symbols:
        p = prices.get(symbol) or EMPTY_PRICES
        if not p["close"]:
            skipped += 1
            continue

        meta = universe[symbol]
        f = fundamentals.get(symbol, {})
        records.append(build_record(
            symbol, p["close"], p["volume"], highs=p["high"], lows=p["low"],
            rs_rank=rs_ranks.get(symbol),
            name=meta.get("name"),
            pe=f.get("pe"), market_cap=f.get("market_cap"),
            sector=meta.get("sector"), lists=meta.get("lists"),
        ))
        details[symbol] = build_detail(f.get("info"), f.get("earnings"), f.get("events"))

    backfill_details(details, load_previous_details(previous_path))

    print(f"Built {len(records)} records ({skipped} tickers had no usable data).")
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
        # Synthetic quarterly earnings history (newest first), shaped like the
        # live merge: the newest quarter is "reported but not yet filed" (EPS
        # vs. estimate only), older ones have full statement numbers.
        shares = info["sharesOutstanding"]
        erng = random.Random(hash(symbol) & 0xFFFF)  # keep ``rng``'s draw order stable
        statement, reported = [], []
        rev = market_cap * rng.uniform(0.15, 0.5)
        for q in range(EARNINGS_QUARTERS):
            period = (date(2026, 9, 1) - timedelta(days=91 * q)).replace(day=28)
            ni = rev * rng.uniform(0.05, 0.25)
            eps = round(ni / shares, 2) if shares else None
            if q > 0:
                statement.append({"period": period.isoformat(), "revenue": rev,
                                  "net_income": ni, "eps": eps})
            if q < 4 and eps is not None:
                est = round(eps * erng.uniform(0.85, 1.1), 2)
                reported.append({"period": period.isoformat(), "eps": eps,
                                 "eps_estimate": est,
                                 "surprise_pct": round((eps - est) / abs(est) * 100, 2) if est else None})
            rev *= rng.uniform(0.92, 0.99)  # older quarters slightly smaller
        earnings = merge_earnings(statement, reported)
        next_iso = _ts_to_date(info["earningsTimestampStart"])
        events = {"reported": reported, "dates": [next_iso],
                  "date_is_estimate": erng.random() < 0.4,
                  "eps_estimate": round(erng.uniform(0.2, 5), 2),
                  "revenue_estimate": rev * erng.uniform(0.95, 1.1)}
        # Sample data is deterministic, so pin "today" for the next-date logic.
        details[symbol] = build_detail(info, earnings, events, today="2026-08-01")
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
    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)
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
    with open(DETAILS_PATH, "w") as f:
        json.dump(payload, f, indent=2)
    print(f"Wrote {len(details)} detail entries to {os.path.relpath(DETAILS_PATH)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", action="store_true",
                        help="write deterministic sample data (no network)")
    parser.add_argument("--previous", metavar="PATH",
                        help="previously published details.json; earnings "
                             "missing from this run are carried over from it "
                             "so a rate-limited fetch never blanks the site")
    args = parser.parse_args()

    if args.sample:
        records, details = generate_sample()
        write_output(records, is_sample=True)
        write_details(details, is_sample=True)
        return 0

    try:
        records, details = fetch_live(args.previous)
    except Exception as exc:  # noqa: BLE001
        print(f"Live fetch raised: {exc}", file=sys.stderr)
        records, details = [], {}

    if not records:
        print("Live fetch produced no data — falling back to sample.",
              file=sys.stderr)
        records, details = generate_sample()
        write_output(records, is_sample=True)
        write_details(details, is_sample=True)
        return 1

    write_output(records, is_sample=False)
    write_details(details, is_sample=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
