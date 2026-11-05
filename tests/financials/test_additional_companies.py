import copy
import unittest
from unittest.mock import patch

from etl import universe


class AdditionalCompanyRegistryTests(unittest.TestCase):
    def manifest(self):
        return {"schema_version": 1, "cohort_id": "test", "active_tickers": [], "companies": [
            {"ticker": "NEW", "cik": 9999999, "name": "New Issuer", "sector": "Industrials"}]}

    def test_pending_companies_are_addressable_but_not_scheduled(self):
        approved, active = universe.load_additional_registry(self.manifest(), universe.SP500_LIST)
        self.assertEqual(approved, [("NEW", 9999999, "New Issuer", "Industrials")])
        self.assertEqual(active, [])
        document = self.manifest()
        document["active_tickers"] = ["NEW"]
        self.assertEqual(universe.load_additional_registry(document, universe.SP500_LIST)[1], approved)

    def test_registry_rejects_duplicate_identity_and_unknown_activation(self):
        for field, value in [("ticker", "AAPL"), ("cik", 320193), ("cik", True),
                             ("sector", "Unknown"), ("ticker", "bad ticker")]:
            document = self.manifest()
            document["companies"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                universe.load_additional_registry(document, universe.SP500_LIST)
        document = self.manifest()
        document["companies"].append(copy.deepcopy(document["companies"][0]))
        with self.assertRaises(ValueError):
            universe.load_additional_registry(document, universe.SP500_LIST)
        document = self.manifest()
        document["active_tickers"] = ["UNKNOWN"]
        with self.assertRaises(ValueError):
            universe.load_additional_registry(document, universe.SP500_LIST)

    def test_original_index_membership_is_preserved(self):
        self.assertEqual(len(universe.SP500_LIST), 500)
        self.assertEqual(universe.SP500, {t for t, *_ in universe.SP500_LIST})
        self.assertTrue(universe.SP100 <= universe.SP500)
        self.assertEqual(len(universe.ADDITIONAL_LIST), 300)
        self.assertFalse(universe.SP500 & {t for t, *_ in universe.ADDITIONAL_LIST})
        self.assertEqual(len({c for _, c, *_ in universe.SP500_LIST + universe.ADDITIONAL_LIST}), 800)
        self.assertTrue(all(t in universe.BY_TICKER for t, *_ in universe.ADDITIONAL_LIST))

    def test_public_index_excludes_additions_from_sp500_membership(self):
        from etl import jobs
        from test_pipeline import MemoryStore
        sb = MemoryStore()
        sb.data["meta/fundamentals.json"] = {t: {"name": t, "sector": "Industrials", "fund": {}}
                                               for t in ("AAPL", "OKTA")}
        sample = [universe.SP500_LIST[0], universe.ADDITIONAL_LIST[0]]
        with patch.object(jobs, "UNIVERSE", sample):
            jobs.build_index(sb, pstats={"AAPL": {}, "OKTA": {}})
        bundle = sb.data["index.json"]
        members = {r["ticker"]: r for r in bundle["companies"]}
        self.assertEqual(bundle["universe"], {"id": "tracked", "membership_version": 1})
        self.assertTrue(members["AAPL"]["sp500"])
        self.assertFalse(members["OKTA"]["sp500"])
        self.assertFalse(members["OKTA"]["sp100"])


if __name__ == "__main__":
    unittest.main()
