import datetime as dt
import unittest
from unittest import mock

from etl import altdata as A


class NameTests(unittest.TestCase):
    def test_legal_name_spellings_normalize_to_one_key(self):
        same = ["LOCKHEED MARTIN CORP", "Lockheed Martin Corporation", "Lockheed Martin Corp."]
        self.assertEqual({A.norm_name(n) for n in same}, {"LOCKHEED MARTIN CORP"})
        self.assertEqual(A.norm_name("Apple Inc."), A.norm_name("APPLE, INC."))
        self.assertEqual(A.norm_name("WALT DISNEY CO/"), "WALT DISNEY CO")
        self.assertEqual(A.norm_name("CACI International Inc /DE/"), "CACI INTERNATIONAL INC")
        self.assertEqual(A.norm_name("Brown-Forman Corporation"), A.norm_name("BROWN FORMAN CORP"))
        self.assertEqual(A.norm_name("The Kroger Co."), "KROGER CO")

    def test_different_companies_stay_different(self):
        self.assertNotEqual(A.norm_name("Apple Inc."), A.norm_name("Apple Hospitality REIT, Inc."))
        self.assertNotEqual(A.norm_name("CACI International Inc"), A.norm_name("CACI, INC. - FEDERAL"))

    def test_core_name_drops_entity_words_only(self):
        self.assertEqual(A.core_name("LOCKHEED MARTIN CORP"), "LOCKHEED MARTIN")
        self.assertEqual(A.core_name("Booz Allen Hamilton Holding Corp"), "BOOZ ALLEN HAMILTON")
        self.assertEqual(A.core_name("Target Corporation"), "TARGET")

    def test_symbol_variants_cover_finra_and_sec_class_shares(self):
        self.assertIn("BRK/B", A.symbol_variants("BRK-B"))
        self.assertIn("MOGA", A.symbol_variants("MOG-A"))
        self.assertEqual(A.symbol_variants("AAPL"), ["AAPL"])


def filing(uuid, client, registrant, period, posted, expenses=None, income=None, year=2025, ftype="Q1"):
    return {"filing_uuid": uuid, "client": {"name": client}, "registrant": {"id": registrant, "name": f"Firm {registrant}"},
            "filing_year": year, "filing_period": period, "dt_posted": posted, "expenses": expenses, "income": income,
            "filing_type": ftype, "filing_document_url": f"https://lda.gov/filings/public/filing/{uuid}/print/"}


class LobbyingTests(unittest.TestCase):
    def run_with(self, results):
        with mock.patch.object(A, "_lda_get", return_value={"results": results, "next": None}):
            return A.lobbying(["APPLE INC"], [2025])

    def test_in_house_report_is_not_added_to_outside_firm_fees(self):
        out = self.run_with([
            filing("a", "Apple Inc.", 1, "first_quarter", "2025-04-10", expenses="2000000.00"),
            filing("b", "APPLE, INC.", 2, "first_quarter", "2025-04-12", income="300000.00"),
        ])
        self.assertEqual(len(out["rows"]), 1)
        row = out["rows"][0]
        self.assertEqual(row["amount"], 2000000.0)
        self.assertEqual(row["basis"], "in-house report")
        self.assertEqual(row["outside"], 300000.0)

    def test_amendment_replaces_the_report_it_amends(self):
        out = self.run_with([
            filing("a", "Apple Inc.", 1, "second_quarter", "2025-07-10", expenses="1000000.00", ftype="Q2"),
            filing("b", "Apple Inc.", 1, "second_quarter", "2025-09-01", expenses="1200000.00", ftype="2A"),
        ])
        self.assertEqual(out["rows"][0]["amount"], 1200000.0)
        self.assertEqual(len(out["rows"][0]["reports"]), 1)

    def test_outside_firms_only_are_summed_and_other_clients_ignored(self):
        out = self.run_with([
            filing("a", "Apple Inc.", 2, "third_quarter", "2025-10-10", income="50000.00"),
            filing("b", "Apple Inc", 3, "third_quarter", "2025-10-11", income="70000.00"),
            filing("c", "Apple Hospitality REIT", 4, "third_quarter", "2025-10-11", income="900000.00"),
        ])
        self.assertEqual(out["rows"][0]["amount"], 120000.0)
        self.assertEqual(out["rows"][0]["basis"], "outside firms")
        self.assertNotIn("Apple Hospitality REIT", out["client_names"])


class ContractTests(unittest.TestCase):
    def test_children_shared_by_two_parents_count_once(self):
        listing = {"results": [
            {"id": "p1-P", "uei": "U1", "name": "ACME CORP", "recipient_level": "P"},
            {"id": "p2-P", "uei": "U2", "name": "Acme Corporation", "recipient_level": "P"},
            {"id": "x-P", "uei": "U3", "name": "ACME HOSPITALITY CORP", "recipient_level": "P"},
        ], "page_metadata": {"hasNext": False}}
        kids = {"U1": [{"recipient_id": "c1", "name": "ACME CORP", "amount": 100.0}, {"recipient_id": "c2", "name": "ACME FEDERAL LLC", "amount": 50.0}],
                "U2": [{"recipient_id": "c2", "name": "ACME FEDERAL LLC", "amount": 50.0}, {"recipient_id": "c3", "name": "ACME CORPORATION", "amount": 25.0}]}

        def post(path, body):
            return listing if path == "recipient/" else {"results": []}

        def get(path):
            uei = path.split("/")[2]
            return kids[uei]
        with mock.patch.object(A, "_usa_post", side_effect=post), mock.patch.object(A, "_usa_get", side_effect=get):
            out = A.contracts(["ACME CORP"], [2025], today=dt.date(2026, 11, 5))
        self.assertEqual(out["parents"], 2)
        self.assertEqual(out["rows"], [{"fy": 2025, "amount": 175.0, "partial": False}])
        self.assertIn("ACME FEDERAL LLC", out["entities"])

    def test_current_fiscal_year_is_marked_partial(self):
        listing = {"results": [], "page_metadata": {"hasNext": False}}
        with mock.patch.object(A, "_usa_post", return_value=listing):
            out = A.contracts(["ACME CORP"], [2027], today=dt.date(2026, 11, 5))
        self.assertTrue(out["rows"][0]["partial"])
        self.assertEqual(out["rows"][0]["amount"], 0)


if __name__ == "__main__":
    unittest.main()
