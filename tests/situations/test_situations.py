"""Situation components: sourced facts, negation-aware text flags, owner classification, screen membership."""
import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import situations as S  # noqa: E402

TODAY = dt.date(2026, 11, 7)


def tx(**k):
    base = dict(ticker="X", cik=1, accession="0000000001-26-000001", seq=0, tx_date="2026-10-20", code="P", shares=1000.0,
                price=50.0, derivative=False, plan_10b5=False, owner="Doe Jane", role="Chief Executive Officer, Director")
    base.update(k)
    return base


class Owners(unittest.TestCase):
    def test_suffix_and_kinds(self):
        self.assertEqual(S.clean_owner("Haggart Dylan G. (+2)"), "Haggart Dylan G.")
        self.assertEqual(S.owner_kind({"owner": "BAKER BROS. ADVISORS LP (+5)", "role": "Director, 10% owner"}), "holder")
        self.assertEqual(S.owner_kind({"owner": "Cohen Ryan", "role": "President, CEO and Chairman, Director"}), "officer")
        self.assertEqual(S.owner_kind({"owner": "Pohlad Robert C", "role": "Director"}), "director")
        self.assertEqual(S.owner_kind({"owner": "LOR INC", "role": "10% owner"}), "holder")
        self.assertEqual(S.owner_kind({"owner": "Smith A", "role": "", "owner_kind": "holder"}), "holder")


class Insiders(unittest.TestCase):
    def test_buys_counted_by_person_funds_separate_plan_sells_ignored(self):
        rows = [tx(owner="Doe Jane (+1)"), tx(owner="Doe Jane (+2)", seq=1), tx(owner="Roe Rick", role="Director", seq=2),
                tx(owner="BIG FUND LP", role="10% owner", seq=3, shares=100000.0),
                tx(code="S", plan_10b5=True, seq=4), tx(code="P", tx_date="2026-06-01", seq=5)]
        c = S.insider_components(rows, TODAY)
        self.assertEqual(c["insider_buy"]["metrics"]["buyers"], 2)
        self.assertEqual(c["insider_buy"]["metrics"]["trades"], 3)
        self.assertEqual(c["holder_buy"]["metrics"]["value"], 5_000_000)
        self.assertNotIn("insider_sell", c)
        self.assertTrue(c["insider_buy"]["evidence"][0]["url"].startswith("https://www.sec.gov/Archives/edgar/data/1/"))

    def test_discretionary_selling_needs_two_people_and_1m(self):
        rows = [tx(code="S", shares=20000.0, owner="A B"), tx(code="S", shares=20000.0, owner="C D", seq=1)]
        self.assertEqual(S.insider_components(rows, TODAY)["insider_sell"]["metrics"]["sellers"], 2)
        self.assertNotIn("insider_sell", S.insider_components(rows[:1], TODAY))


class TextFlags(unittest.TestCase):
    def test_negated_material_weakness_is_not_a_flag(self):
        self.assertEqual(S.text_flags("Management did not identify any material weakness in internal control."), [])
        self.assertEqual(S.text_flags("There were no material weaknesses as of September 30, 2026."), [])
        f = S.text_flags("We identified a material weakness in our internal control over financial reporting related to revenue.")
        self.assertEqual(f[0][:2], ("distress", "Material weakness disclosed"))

    def test_going_concern_waiver_bridge(self):
        kinds = [x[1] for x in S.text_flags(
            "These conditions raise substantial doubt about our ability to continue as a going concern. "
            "On June 8, 2026, we entered into a 364-day bridge loan agreement. "
            "The lenders granted a waiver of the leverage covenant for the quarter.")]
        self.assertEqual(kinds, ["Going-concern doubt disclosed", "Bridge financing", "Covenant waiver"])
        self.assertEqual(S.text_flags("Management concluded that substantial doubt about our ability to continue as a going concern has been alleviated."), [])

    def test_redline_flags_reads_added_and_modified_only(self):
        doc = {"comparisons": [{"new": {"filed": "2026-11-01", "form": "10-Q", "url": "u"}, "sections": [
            {"title": "Item 9A", "changes": [2, [{"kind": "removed", "old": "We identified a material weakness.", "new": None},
                                                 {"kind": "added", "new": "We identified a material weakness in controls."}]]}]}]}
        f = S.redline_flags(doc, "2026-05-01")
        self.assertEqual(len(f), 1)
        self.assertEqual((f[0]["section"], f[0]["url"]), ("Item 9A", "u"))


class Contracts(unittest.TestCase):
    def test_complete_years_only_and_thresholds(self):
        rows = [{"fy": 2024, "amount": 1e9}, {"fy": 2025, "amount": 1e9}, {"fy": 2026, "amount": 1.5e9}, {"fy": 2027, "amount": 9e9, "partial": True}]
        c = S.contracts_components(rows)
        self.assertAlmostEqual(c["contracts_up"]["metrics"]["change"], 0.5)
        self.assertEqual(S.contracts_components([{"fy": 2025, "amount": 8e7}, {"fy": 2026, "amount": 9e8}]), {})
        self.assertEqual(S.contracts_components([{"fy": 2025, "amount": 6e8}, {"fy": 2026, "amount": 0}]), {})
        self.assertIn("contracts_down", S.contracts_components([{"fy": 2025, "amount": 1e9}, {"fy": 2026, "amount": 6e8}]))


class Concentration(unittest.TestCase):
    def test_new_single_source_and_customer_share(self):
        co = {"cik": 5, "filings": [{"accn": "a1", "form": "10-K", "filed": "2025-02-01", "doc": "x.htm"},
                                    {"accn": "a2", "form": "10-K", "filed": "2026-02-01", "doc": "y.htm"}],
              "links": [{"cp": "TSMC", "cpt": "TSM", "ss": True, "q": "sole source", "filed": "2026-02-01"}],
              "history": [{"cp": "TSMC", "cpt": "TSM", "ss": False, "filed": "2025-02-01"}],
              "conc": [{"pct": 12, "form": "10-K", "filed": "2025-02-01", "accn": "a1", "doc": "x.htm"},
                       {"pct": 18, "form": "10-K", "filed": "2026-02-01", "accn": "a2", "doc": "y.htm"}]}
        c = S.concentration_components(co)
        self.assertIn("TSMC", c["new_single_source"]["headline"])
        self.assertEqual(c["customer_conc_up"]["metrics"], {"latest": 18.0, "prior": 12.0})
        co["history"][0]["ss"] = True
        self.assertNotIn("new_single_source", S.concentration_components(co))


class QualityAndTechnicals(unittest.TestCase):
    def test_quality_needs_two_flags(self):
        facts = {"Revenue": [("2025-06-30", 100), ("2025-09-30", 100), ("2025-12-31", 100), ("2026-03-31", 100), ("2026-06-30", 100), ("2026-09-30", 105)],
                 "Accounts receivable": [("2025-09-30", 20), ("2026-03-31", 22), ("2026-06-30", 24), ("2026-09-30", 26), ("2025-12-31", 21)]}
        facts["Accounts receivable"].sort()
        c = S.quality_component({"pio_f": 2, "sector": "Industrials"}, facts)
        self.assertEqual(c["quality_down"]["metrics"]["n"], 2)
        self.assertEqual(S.quality_component({"pio_f": 2}, {}), {})

    def test_mean_reversion_carries_base_rate(self):
        sig = {"signals": {"low_52w": {"first": "2008-12-31", "last": "2026-11-06", "60": {"n": 16000, "excess": 0.0649, "excess_ci": [0.014, 0.1158], "hit": 0.6}}}}
        c = S.mean_reversion_component({"events": [["low_52w", "2026-11-05"], ["golden_cross", "2026-11-05"], ["low_52w", "2026-09-01"]], "rsi": 30}, sig, "2026-11-06")
        ev = c["mean_reversion"]["evidence"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["base_rate"]["excess"], 0.0649)

    def test_screen_membership(self):
        rows = [{"t": "A", "c": {"insider_buy": 1, "cheap_hist": 1}}, {"t": "B", "c": {"insider_buy": 1}}]
        self.assertEqual(S.screen_members(rows, S.SCREENS[0]), ["A"])


if __name__ == "__main__":
    unittest.main()


class Boilerplate(unittest.TestCase):
    def test_conditional_risk_factor_language_is_not_a_disclosure(self):
        self.assertEqual(S.text_flags("If we or our auditors discover a material weakness in our internal controls, the disclosure could harm our stock price."), [])
        self.assertEqual(S.text_flags("We may seek a waiver of the financial covenants under our credit agreement."), [])
        self.assertTrue(S.text_flags("We are implementing measures to remediate these material weaknesses."))
        self.assertTrue(S.text_flags("On March 3, 2026, we obtained a waiver of the leverage covenant from our lenders."))
        self.assertEqual(S.usd(1.5e9), "$1.50B")
        self.assertEqual(S.usd(2.5e6), "$2.5M")
