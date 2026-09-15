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
    def test_retention(self):
        self.assertEqual(rx.retention_end('2026-09-23'), date(2026,10,2))
        self.assertEqual(rx.retention_end('2026-09-25'), date(2026,10,2))
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
    def test_calendar_uses_symbol_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cache = md.Store(root/'cache.sqlite', sleep=lambda _:None)
            today=rx.datetime.now(rx.ZoneInfo('America/New_York')).date()
            frame=pd.DataFrame([{'Company':'Test Company','Event Start Date':pd.Timestamp(today)}],index=['TEST'])
            with patch.object(md,'ROOT',root), patch.object(md,'store',return_value=cache), patch.object(rx.yf,'Calendars') as provider:
                provider.return_value.get_earnings_calendar.return_value=frame
                rx.calendar()
            result=md.read_json(root/'data/earnings_calendar.json')
            self.assertEqual(result['events'][0]['symbol'],'TEST')
            self.assertTrue(result['complete'])
