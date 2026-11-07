"""Shares outstanding from the inline XBRL cover page when companyfacts has no current class-free count."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import sec  # noqa: E402


def ctx(cid, end, member=None):
    seg = (f'<xbrli:segment><xbrldi:explicitMember dimension="us-gaap:StatementClassOfStockAxis">us-gaap:{member}</xbrldi:explicitMember>'
           '</xbrli:segment>') if member else ""
    return f'<xbrli:context id="{cid}"><xbrli:entity><xbrli:identifier scheme="x">1</xbrli:identifier>{seg}</xbrli:entity><xbrli:period><xbrli:instant>{end}</xbrli:instant></xbrli:period></xbrli:context>'


def fact(cid, value):
    return f'<ix:nonFraction name="dei:EntityCommonStockSharesOutstanding" contextRef="{cid}" unitRef="shares" decimals="0">{value}</ix:nonFraction>'


FILINGS = [{"f": "10-Q", "d": "2026-10-29", "a": "0001652044-26-000099", "p": "goog-20260930.htm"},
           {"f": "8-K", "d": "2026-11-01", "a": "x", "p": "a.htm"}]


class CoverPage(unittest.TestCase):
    def test_classes_are_summed_one_for_one_by_default(self):
        html = ctx("a", "2026-10-21", "CommonClassAMember") + ctx("b", "2026-10-21", "CommonClassBMember") + ctx("c", "2026-10-21", "CapitalClassCMember") \
            + fact("a", "5,888,000,000") + fact("b", "827,000,000") + fact("c", "5,579,000,000")
        r = sec.cover_page_shares(1652044, FILINGS, ticker="GOOGL", fetch=lambda url: html)
        self.assertEqual(r["val"], 12_294_000_000)
        self.assertEqual((r["end"], r["accn"], r["form"], r["basis"]), ("2026-10-21", "0001652044-26-000099", "10-Q", "reported_outstanding"))
        self.assertEqual(len(r["parts"]), 3)

    def test_non_equivalent_classes_use_listed_class_equivalents(self):
        html = ctx("a", "2026-07-29", "CommonClassAMember") + ctx("b", "2026-07-29", "CommonClassBMember") + fact("a", "488,450") + fact("b", "1,408,035,161")
        r = sec.cover_page_shares(1067983, FILINGS, ticker="BRK-B", fetch=lambda url: html)
        self.assertEqual(r["val"], 488_450 * 1500 + 1_408_035_161)

    def test_undimensioned_count_wins_and_stale_companyfacts_is_replaced(self):
        html = ctx("t", "2026-10-15") + ctx("a", "2026-10-15", "CommonClassAMember") + fact("t", "100") + fact("a", "60")
        facts = {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 5, "end": "2010-03-31", "accn": "old", "filed": "2010-05-01", "form": "10-Q"}]}}}}
        r = sec.current_shares(facts, FILINGS, 1, ticker="X", fetch=lambda url: html)
        self.assertEqual((r["val"], r["resolved"]), (100, "cover_page_inline_xbrl"))

    def test_current_companyfacts_count_is_kept_without_a_fetch(self):
        facts = {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
            {"val": 5, "end": "2026-10-20", "accn": "a", "filed": "2026-10-29", "form": "10-Q"}]}}}}
        def boom(url):
            raise AssertionError("no fetch expected")
        self.assertEqual(sec.current_shares(facts, FILINGS, 1, fetch=boom)["val"], 5)


if __name__ == "__main__":
    unittest.main()
