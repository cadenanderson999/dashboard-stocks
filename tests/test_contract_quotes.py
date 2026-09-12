import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import strategies

class ContractQuoteTests(unittest.TestCase):
    def contracts(self, bid, ask):
        return strategies.pick_leap_contracts(100, [{'expiry': '2028-01-21', 'dte': 500,
            'calls': [{'strike': 100, 'bid': bid, 'ask': ask, 'last': 15,
                       'iv': .3, 'oi': 1000, 'last_trade_at': '2026-09-10T20:00:00Z'}]}])

    def test_crossed_quote_is_not_liquid(self):
        rows = self.contracts(16, 14)
        self.assertTrue(rows)
        self.assertFalse(rows[0]['liquid'])
        self.assertEqual(rows[0]['premium_source'], 'last_trade')
        self.assertIsNone(rows[0]['spread_pct'])

    def test_valid_quote_uses_midpoint(self):
        rows = self.contracts(14, 16)
        self.assertTrue(rows[0]['liquid'])
        self.assertEqual(rows[0]['premium_source'], 'midpoint')
        self.assertEqual(rows[0]['last_trade_at'], '2026-09-10T20:00:00Z')
