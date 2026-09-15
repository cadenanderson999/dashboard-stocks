"""Budgeted quote snapshots and paginated US earnings discovery."""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import math
import re
import yfinance as yf
import market_data as md


def retention_end(event):
    day = date.fromisoformat(event)
    return day + timedelta(days=11 - day.weekday())


def calendar():
    today = datetime.now(ZoneInfo('America/New_York')).date()
    cache = md.store()
    key = 'earnings-calendar:' + today.isoformat()
    def acquire():
        events = []
        cal = yf.Calendars(start=today-timedelta(days=21), end=today+timedelta(days=7))
        complete = False
        for page in range(30):
            frame = cache.call(lambda: cal.get_earnings_calendar(filter_most_active=False,
                                    limit=100, offset=page*100))
            for symbol_index, row in frame.iterrows():
                symbol = str(row.get('Symbol', symbol_index)).replace('.', '-').upper()
                when = str(row.get('Event Start Date', ''))[:10]
                try:
                    event = date.fromisoformat(when)
                except ValueError:
                    continue
                if re.fullmatch(r'[A-Z0-9^-]{1,16}', symbol) and event-timedelta(days=7) <= today <= retention_end(when):
                    events.append({'symbol':symbol, 'date':when, 'name':str(row.get('Company', symbol)),
                                   'retain_until':retention_end(when).isoformat()})
            if len(frame) < 100:
                complete = True
                break
        return {'events':events, 'complete':complete}
    data, quality = cache.fetch(key, acquire, 86400, network=False)
    old = md.read_json(md.ROOT/'data/earnings_calendar.json')
    # Retain dated membership even during a provider failure or partial page set.
    events = {(e['symbol'], e['date']):e for e in old.get('events', [])
              if e.get('retain_until', '') >= today.isoformat()}
    for e in (data or {}).get('events', []):
        for old_key in list(events):
            if old_key[0] == e['symbol'] and old_key[1] >= today.isoformat() and old_key[1] != e['date']:
                del events[old_key]
        events[(e['symbol'], e['date'])] = e
    md.atomic_json(md.ROOT/'data/earnings_calendar.json', {'is_sample':False,
        'generated_at':quality.get('updated_at'), 'refresh_status':quality['status'],
        'complete':bool(data and data['complete'] and quality['status'] in ('fresh','cached')),
        'events':list(events.values())})
    cache.report('calendar')


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
    {'calendar':calendar, 'quotes':quotes}[sys.argv[1]]()
