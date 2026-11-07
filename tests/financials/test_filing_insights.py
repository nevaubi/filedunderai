import unittest

from etl import filing_insights as FI

NEW = ("We expect tariffs on imported components to increase our cost of revenue by approximately $450 million in fiscal 2027, "
       "and we have begun shifting assembly of these products to facilities outside the affected regions.")
OLD_RISK = "We depend on a single supplier, Acme Foundry, for substantially all of our wafers, and any disruption would harm our business."
DOC = {"comparisons": [{
    "kind": "annual", "form": "10-K",
    "new": {"accn": "0000000001-26-000001", "form": "10-K", "filed": "2026-10-30", "report": "2026-09-30", "doc": "x.htm", "url": "u"},
    "old": {"accn": "0000000001-25-000001", "form": "10-K", "filed": "2025-10-30", "report": "2025-09-30", "doc": "y.htm", "url": "v"},
    "sections": [
        {"title": "Item 7. MD&A", "changes": [
            {"kind": "added", "old": None, "new": NEW, "score": 120, "signals": ["tariffs/trade"]},
            {"kind": "modified", "ops": [["=", "Revenue from our largest customer represented"], ["-", "18%"], ["+", "27%"], ["=", "of total revenue for the fiscal year, and the loss of this customer would materially reduce our revenue and results."]], "score": 60, "signals": []},
        ]},
        {"title": "Item 1A. Risk factors", "changes": [
            {"kind": "removed", "old": OLD_RISK, "new": None, "score": 80, "signals": ["supply"]},
            {"kind": "added", "old": None, "new": "short", "score": 2, "signals": []},
        ]},
    ]}]}


class Store:
    def __init__(self, data):
        self.data = data
        self.writes = {}

    def get_json(self, p):
        return self.data.get(p)

    def put_json(self, p, o, cache=None):
        self.writes[p] = o
        self.data[p] = o


def fake_post(draft_items, verdicts):
    def post(model, messages, max_tokens, deadline=None):
        if model == FI.DRAFT_MODEL and "auditor" not in messages[0]["content"] and "audit" not in messages[0]["content"]:
            return {"insights": draft_items}
        return {"verdicts": verdicts}
    return post


GOOD = {"type": "guidance", "title": "Tariffs to add about $450 million to cost of revenue", "text": "New disclosure: tariffs will raise cost of revenue by about $450 million in fiscal 2027; assembly is moving.",
        "quote": "We expect tariffs on imported components to increase our cost of revenue by approximately $450 million in fiscal 2027",
        "refs": ["R1"], "stake": {"value": 450000000, "unit": "USD", "what": "added cost"}, "direction": "negative", "novelty": "new", "impact": 3}
CUST = {"type": "customer_supplier", "title": "Largest customer rose to 27% of revenue", "text": "Concentration rose: the largest customer went from 18% to 27% of revenue.",
        "quote": "Revenue from our largest customer represented 27% of total revenue for the fiscal year", "refs": ["R3"],
        "stake": {"value": 27, "unit": "percent", "what": "share of revenue"}, "direction": "negative", "novelty": "changed", "impact": 3}
REMOVED = {"type": "customer_supplier", "title": "Single-supplier risk factor dropped", "text": "The risk factor on dependence on Acme Foundry was removed.",
           "quote": "We depend on a single supplier, Acme Foundry, for substantially all of our wafers", "refs": ["R2"], "stake": None,
           "direction": "positive", "novelty": "removed", "impact": 2}


class FilingInsights(unittest.TestCase):
    def test_records_order_and_sides(self):
        comp = FI.latest_comparison(DOC)
        recs = FI.records(comp)
        self.assertEqual([r["kind"] for r in recs], ["added", "removed", "modified"])  # by score, short passages dropped
        self.assertIn("27%", recs[2]["new"])
        self.assertIn("18%", recs[2]["old"])

    def test_verify_rejects_bad_quote_and_invented_numbers(self):
        recs = FI.records(FI.latest_comparison(DOC))
        bad_quote = dict(GOOD, quote="tariffs will cost us around half a billion dollars next year in total costs")
        bad_number = dict(GOOD, text="Tariffs will raise costs by $600 million in fiscal 2027.")
        bad_stake = dict(GOOD, stake={"value": 900000000, "unit": "USD", "what": "x"})
        no_ref = dict(GOOD, refs=["R99"])
        kept, rej = FI.verify([GOOD, bad_quote, bad_number, bad_stake, no_ref, CUST, REMOVED], recs)
        self.assertEqual([k["title"] for k in kept], [GOOD["title"], CUST["title"], REMOVED["title"]])
        self.assertEqual(rej, {"refs": 1, "quote": 1, "numbers": 2, "type": 0})

    def test_company_runs_check_ranks_and_skips_repeats(self):
        sb = Store({"redlines/T.json": DOC})
        state = {}
        post = fake_post([GOOD, CUST, REMOVED], [{"id": "c1", "supported": True, "reason": "stated"}, {"id": "c2", "supported": True, "reason": "stated"},
                                                 {"id": "c3", "supported": False, "reason": "the risk moved, not removed"}])
        res, why = FI.company(sb, "T", "T Corp", 10_000_000_000, state, post=post)
        self.assertIsNone(why)
        self.assertEqual(len(res["insights"]), 2)
        self.assertEqual(res["rejected"]["check"], 1)
        self.assertEqual(res["insights"][0]["rank"], 1)
        self.assertGreaterEqual(res["insights"][0]["score"], res["insights"][1]["score"])
        self.assertEqual(res["insights"][0]["doc"], "new")
        self.assertEqual(state["accn"], "0000000001-26-000001")
        # a later filing repeating the same quote within 30 days drops it
        doc2 = {"comparisons": [dict(DOC["comparisons"][0], new=dict(DOC["comparisons"][0]["new"], accn="0000000001-26-000099"))]}
        sb.data["redlines/T.json"] = doc2
        res2, _ = FI.company(sb, "T", "T Corp", 10_000_000_000, state, post=post)
        self.assertEqual(len(res2["insights"]), 0)
        self.assertEqual(res2["rejected"]["repeat"], 2)

    def test_nothing_published_when_check_fails(self):
        sb = Store({"redlines/T.json": DOC})

        def post(model, messages, max_tokens, deadline=None):
            if "audit" in messages[0]["content"]:
                raise RuntimeError("check model down")
            return {"insights": [GOOD]}
        with self.assertRaises(RuntimeError):
            FI.company(sb, "T", "T Corp", None, {}, post=post)


if __name__ == "__main__":
    unittest.main()


class InsightsIndex(unittest.TestCase):
    def test_run_merges_index_rows(self):
        sb = Store({"redlines/T.json": DOC, "redlines/index.json": {"companies": {"T": {"name": "T", "generated": "g1", "comparisons": [{"filed": "2026-10-30"}]}}},
                    "index.json": {"companies": [{"ticker": "T", "revenue_ttm": 1e10}]}, "insights/index.json": {"companies": {"OLD": {"ticker": "OLD"}}}})
        p = fake_post([GOOD, CUST], [{"id": "c1", "supported": True}, {"id": "c2", "supported": True}])
        orig = FI.company
        FI.company = lambda *a, **k: orig(*a, post=p, **k)
        try:
            FI.run(sb, time_budget=300)
        finally:
            FI.company = orig
        idx = sb.writes["insights/index.json"]
        self.assertEqual(set(idx["companies"]), {"OLD", "T"})
        row = idx["companies"]["T"]
        self.assertEqual((row["form"], row["accn"], row["doc"], row["n"]), ("10-K", "0000000001-26-000001", "x.htm", 2))
        self.assertEqual(row["top"][0]["title"], GOOD["title"])
