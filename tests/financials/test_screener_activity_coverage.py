import datetime as dt
import unittest
from unittest.mock import patch

from etl import screener


class ActivityStore:
    def __init__(self, news=None, transactions=None, filings=None):
        self.rows = {"news_items":news or [], "insider_transactions":transactions or [], "filings":filings or []}

    def select(self, table, query):
        return self.rows[table]


class ScreenerActivityCoverageTests(unittest.TestCase):
    def activity(self, sb):
        with patch.object(screener, "TICKERS", ["AAPL", "OKTA"]):
            return screener.activity(sb, {"AAPL":{}, "OKTA":{}}, dt.date(2026, 11, 5))

    def test_new_company_uncollected_windows_are_unavailable(self):
        out, provenance = self.activity(ActivityStore())
        self.assertEqual(out["OKTA"], {})
        self.assertEqual(provenance["OKTA"], {})
        self.assertEqual(out["AAPL"]["news_30d"], 0)
        for key in ("ins_net_90d", "ins_net_90d_n", "ins_buyers_90d", "ins_net_1y", "ins_net_1y_n", "ins_buyers_1y"):
            self.assertEqual(out["AAPL"][key], 0)
            self.assertIn(key, provenance["AAPL"])

    def test_legacy_financial_exclusion_does_not_erase_known_news_window(self):
        with patch.object(screener, "TICKERS", ["AAPL", "OKTA"]):
            out, provenance = screener.activity(ActivityStore(), {}, dt.date(2026, 11, 5))
        self.assertEqual(out["AAPL"].get("news_30d"), 0)
        self.assertIn("news_30d", provenance["AAPL"])
        self.assertNotIn("news_30d", out["OKTA"])

    def test_stored_rows_do_not_establish_new_company_window_coverage(self):
        news = [{"ticker":"OKTA", "url":"https://example.test/okta", "title":"Reported event", "published_at":"2026-11-01"}]
        transactions = [{"ticker":"OKTA", "owner":"Officer", "code":"P", "shares":10,
                         "price":50, "tx_date":"2026-11-01"}]
        out, provenance = self.activity(ActivityStore(news=news, transactions=transactions))
        self.assertEqual(out["OKTA"], {})
        self.assertEqual(provenance["OKTA"], {})

    def test_existing_company_collected_values_and_provenance_are_unchanged(self):
        news = [{"ticker":"AAPL", "url":"https://example.test/aapl", "title":"Reported event", "published_at":"2026-11-01"}]
        transactions = [{"ticker":"AAPL", "owner":"Officer", "code":"P", "shares":10,
                         "price":50, "tx_date":"2026-11-01"}]
        out, provenance = self.activity(ActivityStore(news=news, transactions=transactions))
        self.assertEqual(out["AAPL"]["news_30d"], 1)
        self.assertEqual(out["AAPL"]["ins_net_90d"], 500)
        self.assertEqual(out["AAPL"]["ins_net_90d_n"], 1)
        self.assertEqual(out["AAPL"]["ins_buyers_90d"], 1)
        self.assertEqual(provenance["AAPL"]["ins_net_90d"]["x"], "Open-market purchases $500 − sales $0 (Form 4, codes P and S)")
        self.assertEqual(provenance["AAPL"]["news_30d"]["u"], [[news[0]["url"], "Reported event", "2026-11-01"]])

    def test_verified_filing_activity_remains_available_for_new_company(self):
        filings = [{"ticker":"OKTA", "form":"10-Q", "filed":"2026-11-01", "accession":"0001660134-26-000001",
                    "items":"", "primary_doc":"okta-20261031.htm"}]
        out, provenance = self.activity(ActivityStore(filings=filings))
        self.assertEqual(out["OKTA"], {"days_10q":4})
        self.assertEqual(provenance["OKTA"]["days_10q"]["a"], [[filings[0]["accession"], "10-Q", "2026-11-01", "okta-20261031.htm"]])


if __name__ == "__main__":
    unittest.main()
