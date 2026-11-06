"""Supply-chain extraction: verbatim verification, rule candidates, ticker resolution, change detection, sympathy
math, agreement terms, and the model paths with mocked responses (the production keys are not available here)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from etl import supply_core as C  # noqa: E402
from etl import supply_intel as I  # noqa: E402
from etl import supply_llm as M  # noqa: E402

FILING = """<html><body><ix:header><p>hidden 12345</p></ix:header>
<p>Item 1. Business</p>
<p>We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC, and Samsung Electronics Co., Ltd., or Samsung, to produce our semiconductor wafers.</p>
<p>Competition</p>
<p>Our current competitors include:</p>
<p>&#8226; suppliers of GPUs, such as Advanced Micro Devices, Inc., Intel Corporation and Huawei Technologies Co. Ltd.;</p>
<p>Sales to Walmart Inc. and its affiliates represent approximately 16% of our total sales in 2026, 2025 and 2024.</p>
<p>He served as Chief Financial Officer at Microsoft Corporation from 2010 to 2015 and was president of the board.</p>
<p>In August 2022, the U.S. government announced export restrictions and export licensing requirements targeting China&#8217;s semiconductor and supercomputing industries.</p>
</body></html>"""
COVERED = {"AMD": ["AMD"], "INTC": ["Intel"], "WMT": ["Walmart"], "MSFT": ["Microsoft"], "NVDA": ["NVIDIA"]}


class Text(unittest.TestCase):
    def test_html_to_text_drops_xbrl_header_and_decodes(self):
        t = C.html_to_text(FILING)
        self.assertNotIn("12345", t)
        self.assertIn("China’s semiconductor", t)

    def test_quote_verbatim_after_normalisation(self):
        tn = C.norm(C.html_to_text(FILING))
        self.assertTrue(C.quote_ok("We utilize foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC", tn))
        self.assertTrue(C.quote_ok("targeting China's   semiconductor and supercomputing industries and export", C.norm("x targeting china’s semiconductor and supercomputing industries and export y")))
        self.assertFalse(C.quote_ok("We use foundries, such as Taiwan Semiconductor Manufacturing Company Limited, or TSMC", tn))
        self.assertFalse(C.quote_ok("such as TSMC", tn))  # under 8 words
        self.assertFalse(C.quote_ok(" ".join(["word"] * 41), " ".join(["word"] * 50)))

    def test_window_quote_is_verbatim_and_bounded(self):
        s = " ".join(f"w{i}" for i in range(80)) + " Samsung " + " ".join(f"v{i}" for i in range(30))
        q = C.window_quote(s, "Samsung")
        self.assertIn("Samsung", q)
        self.assertLessEqual(len(q.split()), 40)
        self.assertIn(q, s)


class Rules(unittest.TestCase):
    def setUp(self):
        self.c = C.rule_candidates(C.html_to_text(FILING), "NVDA", COVERED, ["NVIDIA"])
        self.by = {(x["cp"], x["rel"]): x for x in self.c}

    def test_foundries_are_suppliers_in_taiwan(self):
        tsmc = self.by[("TSMC", "supplier")]
        self.assertEqual(tsmc["cpt"], "TSM")
        self.assertEqual(tsmc["lt"], "foundry")
        self.assertIn(("Samsung", "supplier"), self.by)

    def test_list_items_inherit_competitor_lead_in(self):
        self.assertIn(("AMD", "competitor"), self.by)
        self.assertIn(("Intel", "competitor"), self.by)
        self.assertIn(("Huawei", "competitor"), self.by)

    def test_stated_share_and_noise(self):
        self.assertEqual(self.by[("Walmart", "customer")]["share"], 16.0)
        self.assertFalse(any(x["cp"] == "Microsoft" for x in self.c))  # executive biography
        self.assertFalse(any(x["cp"] == "U.S. government" for x in self.c))  # export rule is policy, not a customer

    def test_every_candidate_quote_is_verbatim(self):
        tn = C.norm(C.html_to_text(FILING))
        for x in self.c:
            self.assertTrue(C.quote_ok(x["q"], tn), x["q"])

    def test_policy_and_concentration(self):
        t = C.html_to_text(FILING)
        p = C.policy_candidates(t)
        self.assertEqual(p[0]["kind"], "export_controls")
        self.assertEqual(p[0]["countries"], ["China"])
        conc = C.concentration_candidates("For fiscal year 2026, sales to one direct customer represented 22% of total revenue, primarily attributable to the Compute segment.")
        self.assertEqual(conc[0]["pct"], 22.0)
        self.assertTrue(conc[0]["unnamed"])
        self.assertEqual(C.concentration_candidates("Revenue from customers headquartered outside of the United States accounted for 31% of total revenue in fiscal 2026 overall."), [])


class Tickers(unittest.TestCase):
    def test_resolve_never_guesses(self):
        covered = {"AAPL": ["Apple"], "TXN": ["Texas Instruments"]}
        sec = {"TSM": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "AMZN": "AMAZON COM INC"}
        self.assertEqual(C.resolve_ticker("Apple Inc.", "", covered, sec), "AAPL")
        self.assertEqual(C.resolve_ticker("Taiwan Semiconductor Manufacturing Company Limited", "TSM", covered, sec), "TSM")
        self.assertIsNone(C.resolve_ticker("Taiwan Semiconductor Manufacturing", "AMZN", covered, sec))  # wrong proposal
        self.assertIsNone(C.resolve_ticker("Acme Widgets LLC", "ACME", covered, sec))

    def test_entity_key_merges_spellings(self):
        self.assertEqual(C.entity_key("Taiwan Semiconductor Manufacturing Company Limited"), "TSM")
        self.assertEqual(C.entity_key("TSMC"), "TSM")
        self.assertEqual(C.entity_key("Acme Holdings, Inc."), "acme")


class Changes(unittest.TestCase):
    def test_added_dropped_share(self):
        L = lambda **k: {"id": k["cp"] + k["accn"], "t": "AAPL", "rel": "supplier", "cpt": None, "form": "10-K", "filed": "2025", "sh": None, **k}
        links = [L(accn="new", cp="TSMC", sh=12), L(accn="new", cp="Foxconn"), L(accn="old", cp="TSMC", sh=9), L(accn="old", cp="Samsung")]
        ch = C.link_changes("AAPL", links, "new", "old")
        self.assertEqual([(c["kind"], c["key"]) for c in ch], [("share", "supplier:TSM"), ("added", "supplier:hon hai"), ("dropped", "supplier:samsung")])
        self.assertEqual((ch[0]["from"], ch[0]["to"]), (9, 12))


class Moves(unittest.TestCase):
    def test_event_return_and_beta(self):
        cl = [("2025-01-02", 100), ("2025-01-03", 100), ("2025-01-06", 110), ("2025-01-07", 121)]
        self.assertAlmostEqual(C.event_return(cl, "2025-01-06"), 0.21)
        self.assertAlmostEqual(C.event_return(cl, "2025-01-04"), 0.21)
        f, o, pf, po = [], [], 100.0, 100.0
        moves = [0.05, -0.03, 0.02, -0.04]
        for k, m in enumerate(moves):
            for d, mm in ((f"2025-0{k+1}-01", 0), (f"2025-0{k+1}-02", m), (f"2025-0{k+1}-03", 0)):
                pf *= 1 + mm; po *= 1 + 2 * mm; f.append((d, pf)); o.append((d, po))
        s = C.spillover(f, o, [f"2025-0{k+1}-02" for k in range(4)])
        self.assertEqual(s["events"], 4)
        self.assertEqual(s["sameDir"], 1)
        self.assertGreater(s["beta"], 1.9)

    def test_earnings_dates_from_events(self):
        ev = {"cols": ["form", "filed", "accession", "primary_doc", "items", "report_date", "cik"],
              "filings": [["8-K", "2025-02-26", "a1", "d.htm", "2.02,9.01", "", 1], ["8-K", "2025-03-01", "a2", "d.htm", "5.02", "", 1], ["10-K", "2025-02-27", "a3", "k.htm", "", "", 1]]}
        self.assertEqual([d["date"] for d in I.earnings_dates(ev, "2020-01-01")], ["2025-02-26"])


class Agreements(unittest.TestCase):
    def test_terms(self):
        a = C.agreement_terms("In January 2025 we entered into a three-year capacity agreement with prepayments of $1.5 billion through fiscal 2028, representing 12% of purchases; the agreement is non-cancellable.")
        self.assertIn("January 2025", a["dates"])
        self.assertIn("$1.5 billion", a["amounts"])
        for c in ("Prepayment / deposit", "Non-cancellable", "Capacity reservation"):
            self.assertIn(c, a["clauses"])

    def test_item_101_section_and_party(self):
        from etl.supply import PARTY, item_101
        t = "Header\nItem 1.01 Entry into a Material Definitive Agreement.\nOn May 1, 2026, the Company entered into a supply agreement with Acme Widgets Inc. for a term of five years.\nItem 9.01 Financial Statements"
        sec = item_101(t)
        self.assertIn("supply agreement", sec)
        self.assertNotIn("9.01", sec)
        self.assertEqual(PARTY.search(sec).group(1).strip(), "Acme Widgets Inc.")


class ModelPaths(unittest.TestCase):
    def test_llm_links_are_verified(self):
        text = C.norm("We rely on TSMC for the production of all wafers for our microprocessor and GPU products today.")
        props = [{"counterparty": "TSMC", "relation": "supplier", "quote": "We rely on TSMC for the production of all wafers for our microprocessor"},
                 {"counterparty": "Samsung", "relation": "supplier", "quote": "We rely on Samsung for the production of all wafers for our products"},
                 {"counterparty": "Foo", "relation": "vendor", "quote": "We rely on TSMC for the production of all wafers for our microprocessor"}]
        kept, dropped = M.verify_links(props, text)
        self.assertEqual([k["counterparty"] for k in kept], ["TSMC"])
        self.assertEqual(dropped, 2)

    def test_extract_links_uses_mocked_post(self):
        seen = {}
        def post(msgs, max_tokens, deadline=None):
            seen["n"] = len(msgs[1]["content"])
            return {"links": [{"counterparty": "TSMC"}]}, "kimi-k3"
        links, model = M.extract_links("NVDA", "NVIDIA", "10-K", "excerpt", "AAPL=Apple", post=post)
        self.assertEqual(model, "kimi-k3")
        self.assertEqual(links[0]["counterparty"], "TSMC")

    def test_insights_validation(self):
        recs = [{"id": "Labc", "kind": "x", "text": "TSMC supplies 95% of wafers"}, {"id": "F2026-1", "kind": "fr", "text": "Taiwan export notice"}]
        out = M.validate_insights({"headline": "h", "insights": [
            {"kind": "dependency", "text": "95% of wafers come from TSMC (Labc).", "refs": ["Labc", "F2026-1"]},
            {"kind": "dependency", "text": "TSMC makes 80% of chips.", "refs": ["Labc"]},
            {"kind": "geography", "text": "No refs here.", "refs": ["nope"]}], "watch": ["w"]}, recs)
        self.assertEqual(len(out["insights"]), 2)
        self.assertEqual(out["insights"][0]["status"], "grounded")
        self.assertNotIn("Labc", out["insights"][0]["text"])
        self.assertEqual(out["insights"][1]["status"], "unverified")

    def test_corroborate_keeps_only_sentences_naming_both(self):
        def search(q, **kw):
            return [{"url": "https://example.com/a", "title": "t", "content": "Nvidia said TSMC will expand CoWoS packaging capacity for its Blackwell chips next year. Unrelated sentence about the weather today here.", "published_date": "2026-10-01"},
                    {"url": "https://www.sec.gov/x", "title": "sec", "content": "Nvidia and TSMC are named in this filing copy which must be ignored entirely."}]
        out = I.corroborate({"id": "L1", "t": "NVDA", "cpt": "TSM", "rel": "supplier", "prod": None}, "NVIDIA", "TSMC", search=search)
        self.assertEqual(len(out), 1)
        self.assertIn("CoWoS", out[0]["q"])
        self.assertEqual(out[0]["host"], "example.com")

    def test_rotate_skips_without_model_key(self):
        from etl import supply
        old = os.environ.pop("FIREWORKS_API_KEY", None), os.environ.pop("SUPPLY_RULES_FALLBACK", None)
        try:
            self.assertIn("skipped", supply.rotate_job(sb=None))
        finally:
            if old[0]:
                os.environ["FIREWORKS_API_KEY"] = old[0]

    def test_risk_tier(self):
        self.assertEqual(I.risk_tier({"stability": -1.2, "ruleOfLaw": 1, "regulatory": 1}), "high")
        self.assertEqual(I.risk_tier({}), "unrated")


if __name__ == "__main__":
    unittest.main()
