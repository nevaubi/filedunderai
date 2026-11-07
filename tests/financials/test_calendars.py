import datetime as dt
import unittest

from etl import calendars as C


class Declarations(unittest.TestCase):
    def test_quarterly_with_years(self):
        t = ("RICHMOND, Va. The Board of Directors of Acme Corp. declared a quarterly cash dividend of $0.34 per share on its "
             "common stock, payable on December 15, 2026 to shareholders of record at the close of business on November 28, 2026. "
             "The company also reported results.")
        d = C.parse_declaration(t, "2026-10-29")
        self.assertEqual(len(d), 1)
        self.assertEqual(d[0]["amount"], 0.34)
        self.assertEqual(d[0]["record"], "2026-11-28")
        self.assertEqual(d[0]["payable"], "2026-12-15")
        self.assertEqual(d[0]["ex_date"], "2026-11-27")  # Nov 28 2026 is a Saturday -> prior business day
        self.assertEqual(d[0]["kind"], "quarterly")
        self.assertIn("declared a quarterly cash dividend", d[0]["quote"])

    def test_missing_year_and_cents(self):
        t = "The Board declared a dividend of 52 cents per share, payable Jan. 10 to holders of record on Dec. 20."
        d = C.parse_declaration(t, "2026-11-30")
        self.assertEqual(d[0]["amount"], 0.52)
        self.assertEqual(d[0]["payable"], "2027-01-10")
        self.assertEqual(d[0]["record"], "2026-12-20")

    def test_preferred_only_is_skipped(self):
        t = "The Board declared a quarterly dividend of $0.40625 per depositary share on its Series A preferred stock, payable December 1, 2026 to holders of record on November 15, 2026."
        self.assertEqual(C.parse_declaration(t, "2026-10-20"), [])

    def test_special_and_increase(self):
        t = "The Board approved a 5% increase and declared a quarterly dividend of $1.05 per share and declared a special dividend of $2.00 per share, both payable on November 20, 2026 to stockholders of record on November 6, 2026."
        d = C.parse_declaration(t, "2026-10-22")
        self.assertTrue(d[0]["increase"])


    def test_rejects_totals_annualized_and_preferred(self):
        self.assertEqual(C.parse_declaration("New EIDP declared cash dividends of $1,815 million for the year, payable on December 1, 2026.", "2026-11-01"), [])
        self.assertEqual(C.parse_declaration("The Board declared a dividend for the month of October 2026, which reflects a targeted annualized dividend of $1.60 per share, payable on November 15, 2026 to holders of record on October 31, 2026.", "2026-10-01"), [])
        t = ('The board declared a cash dividend of $14.375 per share of the 5.75% Preferred Stock, Series B, payable on December 15, 2026 to holders of record on December 1, 2026. '
             'The board declared a cash dividend of $0.20 per common share, payable on December 15, 2026 to holders of record on December 1, 2026.')
        self.assertEqual([d["amount"] for d in C.parse_declaration(t, "2026-11-01")], [0.2])

    def test_kind_from_the_amount_and_headline_dedupe(self):
        t = ("Board declares $3.50 per share special dividend, payable November 25, 2026\n"
             "The Board has declared a quarterly dividend of $0.07 per common share and a one-time special dividend of $3.50 per common share. "
             "Both are payable on November 25, 2026 to shareholders of record as of November 16, 2026.")
        d = C.parse_declaration(t, "2026-11-01")
        self.assertEqual(len([x for x in d if x["amount"] == 3.5]), 1)
        self.assertEqual(next(x for x in d if x["amount"] == 3.5)["record"], "2026-11-16")
        self.assertEqual(next(x for x in d if x["amount"] == 0.07)["kind"], "quarterly")


class Cadence(unittest.TestCase):
    def test_quarterly_projection(self):
        dv = [["2025-11-07", 0.26], ["2026-02-06", 0.26], ["2026-05-08", 0.26], ["2026-08-07", 0.27]]
        c = C.cadence(dv, 100.0, dt.date(2026, 10, 1))
        self.assertEqual(c["frequency"], "quarterly")
        self.assertAlmostEqual(c["ttm"], 1.05)
        self.assertAlmostEqual(c["forward_annual"], 1.08)
        self.assertEqual(c["projected"]["ex_date"], "2026-11-06")
        self.assertAlmostEqual(c["yield_ttm"], 0.0105)

    def test_stale_history_has_no_projection(self):
        dv = [["2022-01-07", 0.1], ["2022-04-07", 0.1], ["2022-07-07", 0.1]]
        self.assertIsNone(C.cadence(dv, 10.0, dt.date(2026, 10, 1))["projected"])


class Terms(unittest.TestCase):
    def test_final_prospectus_cover(self):
        t = ("PROSPECTUS 12,500,000 Shares Example Bio, Inc. Class A Common Stock This is the initial public offering of shares of our "
             "Class A common stock. The initial public offering price is $17.00 per share. Our Class A common stock has been approved for "
             'listing on the Nasdaq Global Select Market under the symbol "EXBO". Investing involves risk.')
        r = C.price_terms(t)
        self.assertEqual(r["price"]["value"], 17.0)
        self.assertEqual(r["symbol"]["value"], "EXBO")
        self.assertEqual(r["symbol"]["exchange"], "Nasdaq Global Select")

    def test_range(self):
        t = "We currently estimate that the initial public offering price will be between $14.00 and $16.00 per share. We are offering 10,000,000 shares of common stock."
        r = C.price_terms(t)
        self.assertEqual((r["range"]["lo"], r["range"]["hi"]), (14.0, 16.0))
        self.assertEqual(r["shares"]["value"], 10_000_000)
        self.assertNotIn("price", r)

    def test_etp_filter(self):
        self.assertTrue(C._etp("Canary Staked INJ ETF", "6221"))
        self.assertFalse(C._etp("Janus Living, Inc.", "6798"))


if __name__ == "__main__":
    unittest.main()
