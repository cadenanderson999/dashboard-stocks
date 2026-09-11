"""Persistent Yahoo data cache and a sequential, budgeted acquisition gateway.

Limits apply to high-level yfinance operations, which may issue multiple HTTP
requests internally. They are conservative controls, not a Yahoo quota claim.
"""
from __future__ import annotations

import hashlib
import json
import math
from numbers import Integral, Real
import os
import random
import sqlite3
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def clean(value):
    """Normalize provider numbers before writing strict JSON."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, bool):
        return value
    if isinstance(value, Integral):
        return int(value)
    if isinstance(value, Real):
        return float(value) if math.isfinite(value) else None
    return value


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, allow_nan=False))
    tmp.replace(path)


def expected_session(now=None):
    """Last completed NYSE session, including holidays and early closes.

    Wait 30 minutes after the close for end-of-day bars to settle.
    """
    import exchange_calendars as xcals
    import pandas as pd
    now = now or datetime.now(timezone.utc)
    cal = xcals.get_calendar('XNYS')
    day = pd.Timestamp(now.date())
    session = cal.date_to_session(day, direction='previous')
    if cal.session_close(session).to_pydatetime() + timedelta(minutes=30) > now:
        session = cal.previous_session(session)
    return session.strftime('%Y-%m-%d')


def price_valid_until():
    import exchange_calendars as xcals
    import pandas as pd
    cal = xcals.get_calendar('XNYS')
    session = cal.next_session(pd.Timestamp(expected_session()))
    return (cal.session_close(session).to_pydatetime() + timedelta(minutes=30)).isoformat()


class FetchError(Exception):
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def reason_for(exc):
    if isinstance(exc, FetchError):
        return exc.reason
    text = (type(exc).__name__ + ' ' + str(exc)).lower()
    if '429' in text or 'ratelimit' in text or 'too many requests' in text:
        return 'rate_limited'
    if 'timeout' in text or 'timed out' in text:
        return 'timeout'
    if '404' in text:
        return 'not_found'
    return 'provider_error'


class Store:
    def __init__(self, path=None, clock=time.time, sleep=time.sleep):
        path = Path(path or os.getenv('MARKET_CACHE', ROOT / '.cache/market.sqlite'))
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, value TEXT)')
        self.clock, self.sleep = clock, sleep
        self.events = []
        self.limit = int(os.getenv('YAHOO_DAILY_OPERATIONS', '5000'))
        self.interval = float(os.getenv('YAHOO_MIN_INTERVAL', '1.0'))

    def get(self, key):
        row = self.db.execute('SELECT value FROM cache WHERE key=?', (key,)).fetchone()
        return json.loads(row[0]) if row else {}

    def put(self, key, value):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO cache VALUES (?, ?)',
                            (key, json.dumps(value, allow_nan=False)))

    def call(self, operation):
        """Retry transient errors; persist budget and circuit state across scripts."""
        for attempt in range(3):
            now = self.clock()
            day = datetime.fromtimestamp(now, timezone.utc).date().isoformat()
            state = self.get('gateway')
            if state.get('day') != day:
                state.update(day=day, used=0)
            if state.get('used', 0) >= self.limit:
                raise FetchError('budget_exhausted')
            if state.get('blocked_until', 0) > now:
                raise FetchError('provider_cooldown')
            wait = max(0, state.get('next_at', 0) - now)
            if wait:
                self.sleep(wait)
            state['used'] = state.get('used', 0) + 1
            state['next_at'] = self.clock() + self.interval
            self.put('gateway', state)
            try:
                result = operation()
                state = self.get('gateway')
                state['rate_failures'] = 0
                self.put('gateway', state)
                return result
            except Exception as exc:
                reason = reason_for(exc)
                if reason == 'rate_limited':
                    state['rate_failures'] = state.get('rate_failures', 0) + 1
                    if state['rate_failures'] >= 3:
                        state['blocked_until'] = self.clock() + 900
                    self.put('gateway', state)
                if reason not in ('rate_limited', 'timeout', 'provider_error', 'empty_response') or attempt == 2:
                    raise FetchError(reason) from exc
                # Retry-After when the underlying exception exposes an HTTP response.
                headers = getattr(getattr(exc, 'response', None), 'headers', {}) or {}
                try:
                    retry_after = float(headers.get('Retry-After', 0))
                except (TypeError, ValueError):
                    retry_after = 0
                delay = max(retry_after, 5 * 2 ** attempt + random.uniform(0, 2))
                if delay > 60:
                    state['blocked_until'] = self.clock() + delay
                    self.put('gateway', state)
                    raise FetchError('provider_cooldown') from exc
                self.sleep(delay)

    def fetch(self, key, operation, ttl, valid=bool, force=False, network=True,
              preserve_none=False):
        old = self.get(key)
        now = self.clock()
        has_data = 'data' in old
        if not force and has_data and old.get('expires_at', 0) > now:
            self.events.append({'key': key, 'status': 'cached'})
            return old['data'], dict(old.get('quality', {}), status='cached',
                                     updated_at=old['updated_at'])
        if old.get('retry_at', 0) > now:
            reason = old.get('reason', 'retry_deferred')
        else:
            try:
                def checked():
                    data = clean(operation())
                    if not valid(data):
                        raise FetchError('empty_response')
                    return data
                data = self.call(checked) if network else checked()
                updated = datetime.fromtimestamp(self.clock(), timezone.utc).isoformat()
                quality = {}
                if preserve_none:
                    previous = old.get('data', {})
                    retained = [k for k, v in data.items() if v is None and previous.get(k) is not None]
                    data = {k: previous[k] if k in retained else v for k, v in data.items()}
                    previous_dates = old.get('quality', {}).get('field_updated_at', {})
                    quality = {'retained_fields': retained, 'field_updated_at': {
                        k: previous_dates.get(k, old.get('updated_at')) if k in retained else updated
                        for k in data}}
                self.put(key, {'data': data, 'updated_at': updated,
                               'expires_at': self.clock() + ttl, 'quality': quality})
                self.events.append({'key': key, 'status': 'fresh'})
                return data, dict(quality, status='fresh', updated_at=updated)
            except FetchError as exc:
                reason = exc.reason
                old.update(reason=reason, retry_at=now + (86400 if reason == 'not_found' else 900))
                self.put(key, old)
        meta = dict(old.get('quality', {}), status='stale' if has_data else 'missing',
                    reason=reason, updated_at=old.get('updated_at'))
        self.events.append(dict(key=key, **meta))
        return old.get('data'), meta

    def report(self, stage):
        atomic_json(ROOT / 'data' / f'health-{stage}.json', {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'counts': dict(Counter(e['status'] for e in self.events)),
            'reasons': dict(Counter(e['reason'] for e in self.events if e.get('reason'))),
            'operations': self.get('gateway').get('used', 0),
            'events': self.events,
        })


_store = None


def store():
    global _store
    if _store is None:
        _store = Store()
    return _store


def staggered_ttl(symbol, days):
    # Stable offsets spread initially simultaneous refreshes across the window.
    bucket = int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16) % days
    return (1 + bucket) * 86400


def yahoo():
    import yfinance as yf
    yf.set_tz_cache_location(str(ROOT / '.cache/yfinance'))
    return yf


def price_history(symbol, period='2y'):
    yf = yahoo()
    cache = store()
    target = expected_session()
    key = f'prices:{symbol}'
    old = cache.get(key).get('data') or {}
    sufficient = old.get('period') == '2y' or old.get('period') == period
    same_session = old.get('dates', [''])[-1:] == [target]

    def acquire():
        tk = yf.Ticker(symbol)
        # Fetch overlapping *raw* bars. Re-adjust the entire retained series
        # using Adj Close/Close so dividends do not create inconsistent scales.
        incremental = (sufficient and old.get('dates') and old.get('raw')
                       and cache.clock() - old.get('full_at', 0) < 30 * 86400)
        full_period = old.get('period', period) if sufficient else period
        kwargs = {'period': full_period}
        if incremental:
            kwargs = {'start': old['dates'][max(0, len(old['dates']) - 10)]}
        df = tk.history(**kwargs, interval='1d', auto_adjust=False,
                        actions=True, raise_errors=True, timeout=20)
        if df is None or df.empty:
            raise FetchError('empty_response')
        # A corporate action changes older adjustment factors: rebuild history.
        action_changed = False
        if incremental:
            for date, row in df.iterrows():
                d = date.strftime('%Y-%m-%d')
                if d > target:
                    continue
                if d > old['dates'][-1] and any(row.get(c, 0) != 0 for c in ('Dividends', 'Stock Splits')):
                    action_changed = True
                previous = old['raw'].get(d)
                if previous and row.get('Close', 0) > 0:
                    factor = row.get('Adj Close', row['Close']) / row['Close']
                    if not math.isclose(factor, previous[1] / previous[0], rel_tol=1e-6):
                        action_changed = True
        if action_changed:
            df = cache.call(lambda: tk.history(period=old.get('period', period),
                           interval='1d', auto_adjust=False, actions=True,
                           raise_errors=True, timeout=20))
            incremental = False
        sub = df[['Close', 'Adj Close', 'High', 'Low', 'Volume']].dropna()
        raw = dict(old.get('raw', {})) if incremental else {}
        for date, row in sub.iterrows():
            d = date.strftime('%Y-%m-%d')
            if d <= target and row['Close'] > 0:
                raw[d] = [float(row[c]) for c in ('Close', 'Adj Close', 'High', 'Low', 'Volume')]
        if not raw:
            raise FetchError('empty_response')
        dates = sorted(raw)[-530:]
        result = {'dates': dates, 'raw': {d: raw[d] for d in dates},
                  'period': full_period,
                  'full_at': old['full_at'] if incremental else cache.clock(),
                  'close': [], 'high': [], 'low': [], 'volume': []}
        for d in dates:
            close, adj, high, low, volume = raw[d]
            factor = adj / close
            for field, value in [('close', adj), ('high', high * factor),
                                 ('low', low * factor), ('volume', volume)]:
                result[field].append(value)
        return result

    # Prices expire by exchange session, not an arbitrary 24-hour timer.
    data, meta = cache.fetch(key, acquire, ttl=86400,
                             force=not (sufficient and same_session))
    data = dict(data or {'dates': [], 'close': [], 'high': [], 'low': [], 'volume': []})
    as_of = data['dates'][-1] if data['dates'] else None
    stale = as_of != target or meta['status'] in ('stale', 'missing')
    data['quality'] = dict(meta, as_of=as_of, expected_session=target, stale=stale)
    if as_of != target:
        data['quality']['reason'] = meta.get('reason', 'outdated_bar' if as_of else 'missing')
        if meta['status'] == 'fresh':
            entry = cache.get(key)
            entry.update(reason='outdated_bar', retry_at=cache.clock() + 900)
            cache.put(key, entry)
            data['quality']['status'] = 'stale'
            cache.events.append({'key': key, 'status': 'stale', 'reason': 'outdated_bar'})
    return data
