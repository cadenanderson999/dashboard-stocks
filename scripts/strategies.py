#!/usr/bin/env python3
"""Trading-strategy engine: indicators, a multi-factor composite rating, and
the LEAP-call screen.

Everything here is pure Python over plain lists so it runs with or without
pandas, and every indicator is computed as a full *series* (aligned with the
input closes, ``None`` where there isn't enough history). That lets the daily
generator score the latest bar and the backtester score every historical bar
with exactly the same code.

The composite rating blends four well-documented strategy families instead of
the old EMA-50/200 + RSI decision table:

  * Trend       -- Mark Minervini's "Trend Template" (price > 50 > 150 > 200
                   day SMA, rising 200 SMA, >= 30% off the 52-week low, within
                   25% of the 52-week high) plus ADX trend strength.
  * Momentum    -- cross-sectional relative strength (IBD-style weighted 3/6/9/
                   12-month returns ranked 1-99 across the universe; the
                   Jegadeesh-Titman momentum anomaly) plus MACD(12,26,9).
  * Timing      -- Connors-style pullback entries in uptrends (RSI(2) < 10 or
                   RSI(14) < 30), 52-week-high breakouts on volume, and an
                   over-extension penalty (price stretched above the 50 SMA).
  * Volume      -- accumulation vs distribution (up-volume / down-volume ratio
                   over 50 days) and OBV vs its 20-day average.

Each family produces a signed sub-score; their sum (about -90..+90) maps to
Strong Buy / Buy / Hold / Sell / Strong Sell. The weights were chosen with
``scripts/backtest.py`` on five years of daily S&P 500 data (2013-2018): the
factors with a measurable forward-return edge got weight (relative strength,
rising 200-day trend, distance above the 52-week low, pullbacks *inside*
uptrends, accumulation volume) and the ones with none or a negative edge
(MACD state, ADX, volume breakouts, "over-extension" penalties) are reported
as labels only and do not move the score.
"""

from __future__ import annotations

import math

# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
SMA_FAST, SMA_MID, SMA_SLOW = 50, 150, 200
RSI_PERIOD = 14
RSI_SHORT = 2
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
ADX_PERIOD = 14
ATR_PERIOD = 14
YEAR = 252                 # trading days in a year
MONTH = 21                 # trading days in a month
UDV_WINDOW = 50            # up/down volume ratio window
OBV_WINDOW = 20
HV_WINDOW = 60             # realised-volatility window (days)
RVOL_AVG_WINDOW = 50

# Composite rating thresholds (score is roughly -95..+95).
STRONG_BUY, BUY, SELL, STRONG_SELL = 60, 25, -20, -50

# LEAP screen thresholds.
LEAP_BUY, LEAP_WATCH = 75, 60


# --------------------------------------------------------------------------- #
# Series indicators
# --------------------------------------------------------------------------- #
def sma_series(values, period):
    """Simple moving average; ``None`` until ``period`` values are available."""
    out = [None] * len(values)
    if period <= 0 or len(values) < period:
        return out
    total = sum(values[:period])
    out[period - 1] = total / period
    for i in range(period, len(values)):
        total += values[i] - values[i - period]
        out[i] = total / period
    return out


def ema_series(values, period):
    """Exponential moving average seeded with the first value."""
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1.0 - k))
    return out


def rsi_series(values, period=RSI_PERIOD):
    """Wilder's RSI as a series (``None`` for the first ``period`` bars)."""
    n = len(values)
    out = [None] * n
    if n < period + 1:
        return out
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        ch = values[i] - values[i - 1]
        avg_gain += max(ch, 0.0)
        avg_loss += max(-ch, 0.0)
    avg_gain /= period
    avg_loss /= period

    def rsi_val(g, l):
        if l == 0:
            return 100.0
        return 100.0 - 100.0 / (1.0 + g / l)

    out[period] = rsi_val(avg_gain, avg_loss)
    for i in range(period + 1, n):
        ch = values[i] - values[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(ch, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-ch, 0.0)) / period
        out[i] = rsi_val(avg_gain, avg_loss)
    return out


def macd_series(values, fast=MACD_FAST, slow=MACD_SLOW, signal=MACD_SIGNAL):
    """Returns (macd, signal, histogram) series. Early bars are ``None``."""
    n = len(values)
    if n == 0:
        return [], [], []
    ef, es = ema_series(values, fast), ema_series(values, slow)
    macd = [ef[i] - es[i] for i in range(n)]
    sig = ema_series(macd, signal)
    hist = [macd[i] - sig[i] for i in range(n)]
    warm = slow + signal  # the seeded EMAs are unreliable before this
    for i in range(min(warm, n)):
        macd[i] = sig[i] = hist[i] = None
    return macd, sig, hist


def true_range(highs, lows, closes, i):
    if i == 0:
        return highs[0] - lows[0]
    pc = closes[i - 1]
    return max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))


def atr_series(highs, lows, closes, period=ATR_PERIOD):
    """Wilder's Average True Range."""
    n = len(closes)
    out = [None] * n
    if n < period + 1:
        return out
    tr = [true_range(highs, lows, closes, i) for i in range(n)]
    atr = sum(tr[1:period + 1]) / period
    out[period] = atr
    for i in range(period + 1, n):
        atr = (atr * (period - 1) + tr[i]) / period
        out[i] = atr
    return out


def adx_series(highs, lows, closes, period=ADX_PERIOD):
    """Wilder's ADX with +DI / -DI. Returns (adx, plus_di, minus_di)."""
    n = len(closes)
    adx = [None] * n
    pdi = [None] * n
    mdi = [None] * n
    if n < 2 * period + 1:
        return adx, pdi, mdi

    tr = [0.0] * n
    pdm = [0.0] * n
    mdm = [0.0] * n
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        dn = lows[i - 1] - lows[i]
        pdm[i] = up if (up > dn and up > 0) else 0.0
        mdm[i] = dn if (dn > up and dn > 0) else 0.0
        tr[i] = true_range(highs, lows, closes, i)

    s_tr = sum(tr[1:period + 1])
    s_p = sum(pdm[1:period + 1])
    s_m = sum(mdm[1:period + 1])
    dx_hist = []
    adx_val = None
    for i in range(period, n):
        if i > period:
            s_tr = s_tr - s_tr / period + tr[i]
            s_p = s_p - s_p / period + pdm[i]
            s_m = s_m - s_m / period + mdm[i]
        if s_tr <= 0:
            continue
        p = 100.0 * s_p / s_tr
        m = 100.0 * s_m / s_tr
        pdi[i], mdi[i] = p, m
        dx = 100.0 * abs(p - m) / (p + m) if (p + m) > 0 else 0.0
        if adx_val is None:
            dx_hist.append(dx)
            if len(dx_hist) == period:
                adx_val = sum(dx_hist) / period
                adx[i] = adx_val
        else:
            adx_val = (adx_val * (period - 1) + dx) / period
            adx[i] = adx_val
    return adx, pdi, mdi


def obv_series(closes, volumes):
    out = [0.0] * len(closes)
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out[i] = out[i - 1] + volumes[i]
        elif closes[i] < closes[i - 1]:
            out[i] = out[i - 1] - volumes[i]
        else:
            out[i] = out[i - 1]
    return out


def rolling_max(values, window):
    """Trailing max over ``window`` bars (inclusive); ``None`` until full."""
    out = [None] * len(values)
    from collections import deque
    dq = deque()
    for i, v in enumerate(values):
        while dq and values[dq[-1]] <= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
        if i >= window - 1:
            out[i] = values[dq[0]]
    return out


def rolling_min(values, window):
    out = [None] * len(values)
    from collections import deque
    dq = deque()
    for i, v in enumerate(values):
        while dq and values[dq[-1]] >= v:
            dq.pop()
        dq.append(i)
        if dq[0] <= i - window:
            dq.popleft()
        if i >= window - 1:
            out[i] = values[dq[0]]
    return out


def return_series(values, lag):
    """values[i] / values[i-lag] - 1 (``None`` until ``lag`` bars exist)."""
    out = [None] * len(values)
    for i in range(lag, len(values)):
        base = values[i - lag]
        out[i] = values[i] / base - 1.0 if base else None
    return out


def weighted_rs_series(closes):
    """IBD-style relative-strength raw score: 2x weight on the latest quarter.

        rs = 2*r(3m) + r(6m) + r(9m) + r(12m)

    Ranked cross-sectionally (see ``percentile_ranks``) it becomes the 1-99
    RS rank used by the Trend Template ("RS >= 70").
    """
    r3 = return_series(closes, 63)
    r6 = return_series(closes, 126)
    r9 = return_series(closes, 189)
    r12 = return_series(closes, YEAR)
    out = [None] * len(closes)
    for i in range(len(closes)):
        if r12[i] is None:
            continue
        out[i] = 2.0 * r3[i] + r6[i] + r9[i] + r12[i]
    return out


def momentum_12_1_series(closes):
    """Classic 12-1 momentum: return from 12 months ago to 1 month ago."""
    out = [None] * len(closes)
    for i in range(YEAR, len(closes)):
        a, b = closes[i - YEAR], closes[i - MONTH]
        out[i] = b / a - 1.0 if a else None
    return out


def up_down_volume_series(closes, volumes, window=UDV_WINDOW):
    """Up-day volume / down-day volume over a trailing window (accumulation
    vs distribution). > 1 means buyers are dominant."""
    n = len(closes)
    out = [None] * n
    up = [0.0] * n
    dn = [0.0] * n
    for i in range(1, n):
        if closes[i] > closes[i - 1]:
            up[i] = volumes[i]
        elif closes[i] < closes[i - 1]:
            dn[i] = volumes[i]
    su = sd = 0.0
    for i in range(n):
        su += up[i]
        sd += dn[i]
        if i >= window:
            su -= up[i - window]
            sd -= dn[i - window]
        if i >= window:
            out[i] = (su / sd) if sd > 0 else (5.0 if su > 0 else None)
    return out


def rvol_series(volumes, window=RVOL_AVG_WINDOW):
    """Volume / trailing ``window``-day average volume (prior days only)."""
    n = len(volumes)
    out = [None] * n
    if n < window + 1:
        return out
    total = sum(volumes[:window])
    for i in range(window, n):
        avg = total / window
        out[i] = volumes[i] / avg if avg > 0 else None
        total += volumes[i] - volumes[i - window]
    return out


def realised_vol_series(closes, window=HV_WINDOW):
    """Annualised close-to-close volatility (%) over a trailing window."""
    n = len(closes)
    out = [None] * n
    if n < window + 1:
        return out
    rets = [0.0] * n
    for i in range(1, n):
        if closes[i - 1] > 0 and closes[i] > 0:
            rets[i] = math.log(closes[i] / closes[i - 1])
    s = ss = 0.0
    for i in range(1, n):
        s += rets[i]
        ss += rets[i] * rets[i]
        if i > window:
            s -= rets[i - window]
            ss -= rets[i - window] ** 2
        if i >= window:
            mean = s / window
            var = max(ss / window - mean * mean, 0.0)
            out[i] = math.sqrt(var) * math.sqrt(YEAR) * 100.0
    return out


def percentile_ranks(values_by_key):
    """Map ``{key: raw}`` to ``{key: rank}`` with rank in 1..99 (higher is
    stronger). Keys with ``None`` are omitted."""
    items = [(k, v) for k, v in values_by_key.items() if v is not None]
    if not items:
        return {}
    if len(items) == 1:
        return {items[0][0]: 50}
    items.sort(key=lambda kv: kv[1])
    n = len(items)
    out = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        pos = (i + j) / 2.0  # average position for ties
        rank = int(round(1 + 98.0 * pos / (n - 1)))
        for k in range(i, j + 1):
            out[items[k][0]] = rank
        i = j + 1
    return out


def rank_against(value, reference_values):
    """Rank one raw RS value (1-99) against a reference distribution."""
    ref = sorted(v for v in reference_values if v is not None)
    if value is None or not ref:
        return None
    below = sum(1 for v in ref if v < value)
    equal = sum(1 for v in ref if v == value)
    pos = below + equal / 2.0
    return int(round(1 + 98.0 * pos / max(len(ref), 1)))


# --------------------------------------------------------------------------- #
# Indicator bundle
# --------------------------------------------------------------------------- #
def compute_indicators(closes, highs=None, lows=None, volumes=None):
    """Compute every series the rating needs. ``highs``/``lows`` fall back to
    the closes (ADX/ATR then degrade gracefully); ``volumes`` may be ``None``."""
    n = len(closes)
    highs = highs if highs and len(highs) == n else closes
    lows = lows if lows and len(lows) == n else closes
    volumes = volumes if volumes and len(volumes) == n else [0.0] * n

    macd, sig, hist = macd_series(closes)
    adx, pdi, mdi = adx_series(highs, lows, closes)
    obv = obv_series(closes, volumes)
    return {
        "n": n,
        "close": closes, "high": highs, "low": lows, "volume": volumes,
        "sma50": sma_series(closes, SMA_FAST),
        "sma150": sma_series(closes, SMA_MID),
        "sma200": sma_series(closes, SMA_SLOW),
        "ema50": ema_series(closes, 50),
        "ema200": ema_series(closes, 200),
        "rsi14": rsi_series(closes, RSI_PERIOD),
        "rsi2": rsi_series(closes, RSI_SHORT),
        "macd": macd, "macd_signal": sig, "macd_hist": hist,
        "adx": adx, "plus_di": pdi, "minus_di": mdi,
        "atr": atr_series(highs, lows, closes),
        "obv": obv,
        "obv_sma": sma_series(obv, OBV_WINDOW),
        "high52": rolling_max(highs, YEAR),
        "low52": rolling_min(lows, YEAR),
        "rs_raw": weighted_rs_series(closes),
        "mom_12_1": momentum_12_1_series(closes),
        "udv": up_down_volume_series(closes, volumes),
        "rvol": rvol_series(volumes),
        "hv": realised_vol_series(closes),
        "ret_1m": return_series(closes, MONTH),
        "ret_3m": return_series(closes, 63),
        "ret_6m": return_series(closes, 126),
        "ret_12m": return_series(closes, YEAR),
    }


def _at(series, i):
    if series is None or i < 0 or i >= len(series):
        return None
    return series[i]


def _crossed_up(a, b, i, lookback):
    """True if series ``a`` crossed above ``b`` within the last ``lookback`` bars."""
    for k in range(max(1, i - lookback + 1), i + 1):
        a0, b0, a1, b1 = _at(a, k - 1), _at(b, k - 1), _at(a, k), _at(b, k)
        if None in (a0, b0, a1, b1):
            continue
        if a0 <= b0 and a1 > b1:
            return True
    return False


def _crossed_down(a, b, i, lookback):
    for k in range(max(1, i - lookback + 1), i + 1):
        a0, b0, a1, b1 = _at(a, k - 1), _at(b, k - 1), _at(a, k), _at(b, k)
        if None in (a0, b0, a1, b1):
            continue
        if a0 >= b0 and a1 < b1:
            return True
    return False


# --------------------------------------------------------------------------- #
# Composite rating
# --------------------------------------------------------------------------- #
def no_data_rating():
    return {
        "rating": "No Data", "score": 0,
        "trend": "Unknown", "momentum": "Unknown", "timing": "Unknown",
        "macd_label": "Unknown",
        "trend_score": 0, "momentum_score": 0, "timing_score": 0,
        "volume_score": 0, "tt_pass": None, "setups": [],
        "reason": "Insufficient price history to compute indicators.",
    }


def score_to_rating(score):
    if score >= STRONG_BUY:
        return "Strong Buy"
    if score >= BUY:
        return "Buy"
    if score > SELL:
        return "Hold"
    if score > STRONG_SELL:
        return "Sell"
    return "Strong Sell"


RATING_SCORE = {"Strong Buy": 2, "Buy": 1, "Hold": 0, "Sell": -1,
                "Strong Sell": -2, "No Data": 0}


def evaluate(ind, i=-1, rs_rank=None):
    """Score bar ``i`` of an indicator bundle. Returns the rating dict."""
    n = ind["n"]
    if i < 0:
        i += n
    close = _at(ind["close"], i)
    sma50, sma150, sma200 = _at(ind["sma50"], i), _at(ind["sma150"], i), _at(ind["sma200"], i)
    if close is None or sma50 is None or sma200 is None:
        return no_data_rating()

    rsi14 = _at(ind["rsi14"], i)
    rsi2 = _at(ind["rsi2"], i)
    macd, sig, hist = _at(ind["macd"], i), _at(ind["macd_signal"], i), _at(ind["macd_hist"], i)
    hist_prev = _at(ind["macd_hist"], i - 3)
    adx, pdi, mdi = _at(ind["adx"], i), _at(ind["plus_di"], i), _at(ind["minus_di"], i)
    high52, low52 = _at(ind["high52"], i), _at(ind["low52"], i)
    sma200_prev = _at(ind["sma200"], i - MONTH)
    udv = _at(ind["udv"], i)
    obv, obv_sma = _at(ind["obv"], i), _at(ind["obv_sma"], i)
    rvol = _at(ind["rvol"], i)
    mom = _at(ind["mom_12_1"], i)

    uptrend = close > sma200
    setups = []
    sma200_rising = (sma200 > sma200_prev) if sma200_prev is not None else None

    # ---- Trend (±35) ---------------------------------------------------- #
    # Price vs 200 SMA, golden cross, rising 200 SMA, the 50/150/200 stack and
    # Minervini's ">= 30% above the 52-week low" test all showed a positive
    # forward-return edge; a bearish stack was the most negative trend state.
    t = 0
    t += 10 if uptrend else -10
    t += 8 if sma50 > sma200 else -8
    if sma200_rising is not None:
        t += 6 if sma200_rising else -6
    if sma150 is not None:
        if sma50 > sma150 > sma200:
            t += 6
        elif sma50 < sma150 < sma200:
            t -= 8
    if low52 is not None and close >= 1.30 * low52:
        t += 5

    # Minervini Trend Template (8 tests) -- reported, and feeds the LEAP screen.
    tt = None
    if sma150 is not None and high52 is not None and low52 is not None:
        tests = [
            close > sma150 and close > sma200,
            sma150 > sma200,
            bool(sma200_rising),
            sma50 > sma150 and sma50 > sma200,
            close > sma50,
            close >= 1.30 * low52,
            close >= 0.75 * high52,
            (rs_rank or 0) >= 70,
        ]
        tt = sum(1 for x in tests if x)
        if tt == 8:
            setups.append("Trend Template")

    if _crossed_up(ind["sma50"], ind["sma200"], i, 10):
        setups.append("Golden Cross")
    elif _crossed_down(ind["sma50"], ind["sma200"], i, 10):
        setups.append("Death Cross")

    # ---- Momentum (±35) ------------------------------------------------- #
    # Cross-sectional RS rank is the single strongest factor in the backtest
    # (top quintile beat the universe by ~+0.9% per 6 months; bottom quintile
    # lagged by ~-1.1%), so it gets the largest weight.
    m = 0.0
    if rs_rank is not None:
        m += max(-25.0, min(25.0, (rs_rank - 50) / 50.0 * 25.0))
    elif mom is not None:
        m += max(-25.0, min(25.0, mom * 60.0))
    if mom is not None:
        m += 5 if mom > 0.30 else -5 if mom < -0.10 else 0
    near_high = high52 is not None and close >= 0.98 * high52
    if near_high:
        m += 5
        setups.append("52-Week High")
    macd_label = "Unknown"
    if macd is not None and sig is not None:
        macd_label = "Bullish" if macd > sig else "Bearish"
        if _crossed_up(ind["macd"], ind["macd_signal"], i, 5):
            setups.append("MACD Bull Cross")
        elif _crossed_down(ind["macd"], ind["macd_signal"], i, 5):
            setups.append("MACD Bear Cross")
    if rs_rank is not None and rs_rank >= 80:
        setups.append("Momentum Leader")

    # ---- Timing (−8..+12) ----------------------------------------------- #
    # Pullbacks inside an uptrend (RSI(14) < 45, RSI(2) < 10) had the best
    # 1-month hit rate (~63-66%) and stayed positive at 3 months. Oversold
    # readings in a *downtrend* were the worst setups, so they are penalised.
    ext = close / sma50 - 1.0
    tm = 0
    timing = "Neutral"
    if uptrend:
        if rsi14 is not None and rsi14 < 30:
            tm, timing = 12, "Pullback"
        elif rsi14 is not None and rsi14 < 45:
            tm, timing = 8 + (4 if (rsi2 is not None and rsi2 < 10) else 0), "Pullback"
        elif rsi2 is not None and rsi2 < 10:
            tm, timing = 4, "Pullback"
        elif ext > 0.15:
            timing = "Extended"
        elif near_high and (rvol or 0) >= 1.5:
            timing = "Breakout"
        elif near_high:
            timing = "At Highs"
        if timing == "Pullback" and (rs_rank or 0) >= 60:
            setups.append("Pullback Buy")
    else:
        if rsi14 is not None and rsi14 > 65:
            tm, timing = -8, "Bear Bounce"
            setups.append("Bear Bounce")
        elif rsi14 is not None and rsi14 < 25:
            tm, timing = -6, "Oversold"

    # ---- Volume (±8) ---------------------------------------------------- #
    v = 0
    if udv is not None:
        if udv >= 1.3:
            v += 5
        elif udv <= 0.77:
            v -= 5
        if udv >= 1.5:
            setups.append("Accumulation")
        elif udv <= 0.67:
            setups.append("Distribution")
    if obv is not None and obv_sma is not None and ind["volume"][i] > 0:
        v += 3 if obv > obv_sma else -3

    score = int(round(t + m + tm + v))
    score = max(-100, min(100, score))
    rating = score_to_rating(score)

    if sma50 > sma200 and uptrend:
        trend = "Bullish"
    elif sma50 < sma200 and not uptrend:
        trend = "Bearish"
    else:
        trend = "Mixed"

    if rsi14 is None:
        momentum = "Unknown"
    elif rsi14 < 30:
        momentum = "Oversold"
    elif rsi14 > 70:
        momentum = "Overbought"
    else:
        momentum = "Neutral"

    # Human-readable reason: the biggest drivers first.
    bits = []
    if tt is not None:
        bits.append(f"Trend Template {tt}/8")
    if rs_rank is not None:
        bits.append(f"RS rank {rs_rank}")
    if macd_label != "Unknown":
        bits.append(f"MACD {macd_label.lower()}")
    if adx is not None and pdi is not None and mdi is not None:
        bits.append(f"ADX {adx:.0f} ({'+' if pdi > mdi else '-'}DI leads)")
    if timing != "Neutral":
        bits.append(f"{timing.lower()}" + (f" (RSI {rsi14:.0f})" if rsi14 is not None else ""))
    if udv is not None:
        bits.append("accumulation" if udv >= 1.3 else "distribution" if udv <= 0.77 else "neutral volume")
    reason = (f"{'Uptrend' if uptrend else 'Downtrend'} vs 200-day SMA · "
              + " · ".join(bits) + ".")

    return {
        "rating": rating,
        "score": score,
        "trend": trend,
        "momentum": momentum,
        "timing": timing,
        "macd_label": macd_label,
        "trend_score": int(round(t)),
        "momentum_score": int(round(m)),
        "timing_score": int(round(tm)),
        "volume_score": int(round(v)),
        "tt_pass": tt,
        "sma200_rising": sma200_rising,
        "setups": setups,
        "reason": reason,
    }


def legacy_rating(ind, i=-1):
    """The original EMA-50/200 + RSI(14) decision table (kept for backtests)."""
    n = ind["n"]
    if i < 0:
        i += n
    if n < 200 or i < 199:
        return "No Data"
    ef, es, r = ind["ema50"][i], ind["ema200"][i], _at(ind["rsi14"], i)
    if r is None:
        return "No Data"
    if ef > es:
        return "Strong Buy" if r < 30 else "Hold" if r > 70 else "Buy"
    return "Strong Sell" if r > 70 else "Hold" if r < 30 else "Sell"


# --------------------------------------------------------------------------- #
# Component strategies (used by the backtester to compare them individually)
# --------------------------------------------------------------------------- #
def component_signals(ind, i, rs_rank=None):
    """Boolean long/short signals from each single strategy, for comparison."""
    n = ind["n"]
    if i < 0:
        i += n
    close = _at(ind["close"], i)
    sma50, sma150, sma200 = _at(ind["sma50"], i), _at(ind["sma150"], i), _at(ind["sma200"], i)
    if close is None or sma200 is None or sma50 is None:
        return {}
    rsi14, rsi2 = _at(ind["rsi14"], i), _at(ind["rsi2"], i)
    macd, sig = _at(ind["macd"], i), _at(ind["macd_signal"], i)
    high52, low52 = _at(ind["high52"], i), _at(ind["low52"], i)
    sma200_prev = _at(ind["sma200"], i - MONTH)
    rvol = _at(ind["rvol"], i)
    ema50, ema200 = ind["ema50"][i], ind["ema200"][i]

    out = {
        "Legacy: EMA50>200 & RSI 30-70": (ema50 > ema200 and rsi14 is not None and 30 <= rsi14 <= 70),
        "Price > 200 SMA": close > sma200,
        "Golden Cross (50>200 SMA)": sma50 > sma200,
        "Trend Template (8/8)": bool(
            sma150 is not None and high52 is not None and low52 is not None
            and sma200_prev is not None
            and close > sma150 > sma200 and sma200 > sma200_prev
            and sma50 > sma150 and close > sma50
            and close >= 1.3 * low52 and close >= 0.75 * high52
            and (rs_rank or 0) >= 70),
        "RS rank >= 80 (momentum)": (rs_rank or 0) >= 80,
        "MACD bullish": (macd is not None and sig is not None and macd > sig),
        "Pullback: >200 SMA & RSI(2)<10": (close > sma200 and rsi2 is not None and rsi2 < 10),
        "Breakout: 52w high + RVOL>=1.5": (high52 is not None and close >= 0.98 * high52
                                           and (rvol or 0) >= 1.5),
    }
    return out


# --------------------------------------------------------------------------- #
# LEAP call screen
# --------------------------------------------------------------------------- #
def _norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(S, K, T, sigma, r=0.04, q=0.0):
    """Black-Scholes call price and delta. Returns (price, delta)."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        intrinsic = max(S - K, 0.0)
        return intrinsic, (1.0 if S > K else 0.0)
    sq = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / sq
    d2 = d1 - sq
    price = S * math.exp(-q * T) * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    delta = math.exp(-q * T) * _norm_cdf(d1)
    return price, delta


def leap_score(stock, detail=None):
    """Score a stock 0-100 as a LEAP *call* candidate from its signal record
    (``data/stocks.json`` shape) and detail record (``data/details.json``).

    A LEAP call is a 1-2+ year bet that a strong, steady uptrend continues, so
    the screen rewards: a rising 200-day trend with a full Trend Template, high
    relative strength, supportive fundamentals (analyst upside, revenue growth,
    earnings), *low* realised volatility (cheaper premium, smoother path), deep
    liquidity (tight LEAP markets) and an unextended entry.
    """
    detail = detail or {}
    price = stock.get("price")
    sma200 = stock.get("sma200")
    if not price or not sma200:
        return None

    checks = {}
    uptrend = price > sma200
    rising = bool(stock.get("sma200_rising"))
    tt = stock.get("tt_pass") or 0
    rs = stock.get("rs_rank")
    hv = stock.get("hv60")
    mcap = stock.get("market_cap") or 0
    timing = stock.get("timing_score") or 0

    # Trend (30): rising 200-day + Trend Template.
    trend_pts = (8 if uptrend else 0) + (7 if rising else 0) + round(15 * tt / 8)
    checks["trend"] = {"points": trend_pts, "max": 30,
                       "detail": f"{'Above' if uptrend else 'Below'} 200 SMA"
                                 f"{', rising' if rising else ', falling'} · Trend Template {tt}/8"}

    # Momentum (20): RS rank.
    mom_pts = round(20 * (rs or 0) / 99)
    checks["momentum"] = {"points": mom_pts, "max": 20,
                          "detail": f"RS rank {rs if rs is not None else '—'}"}

    # Fundamentals (20): analyst upside, revenue growth, profitability.
    a = detail.get("analyst") or {}
    upside = None
    if a.get("target_mean") and price:
        upside = (a["target_mean"] / price - 1.0) * 100.0
    rev_growth = revenue_growth_yoy(detail.get("earnings") or [])
    eps = detail.get("eps")
    fpe = detail.get("forward_pe")
    f_pts = 0
    if upside is not None:
        f_pts += 8 if upside >= 20 else 5 if upside >= 10 else 2 if upside >= 0 else 0
    if rev_growth is not None:
        f_pts += 6 if rev_growth >= 15 else 4 if rev_growth >= 5 else 2 if rev_growth >= 0 else 0
    if eps is not None and eps > 0:
        f_pts += 6 if (fpe is not None and 0 < fpe < 40) else 3
    checks["fundamentals"] = {
        "points": f_pts, "max": 20,
        "detail": " · ".join(x for x in [
            f"analyst upside {upside:+.0f}%" if upside is not None else None,
            f"revenue {rev_growth:+.0f}% YoY" if rev_growth is not None else None,
            (f"fwd P/E {fpe:.0f}" if fpe else "profitable") if eps and eps > 0 else "unprofitable",
        ] if x) or "no fundamentals",
    }

    # Volatility (15): lower realised vol = cheaper, steadier LEAPs.
    if hv is None:
        v_pts = 5
    else:
        v_pts = 15 if hv <= 25 else 11 if hv <= 35 else 7 if hv <= 50 else 3 if hv <= 70 else 0
    checks["volatility"] = {"points": v_pts, "max": 15,
                            "detail": f"60-day realised vol {hv:.0f}%" if hv is not None else "no vol data"}

    # Liquidity (10): big caps have the deepest LEAP markets.
    l_pts = 10 if mcap >= 50e9 else 7 if mcap >= 10e9 else 4 if mcap >= 2e9 else 0
    checks["liquidity"] = {"points": l_pts, "max": 10,
                           "detail": f"market cap ${mcap / 1e9:.0f}B" if mcap else "no market cap"}

    # Timing (5): don't pay up for an extended name.
    t_pts = 5 if timing > 0 else 3 if timing == 0 else 0
    checks["timing"] = {"points": t_pts, "max": 5,
                        "detail": stock.get("timing") or "—"}

    total = sum(c["points"] for c in checks.values())

    # Hard gates for a "LEAP Buy".
    gates_ok = (uptrend and rising and tt >= 7 and (rs or 0) >= 70 and mcap >= 2e9
                and stock.get("rating") in ("Buy", "Strong Buy")
                and (hv is None or hv <= 70))
    if total >= LEAP_BUY and gates_ok:
        rating = "LEAP Buy"
    elif total >= LEAP_WATCH and uptrend:
        rating = "Watch"
    else:
        rating = None

    return {
        "leap_score": total,
        "leap_rating": rating,
        "checks": checks,
        "analyst_upside_pct": round(upside, 1) if upside is not None else None,
        "revenue_growth_yoy": round(rev_growth, 1) if rev_growth is not None else None,
    }


def revenue_growth_yoy(earnings):
    """% growth of the latest quarter's revenue vs the same quarter a year
    earlier (needs >= 5 quarters, newest first). ``None`` if unavailable."""
    if not earnings or len(earnings) < 5:
        return None
    cur, prev = earnings[0].get("revenue"), earnings[4].get("revenue")
    if not cur or not prev or prev <= 0:
        return None
    return (cur / prev - 1.0) * 100.0


# Target deltas for the three suggested contracts.
LEAP_ROLES = [
    ("Stock replacement", 0.80, "Deep in-the-money: moves ~80% like the shares, "
                               "least time-value decay, cheapest per unit of delta."),
    ("Balanced", 0.65, "Moderately in-the-money: more leverage than stock "
                       "replacement, still mostly intrinsic value."),
    ("Aggressive", 0.50, "At-the-money: maximum leverage, all time value — "
                         "needs a decisive move to pay off."),
]


def pick_leap_contracts(price, chains, hv_pct=None, r=0.04, div_yield=0.0,
                        min_oi=25, max_spread=0.20):
    """Choose suggested LEAP calls from option chains.

    ``chains`` is ``[{"expiry": "YYYY-MM-DD", "dte": int, "calls": [...]}]``
    where each call has strike/bid/ask/last/iv/oi/volume/contract keys.
    Returns a list of contract dicts (one per role per expiry) sorted by expiry
    then role.
    """
    out = []
    sigma_fallback = (hv_pct or 30.0) / 100.0
    for ch in chains:
        T = ch["dte"] / 365.0
        cands = []
        for c in ch["calls"]:
            K = c.get("strike")
            bid, ask = c.get("bid") or 0.0, c.get("ask") or 0.0
            if not K or K <= 0:
                continue
            mid = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (c.get("last") or 0.0)
            if mid <= 0:
                continue
            iv = c.get("iv")
            sigma = iv if (iv and 0.02 < iv < 5.0) else sigma_fallback
            _, delta = bs_call(price, K, T, sigma, r, div_yield)
            spread = (ask - bid) / mid if (bid > 0 and ask > 0) else None
            intrinsic = max(price - K, 0.0)
            extrinsic = max(mid - intrinsic, 0.0)
            cands.append({
                "expiry": ch["expiry"], "dte": ch["dte"], "strike": K,
                "bid": round(bid, 2), "ask": round(ask, 2), "mid": round(mid, 2),
                "last": c.get("last"), "iv": round(sigma * 100.0, 1),
                "delta": round(delta, 2), "oi": c.get("oi"), "volume": c.get("volume"),
                "spread_pct": round(spread * 100.0, 1) if spread is not None else None,
                "intrinsic": round(intrinsic, 2), "extrinsic": round(extrinsic, 2),
                "extrinsic_pct": round(extrinsic / price * 100.0, 2),
                "breakeven": round(K + mid, 2),
                "breakeven_pct": round((K + mid) / price * 100.0 - 100.0, 1),
                "leverage": round(price / mid, 1),
                "cost": round(mid * 100.0, 0),
                "contract": c.get("contract"),
                "liquid": bool((c.get("oi") or 0) >= min_oi and spread is not None
                               and spread <= max_spread),
            })
        if not cands:
            continue
        liquid = [c for c in cands if c["liquid"]] or cands
        used = set()
        for role, target, why in LEAP_ROLES:
            best = min(liquid, key=lambda c: abs(c["delta"] - target))
            if abs(best["delta"] - target) > 0.12 or best["strike"] in used:
                continue
            used.add(best["strike"])
            out.append({"role": role, "target_delta": target, "why": why, **best})
    return out
