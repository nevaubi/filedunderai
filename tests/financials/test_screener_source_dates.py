import unittest

from etl import screener


class ScreenerPriceDatesTests(unittest.TestCase):
    def test_last_available_close_preserves_its_actual_date(self):
        dates = ['2026-01-01','2026-02-01','2026-03-01']
        closes = [100,110,None]
        values, proof = screener.price_block('OKTA',dates,closes,[100,100,101],[100,100,101],{})
        self.assertEqual(values['price'],110)
        self.assertEqual(proof['price']['p'],[['2026-02-01',110]])
        self.assertAlmostEqual(values['ret_1m'],0.1)
        self.assertEqual(proof['ret_1m']['p'],[['2026-01-01',100],['2026-02-01',110]])
        self.assertEqual(closes,[100,110,None])
        self.assertEqual(dates[-1],'2026-03-01')

    def test_missing_history_does_not_create_a_quote(self):
        self.assertEqual(screener.price_block('OKTA',['2026-11-03'],[None],None,None,{}),({},{}))


if __name__ == '__main__':
    unittest.main()
