"""KPI matrix Excel export: request validation, formulas, units, stale handling and source links."""
import datetime as dt
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from openpyxl import load_workbook  # noqa: E402

from etl import matrix_export as mx  # noqa: E402

TAX = {"kpis": [
    {"id": "nim", "name": "Net interest margin", "family": "Banks", "unit": "%", "definition": "NII / earning assets", "how": "median", "higher_is_better": True},
    {"id": "deposits", "name": "Total deposits", "family": "Banks", "unit": "USD", "definition": "Deposits", "how": "sum", "higher_is_better": None},
]}
INDEX = {"companies": [
    {"ticker": "AAA", "name": "Alpha Bank", "sector": "Financials", "as_of": "2026-11-04", "price": 50.0, "market_cap": 10e9,
     "revenue_ttm": 2e9, "net_income_ttm": 5e8, "fcf_ttm": 4e8, "rev_growth_lq": 0.05, "latest_quarter": "Q3 FY26", "latest_quarter_end": "2026-09-30"},
    {"ticker": "BBB", "name": "Beta Bank", "sector": "Financials", "as_of": "2026-11-04", "price": 20.0, "market_cap": 2e9,
     "revenue_ttm": None, "net_income_ttm": -1e8, "fcf_ttm": None},
]}
DOCS = {"d1": ["8-K release", "AAA/d1.htm", ".htm", "https://www.sec.gov/a.htm", "release", "Q3 FY26"],
        "d0": ["8-K release", "AAA/d0.htm", ".htm", "https://www.sec.gov/b.htm", "release", "Q3 FY25"]}
SERIES = {
    "nim": {"companies": {
        "AAA": {"kpi_id": "nim", "name": "Net interest margin", "unit": "%", "current": True,
                "Q": [["2025-09-30", 3.0, "Q3 FY25", "d0", 2, "NIM 3.00 %", None, None, None, 3.0],
                      ["2026-06-30", 3.1, "Q2 FY26", "d1", 2, "NIM 3.10 %", None, None, None, 3.1],
                      ["2026-09-30", 3.2, "Q3 FY26", "d1", 2, "NIM 3.20 %", None, None, None, 3.2]], "A": []},
        "BBB": {"kpi_id": "nim_old", "name": "NIM (old)", "unit": "%", "current": False,
                "Q": [["2012-12-31", 2.5, "Q4 FY12", "d0", 1, "NIM 2.5%", None, None, None, 2.5]], "A": []},
    }, "docs": DOCS},
    "deposits": {"companies": {
        "AAA": {"kpi_id": "deposits", "name": "Deposits", "unit": "USD", "current": True,
                "Q": [["2025-09-30", 80e9, "Q3 FY25", "d0", 3, "Deposits 80,000", None, None, None, 80e9],
                      ["2026-09-30", 88e9, "Q3 FY26", "d1", 3, "Deposits 88,000", None, None, None, 88e9]], "A": []},
    }, "docs": DOCS},
}
NOW = dt.datetime(2026, 11, 6, 12, tzinfo=dt.timezone.utc)


def build(**kw):
    body = {"tickers": ["AAA", "BBB"], "kpis": ["nim", "deposits"], "basis": "Q", "label": "Test banks", "hide_stale": False}
    body.update(kw)
    req = mx.clean_request(body, {k["id"] for k in TAX["kpis"]})
    data, name = mx.build(req, INDEX, TAX, SERIES, {"AAA": "Banks"}, now=NOW)
    return load_workbook(io.BytesIO(data)), name


class CleanRequest(unittest.TestCase):
    def test_rejects_empty_and_bad(self):
        ids = {"nim"}
        with self.assertRaises(ValueError):
            mx.clean_request({"tickers": []}, ids)
        with self.assertRaises(ValueError):
            mx.clean_request({"tickers": ["<script>"]}, ids)
        with self.assertRaises(ValueError):
            mx.clean_request({"tickers": [f"T{i}" for i in range(mx.MAX_TICKERS + 1)]}, ids)
        with self.assertRaises(ValueError):
            mx.clean_request([], ids)

    def test_filters_unknown_kpis_and_sanitizes(self):
        r = mx.clean_request({"tickers": ["aaa", "AAA", "bbb"], "kpis": ["nim", "nope", "../x"], "basis": "Z", "label": "<b>Banks</b>=1"}, {"nim"})
        self.assertEqual(r["tickers"], ["AAA", "BBB"])
        self.assertEqual(r["kpis"], ["nim"])
        self.assertEqual(r["basis"], "Q")
        self.assertNotIn("<", r["label"])
        self.assertNotIn("=", r["label"])


class Workbook(unittest.TestCase):
    def test_sheets_and_name(self):
        wb, name = build()
        self.assertEqual(wb.sheetnames, ["Comps", "KPI matrix", "KPI history", "Definitions", "Notes"])
        self.assertTrue(name.startswith("filedunder-comps-test-banks-2026-11-06"))

    def test_comps_inputs_in_millions_and_formulas(self):
        ws = build()[0]["Comps"]
        heads = [c.value for c in ws[4]]
        col = {h: i + 1 for i, h in enumerate(heads)}
        self.assertEqual(ws.cell(5, col["Market cap ($M)"]).value, 10000)
        self.assertEqual(ws.cell(5, col["Market cap ($M)"]).font.color.rgb[-6:], "0000FF")
        pe = ws.cell(5, col["P/E TTM"]).value
        self.assertTrue(pe.startswith("=IF(AND(ISNUMBER(F5),ISNUMBER(H5))"), pe)
        self.assertEqual(ws.cell(5, col["P/E TTM"]).font.color.rgb[-6:], "000000")
        self.assertIsNone(ws.cell(6, col["Revenue TTM ($M)"]).value)
        self.assertEqual(ws.cell(8, 1).value, "Median")
        self.assertIn("MEDIAN(F5:F6)", ws.cell(8, col["Market cap ($M)"]).value)
        self.assertEqual(ws.freeze_panes, "C5")

    def test_kpi_values_units_links_and_yoy(self):
        ws = build()[0]["KPI matrix"]
        self.assertEqual(ws.cell(4, 4).value, "Net interest margin (%)")
        self.assertAlmostEqual(ws.cell(6, 4).value, 0.032)
        self.assertEqual(ws.cell(6, 5).value, "Q3 FY26")
        self.assertAlmostEqual(ws.cell(6, 6).value, 0.030)
        self.assertEqual(ws.cell(6, 7).value, '=IF(AND(ISNUMBER(D6),ISNUMBER(F6)),D6-F6,"")')
        self.assertEqual(ws.cell(6, 4).hyperlink.target, "https://www.sec.gov/a.htm")
        self.assertEqual(ws.cell(4, 8).value, "Total deposits ($M)")
        self.assertEqual(ws.cell(6, 8).value, 88000)
        self.assertEqual(ws.cell(6, 11).value, '=IF(AND(ISNUMBER(H6),ISNUMBER(J6)),IF(J6>0,H6/J6-1,"NM"),"")')

    def test_stale_marked_or_hidden(self):
        ws = build()[0]["KPI matrix"]
        self.assertEqual(ws.cell(7, 5).value, "Q4 FY12 (stale)")
        self.assertTrue(ws.cell(7, 4).font.italic)
        ws = build(hide_stale=True)[0]["KPI matrix"]
        self.assertIsNone(ws.cell(7, 4).value)

    def test_history_rows_cite_sources(self):
        ws = build()[0]["KPI history"]
        heads = [c.value for c in ws[4]]
        rows = [[c.value for c in r] for r in ws.iter_rows(min_row=5) if r[0].value]
        aaa_nim = [r for r in rows if r[0] == "AAA" and r[2] == "Net interest margin"]
        self.assertEqual(len(aaa_nim), 3)
        self.assertEqual(aaa_nim[0][heads.index("Period")], "Q3 FY26")
        self.assertEqual(aaa_nim[0][heads.index("Quoted text")], "NIM 3.20 %")
        self.assertEqual(aaa_nim[0][heads.index("Source URL")], "https://www.sec.gov/a.htm")

    def test_no_kpis_gives_comps_and_notes(self):
        wb, _ = build(kpis=[])
        self.assertEqual(wb.sheetnames, ["Comps", "Notes"])

    def test_year_ago_window(self):
        pts = [["2025-06-30", 1], ["2025-09-30", 2], ["2026-09-30", 3]]
        self.assertEqual(mx.year_ago(pts, 2)[1], 2)
        self.assertIsNone(mx.year_ago([["2024-09-30", 1], ["2026-09-30", 3]], 1))


if __name__ == "__main__":
    unittest.main()
