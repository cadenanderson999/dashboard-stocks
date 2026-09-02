# 📈 Stock Signals — Robinhood 100 + S&P 500

A free, static website covering the **Robinhood Top 100** *and* the **S&P 500**
(~500+ unique tickers), assigning each one a **Buy / Sell rating** from a
backtested, multi-factor composite of four strategy families:

- **Trend** — Minervini's *Trend Template* (price > 50 > 150 > 200-day SMA,
  rising 200 SMA, ≥ 30% above the 52-week low, within 25% of the high)
- **Momentum** — cross-sectional **relative-strength rank** (1–99) + 12-1 momentum
- **Timing** — *pullback-in-uptrend* entries (RSI 14 / RSI 2), bear-bounce penalty
- **Volume** — accumulation vs. distribution (up/down volume, OBV)

A second page screens for **LEAP call options** and suggests specific contracts.
It also reports **relative volume (RVOL)** so you can spot unusual activity, and
tags every ticker by **list** (Robinhood 100 / S&P 500) and **GICS sector** so
you can slice the universe.

Data is fetched from Yahoo Finance (free, no API key) by a Python script, and a
scheduled **GitHub Action** refreshes it every weekday and deploys the site to
**GitHub Pages**.

> ⚠️ **Not financial advice.** These are mechanical signals from technical
> indicators, for educational purposes only. Options can expire worthless.

---

## How the rating works

Every ticker gets a **composite score from about −90 to +90**, the sum of four
signed sub-scores computed on daily data (`scripts/strategies.py`):

| Family | Range | What moves it |
|---|---|---|
| **Trend** | ±35 | price vs 200 SMA (±10), 50 > 200 SMA (±8), 200 SMA rising over the last month (±6), 50 > 150 > 200 stack (+6 / bearish stack −8), ≥ 30% above the 52-week low (+5) |
| **Momentum** | ±35 | **RS rank** 1–99 scaled to ±25, 12-1 momentum > 30% (+5) / < −10% (−5), within 2% of the 52-week high (+5) |
| **Timing** | −8..+12 | *in an uptrend*: RSI(14) < 30 (+12), RSI(14) < 45 (+8, +4 more if RSI(2) < 10), RSI(2) < 10 (+4). *In a downtrend*: RSI(14) > 65 "bear bounce" (−8), RSI(14) < 25 (−6) |
| **Volume** | ±8 | 50-day up-volume / down-volume ≥ 1.3 (+5) / ≤ 0.77 (−5); OBV above its 20-day average (±3) |

| Score | Rating |
|---|---|
| ≥ 60 | **Strong Buy** |
| 25 – 59 | **Buy** |
| −19 – 24 | **Hold** |
| −49 – −20 | **Sell** |
| ≤ −50 | **Strong Sell** |

The **RS rank** is IBD-style: `2·r(3m) + r(6m) + r(9m) + r(12m)`, percentile-ranked
across the whole universe each run, so it is *relative* strength rather than
absolute. The Timing column labels the entry (Pullback, At Highs, Breakout,
Extended, Bear Bounce, Oversold) and each row's rating tooltip lists the named
setups that fired (Trend Template, Momentum Leader, Pullback Buy, Golden /
Death Cross, MACD cross, Accumulation / Distribution…). The detail page shows
the four sub-scores as bars plus MACD, ADX, ATR and a 2×ATR stop.

Thresholds and weights live at the top of `scripts/strategies.py`.

### Why these strategies (backtest)

`scripts/backtest.py` scores every ticker each month with the exact same code
the site uses and records the *forward* 1- and 3-month returns. On five years
of daily S&P 500 data (Feb 2013 – Feb 2018, 505 tickers, 45 monthly rebalances)
the composite ranks stocks correctly at every tier, while the old EMA-50/200 +
RSI table put 62% of names in "Buy" and its "Strong Buy" tier actually lagged:

| Rating tier | Composite (new): 3-mo excess vs. universe | Legacy: 3-mo excess vs. universe |
|---|---|---|
| Strong Buy | **+0.72%** (CAGR 13.1%, Sharpe 1.13) | −0.67% (CAGR 7.0%, Sharpe 0.60) |
| Buy | +0.05% | +0.18% |
| Hold | +0.05% | +0.45% |
| Sell | −0.24% | −0.37% |
| Strong Sell | **−0.72%** (CAGR 5.6%, max DD −24%) | −2.21% (only 1% of names) |

(Equal-weight universe: CAGR 10.1%, Sharpe 0.97.) Per-component results drove
the weights: RS rank ≥ 80 (+0.5% / 3 mo, +0.9% / 6 mo) and pullbacks inside
uptrends (RSI 14 < 45: 63% one-month hit rate) had the clearest edge; MACD
state, ADX, volume breakouts and "over-extension" penalties had none, so they
are shown as labels but don't move the score. Caveats: the sample is a bull
market with survivorship bias, so absolute returns are flattering — treat the
*ranking* between tiers as the evidence, not the CAGR.

Run it yourself — locally or via the **Backtest rating strategies** GitHub
Action (Actions tab → Run workflow), which downloads the live universe:

```bash
python scripts/backtest.py --live --years 6            # Yahoo Finance
python scripts/backtest.py --csv all_stocks_5yr.csv     # any long-format OHLCV CSV
python scripts/backtest.py --sample                     # smoke test, no network
```

## LEAP calls (second tab)

The **LEAP Calls** page (`leaps.html`) screens for stocks that suit a
long-dated call option (≥ 1 year to expiry) and suggests contracts. Each ticker
is scored 0–100 by `strategies.leap_score`:

| Check | Points | Looks at |
|---|---|---|
| Trend | 30 | above a **rising** 200-day SMA, Trend Template criteria passed |
| Momentum | 20 | RS rank |
| Fundamentals | 20 | analyst mean-target upside, YoY revenue growth, profitability / forward P/E |
| Volatility | 15 | 60-day realised vol (lower = cheaper premium, steadier path) |
| Liquidity | 10 | market cap (deep, tight LEAP markets) |
| Timing | 5 | unextended entry (pullback preferred) |

**LEAP Buy** needs ≥ 70 *and* the hard gates (uptrend, RS ≥ 60, a Buy-or-better
rating, cap ≥ $2B, vol ≤ 70%); ≥ 55 in an uptrend is **Watch**. For candidates,
`scripts/generate_leaps.py` pulls the option chain from Yahoo, keeps expiries
≥ 365 days out (nearest + farthest), computes a Black-Scholes delta from each
contract's implied volatility, and suggests three calls per expiry:

- **Stock replacement** (Δ ≈ 0.80) — deep in-the-money, least time decay
- **Balanced** (Δ ≈ 0.65)
- **Aggressive** (Δ ≈ 0.50) — at-the-money, maximum leverage

with bid/ask/mid, cost per contract, IV, open interest, breakeven (price and %),
time-value cost and leverage. Contracts with open interest < 25 or a bid/ask
spread > 20% of mid are flagged. The page also shows LEAP-ATM implied vol
against realised vol (a ratio well above 1 means the options are pricey).
Candidates get a **LEAP** badge on the Signals table and a card on their detail
page. Tunables (`LEAP_MIN_DTE`, `MAX_CHAINS`, `RISK_FREE`, …) are at the top of
`scripts/generate_leaps.py`; output is `data/leaps.json`.

## The stock universe

The universe is the **union of two lists**, and each ticker is tagged with the
list(s) it belongs to plus its GICS sector:

- **Robinhood 100** — a curated list in `scripts/tickers.py`
  (`ROBINHOOD_TOP_100`), with sectors in `RH_SECTORS`.
- **S&P 500** — fetched **live from Wikipedia** at generation time (current
  constituents + sectors). If that fetch ever fails, a committed static snapshot
  (`SP500_FALLBACK` in `scripts/tickers.py`) is used instead, so generation never
  breaks. The live list is authoritative and stays current automatically.

In the UI, the **List** and **Sector** dropdowns filter the table, each row shows
small **RH / S&P badges**, and a "Showing X of Y" count reflects the active
filters (which compose with search, rating, and sorting).

## RVOL Scanner (second tab)

The **RVOL Scanner** page (`scanner.html`) is a market-wide scan, separate from
the curated dashboard. It lists **liquid NYSE + Nasdaq common stocks trading at
more than 2× their normal volume** (today's volume ÷ trailing 50-day average),
and shows the **same full signal set as the Signals page** for each hit (price,
market cap, P/E, EMAs, trend, RSI, momentum, RVOL 30d, surge days, today's RVOL,
sector, and Buy/Sell rating).

It runs in two passes: a fast volume-only scan finds the RVOL hits, then the top
`MAX_RESULTS` (by today's RVOL) are enriched with full history + fundamentals so
the heavier work only touches the names that matter.

## Per-stock detail page

Clicking any ticker opens `stock.html?symbol=XXX`, a detail page with the full
signal set plus extended fundamentals: an interactive TradingView 4-hour chart,
day/52-week ranges,
open/previous close, forward P/E, price-to-book, EPS, beta, dividend, average
volumes, shares outstanding, sector/industry, and a website link. It also shows
**analyst price targets** (mean/median/high/low with a range bar + recommendation),
the **next earnings date**, and a **quarterly earnings history** table (revenue,
net income, diluted EPS). These extras are generated into `data/details.json`
(keyed by symbol) by `generate_data.py` from yfinance `.info` and the quarterly
income statement, alongside the last ~120 daily closes for the chart.

Each detail page also links out to the ticker's **OptionCharts** page for the
full options chain (open interest, put/call, etc.).

- **Symbol source:** the full, free [Nasdaq Trader symbol
  directory](https://ftp.nasdaqtrader.com/SymbolDirectory/) (`nasdaqlisted.txt` +
  `otherlisted.txt`), filtered to common stocks (no ETFs / test issues).
- **Liquidity filters** keep the results meaningful: price ≥ `MIN_PRICE` ($1) and
  ≥ `MIN_AVG_VOL` (50k avg daily shares). RVOL threshold is `RVOL_THRESHOLD` (2.0).
  All three are tunable at the top of `scripts/generate_rvol_scan.py`.
- Generated by `scripts/generate_rvol_scan.py` into `data/rvol_scan.json`; the
  GitHub Action runs it daily alongside the main dashboard. If the symbol
  directory is unreachable it falls back to the site's existing universe.

## Relative volume (RVOL)

Alongside the rating, each ticker shows how heavily it's been trading:

- For every day, **RVOL = that day's volume ÷ its trailing 50-day average
  volume** (1.0 = a normal day, 2.0 = twice normal).
- These daily RVOLs are aggregated over the **last 30 trading days** into two
  columns:
  - **RVOL 30d** — the *mean* RVOL (overall how busy vs. normal).
  - **Surge Days** — *count* of days with RVOL above the threshold (default
    **2×**), i.e. genuine volume spikes.

Tunable at the top of `scripts/generate_data.py` via `RVOL_AVG_WINDOW`,
`RVOL_LOOKBACK`, and `RVOL_THRESHOLD`. (Today's RVOL is also stored in the JSON
as `rvol_today` if you want to surface it.)

## Sorting & fundamentals

Every numeric column is sortable two ways:

- **Click any column header** to sort by it; click again to flip ascending /
  descending.
- Or use the **“Sort by” dropdown + direction toggle** in the toolbar to pick a
  metric (Price, Day %, Market Cap, P/E, EMA 50/200, RSI, RVOL 30d, Surge Days,
  Rating, …) and the order.

The header and the dropdown stay in sync. The table also includes **Market Cap**
and trailing **P/E** for each ticker (fetched best-effort from Yahoo Finance;
tickers with no earnings show “—” for P/E).

### Numeric range filters

Both pages have a **Filters** button that opens a panel with a **min / max** for
every numeric column (Price, Day %, Market Cap, P/E, EMA 50/200, RSI, RVOL 30d,
Surge Days — plus Today's RVOL on the Scanner). Filters apply live and stack, and
the button shows a count of how many are active. Inputs are metric-aware: RSI is
bounded 0–100, Surge Days 0–30, and **Market Cap accepts shorthand like `1M`,
`2.5B`, or `3T`**. Shared implementation lives in `assets/filters.js`.

---

## Project structure

```
dashboard-stocks/
├── index.html                 # the dashboard page (Signals tab)
├── leaps.html                 # LEAP call screen + suggested contracts
├── stock.html                 # per-stock detail page (?symbol=XXX)
├── assets/
│   ├── styles.css             # styling (shared)
│   ├── filters.js             # shared numeric range-filter panel
│   ├── app.js                 # dashboard: loads data/stocks.json (+ leaps.json badges)
│   ├── leaps.js               # LEAP page: loads data/leaps.json
│   └── stock.js               # detail page: loads details.json + leaps.json
├── data/
│   ├── stocks.json            # generated indicators + ratings
│   ├── leaps.json             # generated LEAP candidates + contracts
│   ├── rvol_scan.json         # generated RVOL > 2.0 scan results
│   └── details.json           # extended per-stock fundamentals
├── scripts/
│   ├── tickers.py             # Robinhood list + S&P 500 fallback
│   ├── strategies.py          # indicators, composite rating, LEAP screen
│   ├── generate_data.py       # fetches prices, computes signals + details
│   ├── generate_leaps.py      # LEAP screen + option-chain contract picker
│   ├── generate_rvol_scan.py  # market-wide RVOL scan
│   └── backtest.py            # compares the strategies on forward returns
├── requirements.txt
└── .github/workflows/
    ├── update-and-deploy.yml  # daily refresh + Pages deploy
    └── backtest.yml           # on-demand strategy backtest
```

---

## Run it locally

```bash
# 1. (Optional) create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Fetch live data and compute signals, then the LEAP screen
python scripts/generate_data.py
python scripts/generate_leaps.py
#    ...or, with no internet, generate placeholder data:
python scripts/generate_data.py --sample && python scripts/generate_leaps.py --sample

# 4. Serve the site (any static server works)
python -m http.server 8000
# then open http://localhost:8000
```

> Open `index.html` via a local server (not `file://`) so the browser can
> `fetch()` the JSON.

---

## Deploy to GitHub Pages (one-time setup)

The included workflow handles everything, but you must enable Pages once:

1. Push this repo to GitHub (default branch **`main`**).
2. Go to **Settings → Pages**.
3. Under **Build and deployment → Source**, choose **GitHub Actions**.
4. Open the **Actions** tab and run **“Update data & deploy to Pages”**
   (or just wait for the daily schedule / next push to `main`).

Your site will be published at
`https://<your-username>.github.io/dashboard-stocks/`.

The workflow runs:
- on every push to `main`,
- on a weekday schedule (22:00 UTC, after US market close),
- and manually via **workflow_dispatch**.

> Scheduled workflows and Pages deploy from the **default branch**, so merge
> your work into `main` for automatic refreshes to kick in.

---

## Notes & caveats

- Yahoo Finance is an unofficial/free data source; occasional flakiness or a
  missing ticker is expected. The generator skips bad tickers and keeps going.
- A 200-day EMA needs ~200 trading days of history; newly listed tickers will
  show limited indicators until they have enough data.
- If a live fetch returns nothing (e.g. blocked network), the script writes
  clearly-labelled **sample data** so the page still renders, and the site shows
  a warning banner.
