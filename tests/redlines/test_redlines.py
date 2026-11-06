"""Redline engine: section splitting, paragraph alignment, change classification and pair selection."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import redlines_core as R  # noqa: E402
from etl import redlines  # noqa: E402

P = "The Company relies on a limited number of suppliers for advanced semiconductors used in its products."
Q = "Net sales increased 6% to $416,161 million in 2026 compared to $391,035 million in 2025 driven by iPhone and Services."


def tenk(risk, mdna, notes):
    lines = ["Table of Contents", "Item 1A. Risk Factors 12", "Item 7. Management's Discussion and Analysis 30",
             "PART I", "Item 1. Business", "We design smartphones and other products for consumers worldwide and sell them.",
             "Item 1A.", "Risk Factors", *risk, "Item 1B. Unresolved Staff Comments", "None.",
             "Item 7. Management’s Discussion and Analysis of Financial Condition and Results of Operations", *mdna,
             "Item 8. FINANCI AL STATEMENTS AND SUPPLEMENTARY DATA"]
    for i, (title, paras) in enumerate(notes, 1):
        lines += [f"Note {i} – {title}", *paras]
    lines += ["Item 9A. Controls and Procedures", "Our disclosure controls and procedures were effective as of the end of the period covered."]
    return "\n".join(lines)


class CoreTests(unittest.TestCase):
    def test_split_items_skips_toc_and_handles_bare_and_spaced_headings(self):
        items = R.split_items(tenk([P], [Q], [("Revenue", ["Revenue is recognized when control of the promised goods transfers to customers in amounts."])]), "10-K")
        self.assertEqual(items["1A"]["paras"], [P])
        self.assertEqual(items["7"]["paras"], [Q])
        self.assertIn("8", items)
        self.assertEqual(len(R.split_notes(items["8"]["paras"])), 1)

    def test_classification(self):
        added = "The Company is subject to new export control rules that restrict shipments of certain AI accelerators to some regions."
        old = tenk([P, "Our business depends on the continued service of key personnel including senior management and engineers."], [Q], [])
        new = tenk([P.replace("a limited number of", "single or limited sources of"), added],
                   [Q.replace("416,161", "430,002").replace("391,035", "416,161")], [])
        res = R.compare(old, new, "10-K")
        risk = next(s for s in res["sections"] if s["id"] == "item:1A")
        kinds = sorted(c["kind"] for c in risk["changes"])
        self.assertEqual(kinds, ["added", "modified", "removed"])
        mod = next(c for c in risk["changes"] if c["kind"] == "modified")
        self.assertIn(["+", "single or"], mod["ops"])
        self.assertEqual(" ".join(t for op, t in mod["ops"] if op != "-"), P.replace("a limited number of", "single or limited sources of"))
        add = next(c for c in risk["changes"] if c["kind"] == "added")
        self.assertIn("tariffs/trade", add["signals"])
        mdna = next(s for s in res["sections"] if s["id"] == "item:7")
        self.assertEqual([c["kind"] for c in mdna["changes"]], ["numbers"])

    def test_notes_match_by_title_and_flag_new(self):
        old = tenk([P], [Q], [("Revenue", ["Revenue is recognized when control transfers to the customer at a point in time or over time."])])
        new = tenk([P], [Q], [("Revenue Recognition", ["Revenue is recognized when control transfers to the customer at a point in time or over time."]),
                              ("Segment Information", ["The Company reports two segments based on how the chief operating decision maker reviews results."])])
        res = R.compare(old, new, "10-K")
        notes = {s["title"]: s for s in res["sections"] if s["id"].startswith("note:")}
        self.assertEqual(notes["Note 1. Revenue Recognition"]["status"], "compared")
        self.assertEqual(notes["Note 2. Segment Information"]["status"], "new note")

    def test_word_ops_roundtrip(self):
        a, b = "the quick brown fox jumps", "the slow brown fox leaps far"
        ops = R.word_ops(a, b)
        self.assertEqual(" ".join(t for op, t in ops if op != "+"), a)
        self.assertEqual(" ".join(t for op, t in ops if op != "-"), b)


class PairTests(unittest.TestCase):
    def test_pairs_annual_and_same_quarter_prior_year(self):
        rows = [{"form": "10-K", "filed": "2026-02-20", "accn": "k2", "doc": "k2.htm", "report": "2025-12-31"},
                {"form": "10-K", "filed": "2025-02-21", "accn": "k1", "doc": "k1.htm", "report": "2024-12-31"},
                {"form": "10-Q", "filed": "2026-10-30", "accn": "q3", "doc": "q3.htm", "report": "2026-09-30"},
                {"form": "10-Q", "filed": "2026-07-30", "accn": "q2", "doc": "q2.htm", "report": "2026-06-30"},
                {"form": "10-Q", "filed": "2025-10-31", "accn": "q0", "doc": "q0.htm", "report": "2025-09-30"}]
        pairs = [(k, n["accn"], o["accn"]) for k, n, o in redlines.pick_pairs(rows)]
        self.assertEqual(pairs, [("annual", "k2", "k1"), ("quarterly", "q3", "q0")])

    def test_trim_drops_duplicate_text_when_ops_present(self):
        c = redlines._trim({"kind": "modified", "old": "a b", "new": "a c", "ops": [["=", "a"], ["-", "b"], ["+", "c"]]})
        self.assertNotIn("old", c)
        self.assertNotIn("new", c)


if __name__ == "__main__":
    unittest.main()
