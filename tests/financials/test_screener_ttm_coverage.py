"""Trailing-twelve-month coverage: derived quarter starts and fourth-quarter per-share values."""
import unittest

from etl import screener as S


def rec(end, start, v, derived=None, form="10-Q", filed="2026-01-01", accn="a", concept="C"):
    meta = {"derived": derived} if derived else {}
    return [end, start, v, accn, filed, form, concept, 1 if derived else 0, meta]


class DerivedStarts(unittest.TestCase):
    def test_start_equal_to_prior_end_is_moved_one_day(self):
        r = rec("2025-09-30", "2025-06-30", 5.0, derived={"minus": {"end": "2025-06-30"}, "from": {"end": "2025-09-30"}})
        self.assertEqual(S._contiguous_start(r)[1], "2025-07-01")

    def test_correct_and_reported_starts_are_untouched(self):
        ok = rec("2025-09-30", "2025-07-01", 5.0, derived={"minus": {"end": "2025-06-30"}})
        self.assertEqual(S._contiguous_start(ok)[1], "2025-07-01")
        rep = rec("2025-09-30", "2025-06-30", 5.0)
        self.assertEqual(S._contiguous_start(rep)[1], "2025-06-30")

    def test_ttm_cash_flow_with_old_style_derived_quarters(self):
        ocf = [rec("2025-03-31", "2025-01-01", 10.0),
               rec("2025-06-30", "2025-03-31", 11.0, derived={"minus": {"end": "2025-03-31"}}),
               rec("2025-09-30", "2025-06-30", 12.0, derived={"minus": {"end": "2025-06-30"}}),
               rec("2025-12-31", "2025-09-30", 13.0, derived={"minus": {"end": "2025-09-30"}}, form="10-K")]
        rev = [rec(r[0], S._contiguous_start(r)[1], 100.0) for r in ocf]
        fx = S.Facts({"ticker": "TST", "q": {"Operating cash flow": ocf, "Revenue": rev, "Net income": rev}}, [])
        v, recs = fx.ttm("Operating cash flow", "2025-12-31")
        self.assertEqual(v, 46.0)
        self.assertEqual(len(recs), 4)


class FourthQuarterEPS(unittest.TestCase):
    def setUp(self):
        q = [rec("2024-12-28", "2024-09-29", 2.40), rec("2025-03-29", "2024-12-29", 1.65), rec("2025-06-28", "2025-03-30", 1.57),
             rec("2025-12-27", "2025-09-28", 2.84), rec("2026-03-28", "2025-12-28", 2.01), rec("2026-06-27", "2026-03-29", 2.02)]
        fy = [rec("2025-09-27", "2024-09-29", 7.46, form="10-K"), rec("2026-09-26", "2025-09-28", 8.98, form="10-K")]
        ni = [rec(e, s, 1.0) for e, s in (("2024-12-28", "2024-09-29"), ("2025-03-29", "2024-12-29"), ("2025-06-28", "2025-03-30"),
                                          ("2025-09-27", "2025-06-29"), ("2025-12-27", "2025-09-28"), ("2026-03-28", "2025-12-28"),
                                          ("2026-06-27", "2026-03-29"), ("2026-09-26", "2026-06-28"))]
        self.fx = S.Facts({"ticker": "TST", "q": {"EPS diluted": q, "Net income": ni, "Revenue": ni}, "fy": {"EPS diluted": fy}}, [])

    def test_fiscal_year_window_uses_reported_annual(self):
        v, recs = self.fx.ttm("EPS diluted", "2026-09-26")
        self.assertEqual(v, 8.98)
        self.assertEqual(recs[0][5], "10-K")

    def test_window_across_fourth_quarter_uses_annual_minus_three_quarters(self):
        v, recs = self.fx.ttm("EPS diluted", "2026-06-27")
        q4 = round(7.46 - 2.40 - 1.65 - 1.57, 6)
        self.assertAlmostEqual(v, q4 + 2.84 + 2.01 + 2.02, places=6)
        derived = [r for r in recs if r[7]]
        self.assertEqual(len(derived), 1)
        self.assertEqual(derived[0][8]["derived"]["basis"], "annual minus three reported quarters")

    def test_quarter_fields_do_not_see_the_derived_fourth_quarter(self):
        self.assertIsNone(self.fx.at("EPS diluted", "2025-09-27"))

    def test_no_annual_row_keeps_the_strict_behaviour(self):
        fx = S.Facts({"ticker": "TST", "q": self.fx.data["q"]}, [])
        self.assertEqual(fx.ttm("EPS diluted", "2026-06-27"), (None, []))


if __name__ == "__main__":
    unittest.main()


class FourthQuarterFlows(unittest.TestCase):
    def test_capex_fourth_quarter_from_annual(self):
        capex = [rec("2025-03-31", "2025-01-01", -10.0), rec("2025-06-30", "2025-04-01", -11.0), rec("2025-09-30", "2025-07-01", -12.0),
                 rec("2026-03-31", "2026-01-01", -14.0), rec("2026-06-30", "2026-04-01", -15.0), rec("2026-09-30", "2026-07-01", -16.0)]
        fy = [rec("2025-12-31", "2025-01-01", -46.0, form="10-K")]
        rev = [rec(e, s, 100.0) for e, s in (("2025-03-31", "2025-01-01"), ("2025-06-30", "2025-04-01"), ("2025-09-30", "2025-07-01"),
                                             ("2025-12-31", "2025-10-01"), ("2026-03-31", "2026-01-01"), ("2026-06-30", "2026-04-01"),
                                             ("2026-09-30", "2026-07-01"))]
        fx = S.Facts({"ticker": "TST", "q": {"Capex": capex, "Revenue": rev, "Net income": rev}, "fy": {"Capex": fy}}, [])
        v, recs = fx.ttm("Capex", "2026-09-30")
        self.assertEqual(v, 13.0 + 14.0 + 15.0 + 16.0)  # outflows read as positive; Q4 = 46 - 33
        v, recs = fx.ttm("Capex", "2025-12-31")
        self.assertEqual((v, len(recs)), (46.0, 4))

    def test_held_quarter_is_not_rebuilt_from_annual(self):
        fx = S.Facts({"ticker": "TST", "q": {}, "fy": {}}, [])
        fx.comparability_holds = [{"label": "Capex", "point": {"end": "2025-12-31"}}]
        self.assertTrue(fx._held("Capex", "2025-01-01", "2025-12-31"))
        self.assertFalse(fx._held("Capex", "2026-01-01", "2026-12-31"))
