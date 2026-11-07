import unittest

from etl import jobs


class Movers(unittest.TestCase):
    def test_cap_filter_and_order(self):
        rows = [{"ticker": "A", "price": 10, "chg_1d": 0.05, "ret_1m": 0.2, "market_cap": 5e9, "as_of": "2026-11-06"},
                {"ticker": "B", "price": 10, "chg_1d": -0.08, "ret_1m": -0.1, "market_cap": 3e9, "as_of": "2026-11-06"},
                {"ticker": "C", "price": 10, "chg_1d": 0.30, "ret_1m": 0.9, "market_cap": 1e9, "as_of": "2026-11-06"},
                {"ticker": "D", "price": 10, "chg_1d": None, "ret_1m": 0.01, "market_cap": 9e9, "as_of": "2026-11-05"}]
        m = jobs.movers(rows, n=2)
        self.assertEqual([r["ticker"] for r in m["day"]["up"]], ["A", "B"])
        self.assertEqual([r["ticker"] for r in m["day"]["down"]], ["B", "A"])
        self.assertEqual([r["ticker"] for r in m["month"]["up"]], ["A", "D"])
        self.assertEqual(m["as_of"], "2026-11-06")
        self.assertEqual(m["universe"], 3)


if __name__ == "__main__":
    unittest.main()
