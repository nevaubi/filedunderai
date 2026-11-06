"""New additive issuers may publish directly reported values while retaining reported-vs-derived disagreements."""
import copy
import unittest
from unittest.mock import patch

from etl import jobs
from test_company_fact_safety import CompanyStore, sources, observation
from test_reported_preference import LATER


def conflicted():
    return [observation(50, "2026-01-01", "2026-03-31", "0000320193-26-000001", "2026-05-01"),
            observation(100, "2026-04-01", "2026-06-30", LATER, "2026-11-01"),
            observation(160, "2026-01-01", "2026-06-30", LATER, "2026-11-01")]


class RetainedReportedConflictTests(unittest.TestCase):
    def process(self, store, observations, **kwargs):
        with patch.object(jobs.sec, "fill_gaps", return_value=[]), \
                patch.object(jobs.news, "company_news", return_value=[]), \
                patch.object(jobs.summaries, "company", return_value=None):
            return jobs.process_company(store, "AAPL", facts_only=False, fetch_docs=False,
                                        source_inputs=sources(observations), log=lambda *_: None, **kwargs)

    def test_default_still_blocks_new_issuer(self):
        store = CompanyStore()
        with self.assertRaisesRegex(ValueError, "comparability review"):
            self.process(store, conflicted())
        self.assertEqual((store.writes, store.db, store.facts), ([], [], []))

    def test_opt_in_publishes_reported_value_and_keeps_conflict(self):
        store = CompanyStore()
        self.process(store, conflicted(), retain_reported_conflicts=True)
        bundle = store.data["companies/AAPL.json"]
        point = bundle["statements"]["income"][0]["quarterly"][-1]
        self.assertEqual(point["v"], 100)
        self.assertFalse(point.get("derived"))
        self.assertEqual(point["selection_conflict"]["alternative"]["v"], 110)
        self.assertTrue(any("retained for comparability review" in w for w in bundle["warnings"]))

    def test_opt_in_refresh_publishes_reported_value_over_prior_bundle(self):
        store = CompanyStore()
        store.data["companies/AAPL.json"] = {"ticker": "AAPL", "generated": "previous"}
        self.process(store, conflicted(), retain_reported_conflicts=True)
        point = store.data["companies/AAPL.json"]["statements"]["income"][0]["quarterly"][-1]
        self.assertEqual((point["v"], bool(point.get("derived"))), (100, False))

    def test_reported_value_ambiguity_still_blocks(self):
        observations = conflicted() + [observation(101, "2026-04-01", "2026-06-30", "0000320193-26-000009", "2026-11-01")]
        store = CompanyStore()
        with self.assertRaisesRegex(ValueError, "comparability review"):
            self.process(store, observations, retain_reported_conflicts=True)
        self.assertEqual((store.writes, store.db, store.facts), ([], [], []))

    def test_scheduled_companies_job_opts_in(self):
        seen = {}
        def fake(sb, tk, **kwargs):
            seen.update(kwargs)
            raise ValueError("stop")
        with patch.object(jobs, "process_company", side_effect=fake), patch.object(jobs, "_cache", return_value={}), \
                patch.object(jobs, "Run"):
            try:
                jobs.companies_job.__wrapped__(object(), ["AAPL"], workers=1)
            except Exception:
                pass
        self.assertTrue(seen.get("retain_reported_conflicts"))

if __name__ == "__main__":
    unittest.main()
