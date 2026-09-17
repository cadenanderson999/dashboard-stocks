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
