"""The list of the top ~100 most popular stock tickers on Robinhood.

This is a curated list of widely-held / high-volume names that have
historically dominated Robinhood's "Top 100 Most Popular" list. Tickers are
plain US-exchange symbols compatible with Yahoo Finance (used by yfinance).

Edit this list freely -- the generator will simply fetch whatever is here.
"""

ROBINHOOD_TOP_100 = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "AVGO", "NFLX",
    # Semiconductors / AI
    "AMD", "INTC", "QCOM", "MU", "TXN", "TSM", "ARM", "SMCI", "MRVL", "ON",
    # Software / cloud / growth
    "ORCL", "ADBE", "CRM", "CSCO", "IBM", "PLTR", "SNOW", "CRWD", "NET", "DDOG",
    "MDB", "SHOP", "UBER", "ABNB", "COIN", "HOOD", "SOFI", "RBLX", "DKNG", "ROKU",
    # Social / fintech / consumer internet
    "PINS", "SNAP", "PYPL", "DIS", "T",
    # Autos / EV
    "F", "GM", "RIVN", "LCID", "NIO", "LI", "XPEV",
    # Financials
    "JPM", "BAC", "WFC", "C", "GS", "MS", "V", "MA", "AXP", "SCHW",
    # Consumer staples / retail
    "KO", "PEP", "WMT", "COST", "MCD", "SBUX", "NKE", "HD", "LOW", "TGT",
    # Energy
    "XOM", "CVX", "OXY",
    # Healthcare / pharma
    "UNH", "JNJ", "PFE", "MRNA", "LLY", "ABBV", "CVS",
    # Airlines / travel
    "AAL", "DAL", "UAL", "CCL", "BA",
    # Meme / high-retail-interest
    "GME", "AMC", "BB", "MARA", "RIOT", "CLF", "GE", "PLUG", "CHPT", "QS",
    "VZ", "ET", "WBD",
]

# De-duplicate while preserving order, just in case the list is edited.
_seen = set()
ROBINHOOD_TOP_100 = [t for t in ROBINHOOD_TOP_100 if not (t in _seen or _seen.add(t))]
