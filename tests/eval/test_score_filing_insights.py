import json
import os
import unittest

from tests.eval import score_filing_insights as S

HERE = os.path.dirname(os.path.abspath(__file__))


class LabelSet(unittest.TestCase):
    def test_label_file_shape(self):
        d = json.load(open(os.path.join(HERE, "filing_insights_labels.json")))
        self.assertEqual(len(d["filings"]), 50)
        types = {"guidance", "risk", "customer_supplier", "accounting", "litigation", "capital_allocation", "kpi", "regulation", "operations", "other"}
        for f in d["filings"]:
            self.assertTrue(f["material"], f["ticker"])
            for m in f["material"]:
                self.assertIn(m["type"], types)
                self.assertTrue(8 <= len(m["quote"].split()) <= 40, m["quote"])
                self.assertTrue(m["evidence_urls"][0].startswith("https://www.sec.gov/"))

    def test_scoring(self):
        labels = {"filings": [{"ticker": "T", "accn": "a1", "form": "10-Q", "filed": "2026-10-01", "material": [
            {"type": "litigation", "summary": "Charge for talc litigation resolution", "quote": "includes a charge of approximately $5.5 billion related to the proposed talc ovarian litigation resolution", "section": "Item 2. MD&A"},
            {"type": "kpi", "summary": "Sales up 9%", "quote": "Sales increased by 9% both in the third quarter of 2026 and for the nine months", "section": "Item 2. MD&A"}]}]}
        doc = {"filing": {"accn": "a1"}, "insights": [
            {"type": "litigation", "section": "Item 2. MD&A", "title": "Talc settlement charge", "text": "x",
             "quote": "The fiscal third quarter includes a charge of approximately $5.5 billion related to the proposed talc ovarian litigation resolution"}]}
        res = S.score(labels, lambda p: doc)
        self.assertEqual((res["hits"], res["labels"]), (1, 2))
        stale = S.score(labels, lambda p: {"filing": {"accn": "other"}, "insights": doc["insights"]})
        self.assertEqual(stale["rows"][0]["status"], "stale")
        self.assertEqual(stale["hits"], 0)


if __name__ == "__main__":
    unittest.main()
