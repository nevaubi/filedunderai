"""Filing language themes: matching, boilerplate, like-for-like comparison, added/dropped, baskets."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import themes as T  # noqa: E402


def defs():
    return T.load_defs()


def filing(accn, form, filed, report, hits, words=None):
    return {"a": accn, "f": form, "d": filed, "r": report, "p": "doc.htm", "w": words or {"1A": 1000, "7": 1000}, "hits": hits}


def hit(text, sec="1A", neg=False):
    r = {"s": sec, "h": T.shash(text), "t": text}
    if neg:
        r["n"] = 1
    return r


class Matching(unittest.TestCase):
    def test_tariff_rate_tariffs_of_utilities_do_not_count(self):
        d = defs()
        self.assertEqual([h[0] for h in T.match_sentence(d, "New tariffs on imports from China raised our product costs in the quarter.")], ["tariffs", "china"])
        self.assertNotIn("tariffs", [h[0] for h in T.match_sentence(d, "The FERC approved our open access transmission tariff filing in March 2026.")])

    def test_ai_is_case_sensitive_and_negation_flagged(self):
        d = defs()
        self.assertIn("ai", [h[0] for h in T.match_sentence(d, "Demand for AI servers drove growth in our data center business.")])
        self.assertNotIn("ai", [h[0] for h in T.match_sentence(d, "We paid the claim to the insurer said the adjuster.")])
        neg = T.match_sentence(d, "Tariffs did not have a material impact on our results of operations during the year.")
        self.assertTrue(neg and neg[0][1])

    def test_union_excludes_european_union(self):
        d = defs()
        self.assertNotIn("labor", [h[0] for h in T.match_sentence(d, "Sales in the European Union grew 4% on stronger pricing in our core markets.")])
        self.assertIn("labor", [h[0] for h in T.match_sentence(d, "Approximately 30% of our employees are covered by collective bargaining agreements.")])

    def test_cyber_incident_needs_an_actual_incident(self):
        d = defs()
        self.assertIn("cyber_incidents", [h[0] for h in T.match_sentence(d, "In August 2026, we detected unauthorized access to certain of our information technology systems.")])
        self.assertNotIn("cyber_incidents", [h[0] for h in T.match_sentence(d, "We could experience a cybersecurity incident that disrupts our operations and harms our reputation.")])

    def test_sentences_drop_tables_and_fragments(self):
        out = T.sentences("Net sales increased 5% due to higher volume and pricing in all regions. 2026 2025 1,234 5,678 9,101 1,121 3,141 5,161 7,181 9,202 1,222")
        self.assertEqual(out, ["Net sales increased 5% due to higher volume and pricing in all regions."])


class Comparison(unittest.TestCase):
    def test_like_for_like_prior_for_10q_is_same_quarter_last_year(self):
        fl = [filing("q1", "10-Q", "2025-05-01", "2025-03-31", {}), filing("k", "10-K", "2026-02-20", "2025-12-31", {}),
              filing("q2", "10-Q", "2025-08-01", "2025-06-30", {}), filing("q1b", "10-Q", "2026-05-01", "2026-03-31", {})]
        self.assertEqual(T.prior_like(fl, fl[3])["a"], "q1")
        self.assertIsNone(T.prior_like(fl, fl[1]))

    def test_added_new_and_dropped(self):
        d = defs()
        s_old = "Tariffs on aluminum and steel imported from Canada and Mexico increased our input costs and reduced gross margin during fiscal 2025."
        s_new = "The reciprocal tariffs announced in April raised costs on products we source from Vietnam."
        doc = {"filings": {
            "k1": filing("k1", "10-K", "2025-02-20", "2024-12-31", {"tariffs": [hit(s_old)]}),
            "k2": filing("k2", "10-K", "2026-02-20", "2025-12-31", {"tariffs": [hit(s_old.replace("fiscal 2025", "fiscal 2026").replace("increased", "raised")), hit(s_new)]}),
            "k3": filing("k3", "10-K", "2027-02-20", "2026-12-31", {}),
        }}
        boiler = {t["id"]: set() for t in d["themes"]}
        rows, _ = T.company_view("X", doc, d, boiler)
        k2 = rows[1]["themes"]["tariffs"]
        self.assertEqual(k2["n"], 2)
        self.assertEqual(k2["new"], 1)  # a one-word edit with a new year is the same sentence; only the Vietnam one is new
        k3 = rows[2]["themes"]["tariffs"]
        self.assertEqual((k3["n"], k3["old_n"], k3["comparable"]), (0, 2, True))

    def test_drop_not_counted_when_section_missing(self):
        d = defs()
        doc = {"filings": {
            "k1": filing("k1", "10-K", "2025-02-20", "2024-12-31", {"tariffs": [hit("Tariffs on steel raised our costs this year significantly.", sec="1A")]}),
            "k2": filing("k2", "10-K", "2026-02-20", "2025-12-31", {}, words={"7": 1000}),
        }}
        rows, _ = T.company_view("X", doc, d, {t["id"]: set() for t in d["themes"]})
        self.assertFalse(rows[1]["themes"]["tariffs"]["comparable"])

    def test_shared_boilerplate_is_removed(self):
        s = "Changes in trade policy, including tariffs, could adversely affect our business."
        companies = {f"C{i}": {"filings": {"a": filing("a", "10-K", "2026-02-01", "2025-12-31", {"tariffs": [hit(s)]})}} for i in range(T.BOILER_MIN)}
        b = T.boilerplate(companies)
        self.assertIn(T.shash(s), b["tariffs"])


class Baskets(unittest.TestCase):
    def test_geo_share_uses_latest_annual_geography_table(self):
        seg = {"tables": [{"family": "revenue", "category": "segment", "id": "x", "rows": [
            {"label": "Americas", "depth": 0, "A": {"2026-09-26": {"v": 180.0}, "2025-09-27": {"v": 170.0}}},
            {"label": "Greater China", "depth": 0, "A": {"2026-09-26": {"v": 60.0}, "2025-09-27": {"v": 70.0}}},
            {"label": "Europe", "depth": 0, "A": {"2026-09-26": {"v": 100.0}, "2025-09-27": {"v": 90.0}}},
            {"label": "Rest of Asia Pacific", "depth": 0, "A": {"2026-09-26": {"v": 60.0}}}]}]}
        import re
        share, label, period, _ = T.geo_share(seg, re.compile(r"china", re.I))
        self.assertAlmostEqual(share, 0.15)
        self.assertEqual((label, period), ("Greater China", "2026-09-26"))

    def test_aggregate_series_and_lists(self):
        d = defs()
        companies = {}
        for i, tk in enumerate(["AAPL", "MSFT", "NKE"]):
            hits_old = {"tariffs": [hit(f"Tariffs on component imports increased our costs in fiscal year number {i} materially.")]} if i == 0 else {}
            hits_new = {"tariffs": [hit(f"New tariffs on goods from China and Vietnam raised our product costs and we raised prices {i}.")]} if i < 2 else {}
            companies[tk] = {"filings": {
                "a": filing(f"{tk}-1", "10-K", "2025-02-20", "2024-12-31", hits_old),
                "b": filing(f"{tk}-2", "10-K", "2026-02-20", "2025-12-31", hits_new)}}
            companies[tk]["filings"] = {f["a"]: f for f in companies[tk]["filings"].values()}
        index, out, co = T.aggregate(companies, d)
        s = {r["q"]: r for r in out["tariffs"]["series"]}
        self.assertEqual((s["2026Q1"]["filers"], s["2026Q1"]["mentioning"], s["2026Q1"]["added"]), (3, 2, 1))
        self.assertEqual(s["2026Q1"]["new_language"], 2)
        added = out["tariffs"]["changes"]["2026Q1"]["added"]
        self.assertEqual([a["ticker"] for a in added], ["MSFT"])
        self.assertIn("tariffs", co["AAPL"]["themes"])
        self.assertEqual(index["themes"][0]["id"], "tariffs")


if __name__ == "__main__":
    unittest.main()
