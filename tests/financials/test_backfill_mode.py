import hashlib
import json
import unittest
from unittest.mock import patch

from etl import jobs
from test_pipeline import MemoryStore


class FactsOnlyModeTests(unittest.TestCase):
    def sources(self, cik=320193):
        sub = {"cik": cik, "name": "Apple Inc.", "tickers": ["AAPL"], "fiscalYearEnd": "0930", "filings": {"recent": {"form": []}}}
        fact = {"val": 100, "start": "2026-07-01", "end": "2026-09-30", "accn": "0000320193-26-000001",
                "filed": "2026-10-30", "form": "10-Q", "fy": 2026, "fp": "Q4"}
        cf = {"cik": cik, "entityName": "Apple Inc.", "facts": {"us-gaap": {"Revenues": {"units": {"USD": [fact]}}}}}
        return {"submissions": json.dumps(sub).encode(), "companyfacts": json.dumps(cf, indent=2).encode()}

    def test_explicit_facts_only_never_calls_enrichment_or_gap_filler(self):
        sb = MemoryStore()
        archived = {}
        sb.put_raw = lambda path, body, **kw: archived.update({path: body})
        inputs = self.sources()
        with patch.object(jobs.news, "company_news", side_effect=AssertionError("news forbidden")) as news, \
             patch.object(jobs.summaries, "company", side_effect=AssertionError("model forbidden")) as summary, \
             patch.object(jobs.sec, "fill_gaps", side_effect=AssertionError("unarchived gap fill forbidden")) as gaps, \
             patch.object(jobs.sec, "companyfacts", side_effect=AssertionError("refetch forbidden")), \
             patch.object(jobs.sec, "submissions", side_effect=AssertionError("refetch forbidden")):
            jobs.process_company(sb, "AAPL", facts_only=True, fetch_docs=False, source_inputs=inputs, log=lambda *_: None)
        news.assert_not_called()
        summary.assert_not_called()
        gaps.assert_not_called()
        bundle = sb.data["companies/AAPL.json"]
        self.assertEqual(bundle["provenance"]["status"], "verified")
        self.assertEqual(bundle["provenance"]["snapshot_sha256"], hashlib.sha256(inputs["companyfacts"]).hexdigest())
        self.assertEqual(archived[bundle["source_archive"]], inputs["companyfacts"])
        self.assertEqual(bundle["news"], {})
        self.assertIsNone(bundle["summary"])

    def test_wrong_issuer_is_rejected_before_any_mutation(self):
        sb = MemoryStore()
        sb.put_raw = lambda *_a, **_k: self.fail("source written before issuer validation")
        with self.assertRaisesRegex(ValueError, "issuer|CIK|identity"):
            jobs.process_company(sb, "AAPL", facts_only=True, fetch_docs=False, source_inputs=self.sources(123))
        self.assertEqual(sb.writes, [])
        self.assertEqual(sb.db, [])

    def test_facts_only_requires_immutable_archiving(self):
        sb = MemoryStore()
        with self.assertRaisesRegex(ValueError, "archive|archiv"):
            jobs.process_company(sb, "AAPL", facts_only=True, store_raw=False, source_inputs=self.sources())
        self.assertEqual(sb.writes, [])


if __name__ == "__main__":
    unittest.main()
