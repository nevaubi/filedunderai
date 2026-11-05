"""Scheduled ingestion must preserve reported observations before publication."""
import copy
import json
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from etl import jobs, sec
from test_pipeline import FactStore, MemoryStore


Q1 = "0000320193-26-000001"
Q2 = "0000320193-26-000002"
Q3 = "0000320193-26-000003"


def observation(value, start, end, accession, filed):
    return {"val": value, "start": start, "end": end, "accn": accession,
            "filed": filed, "form": "10-Q"}


def sources(observations):
    sub = {"cik": 320193, "name": "Apple Inc.", "tickers": ["AAPL"],
           "fiscalYearEnd": "0930", "filings": {"recent": {"form": []}}}
    cf = {"cik": 320193, "entityName": "Apple Inc.", "facts": {
        "us-gaap": {"Revenues": {"units": {"USD": observations}}}}}
    return {"submissions": json.dumps(sub).encode(), "companyfacts": json.dumps(cf).encode()}


def reported_row(**changes):
    row = {"ticker": "AAPL", "statement": "income", "label": "Revenue", "kind": "Q",
           "period_start": "2026-04-01", "period_end": "2026-06-30", "concept": "Revenues",
           "unit": "USD", "value": 150, "accession": Q2, "form": "10-Q",
           "filed": "2026-08-01", "derived": False}
    return dict(row, **changes)


def cumulative_sources(total=250):
    return [observation(100, "2026-01-01", "2026-03-31", Q1, "2026-05-01"),
            observation(150, "2026-04-01", "2026-06-30", Q2, "2026-08-01"),
            observation(250, "2026-01-01", "2026-06-30", Q2, "2026-08-01"),
            observation(total, "2026-01-01", "2026-06-30", Q3, "2026-11-01")]


class CompanyStore(MemoryStore):
    def __init__(self, facts=()):
        super().__init__()
        self.facts = copy.deepcopy(list(facts))
        self.raw = {}
        self.fail_fact_write = False
        self.fail_fact_read = False

    def put_raw(self, path, body, **kwargs):
        self.raw[path] = body
        self.writes.append(("raw", path))

    def select(self, table, query):
        if table != "facts":
            return []
        if self.fail_fact_read:
            raise RuntimeError("fact lookup unavailable")
        def matches(row):
            for field, values in parse_qs(query).items():
                value = values[0]
                if field in ("select", "order"):
                    continue
                if value == "is.true" and row.get(field) is not True:
                    return False
                if value.startswith("eq.") and str(row.get(field)) != value[3:]:
                    return False
            return True
        return copy.deepcopy([row for row in self.facts if matches(row)])

    def upsert(self, table, rows, keys, **kwargs):
        rows = list(rows)
        if table == "facts":
            if self.fail_fact_write:
                raise RuntimeError("fact write unavailable")
            key = lambda row: tuple(row[field] for field in keys.split(","))
            current = {key(row): row for row in self.facts}
            current.update({key(row): copy.deepcopy(row) for row in rows})
            self.facts = list(current.values())
        super().upsert(table, rows, keys, **kwargs)


class ScheduledFactSafety(unittest.TestCase):
    def process(self, store, observations):
        # The normalizer, provenance decisions and persistence stay real;
        # only external enrichment/fetch boundaries are replaced.
        with patch.object(jobs.sec, "fill_gaps", return_value=[]), \
                patch.object(jobs.news, "company_news", return_value=[]), \
                patch.object(jobs.summaries, "company", return_value=None):
            return jobs.process_company(store, "AAPL", facts_only=False, fetch_docs=False,
                                        source_inputs=sources(observations), log=lambda *_: None)

    def test_original_company_quarantines_invalid_period_and_keeps_valid_history(self):
        prior = reported_row(period_start="2025-10-01", period_end="2025-12-31",
                             filed="2026-02-01", accession="0000320193-26-000000", value=90)
        store = CompanyStore([prior])
        good = observation(100, "2026-01-01", "2026-03-31", Q1, "2026-05-01")
        bad = observation(120, "2027-07-01", "2027-09-30", Q3, "2026-10-30")
        result = self.process(store, [good, bad])
        bundle = store.data["companies/AAPL.json"]
        self.assertEqual([p["end"] for p in bundle["statements"]["income"][0]["quarterly"]],
                         ["2026-03-31"])
        self.assertEqual(result["fund"]["latest_quarter_end"], "2026-03-31")
        excluded = bundle["quarantined_statement_points"]
        self.assertEqual(len(excluded), 2)  # Selected and history contexts both survive.
        self.assertTrue(all(p["reason"] == "period_ends_after_filing" for p in excluded))
        self.assertIn(prior, store.facts)
        self.assertFalse(any(p["period_end"] == "2027-09-30" for p in store.facts))
        archived = json.loads(store.raw[bundle["source_archive"]])
        self.assertEqual(archived["facts"]["us-gaap"]["Revenues"]["units"]["USD"], [good, bad])

    def test_all_quarantined_source_preserves_old_company_before_any_write(self):
        store = CompanyStore([reported_row()])
        previous = {"ticker": "AAPL", "statements": {"income": []}, "generated": "previous"}
        store.data["companies/AAPL.json"] = copy.deepcopy(previous)
        bad = observation(120, "2027-07-01", "2027-09-30", Q3, "2026-10-30")
        with self.assertRaisesRegex(ValueError, "No usable statement facts"):
            self.process(store, [bad])
        self.assertEqual(store.data["companies/AAPL.json"], previous)
        self.assertEqual(store.facts, [reported_row()])
        self.assertEqual((store.writes, store.db), ([], []))

    def test_equal_later_cumulative_preserves_direct_reported_quarter(self):
        store = CompanyStore([reported_row()])
        self.process(store, cumulative_sources())
        point = store.data["companies/AAPL.json"]["statements"]["income"][0]["quarterly"][-1]
        self.assertEqual((point["v"], point["accn"], point["filed"]), (150, Q2, "2026-08-01"))
        self.assertFalse(point.get("derived"))
        self.assertIn(reported_row(), store.facts)
        history = store.data["companies/AAPL.json"]["statements"]["income"][0]["filing_history"]["quarterly"]
        self.assertTrue(any(p["accn"] == Q3 and p.get("derived") for p in history))

    def test_changed_cumulative_cannot_overwrite_reported_quarter_or_publish(self):
        store = CompanyStore([reported_row()])
        previous = {"ticker": "AAPL", "generated": "previous"}
        store.data["companies/AAPL.json"] = copy.deepcopy(previous)
        with self.assertRaisesRegex(ValueError, "comparability review"):
            self.process(store, cumulative_sources(total=260))
        self.assertEqual(store.data["companies/AAPL.json"], previous)
        self.assertEqual(store.facts, [reported_row()])
        self.assertEqual((store.writes, store.db), ([], []))

    def test_missing_fact_read_blocks_publication_before_any_write(self):
        store = CompanyStore([reported_row()])
        store.fail_fact_read = True
        with self.assertRaisesRegex(RuntimeError, "fact lookup unavailable"):
            self.process(store, cumulative_sources())
        self.assertEqual((store.writes, store.db), ([], []))

    def test_failed_fact_write_does_not_replace_company_bundle(self):
        store = CompanyStore()
        previous = {"ticker": "AAPL", "generated": "previous"}
        store.data["companies/AAPL.json"] = copy.deepcopy(previous)
        store.fail_fact_write = True
        good = observation(100, "2026-01-01", "2026-03-31", Q1, "2026-05-01")
        with self.assertRaisesRegex(RuntimeError, "fact write unavailable"):
            self.process(store, [good])
        self.assertEqual(store.data["companies/AAPL.json"], previous)


class DirectPersistenceSafety(unittest.TestCase):
    def test_direct_persistence_rejects_unsafe_reported_replacements_before_upsert(self):
        changes = [
            {"derived": True, "filed": "2026-11-01", "accession": Q3},
            {"filed": "2026-07-31"},
            {"value": 151},
            {"unit": "EUR"},
            {"period_start": "2026-03-30"},
        ]
        for change in changes:
            with self.subTest(change=change):
                before = reported_row()
                store = FactStore([before])
                with self.assertRaisesRegex(ValueError, "reported"):
                    jobs.persist_normalized_facts(store, "AAPL", [dict(before, **change)])
                self.assertEqual(store.rows, [before])
                self.assertEqual(store.ops, [])

    def test_newer_direct_reported_revision_remains_supported(self):
        before = reported_row()
        revised = reported_row(value=151, accession=Q3, filed="2026-11-01")
        store = FactStore([before])
        jobs.persist_normalized_facts(store, "AAPL", [revised])
        self.assertEqual(store.rows, [revised])

    def test_equal_value_different_period_is_not_reclassified_as_reported(self):
        facts = {"us-gaap": {"Revenues": {"units": {"USD": [
            observation(100, "2026-01-01", "2026-03-29", Q1, "2026-05-01"),
            observation(150, "2026-04-01", "2026-06-30", Q2, "2026-08-01"),
            observation(250, "2026-01-01", "2026-06-30", Q3, "2026-11-01"),
        ]}}}}
        point = sec.build_series(facts, "income")[0]["quarterly"][-1]
        self.assertEqual(point["start"], "2026-03-30")
        self.assertTrue(point.get("derived"))

    def test_older_equal_reported_value_cannot_hide_an_intervening_revision(self):
        observations = cumulative_sources() + [
            observation(160, "2026-04-01", "2026-06-30",
                        "0000320193-26-000004", "2026-09-01")]
        facts = json.loads(sources(observations)["companyfacts"])["facts"]
        point = sec.build_series(facts, "income")[0]["quarterly"][-1]
        self.assertEqual((point["v"], point["accn"], point["filed"]),
                         (160, "0000320193-26-000004", "2026-09-01"))
        self.assertFalse(point.get("derived"))
        self.assertEqual(point['selection_conflict']['alternative']['v'], 150)
        before = reported_row(value=160, accession="0000320193-26-000004",
                              filed="2026-09-01")
        store = CompanyStore([before])
        with self.assertRaisesRegex(ValueError, "comparability review"):
            ScheduledFactSafety().process(store, observations)
        self.assertEqual(store.facts, [before])
        self.assertEqual((store.writes, store.db), ([], []))


if __name__ == "__main__":
    unittest.main()
