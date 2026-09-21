import os
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import market_data as md
import generate_data as stocks
import generate_leaps as leaps
import generate_rvol_scan as scanner
import publish_data as publish
import pandas as pd


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 1789000000.0
        self.cache = md.Store(Path(self.tmp.name) / 'cache.sqlite',
                              clock=lambda: self.now, sleep=self.advance)
        self.addCleanup(self.cache.db.close)
        self.patches = [patch.object(md, '_store', self.cache),
                        patch.object(md, 'ROOT', Path(self.tmp.name))]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def advance(self, seconds):
        self.now += seconds

    def test_cache_survives_new_connection_and_avoids_request(self):
        self.cache.fetch('info:X', lambda: {'pe': 10}, 100)
        other = md.Store(Path(self.tmp.name) / 'cache.sqlite', clock=lambda: self.now)
        self.addCleanup(other.db.close)
        call = Mock(side_effect=AssertionError('must not fetch'))
        data, meta = other.fetch('info:X', call, 100)
        self.assertEqual(data, {'pe': 10})
        self.assertEqual(meta['status'], 'cached')
        call.assert_not_called()

    def test_failed_refresh_keeps_data_and_original_timestamp(self):
        _, original = self.cache.fetch('info:X', lambda: {'pe': 10}, 1)
        self.advance(2)
        operation = Mock(side_effect=TimeoutError())
        data, meta = self.cache.fetch('info:X', operation, 1)
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(data['pe'], 10)
        self.assertEqual(meta['status'], 'stale')
        self.assertEqual(meta['reason'], 'timeout')
        self.assertEqual(meta['updated_at'], original['updated_at'])
        self.cache.fetch('info:X', operation, 1)
        self.assertEqual(operation.call_count, 3)

    def test_partial_fundamentals_preserve_values_with_field_dates(self):
        _, original = self.cache.fetch('metrics:X', lambda: {'pe': 10, 'cap': 20}, 1,
                                       preserve_none=True)
        self.advance(2)
        data, meta = self.cache.fetch('metrics:X', lambda: {'pe': None, 'cap': 30}, 1,
                                     preserve_none=True)
        self.assertEqual(data, {'pe': 10, 'cap': 30})
        self.assertEqual(meta['retained_fields'], ['pe'])
        self.assertEqual(meta['field_updated_at']['pe'], original['updated_at'])
        self.assertNotEqual(meta['field_updated_at']['cap'], original['updated_at'])

    def test_rate_limit_opens_shared_circuit(self):
        operation = Mock(side_effect=md.FetchError('rate_limited'))
        _, meta = self.cache.fetch('info:X', operation, 1)
        self.assertEqual(meta['reason'], 'rate_limited')
        self.assertEqual(operation.call_count, 3)
        _, meta = self.cache.fetch('info:Y', operation, 1)
        self.assertEqual(meta['reason'], 'provider_cooldown')
        self.assertEqual(operation.call_count, 3)
        self.advance(901)
        self.assertEqual(self.cache.fetch('info:Y', lambda: {'ok': True}, 1)[0], {'ok': True})

    def test_daily_budget_persists_and_resets_next_day(self):
        self.cache.limit = 1
        self.cache.call(lambda: True)
        with self.assertRaisesRegex(md.FetchError, 'budget_exhausted'):
            self.cache.call(lambda: True)
        self.advance(86400)
        self.assertTrue(self.cache.call(lambda: True))

    def test_retry_after_long_delay_is_deferred(self):
        error = Exception('429')
        error.response = Mock(headers={'Retry-After': '120'})
        operation = Mock(side_effect=error)
        _, meta = self.cache.fetch('X', operation, 1)
        self.assertEqual(meta['reason'], 'provider_cooldown')
        self.assertEqual(operation.call_count, 1)

    def test_nested_operations_count_both_requests(self):
        self.cache.call(lambda: self.cache.call(lambda: True))
        self.assertEqual(self.cache.get('gateway')['used'], 2)

    def test_shared_info_response_and_scanner_skips_statements(self):
        ticker = Mock()
        ticker.info = {'marketCap': 100, 'trailingPE': 10, 'longName': 'Example'}
        with patch('yfinance.Ticker', return_value=ticker), patch.object(stocks, 'extract_earnings') as earnings:
            data = stocks.fetch_fundamentals(['X'], include_earnings=False)
            stocks.fetch_fundamentals(['X'], include_earnings=False)
        self.assertEqual(self.cache.get('gateway')['used'], 1)
        self.assertEqual(data['X']['pe'], 10)
        earnings.assert_not_called()
        self.assertEqual(data['X']['missing_fields']['targetMeanPrice'], 'unavailable')

    def frame(self, dates, values, dividend=0):
        return pd.DataFrame({'Close': values, 'Adj Close': values,
                             'High': values, 'Low': values, 'Volume': [100] * len(dates),
                             'Dividends': [dividend] * len(dates),
                             'Stock Splits': [0] * len(dates)}, index=pd.to_datetime(dates))

    def test_single_ticker_history_and_cache_reuse(self):
        ticker = Mock()
        ticker.history.return_value = self.frame(['2026-09-09'], [100])
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-09'):
            result = md.price_history('X')
            md.price_history('X', '3mo')
        self.assertEqual(result['close'], [100])
        self.assertFalse(result['quality']['stale'])
        self.assertEqual(ticker.history.call_count, 1)

    def test_partial_session_is_replaced_after_close(self):
        ticker = Mock()
        ticker.history.side_effect = [self.frame(['2026-09-21'], [100]), self.frame(['2026-09-21'], [105])]
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'signal_session', return_value='2026-09-21'):
            with patch.dict(os.environ, {'INCLUDE_CURRENT_SESSION':'1'}), patch.object(md, 'expected_session', return_value='2026-09-18'):
                first = md.price_history('X')
            self.assertEqual(first['close'], [100])
            self.assertTrue(first['partial_session'])
            with patch.dict(os.environ, {'INCLUDE_CURRENT_SESSION':'0'}), patch.object(md, 'expected_session', return_value='2026-09-21'):
                final = md.price_history('X')
            self.assertEqual(final['close'], [105])
            self.assertFalse(final['partial_session'])
            self.assertEqual(ticker.history.call_count, 2)

    def test_incremental_merge_retains_old_dates_and_replaces_overlap(self):
        ticker = Mock()
        ticker.history.side_effect = [self.frame(['2026-09-08', '2026-09-09'], [99, 100]),
                                     self.frame(['2026-09-09', '2026-09-10'], [101, 102])]
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-09'):
            md.price_history('X')
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-10'):
            result = md.price_history('X')
        self.assertEqual(result['close'], [99, 101, 102])
        self.assertIn('start', ticker.history.call_args.kwargs)

    def test_corporate_action_rebuilds_entire_adjusted_history(self):
        ticker = Mock()
        ticker.history.side_effect = [self.frame(['2026-09-09'], [100]),
                                     self.frame(['2026-09-10'], [99], dividend=1),
                                     self.frame(['2026-09-09', '2026-09-10'], [99, 99])]
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-09'):
            md.price_history('X')
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-10'):
            result = md.price_history('X')
        self.assertEqual(result['close'], [99, 99])
        self.assertEqual(ticker.history.call_args.kwargs['period'], '2y')
        self.assertEqual(self.cache.get('gateway')['used'], 3)

    def test_delayed_bar_does_not_refetch_immediately(self):
        ticker = Mock()
        ticker.history.return_value = self.frame(['2026-09-09'], [100])
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-10'):
            first = md.price_history('X')
            second = md.price_history('X')
        self.assertEqual(ticker.history.call_count, 1)
        self.assertTrue(first['quality']['stale'])
        self.assertEqual(second['quality']['reason'], 'outdated_bar')
        self.assertEqual(second['close'], [100])

    def test_empty_history_uses_dated_cache(self):
        ticker = Mock()
        ticker.history.return_value = self.frame(['2026-09-09'], [100])
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-09'):
            md.price_history('X')
        ticker.history.return_value = pd.DataFrame()
        with patch('yfinance.Ticker', return_value=ticker), patch.object(md, 'expected_session', return_value='2026-09-10'):
            result = md.price_history('X')
        self.assertEqual(result['close'], [100])
        self.assertTrue(result['quality']['stale'])
        self.assertEqual(result['quality']['as_of'], '2026-09-09')

    def test_partial_option_failure_preserves_historical_contracts_separately(self):
        self.cache.put('contract_snapshot:X', {'contracts': [{'mid': 10}], 'as_of': '2026-09-08T22:00:00+00:00'})
        candidate = {'symbol': 'X', 'price': 100, 'contracts': []}
        with patch.object(leaps, 'fetch_chain', return_value=('error', [], {'status': 'missing', 'reason': 'timeout'})):
            self.assertFalse(leaps.fill_contracts([candidate], False, datetime.now().date()))
        self.assertEqual(candidate['contracts'], [])
        self.assertEqual(candidate['historical_contracts'], [{'mid': 10}])
        self.assertEqual(candidate['chain_quality']['reason'], 'timeout')

    def test_zero_scanner_hits_is_success(self):
        prices = {'close': [10] * 60, 'volume': [100000] * 60, 'quality': {'stale': False}}
        with patch.object(md, 'price_history', return_value=prices):
            self.assertEqual(scanner.scan_candidates({'X': {}}), [])
        self.assertEqual(scanner.SCAN_COVERAGE['current'], 1)
        self.assertFalse(scanner.SCAN_COVERAGE['partial'])

    def test_failed_scanner_is_not_zero_hits(self):
        with patch.object(md, 'price_history', return_value={'quality': {'stale': True, 'reason': 'timeout'}}):
            with self.assertRaisesRegex(md.FetchError, 'scan_unavailable'):
                scanner.scan_candidates({'X': {}})

    def test_scanner_cursor_rotates_limited_scan(self):
        prices = {'close': [10] * 60, 'volume': [100000] * 60, 'quality': {'stale': False}}
        with patch.dict('os.environ', {'SCAN_MAX_SYMBOLS': '1'}), patch.object(md, 'price_history', return_value=prices) as call:
            scanner.scan_candidates({'X': {}, 'Y': {}})
            scanner.scan_candidates({'X': {}, 'Y': {}})
        self.assertEqual([c.args[0] for c in call.call_args_list], ['X', 'Y'])
        self.assertTrue(scanner.SCAN_COVERAGE['partial'])

    def test_nonfinite_provider_numbers_become_missing(self):
        data, _ = self.cache.fetch('info:X', lambda: {'pe': float('nan'), 'cap': float('inf')}, 100)
        self.assertEqual(data, {'pe': None, 'cap': None})

    def test_missing_price_ticker_is_retained_and_unranked(self):
        good = {'close': [100] * 300, 'high': [101] * 300, 'low': [99] * 300,
                'volume': [10000] * 300, 'quality': {'stale': False, 'as_of': '2026-09-10'}}
        bad = dict(stocks.EMPTY_PRICES, quality={'stale': True, 'reason': 'timeout'})
        with patch.object(stocks, 'build_universe', return_value={'X': {}, 'Y': {}}), \
             patch.object(stocks, 'download_prices', return_value={'X': good, 'Y': bad}), \
             patch.object(stocks, 'fetch_fundamentals', return_value={}), \
             patch.object(stocks.md, 'read_json', return_value={}):
            records, details = stocks.fetch_live()
        self.assertEqual([r['symbol'] for r in records], ['X', 'Y'])
        self.assertIsNone(records[1]['price'])
        self.assertIsNone(records[1]['score'])
        self.assertIsNone(records[1]['rs_rank'])
        self.assertEqual(details['Y']['data_quality']['prices']['reason'], 'timeout')

    def test_options_partial_expiry_failure_is_not_reported_ok(self):
        tk = Mock()
        tk.options = ('2028-01-21', '2029-01-19')
        tk.option_chain.side_effect = [Mock(calls=pd.DataFrame([{'strike': 100, 'bid': 5, 'ask': 6}])),
                                       TimeoutError(), TimeoutError(), TimeoutError()]
        with patch('yfinance.Ticker', return_value=tk):
            status, chains, meta = leaps.fetch_chain('X', datetime(2026, 9, 10).date())
        self.assertEqual(status, 'error')
        self.assertEqual(chains, [])
        self.assertEqual(meta['reason'], 'timeout')


class PublicationTests(unittest.TestCase):
    def test_total_stock_failure_never_calls_sample_generator(self):
        with patch.object(stocks, 'fetch_live', return_value=([], {})), \
             patch.object(stocks, 'generate_sample') as sample, \
             patch.object(md, 'store') as cache, patch.object(sys, 'argv', ['generate_data.py']):
            self.assertEqual(stocks.main(), 1)
            sample.assert_not_called()

    def test_live_options_reject_sample_input(self):
        with patch.object(leaps, 'load_json', return_value={'is_sample': True}), \
             patch.object(leaps, 'fill_contracts') as fill, patch.object(sys, 'argv', ['generate_leaps.py']):
            self.assertEqual(leaps.main(), 1)
            fill.assert_not_called()

    def test_deploy_only_restores_live_snapshot_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'data').mkdir()
            snapshot = root / '.cache/site-data'
            md.atomic_json(snapshot / 'stocks.json', {'is_sample': False, 'stocks': [{'symbol': 'X'}]})
            md.atomic_json(root / 'data/leaps.json', {'is_sample': True})
            with patch.object(publish, 'ROOT', root), patch.object(publish, 'SNAPSHOT', snapshot), \
                 patch.object(publish, 'stage_site') as stage, patch.object(publish.subprocess, 'run') as run, \
                 patch.object(sys, 'argv', ['publish_data.py']):
                self.assertEqual(publish.main(), 0)
                run.assert_not_called()
                stage.assert_called_once()
            self.assertEqual(md.read_json(root / 'data/leaps.json')['refresh_status'], 'unavailable')
            self.assertFalse(md.read_json(root / 'data/leaps.json')['is_sample'])

    def test_earnings_cleanup_preserves_core_and_removes_related_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            md.atomic_json(root/'data/stocks.json', {'is_sample':False, 'stocks':[
                {'symbol':'CORE','lists':['S&P 500','Earnings watch'],'price':42},
                {'symbol':'EXTRA','lists':['Earnings watch'],'price':10}]})
            md.atomic_json(root/'data/details.json', {'stocks':{'CORE':{},'EXTRA':{}}})
            md.atomic_json(root/'data/leaps.json', {'candidates':[
                {'symbol':'CORE','leap_rating':'LEAP Buy'},
                {'symbol':'EXTRA','leap_rating':'LEAP Buy'}]})
            md.atomic_json(root/'data/earnings_calendar.json', {'events':[]})
            with patch.object(publish,'ROOT',root), patch.object(publish,'SNAPSHOT',root/'.cache/site-data'):
                publish.remove_earnings_membership()
            stocks = md.read_json(root/'data/stocks.json')
            self.assertEqual(stocks['stocks'], [{'symbol':'CORE','lists':['S&P 500'],'price':42}])
            self.assertEqual(list(md.read_json(root/'data/details.json')['stocks']), ['CORE'])
            self.assertEqual(md.read_json(root/'data/leaps.json')['buy_count'], 1)
            self.assertFalse((root/'data/earnings_calendar.json').exists())

    def test_failed_snapshot_retains_values_but_disables_ranking(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'stocks.json'
            md.atomic_json(path, {'is_sample': False, 'generated_at': 'old',
                                 'stocks': [{'symbol': 'X', 'price': 100, 'score': 50}]})
            publish.mark_failed(path)
            data = md.read_json(path)
            self.assertEqual(data['generated_at'], 'old')
            self.assertEqual(data['stocks'][0]['price'], 100)
            self.assertEqual(data['stocks'][0]['score'], 50)
            self.assertNotEqual(data['stocks'][0].get('rating'), 'Stale')

    def test_scanner_main_publishes_zero_hits_without_sample(self):
        with patch.object(scanner, 'fetch_symbols', return_value={'X': {}}), \
             patch.object(scanner, 'scan_candidates', return_value=[]), \
             patch.object(scanner, 'enrich', return_value=[]), \
             patch.object(scanner, 'write_output') as write, \
             patch.object(scanner, 'generate_sample') as sample, \
             patch.object(md, 'store'), patch.object(sys, 'argv', ['generate_rvol_scan.py']):
            self.assertEqual(scanner.main(), 0)
            write.assert_called_once_with([], is_sample=False)
            sample.assert_not_called()

    def test_afternoon_session_and_weekend(self):
        from datetime import datetime, timezone
        with patch.dict(os.environ, {'INCLUDE_CURRENT_SESSION':'1'}):
            self.assertEqual(md.signal_session(datetime(2026,9,21,19,45,tzinfo=timezone.utc)), '2026-09-21')
            self.assertEqual(md.signal_session(datetime(2026,9,20,19,45,tzinfo=timezone.utc)), '2026-09-18')
            self.assertEqual(md.signal_session(datetime(2026,9,21,11,0,tzinfo=timezone.utc)), '2026-09-18')

    def test_exchange_holiday_and_early_close(self):
        self.assertEqual(md.expected_session(datetime(2026, 9, 7, 23, tzinfo=timezone.utc)), '2026-09-04')
        self.assertEqual(md.expected_session(datetime(2026, 11, 27, 18, 31, tzinfo=timezone.utc)), '2026-11-27')
        self.assertEqual(md.expected_session(datetime(2026, 11, 27, 18, 10, tzinfo=timezone.utc)), '2026-11-25')


if __name__ == '__main__':
    unittest.main()
