import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs

from etl import backfill, jobs
import test_backfill_mode
from test_pipeline import MemoryStore


class PublishStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.objects = {}
        self.rows = {"companies":[{"ticker":"AAPL","cik":320193}]}
        self.data["meta/fundamentals.json"] = {"AAPL":{"name":"preserved","fund":{"revenue_ttm":123}}}
        self.corrupt_sources = False
    def put_object(self,bucket,path,body,content_type="application/json",cache=None):
        self.objects[(bucket,path)] = body.encode() if isinstance(body,str) else body
        self.writes.append((bucket,path))
    def get_object(self,bucket,path,public=False):
        body = self.objects.get((bucket,path))
        return b"corrupt" if body and self.corrupt_sources and bucket=="raw" and path.startswith("sec/") else body
    def upsert(self,table,rows,on_conflict,**kwargs):
        rows = list(rows)
        keys = on_conflict.split(",")
        key = lambda row:tuple(row[k] for k in keys)
        merged = {key(row):row for row in self.rows.get(table,[])}
        merged.update({key(row):copy.deepcopy(row) for row in rows})
        self.rows[table] = list(merged.values())
        self.db.append((table,rows))
    def select(self,table,query):
        rows = self.rows.get(table,[])
        for name,values in parse_qs(query).items():
            if values[0].startswith("in.("):
                accepted = set(values[0][4:-1].split(","))
                rows = [row for row in rows if str(row.get(name)) in accepted]
        return copy.deepcopy(rows)


class BackfillPublishTests(unittest.TestCase):
    def stage(self,root,ticker="OKTA",filing=None,financial_accession=None):
        inputs = test_backfill_mode.FactsOnlyModeTests().sources(backfill.BY_TICKER[ticker]["cik"])
        if filing:
            submissions = json.loads(inputs["submissions"])
            submissions["filings"]["recent"] = {
                "form":[filing["form"]], "filingDate":[filing["filed"]],
                "reportDate":[filing["report_date"] or ""], "accessionNumber":[filing["accession"]],
                "primaryDocument":[filing["primary_doc"]], "items":[filing["items"]],
                "primaryDocDescription":["Beneficial ownership report"]}
            inputs["submissions"] = backfill.encoded(submissions)
        if financial_accession:
            facts = json.loads(inputs["companyfacts"])
            facts["facts"]["us-gaap"]["Revenues"]["units"]["USD"][0]["accn"] = financial_accession
            inputs["companyfacts"] = backfill.encoded(facts)
        stage = backfill.StageStore(Path(root)/"staged"/ticker,ticker)
        fundamental = jobs.process_company(stage,ticker,facts_only=True,fetch_docs=False,source_inputs=inputs)
        stage.seal(fundamental,{k:{"sha256":hashlib.sha256(body).hexdigest()} for k,body in inputs.items()})
        return stage.root

    def ownership_filing(self,form="SCHEDULE 13G/A"):
        return {"accession":"0002012383-26-003750", "form":form, "filed":"2026-10-27",
                "report_date":None, "primary_doc":"xslSCHEDULE_13G_X02/primary_doc.xml", "items":""}

    def existing_filing(self,filing,ticker="BLK"):
        return {**filing, "ticker":ticker, "description":"Existing ownership report",
                "url":"https://www.sec.gov/Archives/edgar/data/1364742/000201238326003750/xslSCHEDULE_13G_X02/primary_doc.xml",
                "has_source_tables":False, "updated_at":"2026-10-28T00:00:00Z"}

    def test_shared_ownership_filings_preserve_existing_owner_and_report_actual_writes(self):
        for form in ("SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A",
                     "SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A",
                     "3", "3/A", "4", "4/A", "5", "5/A"):
            with self.subTest(form=form),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing(form)
                self.stage(root,filing=filing)
                old = self.existing_filing({**filing,"form":form.replace("SC ","SCHEDULE ")})
                sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                self.assertEqual(result["published"],1)
                self.assertEqual(sb.rows["filings"],[old])
                company = result["results"]["OKTA"]
                self.assertEqual((company["filings_staged"],company["filings_upserted"]),(1,0))
                self.assertEqual(company["filings"],0)
                skipped = company["skipped_shared_ownership_filings"]
                self.assertEqual(len(skipped),1)
                self.assertEqual((skipped[0]["accession"],skipped[0]["owner_ticker"]),("0002012383-26-003750","BLK"))
                self.assertEqual(skipped[0]["ownership_basis"],"existing_row")
                state = json.loads((Path(root)/"publish-state.json").read_bytes())
                self.assertEqual(state["companies"]["OKTA"]["skipped_shared_ownership_filings"],skipped)

    def test_shared_ownership_requires_matching_source_metadata(self):
        for field,value in (("form","SCHEDULE 13D/A"),("filed","2026-10-28"),("report_date","2026-09-30"),
                            ("primary_doc","different.xml"),("items","2.02")):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing();self.stage(root,filing=filing)
                old = self.existing_filing({**filing,field:value})
                sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                self.assertEqual(result["published"],0)
                self.assertEqual(sb.db,[])
                self.assertEqual(sb.rows["filings"],[old])

    def test_matching_unused_144_and_425_references_preserve_the_existing_owner(self):
        for form in ("144", "425"):
            with self.subTest(form=form),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing(form)
                self.stage(root,filing=filing)
                old = self.existing_filing(filing,ticker="DASH" if form == "425" else "AIG")
                sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                self.assertEqual(result["published"],1)
                self.assertEqual(sb.rows["filings"],[old])
                skipped = result["results"]["OKTA"]["skipped_shared_ownership_filings"][0]
                self.assertEqual(skipped["reason"],"shared_issuer_reference" if form == "425" else "shared_ownership_filing")
                self.assertEqual(skipped["owner_ticker"],old["ticker"])
                self.assertEqual(result["results"]["OKTA"]["filings_upserted"],0)
                # The secondary issuer retains its original SEC reference in its
                # source-backed bundle without taking ownership of the DB row.
                bundle = json.loads(sb.objects[("public-data","companies/OKTA.json")])
                self.assertEqual(bundle["filings"][0]["a"],filing["accession"])

    def test_144_and_425_reference_metadata_must_match_exactly(self):
        for form in ("144", "425"):
            for field,value in (("form","8-K"),("filed","2026-10-28"),
                                ("report_date","2026-09-30"),("primary_doc","other.html"),("items","2.02")):
                with self.subTest(form=form,field=field),tempfile.TemporaryDirectory() as root:
                    filing = self.ownership_filing(form);self.stage(root,filing=filing)
                    old = self.existing_filing({**filing,field:value})
                    sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                    result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                    self.assertEqual(result["published"],0)
                    self.assertEqual(sb.db,[])
                    self.assertEqual(sb.rows["filings"],[old])

    def test_reporting_and_other_filing_ownership_conflicts_still_block(self):
        for form in ("10-K","10-Q","8-K","13F-HR","SCHEDULE 13G EXTRA","3 EXTRA","4/A/A","5A",
                     "144 EXTRA","144/A","425/A","425 EXTRA"):
            with self.subTest(form=form),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing(form);self.stage(root,filing=filing)
                old = self.existing_filing(filing)
                sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                self.assertEqual(result["published"],0)
                self.assertEqual(sb.db,[])
                self.assertEqual(sb.rows["filings"],[old])

    def test_shared_ownership_accession_used_by_financial_facts_is_not_skipped(self):
        for form in ("SCHEDULE 13G/A","3","3/A","4","4/A","5","5/A","144","425"):
            with self.subTest(form=form),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing(form)
                self.stage(root,filing=filing,financial_accession=filing["accession"])
                old = self.existing_filing(filing)
                sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
                result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
                self.assertEqual(result["published"],0)
                self.assertEqual(sb.db,[])
                self.assertEqual(sb.rows["filings"],[old])

    def test_new_batch_insider_ownership_filings_use_one_owner(self):
        for form in ("3","3/A","4","4/A","5","5/A"):
            with self.subTest(form=form),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing(form)
                self.stage(root,"CG",filing=filing);self.stage(root,"SARO",filing=filing)
                sb = PublishStore()
                result = backfill.publish_staged(sb,root,["SARO","CG"],workers=2)
                self.assertEqual(result["published"],2)
                self.assertEqual([row["ticker"] for row in sb.rows["filings"]],["CG"])
                self.assertEqual(result["results"]["CG"]["filings_upserted"],1)
                self.assertEqual(result["results"]["SARO"]["filings_upserted"],0)
                skipped = result["results"]["SARO"]["skipped_shared_ownership_filings"][0]
                self.assertEqual((skipped["owner_ticker"],skipped["reason"]),("CG","shared_ownership_filing"))

    def test_document_ownership_queries_fit_gateway_request_budget(self):
        class BoundedQueryStore(PublishStore):
            def select(self,table,query):
                if len(query) > 4096:
                    raise ValueError("Ownership query exceeds gateway request budget")
                return super().select(table,query)
        with tempfile.TemporaryDirectory() as root:
            receipts = {}
            for company in backfill.ADDITIONAL_REGISTRY["companies"][:20]:
                ticker = company["ticker"]
                stage = self.stage(root,ticker)
                receipts[ticker] = json.loads((stage/"receipt.json").read_bytes())
            sb = BoundedQueryStore()
            plans = backfill._publication_ownership(sb,Path(root),receipts,workers=2)
            self.assertEqual(set(plans),set(receipts))
            self.assertTrue(all(plan["error"] is None for plan in plans.values()))
            self.assertEqual(sb.writes,[])

    def test_financial_accession_guard_covers_other_companies_in_the_batch(self):
        with tempfile.TemporaryDirectory() as root:
            filing = self.ownership_filing()
            self.stage(root,"FTI",filing=filing)
            self.stage(root,"OKTA",financial_accession=filing["accession"])
            old = self.existing_filing(filing)
            sb = PublishStore();sb.rows["filings"] = [copy.deepcopy(old)]
            result = backfill.publish_staged(sb,root,["FTI","OKTA"],workers=2)
            self.assertEqual(result["results"]["FTI"]["status"],"partial")
            self.assertEqual(sb.rows["filings"],[old])

    def test_new_batch_shared_filing_uses_one_deterministic_owner(self):
        for requested in (["OKTA","FTI"],["FTI","OKTA"]):
            with self.subTest(requested=requested),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing()
                self.stage(root,"OKTA",filing=filing);self.stage(root,"FTI",filing=filing)
                sb = PublishStore()
                result = backfill.publish_staged(sb,root,requested,workers=2)
                self.assertEqual(result["published"],2)
                self.assertEqual([(row["accession"],row["ticker"]) for row in sb.rows["filings"]],[(filing["accession"],"FTI")])
                self.assertEqual(result["results"]["FTI"]["filings_upserted"],1)
                self.assertEqual(result["results"]["OKTA"]["filings_upserted"],0)
                skipped = result["results"]["OKTA"]["skipped_shared_ownership_filings"][0]
                self.assertEqual(skipped["owner_ticker"],"FTI")
                self.assertEqual(skipped["ownership_basis"],"new_batch_owner")
                self.assertEqual(skipped["owner_publication_status"],"published")

    def test_existing_owner_wins_across_batches_and_resumes(self):
        with tempfile.TemporaryDirectory() as root:
            filing = self.ownership_filing()
            self.stage(root,"OKTA",filing=filing);self.stage(root,"FTI",filing=filing)
            sb = PublishStore()
            self.assertEqual(backfill.publish_staged(sb,root,["OKTA"],workers=1)["published"],1)
            old = copy.deepcopy(sb.rows["filings"])
            for requested in (["FTI","OKTA"],["FTI"]):
                result = backfill.publish_staged(sb,root,requested,workers=2)
                self.assertEqual(result["published"],len(requested))
                self.assertEqual(sb.rows["filings"],old)
                skipped = result["results"]["FTI"]["skipped_shared_ownership_filings"][0]
                self.assertEqual((skipped["owner_ticker"],skipped["ownership_basis"]),("OKTA","existing_row"))

    def test_failed_new_owner_is_reported_honestly_and_can_resume(self):
        class FailedOwnerStore(PublishStore):
            fail_owner = True
            def get_object(self,bucket,path,public=False):
                body = super().get_object(bucket,path,public)
                if body and self.fail_owner and bucket == "raw" and f"CIK{backfill.BY_TICKER['FTI']['cik']:010d}" in path:
                    return b"corrupt"
                return body
        with tempfile.TemporaryDirectory() as root:
            filing = self.ownership_filing()
            self.stage(root,"OKTA",filing=filing);self.stage(root,"FTI",filing=filing)
            sb = FailedOwnerStore()
            result = backfill.publish_staged(sb,root,["OKTA","FTI"],workers=2)
            self.assertEqual(result["results"]["FTI"]["status"],"partial")
            self.assertEqual(result["results"]["OKTA"]["status"],"published")
            self.assertEqual(sb.rows.get("filings",[]),[])
            skipped = result["results"]["OKTA"]["skipped_shared_ownership_filings"][0]
            self.assertEqual((skipped["ownership_basis"],skipped["owner_publication_status"]),("new_batch_owner","partial"))
            sb.fail_owner = False
            self.assertEqual(backfill.publish_staged(sb,root,["OKTA","FTI"],workers=2)["published"],2)
            self.assertEqual([r["ticker"] for r in sb.rows["filings"]],["FTI"])

    def test_new_batch_conflicting_or_reporting_accessions_still_block(self):
        for second in ({"filed":"2026-10-28"},{"form":"10-Q"}):
            with self.subTest(second=second),tempfile.TemporaryDirectory() as root:
                filing = self.ownership_filing()
                self.stage(root,"OKTA",filing=filing);self.stage(root,"FTI",filing={**filing,**second})
                sb = PublishStore()
                with self.assertRaisesRegex(ValueError,"identity|filing|shared"):
                    backfill.publish_staged(sb,root,["OKTA","FTI"],workers=2)
                self.assertEqual(sb.db,[])

    def test_existing_document_identity_is_never_skipped(self):
        with tempfile.TemporaryDirectory() as root:
            stage = self.stage(root,filing=self.ownership_filing())
            receipt = json.loads((stage/"receipt.json").read_bytes())
            table = next(item for item in receipt["tables"] if item["table"] == "documents")
            identity = json.loads((stage/table["path"]).read_bytes())[0]["id"]
            old = {"id":identity,"ticker":"AAPL"}
            sb = PublishStore();sb.rows["documents"] = [copy.deepcopy(old)]
            result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
            self.assertEqual(result["published"],0)
            self.assertEqual(sb.db,[])
            self.assertEqual(sb.rows["documents"],[old])

    def test_source_readback_failure_publishes_no_financial_values_and_resume_is_safe(self):
        with tempfile.TemporaryDirectory() as root:
            self.stage(root)
            sb = PublishStore(); sb.corrupt_sources=True
            result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
            self.assertEqual(result["published"],0)
            self.assertNotIn("facts",sb.rows)
            self.assertNotIn(("public-data","companies/OKTA.json"),sb.objects)
            self.assertEqual(sb.data["meta/fundamentals.json"],{"AAPL":{"name":"preserved","fund":{"revenue_ttm":123}}})
            sb.corrupt_sources=False
            result = backfill.publish_staged(sb,root,["OKTA"],workers=1)
            self.assertEqual(result["published"],1)
            state = json.loads((Path(root)/"publish-state.json").read_bytes())
            self.assertNotIn("error",state["companies"]["OKTA"],"A successful resume clears the prior failure")
            self.assertEqual(len(sb.rows["facts"]),1)
            self.assertEqual(next(r for r in sb.rows["companies"] if r["ticker"]=="AAPL"),{"ticker":"AAPL","cik":320193})
            self.assertEqual(sb.data["meta/fundamentals.json"]["AAPL"]["fund"]["revenue_ttm"],123)

    def test_existing_company_without_exact_checkpoint_blocks_all_writes(self):
        with tempfile.TemporaryDirectory() as root:
            self.stage(root)
            sb=PublishStore();sb.rows["companies"].append({"ticker":"OKTA","cik":1660134})
            with self.assertRaisesRegex(ValueError,"Existing company"):
                backfill.publish_staged(sb,root,["OKTA"])
            self.assertEqual(sb.writes,[])
            self.assertEqual(sb.db,[])

    def test_rehashed_relational_tampering_still_fails_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            stage=self.stage(root)
            receipt=json.loads((stage/"receipt.json").read_bytes())
            item=next(m for m in receipt["tables"] if m["table"]=="facts")
            rows=json.loads((stage/item["path"]).read_bytes());rows[0]["value"]=999
            body=backfill.encoded(rows);(stage/item["path"]).write_bytes(body);item["sha256"]=backfill.digest(body)
            (stage/"receipt.json").write_bytes(backfill.encoded(receipt))
            with self.assertRaisesRegex(ValueError,"Relational facts differ"):
                backfill.validate_stage(stage)

    def test_stage_directory_cannot_impersonate_the_requested_ticker(self):
        with tempfile.TemporaryDirectory() as root:
            stage=self.stage(root);stage.rename(stage.with_name('FTI'))
            sb=PublishStore()
            with self.assertRaisesRegex(ValueError,'requested|ticker|scope'):
                backfill.publish_staged(sb,root,['FTI'],workers=1)
            self.assertEqual(sb.writes,[])
            self.assertEqual(sb.db,[])

    def test_global_document_id_cannot_overwrite_another_company(self):
        with tempfile.TemporaryDirectory() as root:
            stage=self.stage(root);receipt=json.loads((stage/'receipt.json').read_bytes())
            item=next(m for m in receipt['tables'] if m['table']=='documents')
            rows=json.loads((stage/item['path']).read_bytes());rows[0]['id']='old-document'
            body=backfill.encoded(rows);(stage/item['path']).write_bytes(body);item['sha256']=backfill.digest(body)
            (stage/'receipt.json').write_bytes(backfill.encoded(receipt))
            sb=PublishStore();sb.rows['documents']=[{'id':'old-document','ticker':'AAPL'}]
            with self.assertRaisesRegex(ValueError,'document|Document|source'):
                backfill.publish_staged(sb,root,['OKTA'],workers=1)
            self.assertEqual(sb.rows['documents'],[{'id':'old-document','ticker':'AAPL'}])

    def test_fabricated_aggregate_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            stage=self.stage(root);receipt=json.loads((stage/'receipt.json').read_bytes())
            receipt['fundamental']['fund']['revenue_ttm']=999999999999999
            receipt['fundamental']['shares']=999999999
            (stage/'receipt.json').write_bytes(backfill.encoded(receipt))
            with self.assertRaisesRegex(ValueError,'fundamental|aggregate'):
                backfill.validate_stage(stage)

    def test_rehashed_public_profile_and_table_tampering_are_rejected(self):
        for field in ('profile','filingTables'):
            with self.subTest(field=field),tempfile.TemporaryDirectory() as root:
                stage=self.stage(root);receipt=json.loads((stage/'receipt.json').read_bytes())
                item=next(m for m in receipt['objects'] if m['path']=='companies/OKTA.json')
                bundle=json.loads((stage/item['local_path']).read_bytes())
                if field=='profile':
                    bundle['profile']['sharesOutstanding']={'val':999999999,'basis':'reported_outstanding'}
                else:
                    row=next(iter(bundle['filingTables'].values()))['statements']['income']['rows'][0]
                    row['vals'][next(iter(row['vals']))]=999999999
                body=backfill.encoded(bundle);(stage/item['local_path']).write_bytes(body)
                item.update(sha256=backfill.digest(body),bytes=len(body))
                (stage/'receipt.json').write_bytes(backfill.encoded(receipt))
                with self.assertRaisesRegex(ValueError,'profile|table|source'):
                    backfill.validate_stage(stage)


if __name__ == "__main__":
    unittest.main()
