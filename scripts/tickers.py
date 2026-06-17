"""Stock universes for the dashboard.

This module is pure data (no network, no heavy imports) so it can be loaded in
any context. It exposes three things:

  * ``ROBINHOOD_TOP_100`` -- the curated list of popular Robinhood tickers.
  * ``RH_SECTORS``        -- GICS sector for each Robinhood ticker (used so RH
                             names always have a sector, even ones not in the
                             S&P 500, and as a fallback if the live S&P fetch
                             can't supply one).
  * ``SP500_FALLBACK``    -- a static snapshot of the S&P 500 grouped by sector.
                             Only used if the live Wikipedia fetch fails; the
                             live fetch (see scripts/generate_data.py) is the
                             authoritative, always-current source.

GICS sector names used throughout:
  Information Technology, Communication Services, Consumer Discretionary,
  Consumer Staples, Energy, Financials, Health Care, Industrials, Materials,
  Real Estate, Utilities.

Note: tickers use Yahoo Finance format, where a dot becomes a dash
(e.g. BRK.B -> BRK-B).
"""

# --------------------------------------------------------------------------- #
# Robinhood Top 100
# --------------------------------------------------------------------------- #
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

# GICS sector for every Robinhood ticker above.
RH_SECTORS = {
    "AAPL": "Information Technology", "MSFT": "Information Technology",
    "NVDA": "Information Technology", "AMZN": "Consumer Discretionary",
    "GOOGL": "Communication Services", "GOOG": "Communication Services",
    "META": "Communication Services", "TSLA": "Consumer Discretionary",
    "AVGO": "Information Technology", "NFLX": "Communication Services",
    "AMD": "Information Technology", "INTC": "Information Technology",
    "QCOM": "Information Technology", "MU": "Information Technology",
    "TXN": "Information Technology", "TSM": "Information Technology",
    "ARM": "Information Technology", "SMCI": "Information Technology",
    "MRVL": "Information Technology", "ON": "Information Technology",
    "ORCL": "Information Technology", "ADBE": "Information Technology",
    "CRM": "Information Technology", "CSCO": "Information Technology",
    "IBM": "Information Technology", "PLTR": "Information Technology",
    "SNOW": "Information Technology", "CRWD": "Information Technology",
    "NET": "Information Technology", "DDOG": "Information Technology",
    "MDB": "Information Technology", "SHOP": "Information Technology",
    "UBER": "Industrials", "ABNB": "Consumer Discretionary",
    "COIN": "Financials", "HOOD": "Financials", "SOFI": "Financials",
    "RBLX": "Communication Services", "DKNG": "Consumer Discretionary",
    "ROKU": "Communication Services", "PINS": "Communication Services",
    "SNAP": "Communication Services", "PYPL": "Financials",
    "DIS": "Communication Services", "T": "Communication Services",
    "F": "Consumer Discretionary", "GM": "Consumer Discretionary",
    "RIVN": "Consumer Discretionary", "LCID": "Consumer Discretionary",
    "NIO": "Consumer Discretionary", "LI": "Consumer Discretionary",
    "XPEV": "Consumer Discretionary", "JPM": "Financials", "BAC": "Financials",
    "WFC": "Financials", "C": "Financials", "GS": "Financials", "MS": "Financials",
    "V": "Financials", "MA": "Financials", "AXP": "Financials", "SCHW": "Financials",
    "KO": "Consumer Staples", "PEP": "Consumer Staples", "WMT": "Consumer Staples",
    "COST": "Consumer Staples", "MCD": "Consumer Discretionary",
    "SBUX": "Consumer Discretionary", "NKE": "Consumer Discretionary",
    "HD": "Consumer Discretionary", "LOW": "Consumer Discretionary",
    "TGT": "Consumer Staples", "XOM": "Energy", "CVX": "Energy", "OXY": "Energy",
    "UNH": "Health Care", "JNJ": "Health Care", "PFE": "Health Care",
    "MRNA": "Health Care", "LLY": "Health Care", "ABBV": "Health Care",
    "CVS": "Health Care", "AAL": "Industrials", "DAL": "Industrials",
    "UAL": "Industrials", "CCL": "Consumer Discretionary", "BA": "Industrials",
    "GME": "Consumer Discretionary", "AMC": "Communication Services",
    "BB": "Information Technology", "MARA": "Information Technology",
    "RIOT": "Information Technology", "CLF": "Materials", "GE": "Industrials",
    "PLUG": "Industrials", "CHPT": "Industrials", "QS": "Consumer Discretionary",
    "VZ": "Communication Services", "ET": "Energy", "WBD": "Communication Services",
}

# --------------------------------------------------------------------------- #
# S&P 500 static fallback (grouped by GICS sector)
#
# Used ONLY if the live Wikipedia fetch fails. It is a point-in-time snapshot and
# may drift from the real index over time -- the live fetch is authoritative.
# Any ticker here that no longer trades is simply skipped during data generation.
# --------------------------------------------------------------------------- #
SP500_FALLBACK = {
    "Information Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "CSCO", "ACN", "ADBE", "AMD",
        "TXN", "QCOM", "INTU", "IBM", "NOW", "AMAT", "ADI", "MU", "LRCX", "KLAC",
        "SNPS", "CDNS", "INTC", "ANET", "ROP", "MSI", "NXPI", "MCHP", "FTNT", "ADSK",
        "TEL", "CTSH", "APH", "GLW", "HPQ", "IT", "KEYS", "HPE", "MPWR", "CDW",
        "ON", "STX", "WDC", "ANSS", "GEN", "FSLR", "TYL", "PTC", "ZBRA", "JBL",
        "TDY", "TER", "AKAM", "JNPR", "SWKS", "NTAP", "EPAM", "TRMB", "QRVO", "FFIV",
        "SMCI", "DELL", "PLTR", "GDDY", "WDAY",
    ],
    "Communication Services": [
        "GOOGL", "GOOG", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
        "WBD", "EA", "TTWO", "OMC", "IPG", "LYV", "MTCH", "NWSA", "NWS", "FOXA",
        "FOX", "PARA",
    ],
    "Consumer Discretionary": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "BKNG", "TJX", "ORLY",
        "CMG", "MAR", "GM", "F", "HLT", "AZO", "ROST", "YUM", "DHI", "LEN",
        "NVR", "PHM", "RCL", "CCL", "NCLH", "EBAY", "APTV", "GPC", "ULTA", "BBY",
        "DRI", "LVS", "WYNN", "MGM", "EXPE", "POOL", "DPZ", "KMX", "TSCO", "GRMN",
        "LULU", "TPR", "RL", "HAS", "MHK", "WHR", "BWA", "CZR", "ABNB", "DECK",
        "LKQ",
    ],
    "Consumer Staples": [
        "PG", "KO", "PEP", "COST", "WMT", "MDLZ", "PM", "MO", "CL", "MNST",
        "KMB", "GIS", "STZ", "KHC", "SYY", "KDP", "KR", "HSY", "ADM", "KVUE",
        "EL", "MKC", "CHD", "CLX", "TSN", "CAG", "CPB", "K", "HRL", "SJM",
        "BG", "TAP", "BF-B", "LW", "DG", "DLTR", "TGT", "WBA",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "EOG", "SLB", "MPC", "PSX", "WMB", "OKE", "VLO",
        "OXY", "HES", "KMI", "FANG", "BKR", "HAL", "DVN", "TRGP", "CTRA", "APA",
        "EQT",
    ],
    "Financials": [
        "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "AXP", "SPGI",
        "BLK", "C", "SCHW", "CB", "PGR", "MMC", "FI", "BX", "ICE", "CME",
        "PNC", "USB", "AON", "COF", "TFC", "AJG", "MCO", "AFL", "MET", "TRV",
        "BK", "ALL", "AIG", "MSCI", "PRU", "AMP", "DFS", "FIS", "GPN", "NDAQ",
        "RJF", "HIG", "WTW", "ACGL", "FITB", "CINF", "MTB", "STT", "BRO", "NTRS",
        "RF", "CFG", "KEY", "HBAN", "SYF", "PFG", "L", "GL", "IVZ", "BEN",
        "CBOE", "JKHY", "MKTX", "ERIE",
    ],
    "Health Care": [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "AMGN", "PFE",
        "ISRG", "BSX", "MDT", "SYK", "GILD", "VRTX", "CI", "ELV", "REGN", "CVS",
        "BDX", "HCA", "MCK", "ZTS", "BMY", "EW", "HUM", "CNC", "A", "IQV",
        "IDXX", "GEHC", "RMD", "MTD", "BIIB", "DXCM", "WST", "COR", "RVTY", "BAX",
        "ZBH", "STE", "HOLX", "MRNA", "WAT", "MOH", "PODD", "ALGN", "COO", "VTRS",
        "CRL", "TECH", "INCY", "CAH", "DGX", "LH", "UHS", "HSIC", "DVA", "SOLV",
    ],
    "Industrials": [
        "GE", "CAT", "RTX", "HON", "UNP", "BA", "UPS", "ETN", "DE", "LMT",
        "ADP", "GD", "NOC", "WM", "ITW", "CSX", "EMR", "MMM", "NSC", "FDX",
        "GEV", "PH", "TT", "TDG", "CARR", "PCAR", "CMI", "JCI", "PAYX", "AME",
        "ROK", "OTIS", "FAST", "CTAS", "URI", "IR", "GWW", "CPRT", "EFX", "DAL",
        "VRSK", "WAB", "ODFL", "XYL", "RSG", "FTV", "DOV", "HWM", "BR", "LUV",
        "UAL", "PWR", "AXON", "HUBB", "J", "IEX", "SNA", "PNR", "NDSN", "JBHT",
        "SWK", "ROL", "EXPD", "TXT", "MAS", "GNRC", "CHRW", "ALLE", "AOS", "LDOS",
        "HII",
    ],
    "Materials": [
        "LIN", "SHW", "APD", "ECL", "FCX", "NEM", "NUE", "DOW", "DD", "CTVA",
        "PPG", "VMC", "MLM", "IFF", "ALB", "LYB", "STLD", "PKG", "AMCR", "IP",
        "BALL", "CF", "MOS", "FMC", "EMN", "CE", "AVY",
    ],
    "Real Estate": [
        "PLD", "AMT", "EQIX", "WELL", "SPG", "PSA", "O", "DLR", "CCI", "CBRE",
        "EXR", "VICI", "AVB", "IRM", "EQR", "SBAC", "INVH", "ARE", "VTR", "MAA",
        "ESS", "KIM", "UDR", "HST", "REG", "BXP", "FRT", "CPT", "DOC",
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "CEG", "AEP", "SRE", "D", "EXC", "XEL", "PEG",
        "ED", "PCG", "WEC", "EIX", "AEE", "DTE", "ETR", "FE", "PPL", "CNP",
        "CMS", "ATO", "AES", "NI", "LNT", "EVRG", "NRG", "PNW", "VST",
    ],
}


def sp500_fallback_map():
    """Flatten ``SP500_FALLBACK`` into ``{symbol: {"sector": str}}``."""
    out = {}
    for sector, syms in SP500_FALLBACK.items():
        for s in syms:
            out[s] = {"sector": sector, "name": None}
    return out


# De-duplicate the Robinhood list while preserving order, just in case.
_seen = set()
ROBINHOOD_TOP_100 = [t for t in ROBINHOOD_TOP_100 if not (t in _seen or _seen.add(t))]
