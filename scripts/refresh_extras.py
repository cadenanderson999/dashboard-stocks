"""Budgeted quote snapshots."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import math
import yfinance as yf
import market_data as md


def earnings_priority():
    """Retry overdue calendar/results pairs before ordinary snapshot collection."""
    from generate_data import fetch_fundamentals, build_detail
    doc = md.read_json(md.ROOT / 'data/stocks.json')
    details = md.read_json(md.ROOT / 'data/details.json')
    if doc.get('is_sample') is not False:
        return
    cache = md.store()
    now = cache.clock()
    today = datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date().isoformat()
    detail_rows = details.setdefault('stocks', {})
    queue = []

    def latest(rows):
        return max((r.get('period') or '' for r in rows), default='')

    for row in doc.get('stocks', []):
        symbol = row['symbol']
        detail = detail_rows.get(symbol, {})
        key = 'earnings_pending:' + symbol
        pending = cache.get(key)
        next_date = detail.get('next_earnings') or row.get('next_earnings')
        try:
            overdue = bool(next_date and datetime.fromisoformat(next_date).date().isoformat() < today)
        except (ValueError, TypeError):
            overdue = False
        if overdue and not pending:
            pending = {'due_date': next_date, 'baseline_period': latest(detail.get('earnings', []))}
            cache.put(key, pending)
        # Six-hour cadence prevents repeated force refreshes; least recently
        # attempted first keeps a persistently stale provider date from starving others.
        if pending and now - pending.get('last_attempt', 0) >= 6 * 3600:
            queue.append((pending.get('last_attempt', 0), pending['due_date'], symbol, row, pending))

    attempted = 0
    for _, _, symbol, row, pending in sorted(queue, key=lambda item: item[:3])[:50]:
        gateway = cache.get('gateway')
        day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
        used = gateway.get('used', 0) if gateway.get('day') == day else 0
        if used >= cache.limit - 2000 or gateway.get('blocked_until', 0) > now:
            break
        pending['last_attempt'] = now
        cache.put('earnings_pending:' + symbol, pending)
        result = fetch_fundamentals([symbol], force=True)[symbol]
        attempted += 1
        detail = detail_rows.setdefault(symbol, {})
        updated = build_detail(result['info'], result['earnings'])
        # Preserve the published snapshot too, including when the SQLite cache
        # was evicted or a partial response has no usable results.
        for field, value in updated.items():
            if field == 'analyst':
                detail.setdefault(field, {}).update({k: v for k, v in value.items() if v is not None})
            elif value is not None and (field != 'earnings' or value):
                detail[field] = value
        for field in ('pe', 'market_cap'):
            if result.get(field) is not None:
                row[field] = result[field]
        row['next_earnings'] = detail.get('next_earnings')
        quality = row.setdefault('data_quality', {})
        quality['fundamentals'] = result['quality']
        quality['missing_fields'] = result['missing_fields']
        detail['data_quality'] = quality
        # A new future date alone is not proof that quarterly results arrived.
        resolved = (detail.get('next_earnings', '') or '') >= today and latest(detail.get('earnings', [])) > pending['baseline_period']
        cache.put('earnings_pending:' + symbol, {} if resolved else pending)
    doc['earnings_priority'] = {'checked_at': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                               'eligible': len(queue), 'attempted': attempted}
    md.atomic_json(md.ROOT / 'data/stocks.json', doc)
    md.atomic_json(md.ROOT / 'data/details.json', details)
    cache.report('earnings')


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
