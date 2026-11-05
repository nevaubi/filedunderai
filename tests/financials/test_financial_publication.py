"""Regression coverage for valuation dates and publication of normalized inputs."""
import copy
import datetime as dt
import inspect
import textwrap
import unittest
from unittest.mock import patch

from etl import fundamentals, jobs, screener
from test_pipeline import MemoryStore


def metadata(version=2, eps=2):
    return {"name": "Test Company", "sector": "Industrials", "pipeline_version": version,
            "material_accession": "same-filing", "shares": 100,
            "shares_metadata": {"val": 100, "end": "2026-10-20", "filed": "2026-10-25",
                                "basis": "reported_outstanding"},
            "fund": {"eps_ttm": eps, "revenue_ttm": 1000, "latest_quarter_end": "2026-09-30"},
            "div_ttm": 10, "bb_ttm": 20}


class ValuationDates(unittest.TestCase):
    def test_market_cap_rejects_future_stale_unavailable_or_invalid_shares(self):
        shares = metadata()["shares_metadata"]
        cases = [
            {"end": "2027-07-17", "filed": "2026-07-23"},
            {"end": "2026-11-05", "filed": "2026-11-05"},
            {"end": "2025-09-29"},
            {"end": "2026-02-31"}, {"end": "20261020"}, {"end": None},
            {"filed": "2026-11-05"}, {"available_at": "2026-11-05"},
            {"filed": "2026-10-19"}, {"available_at": "2026-10-19"},
            {"filed": "invalid"}, {"available_at": "2026-02-31"},
            {"derived": True}, {"basis": "weighted_diluted_proxy"},
            {"val": -28_000_000}, {"val": 0}, {"val": float("nan")},
            {"val": float("inf")}, {"val": True}, {"val": "100"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertIsNone(fundamentals.market_cap(10, dict(shares, **changes), "2026-11-04"))
        for price in (0, -10, float("nan"), float("inf"), True, "10"):
            with self.subTest(price=price):
                self.assertIsNone(fundamentals.market_cap(price, shares, "2026-11-04"))
        self.assertIsNone(fundamentals.market_cap(10, shares, "2026-02-31"))
        self.assertIsNone(fundamentals.market_cap(1e308, dict(shares, val=1e308), "2026-11-04"))

    def test_market_cap_accepts_reported_shares_at_zero_and_400_days(self):
        as_of = dt.date(2026, 11, 4)
        for age in (0, 400):
            shares = {"val": 100, "end": (as_of - dt.timedelta(days=age)).isoformat(),
                      "basis": "reported_outstanding", "derived": False}
            with self.subTest(age=age):
                self.assertEqual(fundamentals.market_cap(10, shares, as_of.isoformat()), 1000)


class FinancialPublication(unittest.TestCase):
    def test_company_job_publishes_the_snapshot_it_just_processed_despite_stale_storage_reads(self):
        class DelayedReadStore(MemoryStore):
            def get_json(self, path):
                if path == "meta/fundamentals.json":
                    return {"TEST": metadata(eps=19.16)}
                return super().get_json(path)

        sb = DelayedReadStore()
        prices = {"TEST": {"price": 10, "as_of": "2026-11-04"}}
        fresh = metadata(eps=None)
        with patch.object(jobs, "UNIVERSE", [("TEST", 1, "Test Company", "Industrials")]), \
                patch.object(jobs, "process_company", return_value=fresh), \
                patch.object(jobs, "load_pricestats", return_value=prices):
            done, failed = jobs.companies_job(sb, ["TEST"], workers=1)
        self.assertEqual((done, failed), (["TEST"], {}))
        published = sb.data["index.json"]["companies"][0]
        self.assertIsNone(published["eps_ttm"])
        self.assertIsNone(published["pe_ttm"])
        self.assertEqual(published["market_cap"], 1000)
        db_row = next(rows[0] for table, rows in sb.db if table == "company_metrics")
        self.assertIsNone(db_row["eps_ttm"])

    def test_index_keeps_legacy_company_identity_and_prices_but_holds_uncertified_financials(self):
        sb = MemoryStore()
        sb.data["meta/fundamentals.json"] = {"TEST": metadata(version=1)}
        with patch.object(jobs, "UNIVERSE", [("TEST", 1, "Test Company", "Industrials")]):
            jobs.build_index(sb, pstats={"TEST": {"price": 10, "as_of": "2026-11-04"}})
        row = sb.data["index.json"]["companies"][0]
        self.assertEqual(row["name"], "Test Company")
        self.assertEqual(row["price"], 10)
        self.assertEqual(row["financial_status"], "normalization_pending")
        for field in ("eps_ttm", "pe_ttm", "revenue_ttm", "dividend_yield", "buyback_yield"):
            with self.subTest(field=field):
                self.assertIsNone(row[field])

    def test_screener_reextracts_same_filing_after_normalization_and_holds_failed_old_cache(self):
        fm = metadata()
        old_key = "same-filing|2026-09-30|100"
        cache = {"_v": screener.FUND_VERSION, "TEST": {"key": old_key, "q": {"EPS diluted": [["old"]]}}}
        with patch.object(screener, "TICKERS", ["TEST"]), \
                patch.object(screener, "gz_json", return_value=cache), \
                patch.object(screener, "extract_company", side_effect=RuntimeError("stale source bundle")), \
                patch.object(screener, "put_gz"):
            result = screener.update_fund(MemoryStore(), {"TEST": fm}, {}, workers=1, log=lambda *a: None)
        self.assertNotIn("TEST", result)

    def test_screener_does_not_extract_or_reuse_unversioned_financials(self):
        fm = metadata()
        fm.pop("pipeline_version")
        cache = {"_v": screener.FUND_VERSION, "TEST": {"key": "same-filing|2026-09-30|100", "q": {}}}
        with patch.object(screener, "TICKERS", ["TEST"]), \
                patch.object(screener, "gz_json", return_value=cache), \
                patch.object(screener, "extract_company") as extract, \
                patch.object(screener, "put_gz"):
            result = screener.update_fund(MemoryStore(), {"TEST": fm}, {}, workers=1, log=lambda *a: None)
        self.assertNotIn("TEST", result)
        extract.assert_not_called()

    def test_screener_price_earnings_does_not_require_market_cap(self):
        src = inspect.getsource(screener.screener_job.__wrapped__)
        code = textwrap.dedent(src[src.index('            price = out.get("price")'):src.index('            # correlations')])
        out, prov = {"price": 10}, screener.Prov()
        inp = {"eps": 2, "recs": {"eps": []}}
        scope = dict(screener.__dict__, out=out, prov=prov, inp=inp, fundc={"TEST": {}}, fm={}, tk="TEST",
                     dates=["2026-11-04"], c=[10], fx=None, pp={"price": {"p": [["2026-11-04", 10]]}})
        exec(compile(code, "production_valuation_branch", "exec"), scope)
        self.assertNotIn("mcap", out)
        self.assertEqual(out.get("pe"), 5)
        self.assertEqual(out.get("earn_yield"), .2)
        self.assertEqual(prov.f["pe"]["p"], [["2026-11-04", 10]])


if __name__ == "__main__":
    unittest.main()
