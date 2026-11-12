"""Trend workbooks: reading one, settling units, and tying each figure to a page line of a stored supplement."""
import os
import tempfile
import unittest
from unittest.mock import patch

import openpyxl
from openpyxl.styles import Alignment, Font

from etl import ir_workbook as wb, kpitools

URLS = {"4Q25": "https://ir.example.test/2025/4q25-supplement.pdf", "3Q25": "https://ir.example.test/2025/3q25-supplement.pdf"}
PERIODS = ["4Q24", "1Q25", "2Q25", "3Q25", "4Q25", None, "FY2024", "FY2025"]
SOURCE = ["4Q25", "4Q25", "4Q25", "4Q25", "4Q25", None, "4Q25", "4Q25"]


def book(rows, sheet="Income Statement"):
    """A workbook in the trend layout. rows: (label, unit, indent, bold, [cell per period])."""
    w = openpyxl.Workbook()
    index = w.active
    index.title = "Index"
    index.append(["Supplement", "URL"])
    for label, url in URLS.items():
        index.append([label, url])
    ws = w.create_sheet(sheet)
    ws.append(["Test Co. | " + sheet])
    ws.append(["Statements"])
    ws.append(["Source: supplements"])
    ws.append(["Line item", "Unit"] + PERIODS)
    ws.append(["Source supplement (default for the column)", None] + SOURCE)
    for label, unit, indent, bold, cells in rows:
        ws.append([label, unit] + list(cells))
        cell = ws.cell(ws.max_row, 1)
        cell.alignment = Alignment(indent=indent)
        cell.font = Font(bold=bold)
    ws.append([None])
    ws.append(["CHECKS: reported full year less sum of four reported quarters"])
    ws.append(["Total net revenue", None, 1, 2, 3])
    path = os.path.join(tempfile.mkdtemp(), "book.xlsx")
    w.save(path)
    return wb.read(path)


def col(n):
    return openpyxl.utils.get_column_letter(n)


def subtotal(row_numbers, signs=None):
    return [None if p is None else "=" + "".join(("-" if s < 0 else "+" if i else "") + f"{col(3 + j)}{r}"
                                                  for i, (r, s) in enumerate(zip(row_numbers, signs or [1] * len(row_numbers))))
            for j, p in enumerate(PERIODS)]


Q4_PAGE = """TEST CO.
CONSOLIDATED STATEMENTS OF INCOME
(in millions, except per share and ratio data)
QUARTERLY TRENDS FULL YEAR
4Q25 3Q25 2Q25 1Q25 4Q24 2025 2024
REVENUE
Investment banking fees $ 2,960 $ 2,652 $ 2,248 $ 2,178 $ 2,479 $ 10,038 $ 8,910
Principal transactions (a) 6,516 7,119 8,200 7,614 5,195 29,449 24,787
Noninterest revenue 9,476 9,771 10,448 9,792 7,674 39,487 33,697
Provision for credit losses (1,210) 2,515 2,507 4,655 3,403 8,467 9,557
Return on common equity 17 % 18 % 16 % 15 % 14 % 17 % 16 %
Diluted earnings per share 5.57 7.70 5.94 4.63 5.07 23.84 19.75"""


class ReadTest(unittest.TestCase):
    def test_periods_sources_rows_and_subtotals(self):
        b = book([("REVENUE", None, 0, True, [None] * 8),
                  ("Investment banking fees (a)", "USD mm", 1, False, [2479, 2178, 2248, 2652, 2960, None, 8910, 10038]),
                  ("Principal transactions", "USD mm", 1, False, [5195, 7614, 8200, 7119, 6516, None, 24787, 29449]),
                  ("Noninterest revenue", "USD mm", 0, True, subtotal([7, 8]))])
        self.assertEqual(b["sources"], URLS)
        sheet = b["sheets"][0]
        self.assertEqual([(p["label"], p["kind"], p["end"], p["source"]) for p in sheet["periods"]][3:6],
                         [("3Q25", "Q", "2025-09-30", "4Q25"), ("4Q25", "Q", "2025-12-31", "4Q25"), ("FY2024", "A", "2024-12-31", "4Q25")])
        self.assertEqual([r["label"] for r in sheet["rows"]], ["Investment banking fees (a)", "Principal transactions", "Noninterest revenue"])  # header and checks are not lines
        total = sheet["rows"][2]
        self.assertEqual((total["figures"]["4Q25"], total["figures"]["FY2025"], total["terms"]), (9476, 39487, [(1, 7), (1, 8)]))
        self.assertEqual(sheet["rows"][0]["heads"], ["REVENUE"])

    def test_outline_gives_subtotals_their_parts(self):
        b = book([("Interest income", "USD mm", 1, False, [10, 11, 12, 13, 14, None, 40, 50]),
                  ("Interest expense", "USD mm", 1, False, [4, 4, 5, 5, 6, None, 16, 20]),
                  ("Net interest income", "USD mm", 0, True, subtotal([6, 7], [1, -1])),
                  ("of which markets", "USD mm", 1, False, [1, 1, 1, 1, 1, None, 4, 4])])
        ks = {k["name"]: k for k in wb.outline(b, {"prefixes": {"Income Statement": "is"}})}
        self.assertEqual(ks["Net interest income"]["id"], "is_net_interest_income")
        self.assertEqual((ks["Interest income"]["parent"], ks["Interest income"]["sums_to_parent"], ks["Interest income"]["subtracts"]), ("is_net_interest_income", True, False))
        self.assertEqual((ks["Interest expense"]["parent"], ks["Interest expense"]["subtracts"]), ("is_net_interest_income", True))
        self.assertEqual((ks["of which markets"]["parent"], ks["of which markets"]["sums_to_parent"]), ("is_net_interest_income", False))
        self.assertEqual(b["sheets"][0]["rows"][2]["figures"]["4Q25"], 8)

    def test_same_label_twice_gets_two_ids(self):
        b = book([("CONSUMER", None, 0, True, [None] * 8), ("Net income", "USD mm", 1, False, [1, 2, 3, 4, 5, None, 9, 14]),
                  ("WHOLESALE", None, 0, True, [None] * 8), ("Net income", "USD mm", 1, False, [2, 3, 4, 5, 6, None, 11, 18])])
        ids = [k["id"] for k in wb.outline(b)]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(ids[0].endswith("consumer_net_income") and ids[1].endswith("wholesale_net_income"), ids)


class UnitTest(unittest.TestCase):
    def unit(self, label, stated, figures, heads=()):
        row = {"row": 9, "label": label, "unit": stated, "heads": list(heads), "figures": dict(enumerate(figures))}
        unit, scale, mult, _note = wb.unit_of(row, "Sheet")
        return unit, scale, mult, wb.unclear(row, unit, scale)

    def test_stated_units(self):
        self.assertEqual(self.unit("Total net revenue", "USD mm", [50172, 57347]), ("USD", 1e6, 1.0, None))
        self.assertEqual(self.unit("Return on common equity", "%", [0.17, 0.18]), ("%", 1.0, 100.0, None))
        self.assertEqual(self.unit("Diluted earnings per share", "$ per share", [5.57, 7.7]), ("USD_per_share", 1.0, 1.0, None))
        self.assertEqual(self.unit("Client assets (in billions)", "USD bn", [3360, 3177]), ("USD", 1e9, 1.0, None))
        self.assertEqual(self.unit("Active mobile customers (in thousands)", "thousands", [64919, 63746]), ("count", 1e3, 1.0, None))
        self.assertEqual(self.unit("Common shares at period-end", "mm shares", [2655.6, 2658.2]), ("shares", 1e6, 1.0, None))
        self.assertEqual(self.unit("Employees", "count", [322964, 320560]), ("count", 1.0, 1.0, None))

    def test_unit_column_is_corrected_from_the_label(self):
        self.assertEqual(self.unit("Accrued interest and accounts receivable", "count", [164997, 179939])[:2], ("USD", 1e6))
        self.assertEqual(self.unit("Shares held in RSU Trust, at cost", "mm shares", [0, 21])[:2], ("USD", 1e6))
        self.assertEqual(self.unit("Basic earnings per share Net income", "$ per share", [15305, 21155])[:2], ("USD", 1e6))
        self.assertEqual(self.unit("Total transactions (in billions)", "USD bn", [11.7, 11.2])[:2], ("count", 1e9))
        self.assertEqual(self.unit("Personal bankers", "USD mm", [18041, 18102])[:2], ("count", 1.0))
        self.assertEqual(self.unit("Total net revenue: (in millions) Europe/Middle East/Africa", "millions", [515, 520])[:2], ("USD", 1e6))
        self.assertEqual(self.unit("Net interest income - reported", None, [14354, 14453])[:2], ("USD", 1e6))

    def test_scale_marker_on_a_header_applies_to_the_lines_under_it(self):
        self.assertEqual(self.unit("Retail", "USD mm", [8.7, 9.1], heads=["Mortgage origination volume by channel (in billions)"])[:2], ("USD", 1e9))
        self.assertEqual(self.unit("Open accounts", "count", [59.3, 58.1], heads=["Card, excluding Commercial Card (in millions)"])[:2], ("count", 1e6))

    def test_per_share_lines_under_a_per_share_heading(self):
        self.assertEqual(self.unit("NET INCOME", "USD mm", [3.96, 4.12], heads=["DILUTED EARNINGS PER SHARE"])[:2], ("USD_per_share", 1.0))
        self.assertEqual(self.unit("Close", "USD mm", [58.48, 61.2], heads=["SHARE PRICE"])[:2], ("USD_per_share", 1.0))

    def test_lines_whose_unit_cannot_be_settled_are_held(self):
        self.assertEqual(self.unit("Home equity", "USD mm", [88.4, 91.2])[3], "fractional figures in a line taken as millions of dollars")
        self.assertEqual(self.unit("Open accounts", "count", [59.3, 58.1])[3], "fractional figures in a count")
        self.assertEqual(self.unit("Total end-of-period deposits", "USD mm", [342, 376413])[3], "figures change scale between periods")
        # share counts and earnings per share joined in one line stay out
        self.assertIsNotNone(self.unit("Diluted", "USD mm", [5.57, 2781.5], heads=["EARNINGS PER SHARE DATA"])[3])


class AnchorTest(unittest.TestCase):
    def setUp(self):
        self.book = book([("REVENUE", None, 0, True, [None] * 8),
                          ("Investment banking fees", "USD mm", 1, False, [2479, 2178, 2248, 2652, 2960, None, 8910, 10038]),
                          ("Principal transactions", "USD mm", 1, False, [5195, 7614, 8200, 7119, 6516, None, 24787, 29449]),
                          ("Noninterest revenue", "USD mm", 0, True, subtotal([7, 8])),
                          ("Provision for credit losses", "USD mm", 1, False, [3403, 4655, 2507, 2515, -1210, None, 9557, 8467]),
                          ("Return on common equity", "%", 1, False, [0.14, 0.15, 0.16, 0.18, 0.17, None, 0.16, 0.17]),
                          ("Diluted earnings per share", "$ per share", 1, False, [5.07, 4.63, 5.94, 7.70, 5.57, None, 19.75, 23.84]),
                          ("Trading revenue", "USD mm", 1, False, [5195, 7614, 8200, 7119, None, None, None, None]),
                          ("Other income", "USD mm", 1, False, [77, 0, 12, 13, 14, None, 90, 39])])
        self.texts = {"4Q25": ("doc4q25", ["cover", Q4_PAGE])}
        self.data, self.report = wb.anchor(self.book, self.texts, {"ticker": "TEST", "prefixes": {"Income Statement": "is"},
                                                                 "page_titles": {"Income Statement": "statements of income"}})
        self.kpis = {k["id"]: k for k in self.data["kpis"]}

    def value(self, kid, end, kind="Q"):
        return next(v for v in self.kpis[kid]["values"] if v["period_end"] == end and v["kind"] == kind)

    def test_figures_carry_document_page_line_and_printed_number(self):
        v = self.value("is_revenue_investment_banking_fees", "2025-12-31")
        self.assertEqual((v["value"], v["doc"], v["page"], v["printed"], v["scale"]), (2960e6, "doc4q25", 2, "2,960", 1000000))
        self.assertEqual(v["quote"], "Investment banking fees $ 2,960 $ 2,652 $ 2,248 $ 2,178 $ 2,479 $ 10,038 $ 8,910")
        self.assertEqual(self.value("is_revenue_investment_banking_fees", "2025-12-31", "A")["value"], 10038e6)
        self.assertEqual(self.value("is_revenue_principal_transactions", "2025-09-30")["printed"], "7,119")  # footnote marker on the page line
        loss = self.value("is_revenue_provision_for_credit_losses", "2025-12-31")
        self.assertEqual((loss["value"], loss["printed"]), (-1210e6, "(1,210)"))
        roe = self.value("is_revenue_return_on_common_equity", "2025-12-31")
        self.assertEqual((roe["value"], roe["printed"], roe["scale"], self.kpis["is_revenue_return_on_common_equity"]["unit"]), (17.0, "17", 1, "%"))
        self.assertEqual(self.value("is_revenue_diluted_earnings_per_share", "2025-09-30")["value"], 7.70)

    def test_a_renamed_line_is_found_by_its_neighbouring_periods(self):
        # "Trading revenue" is printed as "Principal transactions": same figures in the periods around it
        v = self.value("is_revenue_trading_revenue", "2025-06-30")
        self.assertEqual((v["printed"], v["quote"].split(" (a)")[0]), ("8,200", "Principal transactions"))
        self.assertEqual(self.report["by_neighbours"], 4)

    def test_what_is_not_on_a_page_is_not_published(self):
        other = self.kpis.get("is_revenue_other_income")
        self.assertIsNone(other)  # the supplement prints no such line
        self.assertEqual(self.report["not_found"], 6)
        self.assertEqual(self.report["zero_skipped"], 1)
        self.assertEqual(self.report["anchored"], sum(len(k["values"]) for k in self.data["kpis"]))
        self.assertEqual(self.report["missing_supplements"], ["3Q25"])

    def test_subtotal_ties_and_passes_the_page_check(self):
        total = self.kpis["is_revenue_noninterest_revenue"]
        self.assertTrue(self.kpis["is_revenue_investment_banking_fees"]["sums_to_parent"])
        self.assertEqual(self.kpis["is_revenue_investment_banking_fees"]["parent"], total["id"])
        docs = [{"id": "doc4q25", "ticker": "TEST", "url": URLS["4Q25"], "ext": ".pdf"}]
        with patch.object(kpitools, "docs_for", return_value=docs), patch.object(kpitools, "text_of", return_value={"kind": "pages", "pages": self.texts["4Q25"][1]}):
            report, _clean = kpitools.validate(None, self.data, fix=True)
        self.assertEqual(report["bad_values"], [])
        self.assertEqual(report["tie_failures"], [])
        self.assertEqual(len(report["ties"]), 7)  # five quarters and two years

    def test_period_end_balances_are_instants_without_year_columns(self):
        b = book([("Total assets", "USD mm", 0, True, [100, 110, 120, 130, 140, None, 100, 140])], sheet="Balance Sheet")
        page = "TEST CO.\nCONSOLIDATED BALANCE SHEETS\nTotal assets $ 140 $ 130 $ 120 $ 110 $ 100"
        data, report = wb.anchor(b, {"4Q25": ("d", [page])}, {"ticker": "TEST", "instant_sheets": ["Balance Sheet"]})
        self.assertEqual({(v["kind"], v["period_end"]) for v in data["kpis"][0]["values"]},
                         {("I", e) for e in ("2024-12-31", "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31")})
        self.assertEqual(report["figures"], 5)

    def test_candidates_are_the_supplements_that_print_the_period(self):
        every = {f"{q}Q{y}": "u" for y in (24, 25, 26) for q in (1, 2, 3, 4)}
        self.assertEqual(wb.candidates({"kind": "Q", "end": "2025-03-31", "source": "1Q26"}, every), ["1Q26", "4Q25", "3Q25", "2Q25", "1Q25"])
        self.assertEqual(wb.candidates({"kind": "A", "end": "2024-12-31", "source": "4Q25"}, every), ["4Q25", "4Q24"])


if __name__ == "__main__":
    unittest.main()
