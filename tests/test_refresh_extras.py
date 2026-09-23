import unittest
from datetime import date
from unittest.mock import patch
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import refresh_extras as rx
import market_data as md
import pandas as pd

class RefreshTests(unittest.TestCase):
    def test_quote_failure_preserves_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = md.Store(root/'cache.sqlite', sleep=lambda _:None)
            md.atomic_json(root/'data/stocks.json', {'is_sample':False,'stocks':[{'symbol':'TEST','price':42,'score':60}]})
            with patch.object(md,'ROOT',root), patch.object(md,'store',return_value=cache), patch.object(rx.yf,'Ticker',side_effect=md.FetchError('not_found')):
                rx.quotes()
            row = md.read_json(root/'data/stocks.json')['stocks'][0]
            self.assertEqual(row['price'],42)
            self.assertEqual(row['score'],60)

class EarningsPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        from datetime import datetime, timezone
        self.now = datetime(2026, 9, 23, 15, tzinfo=timezone.utc).timestamp()
        self.cache = md.Store(self.root/'cache.sqlite', clock=lambda: self.now, sleep=lambda _: None)
        self.addCleanup(self.cache.db.close)
        for p in (patch.object(md, 'ROOT', self.root), patch.object(md, 'store', return_value=self.cache)):
            p.start()
            self.addCleanup(p.stop)
        md.atomic_json(self.root/'data/stocks.json', {'is_sample': False, 'stocks': [
            {'symbol': 'OLD', 'next_earnings': '2026-08-01', 'score': 80},
            {'symbol': 'TODAY', 'next_earnings': '2026-09-23'},
            {'symbol': 'FUTURE', 'next_earnings': '2026-10-01'}]})
        md.atomic_json(self.root/'data/details.json', {'is_sample': False, 'stocks': {
            'OLD': {'next_earnings': '2026-08-01', 'earnings': [{'period': '2026-03-31', 'eps': 1}]}}})

    def response(self, period='2026-03-31'):
        from datetime import datetime, timezone
        return {'OLD': {'info': {'earningsTimestampStart': datetime(2026, 11, 1, tzinfo=timezone.utc).timestamp()},
                        'earnings': [{'period': period}], 'quality': {}, 'missing_fields': {}}}

    def test_pending_until_new_quarter_with_cooldown(self):
        with patch('generate_data.fetch_fundamentals', return_value=self.response()) as fetch:
            rx.earnings_priority()
            fetch.assert_called_once_with(['OLD'], force=True)
            self.assertTrue(self.cache.get('earnings_pending:OLD'))
            rx.earnings_priority()
            self.assertEqual(fetch.call_count, 1)
            self.now += 6 * 3600
            fetch.return_value = self.response('2026-06-30')
            rx.earnings_priority()
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(self.cache.get('earnings_pending:OLD'), {})
        row = md.read_json(self.root/'data/stocks.json')['stocks'][0]
        self.assertEqual(row['score'], 80)
        self.assertEqual(row['next_earnings'], '2026-11-01')

    def test_empty_result_preserves_published_data_without_cache(self):
        result = {'OLD': {'info': {}, 'earnings': [], 'quality': {}, 'missing_fields': {}}}
        with patch('generate_data.fetch_fundamentals', return_value=result):
            rx.earnings_priority()
        detail = md.read_json(self.root/'data/details.json')['stocks']['OLD']
        self.assertEqual(detail['next_earnings'], '2026-08-01')
        self.assertEqual(detail['earnings'][0]['eps'], 1)
        self.assertTrue(self.cache.get('earnings_pending:OLD'))

    def test_budget_defers_without_marking_attempted(self):
        self.cache.put('gateway', {'day': '2026-09-23', 'used': self.cache.limit - 2000})
        with patch('generate_data.fetch_fundamentals') as fetch:
            rx.earnings_priority()
            fetch.assert_not_called()
        self.assertNotIn('last_attempt', self.cache.get('earnings_pending:OLD'))

    def test_date_boundary_uses_eastern_time(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 9, 24, 1, tzinfo=timezone.utc).timestamp()
        with patch('generate_data.fetch_fundamentals', return_value=self.response()) as fetch:
            rx.earnings_priority()
            fetch.assert_called_once_with(['OLD'], force=True)

    def test_force_bypasses_info_and_statement_ttls(self):
        import generate_data as stocks
        from unittest.mock import Mock
        ticker = Mock()
        ticker.info = {'longName': 'Updated', 'marketCap': 500}
        provider = Mock()
        provider.Ticker.return_value = ticker
        with patch.object(md, 'yahoo', return_value=provider), patch.object(stocks, 'extract_earnings', return_value=[{'period': '2026-03-31'}]) as extract:
            stocks.fetch_fundamentals(['OLD'])
            ticker.info = {'longName': 'Updated', 'marketCap': 900}
            extract.return_value = [{'period': '2026-06-30'}]
            result = stocks.fetch_fundamentals(['OLD'], force=True)['OLD']
        self.assertEqual(result['market_cap'], 900)
        self.assertEqual(result['earnings'][0]['period'], '2026-06-30')
        self.assertEqual(extract.call_count, 2)
        self.assertEqual(self.cache.get('gateway')['used'], 4)
