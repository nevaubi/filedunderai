"""Percentage EPS surprise is withheld when it is not meaningful."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import estimates as E  # noqa: E402


class Surprise(unittest.TestCase):
    def test_near_zero_consensus_has_no_percentage(self):
        self.assertIsNone(E.sane_surprise(-3.20, 0.01, -321.0))      # LLYVA
        self.assertIsNone(E.sane_surprise(-0.32, -0.00667, -46.976))  # VSAT

    def test_extreme_results_are_withheld(self):
        self.assertIsNone(E.sane_surprise(-4.41, 0.40))  # -1,202%

    def test_normal_and_negative_estimates(self):
        self.assertAlmostEqual(E.sane_surprise(2.10, 1.98579), 0.057514, places=5)
        self.assertAlmostEqual(E.sane_surprise(-0.30, -0.20), -0.5)  # a bigger loss than expected is a miss

    def test_provider_value_kept_within_bounds_without_eps(self):
        self.assertEqual(E.sane_surprise(None, None, 0.12), 0.12)
        self.assertIsNone(E.sane_surprise(None, None, 50.0))

    def test_parse_applies_the_bounds(self):
        out = E.parse_yahoo_earnings({"history": [{"quarter": {"date": "2026-03-31"}, "eps_actual": -3.2, "eps_estimate": 0.01,
                                                   "surprise_percent": -321.0}],
                                      "trend": [{"period": "0q", "end_date": "2026-06-30", "eps_estimate": {"avg": 0.1}}]})
        self.assertIsNone(out["history"][0]["surprise_pct"])


if __name__ == "__main__":
    unittest.main()
