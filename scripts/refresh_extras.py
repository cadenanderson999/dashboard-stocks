"""Budgeted quote snapshots."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import yfinance as yf
import market_data as md


def quotes():
    doc = md.read_json(md.ROOT/'data/stocks.json')
    if doc.get('is_sample') is not False:
        raise RuntimeError('Live stock snapshot required')
    cache = md.store()
    now = datetime.now(timezone.utc)
    # Preserve at least 2,000 operations for completed-session data and new entrants.
    state = cache.get('gateway')
    used = state.get('used', 0) if state.get('day') == now.date().isoformat() else 0
    allowance = min(800, max(0, cache.limit-used-2000))
    rows = sorted(doc['stocks'], key=lambda s: (s.get('quote') or {}).get('as_of', ''))
    for row in rows[:allowance]:
        gateway = cache.get('gateway')
        if gateway.get('day') == now.date().isoformat() and gateway.get('used', 0) >= cache.limit-2000:
            break
        symbol = row['symbol']
        def acquire():
            frame = yf.Ticker(symbol).history(period='5d', interval='5m', auto_adjust=False,
                                             prepost=False, raise_errors=True, timeout=15)
            frame = frame.dropna(subset=['Close'])
            if frame.empty:
                raise md.FetchError('empty_response')
            value = float(frame['Close'].iloc[-1])
            if not math.isfinite(value) or value <= 0:
                raise md.FetchError('empty_response')
            last = frame.index[-1]
            earlier = frame[frame.index.date < last.date()]
            previous = float(earlier['Close'].iloc[-1]) if not earlier.empty else None
            return {'price':value, 'as_of':last.isoformat(),
                    'change_pct':(value/previous-1)*100 if previous else None}
        data, quality = cache.fetch('quote:'+symbol, acquire, 3600)
        if data:
            row['quote'] = dict(data, status=quality['status'])
    doc['quote_refresh_at'] = now.isoformat()
    md.atomic_json(md.ROOT/'data/stocks.json', doc)
    cache.report('quotes')

if __name__ == '__main__':
    import sys
    {'quotes':quotes}[sys.argv[1]]()
