"""Rebuilt companies keep published reported observations: equal amounts always, disagreements held for review."""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import jobs  # noqa: E402


def prior(**k):
    row = dict(ticker="AAPL", statement="income", label="Revenue", kind="Q", period_end="2015-09-30", period_start="2015-07-01",
               concept="Revenues", unit="USD", value=100.0, accession="0000320193-15-000001", form="10-Q", filed="2015-10-27", derived=False)
    row.update(k)
    return row


def statements(**point):
    p = dict(v=100.0, end="2015-09-30", start="2015-06-28", c="Revenues", accn="0000320193-16-000009", form="10-K", filed="2016-10-26")
    p.update(point)
    return {"income": [{"label": "Revenue", "concept": "Revenues", "unit": "USD", "annual": [], "quarterly": [p]}]}


class Reconcile(unittest.TestCase):
    def test_equal_amount_with_shifted_start_keeps_published_observation(self):
        st = statements()
        kept, held = jobs.reconcile_with_published(None, "AAPL", st, existing=[prior()])
        p = st["income"][0]["quarterly"][0]
        self.assertEqual((kept, held), (1, []))
        self.assertEqual((p["start"], p["accn"], p["filed"]), ("2015-07-01", "0000320193-15-000001", "2015-10-27"))
        jobs.check_reported_fact_replacements(type("S", (), {"select": lambda self, t, q: [prior()]})(), "AAPL",
                                              jobs.statement_fact_rows("AAPL", st))

    def test_equal_derived_amount_becomes_the_reported_observation(self):
        st = statements(start="2015-07-01", derived={"minus": {}, "from": {}}, accessions=["x", "y"])
        kept, _ = jobs.reconcile_with_published(None, "AAPL", st, existing=[prior()])
        p = st["income"][0]["quarterly"][0]
        self.assertEqual(kept, 1)
        self.assertNotIn("derived", p)
        self.assertEqual(p["accessions"], ["0000320193-15-000001"])

    def test_concept_switch_at_equal_amount_is_not_the_same_observation(self):
        st = statements(c="SalesRevenueNet", start="2015-07-01")
        self.assertEqual(jobs.reconcile_with_published(None, "AAPL", copy.deepcopy(st), existing=[prior()]), (0, []))
        kept, held = jobs.reconcile_with_published(None, "AAPL", st, existing=[prior()], hold_conflicts=True)
        p = st["income"][0]["quarterly"][0]
        self.assertEqual((kept, held[0][4]), (0, "reported_accounting_scope_changed"))
        self.assertEqual((p["c"], p["review"]["alternative"]["concept"]), ("Revenues", "SalesRevenueNet"))

    def test_disagreement_blocks_without_hold_and_is_held_with_hold(self):
        st = statements(v=90.0, start="2015-07-01", derived={"minus": {}, "from": {}})
        kept, held = jobs.reconcile_with_published(None, "AAPL", copy.deepcopy(st), existing=[prior()])
        self.assertEqual((kept, held), (0, []))
        kept, held = jobs.reconcile_with_published(None, "AAPL", st, existing=[prior()], hold_conflicts=True)
        p = st["income"][0]["quarterly"][0]
        self.assertEqual(held[0][4], "reported_to_derived")
        self.assertEqual((p["v"], p["review"]["alternative"]["value"]), (100.0, 90.0))
        rows = jobs.statement_fact_rows("AAPL", st)
        jobs.check_reported_fact_replacements(type("S", (), {"select": lambda self, t, q: [prior()]})(), "AAPL", rows)

    def test_gate_still_requires_review_for_any_start_change(self):
        st = statements(v=90.0)
        with self.assertRaisesRegex(ValueError, "reported_accounting_scope_changed"):
            jobs.check_reported_fact_replacements(type("S", (), {"select": lambda self, t, q: [prior()]})(), "AAPL",
                                                  jobs.statement_fact_rows("AAPL", st))


if __name__ == "__main__":
    unittest.main()
