"""KPI insights: detectors over synthetic KPI series and alt data, and the verified model layer with fake models."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import kpi_insights as K  # noqa: E402


def qends(n, last=None):
    """n quarter ends ending at the latest quarter end before today."""
    last = last or dt.date.today() - dt.timedelta(days=40)
    out = []
    d = last
    for _ in range(n):
        out.append(d)
        d = d - dt.timedelta(days=91)
    return [x.isoformat() for x in reversed(out)]


def item(iid, name, unit, values, parent=None, depth=0):
    ends = qends(len(values))
    return {"id": iid, "name": name, "unit": unit, "parent": parent, "depth": depth,
            "values": {"Q": {e: {"v": v, "doc": f"doc{k}", "page": 1, "quote": f"{name} {v}", "label": f"Q{k}", "url": f"https://ir.example/{k}"}
                             for k, (e, v) in enumerate(zip(ends, values))}}}


class Detectors(unittest.TestCase):
    def test_rate_break_is_flagged_with_sources(self):
        it = item("comps", "Comparable sales growth", "%", [3.0, 3.2, 2.9, 3.1, 3.0, 3.3, 2.8, 3.1, 3.0, 3.2, 9.5])
        F = K.Facts()
        sigs = K.detect_trend(F, "TST", K.kpi_items({"groups": [{"category": "Comps", "items": [it]}]}))
        self.assertEqual(len(sigs), 1)
        self.assertEqual(sigs[0]["kind"], "trend")
        fid = sigs[0]["facts"][0]
        self.assertIn("9.5%", F.text[fid])
        self.assertIn("+6.3 pp", F.text[fid])
        self.assertTrue(all(s.get("doc") for s in F.sources[fid]))

    def test_components_moving_apart(self):
        parent = item("comps", "Comparable sales growth", "%", [3, 3, 3, 3, 3, 4, 5, 6, 7.9, 7.2])
        traffic = item("traffic", "Comparable transactions growth", "%", [1, 1, 1, 1, 1, 2, 3, 3.8, 4.2, 1.8], parent="comps", depth=1)
        ticket = item("ticket", "Comparable ticket growth", "%", [2, 2, 2, 2, 2, 2, 2, 2.3, 3.5, 5.2], parent="comps", depth=1)
        F = K.Facts()
        sigs = K.detect_mix(F, "TST", K.kpi_items({"groups": [{"category": "Comps", "items": [parent, traffic, ticket]}]}))
        self.assertEqual(len(sigs), 1)
        txt = F.text[sigs[0]["facts"][0]]
        self.assertIn("Comparable transactions growth 1.8% (prior quarter 4.2%)", txt)
        self.assertIn("Comparable ticket growth 5.2% (prior quarter 3.5%)", txt)

    def test_units_vs_revenue(self):
        rev = item("revenue_total", "Total revenue", "USD", [100e6, 102e6, 104e6, 106e6, 110e6, 115e6, 118e6, 120e6, 125e6])
        units = item("deliveries", "Vehicle deliveries", "count", [1000, 1000, 1000, 1000, 1000, 990, 980, 960, 900])
        F = K.Facts()
        items = K.kpi_items({"groups": [{"category": "Ops", "items": [rev, units]}]})
        sigs = K.detect_units(F, "TST", items, {})
        self.assertEqual(len(sigs), 1)
        self.assertIn("revenue per unit", F.text[sigs[0]["facts"][0]])

    def test_contracts_and_market_wide_ftd_are_handled(self):
        F = K.Facts()
        alt = {"contracts": {"rows": [{"fy": 2021, "amount": 9e8}, {"fy": 2022, "amount": 1.0e9}, {"fy": 2023, "amount": 1.1e9},
                                      {"fy": 2024, "amount": 1.0e9}, {"fy": 2025, "amount": 1.1e9}, {"fy": 2026, "amount": 2.3e9},
                                      {"fy": 2027, "amount": 1e8, "partial": True}], "awards": []}}
        sigs = K.detect_contracts(F, "TST", alt, [])
        self.assertEqual(len(sigs), 1)
        self.assertIn("fiscal 2026", F.text[sigs[0]["facts"][0]])
        # a fails-to-deliver peak on a market-wide date is not a company signal
        day = (dt.date.today() - dt.timedelta(days=20)).isoformat()
        rows = [[(dt.date.today() - dt.timedelta(days=200 - k)).isoformat(), 1000, 10.0, "x"] for k in range(120)] + [[day, 10_000_000, 10.0, "x"]]
        K.MARKET_FTD = {day}
        self.assertEqual(K.detect_trading(K.Facts(), "TST", {"ftd": {"rows": rows}}, {}), [])
        K.MARKET_FTD = set()
        self.assertEqual(len(K.detect_trading(K.Facts(), "TST", {"ftd": {"rows": rows}}, {})), 1)


class ModelLayer(unittest.TestCase):
    def setUp(self):
        self.F = K.Facts()
        self.F.add("a", "Comparable sales growth was 7.2% in Q4, against 7.9% the quarter before.")
        self.F.add("b", "Transactions growth was 1.8% (prior quarter 4.2%); ticket growth 5.2% (prior quarter 3.5%).")
        self.sigs = [K._sig("mix", 3, "ticket up, traffic down", ["a", "b"]), K._sig("trend", 2, "comps", ["a"])]

    def test_numbers_must_be_in_cited_facts_and_check_must_pass(self):
        def draft(prompt):
            return {"insights": [
                {"title": "Price carried comparable sales", "text": "Comparable sales grew 7.2% as ticket rose to 5.2% and transactions slowed to 1.8%.",
                 "kind": "mix", "refs": ["a", "b"]},
                {"title": "Made-up figure", "text": "Margins reached 31%.", "kind": "trend", "refs": ["a"]},
                {"title": "Unsupported by checker", "text": "Comparable sales were 7.2% against 7.9%.", "kind": "trend", "refs": ["a"]}]}, "kimi-k3p1-pro"

        def post(model, prompt):
            self.assertEqual(model, "deepseek-v4p1-flash")
            return {"verdicts": [{"n": 1, "supported": True}, {"n": 2, "supported": False, "reason": "no"}]}
        kept, dropped, models = K.write("TST", "Test", self.F, self.sigs, {}, complete_fn=draft, post=post)
        self.assertEqual([k["title"] for k in kept], ["Price carried comparable sales"])
        reasons = " ".join(d["reason"] for d in dropped)
        self.assertIn("numbers not in cited facts", reasons)
        self.assertIn("check:", reasons)
        self.assertEqual(models, {"draft": "kimi-k3p1-pro", "check": "deepseek-v4p1-flash"})

    def test_repeat_within_window_is_dropped(self):
        state = {"history": [{"date": dt.date.today().isoformat(), "refs": ["a", "b"], "key": "old"}]}
        draft = lambda p: ({"insights": [{"title": "Again", "text": "Comparable sales 7.2%.", "kind": "mix", "refs": ["a"]}]}, "kimi-k3p1-pro")
        post = lambda m, p: {"verdicts": [{"n": 1, "supported": True}]}
        kept, dropped, _ = K.write("TST", "Test", self.F, self.sigs, state, complete_fn=draft, post=post)
        self.assertEqual(kept, [])
        self.assertIn("not new", dropped[0]["reason"])


if __name__ == "__main__":
    unittest.main()
