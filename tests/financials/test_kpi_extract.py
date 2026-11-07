"""Automated KPI extraction: document choice, cleaning, verification against the page, publication modes."""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from etl import kpi_extract as K  # noqa: E402
from etl import kpitools  # noqa: E402

PAGE = ("THE EXAMPLE CO ANNOUNCES THIRD QUARTER 2026 RESULTS\n"
        "GMV was $642 million, an increase of 23% compared to the same period in 2025\n"
        "Trailing twelve months active buyers was 1,152,000, an increase of 13%\n"
        "Adjusted EBITDA was $17.0 million compared to $9.3 million in the same period in 2025\n")
DOC = {"id": "d1", "doc_type": "release", "period_end": "2026-09-30", "fiscal_label": "Q3 FY26", "ext": ".htm", "pages": 1,
       "title": "8-K 2026-10-29 ex991.htm (Items 2.02,9.01)", "text_path": "EX/d1.json", "storage_path": "EX/d1.htm", "url": "u"}


class FakeSB:
    def __init__(self, defs=(), points=()):
        self.objects, self.defs, self.points = {}, list(defs), list(points)

    def get_json(self, path):
        if path.startswith("companies/"):
            return {"profile": {"fiscalYearEnd": "1231"}, "statements": {"income": [{"quarterly": [{"end": "2025-09-30"}, {"end": "2026-09-30"}],
                                                                                     "annual": [{"end": "2025-12-31"}]}]}}
        return None

    def get_object(self, bucket, path):
        return self.objects.get((bucket, path))

    def put_object(self, bucket, path, body, *a, **k):
        self.objects[(bucket, path)] = body.encode() if isinstance(body, str) else body

    def select(self, table, query):
        if table == "kpi_definitions":
            return self.defs
        if table == "kpi_values":
            return self.points
        if table == "ir_documents":
            return [DOC]
        return []


def reply(messages):
    return ({"kpis": [
        {"id": "GMV", "name": "Gross merchandise value", "category": "Operating metrics", "unit": "USD", "values": [
            {"kind": "Q", "period_end": "2026-09-30", "page": 1, "quote": "GMV was $642 million, an increase of 23%", "printed": "642", "scale": 1000000, "value": 642000000},
            {"kind": "Q", "period_end": "2026-07-31", "page": 1, "quote": "GMV was $642 million", "printed": "642", "scale": 1000000, "value": 642000000}]},
        {"id": "adjusted_ebitda", "name": "Adjusted EBITDA", "category": "Non-GAAP", "unit": "USD", "values": [
            {"kind": "Q", "period_end": "2026-09-30", "page": 1, "quote": "Adjusted EBITDA was $17.0 million", "printed": "17.0", "scale": 1000000, "value": 17000000},
            {"kind": "Q", "period_end": "2025-09-30", "page": 1, "quote": "compared to $9.3 million in the same period", "printed": "9.3", "scale": 1000000, "value": 9300000},
            {"kind": "Q", "period_end": "2025-12-31", "page": 1, "quote": "not on the page", "printed": "5", "scale": 1, "value": 5}]},
        {"id": "made_up", "name": "Made up", "unit": "count", "values": [
            {"kind": "Q", "period_end": "2026-09-30", "page": 1, "quote": "Revenue rose 99%", "printed": "99", "scale": 1, "value": 99}]},
    ]}, "test-model", {})


class Choice(unittest.TestCase):
    def test_sec_results_release_first_and_one_supplement_per_period(self):
        docs = [{**DOC, "id": "site", "title": "Q3 results", "pages": 9},
                {**DOC, "id": "sec"},
                {**DOC, "id": "supp", "doc_type": "supplement"},
                {**DOC, "id": "supp2", "doc_type": "supplement"},
                {**DOC, "id": "old", "period_end": "2026-06-30"},
                {**DOC, "id": "xls", "doc_type": "supplement", "ext": ".xlsx", "period_end": "2026-03-31"},
                {**DOC, "id": "notext", "text_path": None, "period_end": "2025-12-31"}]
        self.assertEqual([d["id"] for d in K.earnings_docs(docs, 6)], ["sec", "supp", "old"])
        self.assertEqual([d["id"] for d in K.earnings_docs(docs, 6, since="2026-06-30")], ["sec", "supp"])

    def test_value_needs_a_known_period_end(self):
        ends = {"Q": {"2026-09-30"}, "A": {"2025-12-31"}}
        good = K._clean_value({"kind": "Q", "period_end": "2026-09-30", "value": "1.5", "page": 2, "quote": "q", "printed": "1.5", "scale": 1e6}, "d1", ends)
        self.assertEqual((good["doc"], good["value"], good["scale"]), ("d1", 1.5, 1000000))
        self.assertIsNone(K._clean_value({"kind": "Q", "period_end": "2026-08-31", "value": 1}, "d1", ends))
        self.assertIsNone(K._clean_value({"kind": "A", "period_end": "2026-09-30", "value": 1}, "d1", ends))
        self.assertIsNone(K._clean_value({"kind": "Q", "period_end": "2026-09-30", "value": "n/a"}, "d1", ends))


class Pipeline(unittest.TestCase):
    def run_company(self, sb, state=None):
        published = []
        with patch.object(kpitools, "text_of", return_value={"kind": "pages", "pages": [PAGE]}), \
             patch.object(kpitools, "docs_for", return_value=[DOC]), \
             patch.object(kpitools, "publish", side_effect=lambda sb, data, **kw: published.append((json.loads(json.dumps(data)), kw))):
            entry = K.company(sb, "EX", "Example", state, complete=reply)
        return entry, published

    def test_only_values_found_on_the_page_are_published(self):
        sb = FakeSB()
        entry, published = self.run_company(sb)
        self.assertEqual(entry["status"], "ok")
        self.assertEqual(entry["mode"], "auto")
        data, kw = published[0]
        self.assertFalse(kw.get("additive"))
        got = {k["id"]: sorted((v["period_end"], v["value"]) for v in k["values"]) for k in data["kpis"]}
        self.assertEqual(got, {"gmv": [("2026-09-30", 642000000.0)], "adjusted_ebitda": [("2025-09-30", 9300000.0), ("2026-09-30", 17000000.0)]})
        self.assertEqual(entry["rejected"], 2)  # the quote not on the page and the made-up figure
        self.assertIn(("raw", "kpi_extract/EX.json"), sb.objects)
        self.assertEqual(entry["read"], ["d1"])
        # nothing unread: the next run does not call the model
        again, published2 = self.run_company(sb, state=entry)
        self.assertEqual((again["status"], published2), ("ok", []))

    def test_hand_built_kpis_get_new_points_only(self):
        sb = FakeSB(defs=[{"kpi_id": "adjusted_ebitda", "name": "Adjusted EBITDA", "unit": "USD", "parent_id": None}],
                    points=[{"kpi_id": "adjusted_ebitda", "period_kind": "Q", "period_end": "2025-09-30"}])
        entry, published = self.run_company(sb)
        data, kw = published[0]
        self.assertTrue(kw.get("additive"))
        got = {k["id"]: [v["period_end"] for v in k["values"]] for k in data["kpis"]}
        self.assertEqual(got, {"gmv": ["2026-09-30"], "adjusted_ebitda": ["2026-09-30"]})
        self.assertNotIn(("raw", "kpi_extract/EX.json"), sb.objects)


if __name__ == "__main__":
    unittest.main()
