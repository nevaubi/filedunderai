"""Official IR source lists: raw research files -> records, and what the collector does with a list."""
import collections
import json
import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from etl import common, ir, ir_official, jobs

try:
    from test_ir import Store, existing, pdf_document, sub
except ImportError:  # run as tests.financials.test_ir_official
    from tests.financials.test_ir import Store, existing, pdf_document, sub


def read(path):
    with open(path) as f:
        return json.load(f)


def raw_files(ticker):
    return {n: read(os.path.join(ir_official.RAW, n)) for n in sorted(os.listdir(ir_official.RAW)) if n.startswith(ticker + "--")}


def listing(*docs):
    """A one-company list as `build` writes it."""
    return {"version": ir_official.VERSION, "ticker": "TEST", "company": "Test", "fye": "1231",
            "built_from": [{"file": "raw/TEST--results.json", "page": "https://ir.example.test/results", "links": len(docs)}],
            "documents": [{"section": ir_official.QUARTERLY, "fiscal_year": 2026, "fiscal_quarter": 1, "fiscal_label": "Q1 FY26",
                           "period_end": "2026-03-31", "published": "2026-04-14", "source_page": "https://ir.example.test/results",
                           "store": "core", "listed_as": None, **d} for d in docs]}


class ReadersTest(unittest.TestCase):
    def test_quarter_names(self):
        for text, want in (("Third Quarter 2026", (2026, 3)), ("Q3 2026", (2026, 3)), ("3Q26 Earnings Supplement", (2026, 3)),
                           ("2026-Q3", (2026, 3)), ("4q05-earnings-supplement", (2005, 4)), ("first-quarter-2025-earnings", (2025, 1)),
                           ("Q4 FY24", (2024, 4))):
            self.assertEqual(ir_official.quarter_in(text), want, text)
        self.assertIsNone(ir_official.quarter_in("2025 Annual Report"))

    def test_dates(self):
        self.assertEqual(ir_official.iso_date("October 15, 2026"), "2026-10-15")
        self.assertEqual(ir_official.iso_date("Historical Financial Supplement - April 09, 2018"), "2018-04-09")
        self.assertEqual(ir_official.iso_date("View 2026 Nov 05 10-q filing PDF"), "2026-11-05")
        self.assertIsNone(ir_official.iso_date("Third Quarter 2026"))

    def test_quarter_ends_follow_the_fiscal_year_end(self):
        self.assertEqual(ir_official.quarter_end("1231", 2026, 3), "2026-09-30")
        self.assertEqual(ir_official.quarter_end("1231", 2024, 1), "2024-03-31")
        self.assertEqual(ir_official.quarter_end("0930", 2026, 1), "2025-12-31")
        self.assertEqual(ir_official.quarter_end("0630", 2026, 2), "2025-12-31")
        self.assertEqual(ir_official.labels("1231", 2026, 3), ("Q3 FY26", "2026-09-30"))
        self.assertEqual(ir_official.labels("1231", 2025, None), ("FY2025", "2025-12-31"))

    def test_same_file_under_a_new_site_build_token(self):
        a = "https://d1io3yog0oux5.cloudfront.net/_bc4a965077ef1fae05650189449ffdf0/pnc/db/2463/24028/earnings_release/3Q26.pdf"
        b = "https://D1IO3YOG0OUX5.cloudfront.net/_089afc4ce03f3c8175c939c4de65c807/pnc/db/2463/24028/earnings_release/3Q26.pdf#page=2"
        self.assertEqual(ir_official.url_key(a), ir_official.url_key(b))
        self.assertNotEqual(ir_official.url_key(a), ir_official.url_key(a.replace("3Q26", "2Q26")))

    def test_types(self):
        for words, url, want in (("earnings_release_pdf", "https://x.test/a.pdf", "release"), ("Earnings Supplement (xls)", "https://x.test/a.xlsx", "supplement"),
                                 ("sec_filing_xbrl_zip", "https://x.test/a.zip", "sec_filing"), ("10-Q", "https://x.test/a.pdf", "sec_filing"),
                                 ("Webcast", "https://x.test/call.mp3", "webcast"), ("Earnings conference call / event page", "https://x.test/e.aspx", "webcast"),
                                 ("Earnings Release (news page)", "https://x.test/n.aspx", "release"), ("Earnings Transcript", "https://x.test/t.pdf", "transcript"),
                                 ("quarterly supplement", "https://x.test/s.pdf", "supplement"), ("historic annual report", "https://x.test/ar.pdf", "annual_report")):
            self.assertEqual(ir_official.doc_type(words, url), want, words)


class BuildTest(unittest.TestCase):
    """The five lists against what each research file says it found."""

    @classmethod
    def setUpClass(cls):
        meta = read(os.path.join(ir_official.RAW, "_meta.json"))
        cls.lists = {t: ir_official.build_ticker(t, raw_files(t), meta.get(t)) for t in ("JPM", "AMZN", "WFC", "PNC", "C")}

    def quarterly(self, ticker, **where):
        return [d for d in self.lists[ticker]["documents"] if d["section"] == ir_official.QUARTERLY and all(d[k] == v for k, v in where.items())]

    def test_committed_lists_are_the_build_output(self):
        for ticker, lst in self.lists.items():
            with open(os.path.join(ir_official.SOURCES, f"{ticker}.json")) as f:
                self.assertEqual(f.read(), ir_official.dumps(lst), f"{ticker}: run python -m etl.ir_official build")

    def test_jpm(self):
        raw = raw_files("JPM")["JPM--quarterly-earnings-inventory.json"]
        self.assertEqual(len({d["period_end"] for d in self.quarterly("JPM")}), raw["quarter_count"])  # 87 quarters, 1Q05 to 3Q26
        self.assertEqual(len(self.quarterly("JPM", doc_type="release")), 87)
        self.assertEqual(len(self.quarterly("JPM", doc_type="presentation")), 87)
        self.assertEqual(len(self.quarterly("JPM", doc_type="supplement", ext=".pdf")), 87)
        self.assertEqual(len(self.quarterly("JPM", doc_type="supplement", ext=".xlsx")), 23)
        self.assertEqual(len(self.quarterly("JPM", doc_type="webcast")), len(raw["webcasts"]))
        # every file of the inventory is in the list; the second crawl adds three it missed
        keys = {ir_official.url_key(d["url"]) for d in self.lists["JPM"]["documents"]}
        self.assertTrue(all(ir_official.url_key(f["url"]) in keys for f in raw["files"]))
        self.assertEqual(len(keys), len(raw["files"]) + len(raw["webcasts"]) + 3)
        first = self.quarterly("JPM", doc_type="supplement", ext=".pdf", fiscal_year=2005, fiscal_quarter=1)[0]
        self.assertEqual((first["fiscal_label"], first["period_end"], first["store"]), ("Q1 FY05", "2005-03-31", "core"))
        self.assertTrue(first["url"].endswith("/2005/1st-quarter/1q05-earnings-supplement.pdf"))
        self.assertTrue(all(d["store"] == "link" for d in self.quarterly("JPM", doc_type="sec_filing")))

    def test_amzn(self):
        raw = raw_files("AMZN")["AMZN--quarterly-results-and-events.json"]
        self.assertEqual(len(self.lists["AMZN"]["documents"]), raw["file_count"])
        self.assertEqual(len({d["period_end"] for d in self.quarterly("AMZN")}), raw["quarter_count"])
        pdfs = self.quarterly("AMZN", doc_type="release", ext=".pdf")
        self.assertEqual((len(pdfs), {d["store"] for d in pdfs}), (35, {"core"}))
        # the web page of a release is the same release as its PDF: listed, not stored twice
        pages = [d for d in self.quarterly("AMZN", doc_type="release") if d["ext"] != ".pdf"]
        self.assertEqual((len(pages), {d["store"] for d in pages}), (35, {"link"}))
        self.assertEqual(len(self.quarterly("AMZN", doc_type="presentation")), 34)  # none for Q2 2018
        self.assertFalse(self.quarterly("AMZN", doc_type="presentation", fiscal_year=2018, fiscal_quarter=2))

    def test_wfc(self):
        raw = raw_files("WFC")["WFC--quarterly-earnings-and-history.json"]
        current = [d for d in self.quarterly("WFC") if "wellsfargo.com/assets/pdf" in d["url"]]
        self.assertEqual(len(current), raw["coverage"]["files_found"])
        self.assertEqual(collections.Counter(d["doc_type"] for d in current), {"release": 23, "presentation": 23, "supplement": 23})
        self.assertEqual(len({d["period_end"] for d in self.quarterly("WFC")}), 23 + raw["historical_coverage"]["quarters"])
        self.assertEqual(len([d for d in self.lists["WFC"]["documents"] if d["section"] == "Annual reports"]), raw["historical_coverage"]["history_annual_reports"])
        filing = self.quarterly("WFC", fiscal_year=2000, fiscal_quarter=1)[0]
        self.assertEqual((filing["doc_type"], filing["store"], filing["published"]), ("sec_filing", "link", "2000-05-15"))

    def test_pnc(self):
        raw = raw_files("PNC")["PNC--financial-press-releases.json"]
        self.assertEqual(len(self.quarterly("PNC", doc_type="release")), len(raw["quarters"]))
        self.assertTrue(all(d["published"] and d["source_page"].startswith("https://investor.pnc.com/news-events/financial-press-releases/detail/")
                            for d in self.quarterly("PNC", doc_type="release")))
        # the January 2009 page of preliminary 2008 results reports the quarter that had just ended
        early = [d for d in self.quarterly("PNC", doc_type="release") if d["published"] == "2009-01-21"]
        self.assertEqual([(d["fiscal_year"], d["fiscal_quarter"]) for d in early], [(2008, 4)])
        annual = [d for d in self.lists["PNC"]["documents"] if d["doc_type"] == "annual_report"]
        self.assertEqual((len(annual), {d["section"] for d in annual}, {d["fiscal_quarter"] for d in annual}), (19, {"Annual reports"}, {None}))

    def test_citi(self):
        raw = raw_files("C")["C--quarterly-earnings.json"]
        cov = raw["coverage"]
        files = [d for d in self.lists["C"]["documents"] if d["section"] in (ir_official.QUARTERLY, "Historical supplements") and d["store"] == "core"]
        self.assertEqual(len(files), cov["direct_file_count"])  # 195 in the quarter cards, 11 historical supplements
        self.assertEqual(collections.Counter(d["ext"].lstrip(".") for d in files if d["section"] == ir_official.QUARTERLY), cov["direct_file_formats"])
        self.assertEqual(len({d["period_end"] for d in self.quarterly("C")}), cov["quarter_count"])
        q = self.quarterly("C", fiscal_year=2026, fiscal_quarter=3, doc_type="release", ext=".pdf")[0]
        self.assertEqual((q["published"], q["fiscal_label"]), ("2026-10-13", "Q3 FY26"))
        extra = raw_files("C")["C--quarterly-earnings-and-investor-pages.json"]["additional_ir_materials"]
        # every file of the investor pages is in the list once, under the section it was listed in first
        by_key = {ir_official.url_key(d["url"]): d for d in self.lists["C"]["documents"]}
        self.assertEqual(len(by_key), len(self.lists["C"]["documents"]))
        for cat in extra["categories"]:
            self.assertEqual(len(cat["documents"]), extra["document_count_by_category"][cat["category"]])
            for doc in cat["documents"]:
                self.assertIn(ir_official.url_key(doc["file_url"]), by_key, doc["name"])
        debt = [d for d in self.lists["C"]["documents"] if d["section"] == "Debt securities"]
        self.assertEqual(len(debt), extra["document_count_by_category"]["Debt securities"])
        days = [d for d in self.lists["C"]["documents"] if d["section"] == "Investor days" and d["store"] == "core"]
        self.assertEqual({d["fiscal_year"] for d in days}, {2022, 2024, 2026})
        self.assertFalse([d for d in self.lists["C"]["documents"] if d["doc_type"] in ("regulatory", "prospectus", "proxy") and d["store"] != "extended"])


class CollectorTest(unittest.TestCase):
    def providers(self, lst, bodies=None):
        stack = ExitStack()
        stack.enter_context(patch.object(ir_official, "load", side_effect=lambda t: lst if t == "TEST" else None))
        stack.enter_context(patch.object(ir, "OFFICIAL_PAUSE", 0))
        stack.enter_context(patch.object(ir, "firecrawl_map", return_value=[]))
        stack.enter_context(patch.object(ir, "_scrape", side_effect=lambda *a, **k: {"html": "<html></html>"}, create=True))
        stack.enter_context(patch.object(ir, "_sec_json", return_value=sub(), create=True))
        stack.enter_context(patch.object(jobs, "periodic_filings", return_value=[]))
        self.requested = []

        def download(url, **kw):
            self.requested.append(url)
            body = (bodies or {}).get(url)
            if isinstance(body, Exception):
                raise body
            return body or ("Label,Value\n" + url + ",42\n").encode()
        stack.enter_context(patch.object(ir, "download", side_effect=download))
        return stack

    def test_listed_files_are_stored_with_the_stated_period_and_type(self):
        sb = Store()
        # no quarter in the file name, and "supplement" in a release's name: the listing decides both
        lst = listing({"url": "https://cdn.example.test/files/6c1f8114-1be4.csv", "title": "Q1 2026 Earnings release", "doc_type": "release", "ext": ".csv"},
                      {"url": "https://cdn.example.test/files/supplement-notes.csv", "title": "Q1 2026 Earnings call transcript", "doc_type": "transcript", "ext": ".csv"})
        with self.providers(lst):
            result = ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual((result["web"], result["official_queued"]), (2, 2))
        rows = {r["url"]: r for r in sb.rows.values()}
        release = rows["https://cdn.example.test/files/6c1f8114-1be4.csv"]
        self.assertEqual((release["doc_type"], release["fiscal_label"], release["period_end"], release["published"], release["source_page"]),
                         ("release", "Q1 FY26", "2026-03-31", "2026-04-14", "https://ir.example.test/results"))
        self.assertEqual(rows["https://cdn.example.test/files/supplement-notes.csv"]["doc_type"], "transcript")
        official = sb.json["ir/TEST.json"]["official"]
        self.assertEqual((official["listed"], official["stored"], official["missing"]), (2, 2, 0))
        self.assertEqual(official["pages"], ["https://ir.example.test/results"])
        self.assertEqual({f["state"] for f in official["files"]}, {"stored"})

    def test_stored_documents_take_the_listed_period_and_type(self):
        sb = Store()
        url = "https://ir.example.test/q1-2026-earnings.csv"
        did = existing(sb, url)
        sb.rows[did].update(doc_type="supplement", fiscal_label=None, period_end=None, published=None, title="q1-2026-earnings.csv")
        lst = listing({"url": url, "title": "Q1 2026 Earnings release", "doc_type": "release", "ext": ".csv"})
        with self.providers(lst):
            result = ir.process_company(sb, "TEST", "Test", 1)
        row = sb.rows[did]
        self.assertEqual(result["official_corrected"], 1)
        self.assertEqual((row["doc_type"], row["fiscal_label"], row["period_end"], row["published"]), ("release", "Q1 FY26", "2026-03-31", "2026-04-14"))
        self.assertEqual(row["title"], "q1-2026-earnings.csv")  # a title is filled in, never replaced
        self.assertEqual(self.requested, [])  # nothing is downloaded again
        with self.providers(lst):
            self.assertEqual(ir.process_company(sb, "TEST", "Test", 1)["official_corrected"], 0)

    def test_links_and_extended_material_are_listed_not_stored(self):
        sb = Store()
        lst = listing({"url": "https://ir.example.test/q1-2026-10q.pdf", "title": "Q1 2026 10-Q", "doc_type": "sec_filing", "ext": ".pdf", "store": "link"},
                      {"url": "https://ir.example.test/pillar-3-q1-2026.pdf", "title": "Pillar 3", "doc_type": "regulatory", "ext": ".pdf", "store": "extended",
                       "section": "Other regulatory filings"})
        with self.providers(lst):
            result = ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual((result["web"], self.requested, sb.rows), (0, [], {}))
        official = sb.json["ir/TEST.json"]["official"]
        self.assertEqual((official["link"], official["not_collected"], [f["state"] for f in official["files"]]), (1, 1, ["link"]))

    def test_a_quarter_held_only_as_an_sec_copy_is_reported_as_a_copy_and_still_queued(self):
        sb = Store()
        copy_id = existing(sb, "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/ex991.htm")
        sb.rows[copy_id].update(period_end="2026-03-31", doc_type="release")
        lst = listing({"url": "https://ir.example.test/q1-2026-release.csv", "title": "Q1 2026 Earnings release", "doc_type": "release", "ext": ".csv"})
        cov = ir_official.coverage(lst, list(sb.rows.values()))
        self.assertEqual((cov["copy"], cov["missing"], cov["files"][0]["copy_id"]), (1, 0, copy_id))
        with self.providers(lst):
            ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual(self.requested, ["https://ir.example.test/q1-2026-release.csv"])

    def test_a_blocked_listed_file_is_tried_again_once_per_list_version(self):
        sb = Store()
        url = "https://ir.example.test/q1-2026-release.csv"
        lst = listing({"url": url, "title": "Q1 2026 Earnings release", "doc_type": "release", "ext": ".csv"})
        with self.providers(lst, {url: ValueError("refused")}):
            for _ in range(ir.MAX_ATTEMPTS + 2):
                ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual(len(self.requested), ir.MAX_ATTEMPTS)
        state = json.loads(sb.objects["raw", "ir/checkpoints/TEST.json"])
        self.assertEqual(state["queues"]["documents"][url]["status"], "blocked")
        with self.providers({**lst, "version": ir_official.VERSION + 1}), patch.object(ir_official, "VERSION", ir_official.VERSION + 1):
            ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual(self.requested, [url])
        self.assertEqual(len(sb.rows), 1)

    def test_a_refused_listed_pdf_is_retried_before_a_text_only_copy_stands_in(self):
        sb = Store()
        url = "https://ir.example.test/q1-2026-supplement.pdf"
        lst = listing({"url": url, "title": "Q1 2026 Financial supplement", "doc_type": "supplement", "ext": ".pdf"})
        refused = common.HTTPError(403, "Forbidden")
        copies = []
        with patch.object(ir, "text_copy", side_effect=lambda u, **kw: copies.append(u) or b"<!doctype html><html><body><pre>copy</pre></body></html>"):
            with self.providers(lst, {url: refused}):
                ir.process_company(sb, "TEST", "Test", 1)
            self.assertEqual((copies, sb.rows), ([], {}))  # throttled: no stand-in on the first refusal
            original = pdf_document(["Total net revenue 50,172 57,347"])
            with self.providers(lst, {url: original}):
                ir.process_company(sb, "TEST", "Test", 1)
        row = next(iter(sb.rows.values()))
        self.assertEqual((copies, row["ext"], row["doc_type"], row["period_end"]), ([], ".pdf", "supplement", "2026-03-31"))
        self.assertFalse(row["title"].startswith(ir.TEXT_COPY_PREFIX))

    def test_a_file_listed_on_another_host_is_not_dropped_as_work_from_a_wrong_site(self):
        sb = Store()
        # annual reports listed on the company's history site, not on the IR site the collector crawls
        url = "https://history.example.test/assets/2019-annual-report.csv"
        lst = listing({"url": url, "title": "2019 annual report", "doc_type": "annual_report", "ext": ".csv", "section": "Annual reports",
                       "fiscal_year": 2019, "fiscal_quarter": None, "fiscal_label": "FY2019", "period_end": "2019-12-31", "published": None,
                       "source_page": "https://history.example.test/annual-reports/"})
        with self.providers(lst):
            result = ir.process_company(sb, "TEST", "Test", 1)
        row = next(iter(sb.rows.values()))
        self.assertEqual((self.requested, row["doc_type"], row["fiscal_label"], row["period_end"]), ([url], "annual_report", "FY2019", "2019-12-31"))
        self.assertEqual(result["official"]["missing"], 0)

    def test_same_file_under_another_site_token_is_not_stored_twice(self):
        sb = Store()
        old = "https://cdn.example.test/_0123456789abcdef0123456789abcdef/db/1/q1-2026-release.csv"
        did = existing(sb, old)
        lst = listing({"url": old.replace("0123456789abcdef0123456789abcdef", "fedcba9876543210fedcba9876543210"), "title": "Q1 2026 Earnings release",
                       "doc_type": "release", "ext": ".csv"})
        with self.providers(lst):
            result = ir.process_company(sb, "TEST", "Test", 1)
        self.assertEqual((self.requested, list(sb.rows), result["official"]["stored"]), ([], [did], 1))


if __name__ == "__main__":
    unittest.main()
