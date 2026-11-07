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


class DeclarationsLater(unittest.TestCase):
    def test_dates_for_a_defined_term_in_a_later_paragraph(self):
        t = ("Item 8.01. Other Events. On September 15, 2026, the Board authorized and the Company declared a dividend of $0.01 per share on the "
             "Company's common shares of beneficial interest, $0.01 par value per share (\"Common Shares\"), for the quarter ending September 30, 2026 "
             "(the \"Common Dividend\"). On September 15, 2026, the Board also authorized and the Company declared a quarterly dividend of $0.39844 per "
             "share on the Company's 6.375% Series E Cumulative Redeemable Preferred Shares (the \"Series E Preferred Dividend\"). The Series E Preferred "
             "Dividend is payable on October 15, 2026 to holders of record of Series E Preferred Shares as of October 1, 2026. The Common Dividend is "
             "payable on October 15, 2026 to holders of record of Common Shares as of the close of business on September 30, 2026.")
        d = C.parse_declaration(t, "2026-09-15")
        self.assertEqual([(x["amount"], x["record"], x["payable"]) for x in d], [(0.01, "2026-09-30", "2026-10-15")])
        self.assertIn("The Common Dividend is payable", d[0]["quote"])

    def test_share_classes(self):
        t = ("The Board declared quarterly dividends of $0.25 per share on its Class A common stock and $0.275 per share on its Class B common stock, "
             "payable on December 10, 2026 to stockholders of record on November 20, 2026.")
        d = C.parse_declaration(t, "2026-11-01")
        self.assertEqual(sorted((x["share_class"], x["amount"]) for x in d), [("Class A", 0.25), ("Class B", 0.275)])


class IpoTerms(unittest.TestCase):
    def test_price_phrasings_and_shares(self):
        for t, v in [("The initial public offering price per share of the Class A common stock is $18.00 . We have been approved", 18.0),
                     ("The initial public offering price of Class A common stock will be $16.00 per share. We have", 16.0),
                     ("The public offering price for our ordinary shares is $5.00 per ordinary share. We have been approved", 5.0)]:
            self.assertEqual(C.price_terms(t)["price"]["value"], v, t)
        r = C.price_terms("We are offering, on a firm commitment basis, 6,250,000 Ordinary Shares. Prior to this offering")
        self.assertEqual(r["shares"]["value"], 6_250_000)

    def test_preliminary_ranges_and_expected_price(self):
        pre = "PRELIMINARY PROSPECTUS Subject to Completion. "
        r = C.price_terms(pre + "We expect the initial public offering price will be between US$4.0 to US$6.0 per Ordinary Share.")
        self.assertEqual((r["range"]["lo"], r["range"]["hi"]), (4.0, 6.0))
        r = C.price_terms(pre + "The assumed price is the midpoint of the estimated initial public offering price range of $4.00 to $ 6.00 per ordinary share.")
        self.assertEqual((r["range"]["lo"], r["range"]["hi"]), (4.0, 6.0))
        r = C.price_terms(pre + "The initial public offering price for our common stock is expected to be $5.00 per share. We have applied")
        self.assertEqual((r["range"]["lo"], r["range"].get("assumed")), (5.0, True))
        self.assertNotIn("price", r)  # a preliminary prospectus has no final price
        r = C.price_terms(pre + "The company is offering up to 35,000,000 shares at a fixed offering price of $0.01 per share for maximum gross proceeds")
        self.assertEqual(r["range"]["lo"], 0.01)

    def test_underwriters_block(self):
        t = ("Any representation to the contrary is a criminal offense. The underwriters expect to deliver the shares on May 15, 2026. "
             "MORGAN STANLEY J.P. Morgan Goldman Sachs & Co. LLC Co-Managers Keefe, Bruyette & Woods A Stifel Company Tuohy Brothers "
             "__________________ Prospectus dated May 13, 2026 Table of Contents")
        u = C.underwriters(t)
        self.assertEqual(u["names"], ["Morgan Stanley", "J.P. Morgan", "Goldman Sachs & Co. LLC", "Keefe, Bruyette & Woods", "Tuohy Brothers"])
        self.assertIn("MORGAN STANLEY", u["quote"])
        self.assertIsNone(C.underwriters("No cover marker here."))


class IpoStatusAndFirstDay(unittest.TestCase):
    def test_stalled_after_thirty_days(self):
        reg = [{"form": "S-1", "date": "2026-06-01"}]
        eff = [{"form": "EFFECT", "date": "2026-09-01"}]
        self.assertEqual(C.ipo_status(reg, [], eff, [], dt.date(2026, 9, 20)), "effective")
        self.assertEqual(C.ipo_status(reg, [], eff, [], dt.date(2026, 11, 7)), "stalled")
        self.assertEqual(C.ipo_status(reg, [{"form": "424B4"}], eff, [], dt.date(2026, 11, 7)), "priced")

    def test_first_day_against_offer(self):
        ts = lambda d: int(dt.datetime(d.year, d.month, d.day, 14, 30, tzinfo=dt.timezone.utc).timestamp())
        bars = [[ts(dt.date(2026, 10, 30)), 14.7, 14.9, 11.6, 14.89, 1], [ts(dt.date(2026, 11, 6)), 13.0, 13.5, 12.0, 13.2, 1]]
        row = {"priced": "2026-10-29", "symbol": "ASPE", "price": {"value": 15.0}}
        f = C.first_day(row, dt.date(2026, 11, 7), chart=lambda s, r, i: {"bars": bars})
        self.assertEqual(f["date"], "2026-10-30")
        self.assertAlmostEqual(f["close_ret"], 14.89 / 15 - 1, 6)
        self.assertAlmostEqual(f["since_ret"], 13.2 / 15 - 1, 6)
        split = C.first_day(row, dt.date(2026, 11, 7), chart=lambda s, r, i: {"bars": bars, "splits": [{"date": "2026-11-02", "numerator": 2, "denominator": 1}]})
        self.assertAlmostEqual(split["since_ret"], 13.2 / 7.5 - 1, 6)
        late = [[ts(dt.date(2026, 11, 25)), 1, 1, 1, 1, 1]]
        self.assertIsNone(C.first_day(row, dt.date(2026, 11, 30), chart=lambda s, r, i: {"bars": late}))


class EarningsCalendarEntry(unittest.TestCase):
    def test_last_report_move_and_typical_reaction(self):
        from etl.estimates_sources import _calendar_entry
        sur = [{"quarter_end": f"2026-0{m}-30", "eps_actual": 1.0, "eps_estimate": 0.9, "surprise_pct": 0.11, "beat": 1,
                "report": {"filed": f"2026-0{m + 1}-05", "accn": "x", "url": "u"}, "reaction": {"date": f"2026-0{m + 1}-06", "r1": r}}
               for m, r in [(1, 0.02), (3, -0.06), (5, 0.04), (7, -0.01)]]
        e = _calendar_entry({"next_earnings": {"date": "2026-11-05", "is_estimate": False, "eps": {"avg": 1.1}}}, {"surprises": sur})
        self.assertEqual(e["last"]["move"], -0.01)
        self.assertEqual(e["last"]["report"]["filed"], "2026-08-05")
        self.assertAlmostEqual(e["reaction"]["median_abs_move"], 0.03)
        self.assertEqual(e["reaction"]["n"], 4)
        self.assertIsNone(_calendar_entry({}, {"surprises": []})["reaction"])


if __name__ == "__main__":
    unittest.main()
