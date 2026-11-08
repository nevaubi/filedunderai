"""What-changed feed: tier rules, text rules, insider grouping, analyst/earnings/contract/dividend/IPO/macro builders,
ranking, stable ids and first-seen stamping."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import changes as C  # noqa: E402

TODAY = dt.date(2026, 11, 7)
SINCE = TODAY - dt.timedelta(days=90)


class FilingChangeRules(unittest.TestCase):
    def test_amended_and_restated_is_not_a_restatement(self):
        c = {"kind": "added", "new": "At closing, the limited liability company agreement of Charter Holdings was amended and restated by and among the parties.", "signals": ["restatement"]}
        self.assertIsNone(C.classify_change(c, "Item 2. MD&A"))

    def test_real_restatement_material_weakness_going_concern_bridge_loan(self):
        cases = [
            ("We restated our previously issued financial statements for 2025 to correct an error.", "restatement"),
            ("Management identified a material weakness in internal control over financial reporting.", "material weakness"),
            ("These conditions raise substantial doubt about our ability to continue as a going concern.", "going concern"),
            ("On June 8, 2026, we entered into a 364-day bridge loan agreement to fund the acquisition.", "bridge loan"),
        ]
        for text, rule in cases:
            tier, reason, tags, got = C.classify_change({"kind": "added", "new": text, "signals": []}, "Item 2. MD&A")
            self.assertEqual((tier, got), ("high", rule), text)
            self.assertIn(rule, reason)

    def test_removed_distress_language_is_medium_and_modified_reads_inserted_words(self):
        tier, _, _, rule = C.classify_change({"kind": "removed", "old": "We previously identified a material weakness that has now been remediated.", "signals": []}, "Item 4. Controls")
        self.assertEqual((tier, rule), ("medium", "material weakness"))
        mod = {"kind": "modified", "ops": [["=", "Our credit agreement contains covenants."], ["+", "We were not in compliance with the financial covenants as of September 30, 2026."]], "signals": ["covenant"]}
        tier, _, _, rule = C.classify_change(mod, "Note 6. Debt")
        self.assertEqual((tier, rule), ("high", "event of default"))

    def test_new_risk_factor_tiers(self):
        self.assertEqual(C.classify_change({"kind": "added", "new": "Tariffs on imported components could increase our costs materially.", "signals": ["tariffs/trade"]}, "Item 1A. Risk factors")[0], "medium")
        self.assertEqual(C.classify_change({"kind": "added", "new": "Our brand depends on the continued quality of our products and services.", "signals": []}, "Item 1A. Risk factors")[0], "low")
        self.assertIsNone(C.classify_change({"kind": "added", "new": "Revenue was $10 million in the quarter compared to $9 million.", "signals": []}, "Item 2. MD&A"))

    def test_redline_events_cap_per_rule_and_summary(self):
        para = "On June 8, 2026, we entered into a 364-day bridge loan agreement (the Bridge Loan) of up to $4 billion. Paragraph {}"
        doc = {"comparisons": [{"form": "10-Q", "new": {"accn": "a1", "form": "10-Q", "filed": "2026-11-06", "url": "https://sec/x"},
                                "old": {"form": "10-Q", "filed": "2025-11-01", "url": "https://sec/y"}, "totals": {"added": 5, "removed": 1, "modified": 2},
                                "sections": [{"id": "s1", "title": "Item 2. MD&A", "score": 90, "changes": [{"kind": "added", "new": para.format(i), "score": 50, "signals": []} for i in range(5)]}]}]}
        evs = C.redline_events("INGR", doc, SINCE)
        self.assertEqual(sum(e["sub"] == "bridge loan" for e in evs), 2)
        self.assertEqual(sum(e["sub"] == "summary" for e in evs), 1)
        self.assertTrue(all(e["evidence"] for e in evs))


class InsiderRules(unittest.TestCase):
    def row(self, owner, role, code, shares, price, date, plan=False, after=1000.0, acc="0001"):
        return {"owner": owner, "role": role, "code": code, "shares": shares, "price": price, "tx_date": date, "derivative": False,
                "plan_10b5": plan, "owned_after": after, "accession": acc}

    def test_owner_kind_and_suffix_identity(self):
        self.assertEqual(C.owner_kind("BAKER BROS. ADVISORS LP (+5)", "Director, 10% owner"), "holder")
        self.assertEqual(C.owner_kind("Cohen Ryan", "President, CEO and Chairman, Director"), "top")
        self.assertEqual(C.owner_kind("Smith Jane", "Director"), "director")
        self.assertEqual(C.owner_id("Haggart Dylan G. (+1)"), C.owner_id("Haggart Dylan G. (+2)"))

    def test_ceo_buy_high_plan_sale_ignored_and_cluster(self):
        rows = [self.row("Tan Lip Bu", "CEO, Director", "P", 150000, 64.73, "2026-11-02"),
                self.row("Exec A", "EVP", "S", 1000, 50, "2026-10-20", plan=True),
                self.row("Dir One", "Director", "P", 1000, 50, "2026-10-01"), self.row("Dir Two", "Director", "P", 1000, 50, "2026-10-05"),
                self.row("Dir Three (+1)", "Director", "P", 1000, 50, "2026-10-09")]
        evs = C.insider_events("INTC", {"rows": rows}, 3e11, SINCE, TODAY)
        ceo = [e for e in evs if "Tan" in e["title"]][0]
        self.assertEqual(ceo["tier"], "high")
        self.assertFalse(any(e["sub"] == "insider_sell" for e in evs))
        self.assertTrue(any(e["sub"] == "cluster_buy" for e in evs))

    def test_discretionary_sale_share_of_holding(self):
        rows = [self.row("Exec B", "EVP", "S", 30000, 100, "2026-10-20", after=60000.0)]
        e = C.insider_events("KO", {"rows": rows}, 4e11, SINCE, TODAY)[0]
        self.assertEqual(e["tier"], "medium")
        self.assertAlmostEqual(e["metrics"]["fraction_of_holding"], 0.333, places=3)


class AnalystEarningsContracts(unittest.TestCase):
    def test_analyst_day_aggregation(self):
        acts = [{"date": "2026-10-30", "action": "Downgrades", "firm": f"F{i}", "rating_prior": "Buy", "rating_current": "Hold", "pt_prior": 100, "pt_current": 80} for i in range(2)]
        e = C.analyst_events("INTC", {"actions": acts}, SINCE)[0]
        self.assertEqual(e["tier"], "high")
        self.assertEqual(e["metrics"]["downgrades"], 2)
        self.assertEqual(e["metrics"]["target_cuts"], 2)

    def test_earnings_surprise_sanity_and_reaction_rule(self):
        est = {"surprises": [{"quarter_end": "2026-09-30", "eps_actual": 0.07, "eps_estimate": 0.0001, "surprise_pct": 699.0,
                              "report": {"filed": "2026-11-03", "accn": "x", "url": "https://sec/x"}, "reaction": {"r1": 0.16, "spy1": 0.01}}]}
        evs, seen = C.earnings_events("AHCO", est, SINCE)
        self.assertIsNone(evs[0]["metrics"]["surprise_pct"])
        self.assertIn("not meaningful", evs[0]["title"])
        self.assertEqual(evs[0]["tier"], "high")
        self.assertEqual(seen, {("AHCO", "x")})

    def test_contract_shift_and_matching_gap(self):
        good = {"rows": [{"fy": y, "amount": a} for y, a in [(2022, 9e9), (2023, 9.5e9), (2024, 9.1e9), (2025, 9.2e9), (2026, 16.8e9)]] + [{"fy": 2027, "amount": 1e9, "partial": True}]}
        e = C.contract_events("GD", good, "2026-11-07")[0]
        self.assertEqual(e["tier"], "high")
        self.assertEqual(e["metrics"]["fy"], 2026)
        gap = {"rows": [{"fy": y, "amount": a} for y, a in [(2022, 30e9), (2023, 31e9), (2024, 29e9), (2025, 19e6), (2026, 37e9)]]}
        self.assertEqual(C.contract_events("RTX", gap, "2026-11-07"), [])


class CalendarsAndMacro(unittest.TestCase):
    def test_dividend_increase_and_cut(self):
        rows = [{"ticker": "AAPL", "history": [["2026-08-10", 0.26]], "declared": [{"amount": 0.27, "kind": "regular", "ex_date": "2026-11-12", "filed": "2026-11-02", "accn": "a", "url": "https://sec/a"}]},
                {"ticker": "KO", "history": [["2026-08-10", 0.50]], "declared": [{"amount": 0.25, "kind": "regular", "ex_date": "2026-11-12", "filed": "2026-11-02", "accn": "b", "url": "https://sec/b"}]}]
        evs = {e["t"]: e for e in C.dividend_events(rows, SINCE)}
        self.assertEqual((evs["AAPL"]["sub"], evs["AAPL"]["tier"]), ("increase", "medium"))
        self.assertEqual((evs["KO"]["sub"], evs["KO"]["tier"]), ("cut", "high"))

    def test_ipo_lockup_upcoming(self):
        rows = [{"cik": 1, "name": "NewCo", "symbol": "NEW", "priced": "2026-05-20", "spac": False}]
        evs = C.ipo_events(rows, TODAY - dt.timedelta(days=30), TODAY)
        lock = [e for e in evs if e["sub"] == "lockup"]
        self.assertEqual(len(lock), 1)
        self.assertTrue(lock[0]["upcoming"])

    def test_macro_sensitive_names(self):
        rel = [{"release": "BLS:CPI", "release_date": "2026-11-10", "title": "Consumer Price Index", "provider": "BLS", "source_url": "https://bls"}]
        e = C.macro_events(rel, {"cm_cpi": {"XOM": 0.4, "AAPL": -0.1, "NEM": -0.5}}, TODAY - dt.timedelta(days=14), TODAY)[0]
        self.assertTrue(e["upcoming"])
        self.assertEqual(e["metrics"]["sensitive"][0]["t"], "NEM")


class RankAndIds(unittest.TestCase):
    def test_rank_tier_then_magnitude_and_stable_ids(self):
        a = C.event("insider", "insider_buy", "AAPL", "2026-11-01", "a", "medium", "r", ("k1",), metrics={"bps_mcap": 1})
        b = C.event("insider", "insider_buy", "AAPL", "2026-11-02", "b", "high", "r", ("k2",), metrics={"bps_mcap": 0.1})
        c = C.event("insider", "insider_buy", "AAPL", "2026-11-03", "c", "medium", "r", ("k3",), metrics={"bps_mcap": 5})
        out = C.rank([a, b, c])
        self.assertEqual([e["title"] for e in out], ["b", "c", "a"])
        self.assertEqual(C.event("insider", "insider_buy", "AAPL", "2026-11-01", "x", "low", "r", ("k1",))["id"], a["id"])

    def test_first_seen_is_kept(self):
        seen = {}
        e = C.event("macro", "x", None, "2026-11-01", "t", "low", "r", ("k",))
        C.stamp([e], seen, "2026-11-07T00:00:00Z")
        e2 = C.event("macro", "x", None, "2026-11-01", "t", "low", "r", ("k",))
        C.stamp([e2], seen, "2026-11-08T00:00:00Z")
        self.assertEqual(e2["first_seen"], "2026-11-07T00:00:00Z")


if __name__ == "__main__":
    unittest.main()


def test_form144_events_skip_executed_and_scale_plan_sales():
    import datetime as dt
    from etl.changes import form144_events
    since = dt.date(2026, 10, 1)
    rows = [
        {"ticker": "AAPL", "filed": "2026-11-05", "accession": "a1", "url": "https://www.sec.gov/x", "person": "Jane Doe",
         "relationship": "Officer", "value": 3e7, "plan": False, "status": "open", "sale_date": "2026-11-06"},
        {"ticker": "AAPL", "filed": "2026-11-05", "accession": "a2", "value": 3e7, "plan": True, "plan_adopted": ["2026-05-01"], "status": "open"},
        {"ticker": "AAPL", "filed": "2026-11-05", "accession": "a3", "value": 9e7, "plan": False, "status": "executed"},
        {"ticker": "AAPL", "filed": "2026-11-05", "accession": "a4", "value": 2e5, "plan": False, "status": "open"},
    ]
    evs = form144_events(rows, {}, since)
    assert [e["tier"] for e in evs] == ["high", "medium"]
    assert evs[0]["sub"] == "form144" and "no 10b5-1 plan" in evs[0]["reason"]
    assert "2026-05-01" in evs[1]["reason"]


def test_new_8k_items_are_tiered():
    from etl.changes import ITEMS
    assert ITEMS["1.05"][0] == "high" and ITEMS["5.07"][0] == "low"


def test_short_interest_summary_and_block():
    from etl.flows_market import si_summary
    from etl.screener import short_interest_block
    rows = [{"date": f"2026-0{m}-15", "short": s, "prev": p, "dtc": 2.5, "adv": 1e6}
            for m, s, p in ((5, 100, 90), (6, 110, 100), (7, 120, 110), (8, 130, 120), (9, 140, 130), (10, 200, 140))]
    s = si_summary(rows)
    assert s["date"] == "2026-010-15" or s["short"] == 200
    assert round(s["chg"], 4) == round(200 / 140 - 1, 4)
    assert round(s["chg_3m"], 4) == 1.0
    out, prov = short_interest_block(s, {"v": 1000, "end": "2026-09-30"})
    assert out["si_pct_shares"] == 0.2 and out["si_dtc"] == 2.5 and "si_chg_3m" in prov
    assert short_interest_block(None, {"v": 1}) == ({}, {})


def test_short_interest_events_tiers():
    import datetime as dt
    from etl.changes import short_interest_events
    since = dt.date(2026, 10, 1)
    cos = {"AAPL": {"date": "2026-10-15", "short": 300, "prev": 100, "chg": 2.0, "dtc": 5},
           "MSFT": {"date": "2026-10-15", "short": 135, "prev": 100, "chg": 0.35, "dtc": 4.5},
           "NVDA": {"date": "2026-10-15", "short": 110, "prev": 100, "chg": 0.1, "dtc": 9},
           "AMZN": {"date": "2026-09-15", "short": 300, "prev": 100, "chg": 2.0, "dtc": 9}}
    evs = {e["t"]: e for e in short_interest_events(cos, since)}
    assert evs["AAPL"]["tier"] == "high" and evs["MSFT"]["tier"] == "medium"
    assert "NVDA" not in evs and "AMZN" not in evs
    assert evs["AAPL"]["title"].startswith("Short interest rose 200%")


def test_comment_letter_events():
    import datetime as dt
    from etl.changes import comment_letter_events
    rows = [{"ticker": "AAPL", "filed": "2026-10-20", "accession": "x", "url": "https://www.sec.gov/x", "kind": "comments", "form": "10-K",
             "period": "2025-09-27", "periodic": True, "n_comments": 3, "topics": ["non_gaap", "segments"]},
            {"ticker": "AAPL", "filed": "2026-10-21", "accession": "y", "kind": "review_complete", "form": "10-K", "periodic": True},
            {"ticker": "AAPL", "filed": "2026-10-21", "accession": "z", "kind": "comments", "form": "S-1", "periodic": False}]
    evs = comment_letter_events(rows, {"non_gaap": "Non-GAAP measures", "segments": "Segment reporting"}, dt.date(2026, 10, 1))
    assert [e["tier"] for e in evs] == ["medium", "low"]
    assert "Non-GAAP measures" in evs[0]["title"] and "3 comments" in evs[0]["reason"]


def test_regulatory_events():
    import datetime as dt
    from etl.changes import regulatory_events
    today = dt.date(2026, 11, 8)
    recent = {"rows": [
        {"ticker": "LLY", "kind": "approval", "date": "2026-11-01", "title": "NEWDRUG", "detail": "Type 1", "priority": "PRIORITY", "url": "https://x", "id": "1"},
        {"ticker": "MDT", "kind": "recall", "date": "2026-10-20", "title": "Pump", "detail": "Battery", "recall_kind": "device", "id": "Z-1", "url": "https://y"},
        {"ticker": "PFE", "kind": "trial_stopped", "date": "2026-10-15", "title": "Study", "detail": "Futility", "phase": "P3", "status": "TERMINATED", "id": "NCT1", "url": "https://z"},
    ]}
    cats = {"rows": [{"ticker": "MRK", "nct": "NCT2", "title": "Big trial", "phase": "P3", "primary_completion": "2026-11-20"},
                     {"ticker": "MRK", "nct": "NCT3", "title": "Later", "phase": "P3", "primary_completion": "2027-06-01"}]}
    evs = regulatory_events(recent, cats, dt.date(2026, 8, 10), today)
    tiers = {e["sub"]: e["tier"] for e in evs}
    assert tiers == {"fda_approval": "high", "fda_recall": "medium", "trial_stopped": "high", "readout_due": "low"}
    assert [e for e in evs if e["sub"] == "readout_due"][0]["upcoming"]
