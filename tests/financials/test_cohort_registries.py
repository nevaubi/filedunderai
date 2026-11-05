import json
import tempfile
import unittest
from pathlib import Path

from etl import universe


def registry(cohort_id, companies, active=None):
    return {"schema_version": 1, "cohort_id": cohort_id, "companies": companies,
            "active_tickers": [c["ticker"] for c in companies] if active is None else active}


def company(ticker, cik, sector="Industrials"):
    return {"ticker": ticker, "cik": cik, "name": f"{ticker} Inc.", "sector": sector}


class CohortRegistryTests(unittest.TestCase):
    def write(self, folder, name, doc):
        path = Path(folder) / name
        path.write_text(json.dumps(doc))
        return path

    def test_cohorts_load_in_order_and_activate_independently(self):
        base = [("AAA", 1, "Base", "Industrials")]
        with tempfile.TemporaryDirectory() as folder:
            first = self.write(folder, "a.json", registry("c1", [company("BBB", 2), company("CCC", 3)]))
            second = self.write(folder, "b.json", registry("c2", [company("DDD", 4), company("EEE", 5)], active=["DDD"]))
            regs, approved, active = universe.load_registries([first, second], base)
        self.assertEqual([r["cohort_id"] for r in regs], ["c1", "c2"])
        self.assertEqual([t for t, *_ in approved], ["BBB", "CCC", "DDD", "EEE"])
        self.assertEqual([t for t, *_ in active], ["BBB", "CCC", "DDD"])

    def test_a_later_cohort_cannot_reuse_a_ticker_or_cik(self):
        base = [("AAA", 1, "Base", "Industrials")]
        with tempfile.TemporaryDirectory() as folder:
            first = self.write(folder, "a.json", registry("c1", [company("BBB", 2)]))
            dup_ticker = self.write(folder, "b.json", registry("c2", [company("BBB", 9)]))
            dup_cik = self.write(folder, "c.json", registry("c3", [company("ZZZ", 2)]))
            for second in (dup_ticker, dup_cik):
                with self.assertRaises(ValueError):
                    universe.load_registries([first, second], base)

    def test_cohort_ids_must_be_unique(self):
        with tempfile.TemporaryDirectory() as folder:
            first = self.write(folder, "a.json", registry("same", [company("BBB", 2)]))
            second = self.write(folder, "b.json", registry("same", [company("CCC", 3)]))
            with self.assertRaises(ValueError):
                universe.load_registries([first, second], [])

    def test_live_registry_lookup(self):
        first = universe.ADDITIONAL_REGISTRIES[0]
        ticker = first["companies"][0]["ticker"]
        self.assertIs(universe.registry_for(ticker), first)
        self.assertIs(universe.cohort_registry([ticker]), first)
        self.assertIs(universe.ADDITIONAL_REGISTRY, first)
        with self.assertRaises(ValueError):
            universe.registry_for("AAPL")
        with self.assertRaises(ValueError):
            universe.registry_by_id("no-such-cohort")


if __name__ == "__main__":
    unittest.main()
