# 📈 Robinhood Top 100 — Buy / Sell Signals

A free, static website that lists the **top ~100 most popular stock tickers on
Robinhood** and assigns each one a **Buy / Sell rating** derived from two
classic technical indicators:

- **50 / 200 EMA** — the trend (golden cross vs. death cross)
- **Daily RSI(14)** — momentum (overbought vs. oversold)

It also reports **relative volume (RVOL)** so you can spot unusual activity.

Data is fetched from Yahoo Finance (free, no API key) by a Python script, and a
scheduled **GitHub Action** refreshes it every weekday and deploys the site to
**GitHub Pages**.

> ⚠️ **Not financial advice.** These are mechanical signals from two indicators,
> for educational purposes only.

---

## How the rating works

For each ticker, on the **daily** timeframe:

| Trend (EMA 50 vs 200) | RSI(14) | Rating | Why |
|---|---|---|---|
| Bullish (50 > 200) | < 30 (oversold) | **Strong Buy** | Uptrend + oversold dip |
| Bullish | 30–70 | **Buy** | Healthy uptrend |
| Bullish | > 70 (overbought) | **Hold** | Uptrend but extended |
| Bearish (50 < 200) | > 70 (overbought) | **Strong Sell** | Downtrend + overbought bounce |
| Bearish | 30–70 | **Sell** | Weak downtrend |
| Bearish | < 30 (oversold) | **Hold** | Downtrend but possible bounce |

Indicator parameters live at the top of `scripts/generate_data.py`
(`EMA_FAST`, `EMA_SLOW`, `RSI_PERIOD`) and the ticker list is in
`scripts/tickers.py` — edit either to taste.

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

---

## Project structure

```
dashboard-stocks/
├── index.html                 # the dashboard page
├── assets/
│   ├── styles.css             # styling
│   └── app.js                 # loads data/stocks.json, renders table
├── data/
│   └── stocks.json            # generated indicators + ratings
├── scripts/
│   ├── tickers.py             # the Top 100 ticker list
│   └── generate_data.py       # fetches prices, computes signals
├── requirements.txt
└── .github/workflows/
    └── update-and-deploy.yml  # daily refresh + Pages deploy
```

---

## Run it locally

```bash
# 1. (Optional) create a virtual environment
python3 -m venv .venv && source .venv/bin/activate

# 2. Install deps
pip install -r requirements.txt

# 3. Fetch live data and compute signals
python scripts/generate_data.py
#    ...or, with no internet, generate placeholder data:
python scripts/generate_data.py --sample

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
