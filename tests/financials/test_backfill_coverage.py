import copy
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from etl.backfill import encoded
from etl.universe import ADDITIONAL_REGISTRY, SP500
from test_pipeline import MemoryStore

try:
    from etl import backfill_coverage as coverage
except ImportError:
    coverage = None


class CoverageStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.objects, self.queries = {}, []
        self.corrupt_backup = self.corrupt_public = False
        self.new = {row["ticker"]:row["cik"] for row in ADDITIONAL_REGISTRY["companies"]}
        companies = {ticker:{"docs":0,"bytes":0,"types":{},"kpis":0,"kpi_values":0,
            "segment_tables":0,"segment_ties":0,"coverage":{"status":"unverified","total_documents":0}}
            for ticker in SP500}
        companies["AAPL"].update(docs=2,bytes=40,types={"release":2},kpis=227953,kpi_values=3944368,
            segment_tables=1,segment_ties=1,coverage={"status":"unverified","total_documents":2},
            custom_note="Preserve this",documents_as_of="2026-11-01T10:00:00Z")
        self.data["meta/coverage_ir.json"] = {
            "generated":"2026-11-01T10:00:00Z","kpi_counts_as_of":"2026-10-31T09:00:00Z",
            "segment_counts_as_of":"2026-10-30T08:00:00Z","custom_root":"preserve",
            "companies":companies,"summary":{"companies":500,"documents":2,"bytes":40,
                "kpis":227953,"kpi_values":3944368,"companies_with_docs":1,"companies_with_kpis":1,
                "companies_with_segments":1,"segment_tables":1,"coverage":{"unverified":500}}}
        self.data["meta/segments.json"] = {"generated":"2026-11-01T00:00:00Z",
            "companies":{"AAPL":{"tables":1,"recon_ok":1}}}
        self.rows = {"companies":[{"ticker":ticker,"cik":cik} for ticker,cik in self.new.items()],
            "ir_documents":[{"ticker":ticker,"doc_type":"release","bytes":100} for ticker in self.new],
            "kpi_definitions":[{"ticker":"AAPL"}],"kpi_values":[{"ticker":"AAPL"}]}
        self.extra = set(list(self.new)[:7])
        self.rows["ir_documents"] += [{"ticker":ticker,"doc_type":"supplement","bytes":200} for ticker in self.extra]

    def select(self,table,query=""):
        self.queries.append((table,query))
        parsed = parse_qs(query)
        scope = parsed.get("ticker",[""])[0]
        if not scope.startswith("in.(") or set(scope[4:-1].split(",")) != set(self.new):
            raise AssertionError("Every relational read must target exactly the new cohort")
        if table in ("kpi_definitions","kpi_values") and parsed.get("limit") != ["1"]:
            raise AssertionError("KPI absence checks must use limit=1")
        rows = [row for row in self.rows[table] if row["ticker"] in self.new]
        return copy.deepcopy(rows[:1] if parsed.get("limit") == ["1"] else rows)

    def put_object(self,bucket,path,body,content_type="application/json",cache=None):
        self.objects[(bucket,path)] = body.encode() if isinstance(body,str) else bytes(body)
        if bucket == "public-data":
            self.data[path] = json.loads(self.objects[(bucket,path)])
        self.writes.append((bucket,path))

    def get_object(self,bucket,path,public=False):
        body = self.objects.get((bucket,path))
        if body and ((bucket == "raw" and self.corrupt_backup) or (bucket == "public-data" and self.corrupt_public)):
            return b"corrupt"
        return body


class CoverageBackfillTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(coverage,"The bounded coverage bootstrap is not implemented")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sb = CoverageStore()
        self.report = {"cohort_id":ADDITIONAL_REGISTRY["cohort_id"],"published":300,"results":{
            ticker:{"ticker":ticker,"status":"published","documents":2 if ticker in self.sb.extra else 1,
                "coverage":{"version":1,"status":"partial","total_documents":2 if ticker in self.sb.extra else 1,
                    "limits":["Historical archive enumeration remains incomplete"]}}
            for ticker in self.sb.new}}
        self.write_report()

    def write_report(self):
        (self.root/"ir-stream-report.json").write_bytes(encoded(self.report))

    def publish(self):
        return coverage.bootstrap_coverage(self.sb,self.root)

    def test_bootstrap_preserves_old_counts_and_only_queries_new_companies(self):
        prior = copy.deepcopy(self.sb.data["meta/coverage_ir.json"])
        result = self.publish()
        current = self.sb.data["meta/coverage_ir.json"]
        self.assertEqual({ticker:current["companies"][ticker] for ticker in SP500},prior["companies"])
        for key in ("kpi_counts_as_of","segment_counts_as_of","custom_root"):
            self.assertEqual(current[key],prior[key])
        self.assertEqual(current["summary"],{"companies":800,"documents":309,"bytes":31440,
            "kpis":227953,"kpi_values":3944368,"companies_with_docs":301,"companies_with_kpis":1,
            "companies_with_segments":1,"segment_tables":1,"coverage":{"unverified":500,"partial":300}})
        for ticker in self.sb.new:
            entry = current["companies"][ticker]
            self.assertEqual(entry["coverage"],self.report["results"][ticker]["coverage"])
            self.assertEqual([entry[key] for key in ("kpis","kpi_values","segment_tables","segment_ties")],[0,0,0,0])
        self.assertEqual(result["new_documents"],307)
        self.assertEqual(result["added_companies"],300)
        self.assertEqual(json.loads(self.sb.objects[("raw",result["backup_path"])]),prior)
        self.assertEqual(json.loads((self.root/"coverage-publication-report.json").read_bytes()),result)
        self.assertEqual([table for table,_ in self.sb.queries], ["companies","ir_documents","kpi_definitions","kpi_values"])
        self.assertEqual(self.sb.db,[])

    def test_resume_800_keeps_original_500_and_does_not_double_count(self):
        prior = copy.deepcopy(self.sb.data["meta/coverage_ir.json"])
        first = self.publish()
        second = self.publish()
        current = self.sb.data["meta/coverage_ir.json"]
        self.assertEqual(second["added_companies"],0)
        self.assertEqual(current["summary"]["companies"],800)
        self.assertEqual(current["summary"]["documents"],309)
        self.assertEqual(current["summary"]["kpi_values"],3944368)
        self.assertEqual({ticker:current["companies"][ticker] for ticker in SP500},prior["companies"])
        self.assertEqual(json.loads(self.sb.objects[("raw",first["backup_path"])]),prior)

    def test_resume_refuses_to_erase_recorded_new_kpi_or_segment_counts(self):
        self.publish()
        valid = copy.deepcopy(self.sb.data["meta/coverage_ir.json"])
        ticker = next(iter(self.sb.new))
        for key in ("kpis","kpi_values","segment_tables","segment_ties"):
            with self.subTest(key=key):
                self.sb = CoverageStore()
                prior = copy.deepcopy(valid)
                prior["companies"][ticker][key] = 1
                # Keep the snapshot internally consistent so the zero-overwrite
                # guard, rather than an unrelated summary mismatch, blocks it.
                if key != "segment_ties":prior["summary"][key] += 1
                if key == "kpis":prior["summary"]["companies_with_kpis"] += 1
                if key == "segment_tables":prior["summary"]["companies_with_segments"] += 1
                self.sb.data["meta/coverage_ir.json"] = prior
                with self.assertRaisesRegex(ValueError,"Resume cannot replace"):
                    self.publish()
                self.assertEqual(self.sb.queries,[])
                self.assertEqual(self.sb.writes,[])

    def test_invalid_prior_snapshot_fails_before_relational_reads(self):
        valid = copy.deepcopy(self.sb.data["meta/coverage_ir.json"])
        variants = []
        row = copy.deepcopy(valid);row["summary"]["kpi_values"] = 0;variants.append(row)
        row = copy.deepcopy(valid);del row["companies"]["AAPL"];variants.append(row)
        row = copy.deepcopy(valid);row["companies"]["AAPL"]["docs"] = True;variants.append(row)
        row = copy.deepcopy(valid);row["companies"]["AAPL"]["types"] = {};variants.append(row)
        row = copy.deepcopy(valid);del row["kpi_counts_as_of"];variants.append(row)
        row = copy.deepcopy(valid);row["summary"]["coverage"] = {"complete":500};variants.append(row)
        for prior in variants:
            with self.subTest(prior_summary=prior["summary"]):
                self.sb = CoverageStore();self.sb.data["meta/coverage_ir.json"] = prior
                with self.assertRaises(ValueError):
                    self.publish()
                self.assertEqual(self.sb.queries,[])
                self.assertEqual(self.sb.writes,[])

    def test_incomplete_or_mismatched_stream_receipts_fail_before_reads(self):
        valid = copy.deepcopy(self.report)
        ticker = next(iter(self.sb.new))
        for change in ("published","status","ticker","coverage","missing"):
            with self.subTest(change=change):
                self.report = copy.deepcopy(valid)
                if change == "published":self.report["published"] = 299
                elif change == "missing":del self.report["results"][ticker]
                elif change == "coverage":self.report["results"][ticker]["coverage"]["total_documents"] = 999
                else:self.report["results"][ticker][change] = "wrong"
                self.write_report()
                with self.assertRaises(ValueError):self.publish()
                self.assertEqual(self.sb.queries,[])
                self.assertEqual(self.sb.writes,[])

    def test_core_documents_kpi_and_segment_guards_block_before_writes(self):
        ticker = next(iter(self.sb.new))
        for change in ("core","missing_core","docs","bytes","definitions","values","segments"):
            with self.subTest(change=change):
                self.sb = CoverageStore()
                if change == "core":self.sb.rows["companies"][0]["cik"] = 320193
                elif change == "missing_core":self.sb.rows["companies"].pop()
                elif change == "docs":self.sb.rows["ir_documents"].append({"ticker":ticker,"doc_type":"release","bytes":1})
                elif change == "bytes":self.sb.rows["ir_documents"][0]["bytes"] = -1
                elif change == "definitions":self.sb.rows["kpi_definitions"].append({"ticker":ticker})
                elif change == "values":self.sb.rows["kpi_values"].append({"ticker":ticker})
                else:self.sb.data["meta/segments.json"]["companies"][ticker] = {"tables":1,"recon_ok":1}
                with self.assertRaises(ValueError):self.publish()
                self.assertEqual(self.sb.writes,[])

    def test_backup_readback_failure_preserves_public_coverage(self):
        before = copy.deepcopy(self.sb.data["meta/coverage_ir.json"])
        self.sb.corrupt_backup = True
        with self.assertRaisesRegex(RuntimeError,"backup|read.back"):
            self.publish()
        self.assertEqual(self.sb.data["meta/coverage_ir.json"],before)
        self.assertFalse((self.root/"coverage-publication-report.json").exists())

    def test_public_readback_failure_has_no_success_receipt_and_can_resume(self):
        self.sb.corrupt_public = True
        with self.assertRaisesRegex(RuntimeError,"read.back"):
            self.publish()
        self.assertFalse((self.root/"coverage-publication-report.json").exists())
        self.sb.corrupt_public = False
        result = self.publish()
        self.assertEqual(result["added_companies"],0)
        self.assertEqual(self.sb.data["meta/coverage_ir.json"]["summary"]["kpi_values"],3944368)


if __name__ == "__main__":
    unittest.main()
