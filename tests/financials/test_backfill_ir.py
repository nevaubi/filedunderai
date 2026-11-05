import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from etl import backfill_ir
from etl.backfill import digest, encoded
from etl.universe import ADDITIONAL_REGISTRY, BY_TICKER, registry_for


class BackfillIRTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cik = BY_TICKER['OKTA']['cik']
        self.url = f'https://www.sec.gov/Archives/edgar/data/{self.cik}/000166013426000068/earnings.htm'
        self.body = b'<DOCUMENT>\n<TYPE>EX-99.1\n<TEXT>\n<html><body>Okta quarterly revenue $100 million; net income $20 million.</body></html>\n</TEXT></DOCUMENT>'
        original = self.root/'ir-discovery/originals/OKTA/release.html'
        original.parent.mkdir(parents=True)
        original.write_bytes(self.body)
        self.manifest = {'ticker':'OKTA', 'cik':self.cik, 'cohort_id':ADDITIONAL_REGISTRY['cohort_id'], 'name':'Okta', 'documents':[
            {'publication_ready':True, 'document_type':'financial_release', 'official_identity_status':'sec_cik_verified',
             'source_url':self.url, 'original':{'ticker':'OKTA', 'url':self.url, 'original_path':str(original),
                'sha256':digest(self.body), 'bytes':len(self.body), 'retrieved_at':'2026-11-05T00:00:00Z'},
             'original_validation':{'target_cik':self.cik, 'target_ticker':'OKTA', 'source_url':self.url, 'status':'verified_financial_content',
                'original_sha256':digest(self.body)}}]}
        self.path = self.root/'ir-discovery/companies/OKTA.json'
        self.path.parent.mkdir(parents=True)
        sub = encoded({'cik':str(self.cik), 'filings':{'recent':{'form':['8-K'], 'filingDate':['2026-08-26'],
            'reportDate':['2026-07-31'], 'accessionNumber':['0001660134-26-000068'], 'primaryDocument':['form8k.htm'],
            'primaryDocDescription':['Results']}}})
        raw_path = self.root/f'raw/sec/submissions/CIK{self.cik:010d}.json.gz'
        raw_path.parent.mkdir(parents=True)
        raw_path.write_bytes(gzip.compress(sub))
        source = self.root/'sec-manifests/OKTA.json'
        source.parent.mkdir()
        source.write_bytes(encoded({'cik':self.cik, 'sources':{'submissions':{'sha256':digest(sub)}}}))

    def prepare(self):
        self.path.write_bytes(encoded(self.manifest))
        return backfill_ir.prepare_documents(self.root, 'OKTA')

    def test_sec_sgml_wrapped_html_keeps_original_bytes_and_filing_date(self):
        documents = self.prepare()
        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0]['body'], self.body)
        self.assertEqual(documents[0]['record']['ext'], '.html')
        self.assertEqual(documents[0]['record']['published'], '2026-08-26')
        self.assertIsNone(documents[0]['record']['period_end'])

    def test_verified_original_from_second_approved_cohort_is_accepted(self):
        ticker = 'GKOS'
        cik = BY_TICKER[ticker]['cik']
        manifest = json.loads(json.dumps(self.manifest))
        manifest.update(ticker=ticker, cik=cik, name=BY_TICKER[ticker]['name'],
                        cohort_id=registry_for(ticker)['cohort_id'])
        doc = manifest['documents'][0]
        url = self.url.replace(f'/data/{self.cik}/', f'/data/{cik}/')
        body = self.body.replace(b'Okta', b'Glaukos')
        path = self.root/f'ir-discovery/originals/{ticker}/release.html'
        path.parent.mkdir(parents=True)
        path.write_bytes(body)
        doc['source_url'] = url
        doc['original'].update(ticker=ticker, url=url, original_path=str(path),
                               sha256=digest(body), bytes=len(body))
        doc['original_validation'].update(target_cik=cik, target_ticker=ticker,
                                          source_url=url, original_sha256=digest(body))
        sub = json.loads(gzip.decompress((self.root/f'raw/sec/submissions/CIK{self.cik:010d}.json.gz').read_bytes()))
        sub['cik'] = str(cik)
        raw = encoded(sub)
        (self.root/f'raw/sec/submissions/CIK{cik:010d}.json.gz').write_bytes(gzip.compress(raw))
        (self.root/f'sec-manifests/{ticker}.json').write_bytes(encoded(
            {'cik':cik, 'sources':{'submissions':{'sha256':digest(raw)}}}))
        manifest_path = self.root/f'ir-discovery/companies/{ticker}.json'
        manifest_path.write_bytes(encoded(manifest))
        try:
            prepared = backfill_ir.prepare_documents(self.root, ticker)
        except ValueError as error:
            self.fail(f'Approved second-cohort original was rejected: {error}')
        self.assertEqual(prepared[0]['record']['ticker'], ticker)
        self.assertEqual(prepared[0]['body'], body)
        manifest['cohort_id'] = ADDITIONAL_REGISTRY['cohort_id']
        manifest_path.write_bytes(encoded(manifest))
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            backfill_ir.prepare_documents(self.root, ticker)

    def test_publication_report_identifies_the_selected_cohort(self):
        with patch.object(backfill_ir, 'publish_company', return_value={'ticker':'GKOS','status':'published'}):
            backfill_ir.main(['--root',str(self.root),'--tickers','GKOS'])
        report = json.loads((self.root/'ir-publication-report.json').read_bytes())
        self.assertEqual(report['cohort_id'], registry_for('GKOS')['cohort_id'])

    def test_mixed_cohort_batch_is_rejected_before_any_publication(self):
        with patch.object(backfill_ir, 'publish_company',
                          side_effect=lambda root,ticker:{'ticker':ticker,'status':'published'}) as publish:
            with self.assertRaisesRegex(ValueError, 'exactly one cohort'):
                backfill_ir.main(['--root',str(self.root),'--tickers','OKTA','GKOS'])
        publish.assert_not_called()

    def test_validation_cannot_be_reused_for_another_issuer(self):
        self.manifest['documents'][0]['original_validation']['target_cik'] = 320193
        with self.assertRaisesRegex(ValueError, 'issuer'):
            self.prepare()

    def test_url_cannot_be_reassigned_after_retrieval(self):
        self.manifest['documents'][0]['original']['url'] = 'https://other.example/actual.html'
        with self.assertRaisesRegex(ValueError, 'URL'):
            self.prepare()

    def test_filing_dates_require_the_archived_submissions_hash(self):
        source = self.root/f'raw/sec/submissions/CIK{self.cik:010d}.json.gz'
        body = json.loads(gzip.decompress(source.read_bytes()))
        body['filings']['recent']['filingDate'][0] = '2026-11-05'
        source.write_bytes(gzip.compress(encoded(body)))
        with self.assertRaisesRegex(ValueError, 'submissions'):
            self.prepare()

    def issuer_http_source(self, final_url):
        doc = self.manifest['documents'][0]
        url = 'http://issuer.example/results/2026-quarter-three'
        body = b'<html><body>Okta quarterly revenue $100 million; net income $20 million.</body></html>'
        Path(doc['original']['original_path']).write_bytes(body)
        doc['source_url'] = url
        doc['official_identity_status'] = 'issuer_identity_supported'
        doc['original'].update(url=url,final_url=final_url,status='archived',http_status=200,
                               sha256=digest(body),bytes=len(body),source_page='https://issuer.example/investors')
        doc['original_validation'].update(source_url=url,original_sha256=digest(body))
        return body

    def test_observed_same_issuer_https_upgrade_keeps_the_requested_url_and_exact_bytes(self):
        final = 'https://issuer.example/results/2026-quarter-three/'
        body = self.issuer_http_source(final)
        prepared = self.prepare()[0]
        self.assertEqual(prepared['record']['url'],final)
        self.assertEqual(prepared['body'],body)
        self.assertEqual(prepared['record']['source_page'],'https://issuer.example/investors')
        self.assertEqual(prepared['redirect']['requested_url'],self.manifest['documents'][0]['source_url'])
        self.assertEqual(prepared['redirect']['final_url'],final)
        self.assertEqual(prepared['redirect']['original_sha256'],digest(body))

    def test_http_source_requires_a_recorded_successful_same_host_and_path_https_upgrade(self):
        for final in (None,'http://issuer.example/results/2026-quarter-three',
                      'https://other.example/results/2026-quarter-three/',
                      'https://issuer.example/unrelated/',
                      'https://user:password@issuer.example/results/2026-quarter-three/',
                      'https://issuer.example/results/2026-quarter-three/?other=1'):
            with self.subTest(final=final):
                self.issuer_http_source(final)
                with self.assertRaisesRegex(ValueError,'URL|redirect'):
                    self.prepare()
        for changes in ({'http_status':404},{'status':'failed'}):
            self.issuer_http_source('https://issuer.example/results/2026-quarter-three/')
            self.manifest['documents'][0]['original'].update(changes)
            with self.subTest(changes=changes),self.assertRaisesRegex(ValueError,'URL|redirect'):
                self.prepare()

    def test_empty_userinfo_is_rejected_in_sources_and_observed_redirects(self):
        final = 'https://issuer.example/results/2026-quarter-three/'
        for scheme in ('http','https'):
            for userinfo in ('@',':@'):
                self.issuer_http_source(final)
                document = self.manifest['documents'][0]
                source = f'{scheme}://{userinfo}issuer.example/results/2026-quarter-three'
                document['source_url'] = source
                document['original']['url'] = source
                document['original_validation']['source_url'] = source
                with self.subTest(source=source),self.assertRaisesRegex(ValueError,'URL|redirect'):
                    self.prepare()
        for userinfo in ('@',':@'):
            self.issuer_http_source(f'https://{userinfo}issuer.example/results/2026-quarter-three/')
            with self.subTest(redirect_userinfo=userinfo),self.assertRaisesRegex(ValueError,'URL|redirect'):
                self.prepare()

    def test_resume_after_document_commit_recovers_missing_extraction_outcome_from_readbacks(self):
        item = self.prepare()[0]
        row = item['record']
        objects = {row['storage_path']:item['body'],row['text_path']:item['text']}
        sb = SimpleNamespace(get_object=lambda bucket,path:objects.get(path))
        state = {'outcomes':{}}
        backfill_ir.restore_missing_extraction(sb,item,row,state)
        self.assertEqual(state['outcomes'][row['id']],item['quality'])

    def test_resume_does_not_claim_extraction_if_either_stored_object_differs(self):
        item = self.prepare()[0]
        row = item['record']
        for bad in ('storage_path','text_path'):
            with self.subTest(bad=bad):
                objects = {row['storage_path']:item['body'],row['text_path']:item['text']}
                objects[row[bad]] = b'changed after the incomplete run'
                sb = SimpleNamespace(get_object=lambda bucket,path:objects.get(path))
                state = {'outcomes':{}}
                with self.assertRaisesRegex(RuntimeError,'read-back'):
                    backfill_ir.restore_missing_extraction(sb,item,row,state)
                self.assertEqual(state['outcomes'],{})

    def test_resume_requires_exact_identity_url_and_canonical_paths_before_readbacks(self):
        item = self.prepare()[0]
        for field,value in (
                ('id','different-id'),('url','https://issuer.example/different-release'),
                ('storage_path','OKTA/different-id-alias.html'),('text_path','OKTA/text/different-id.json'),
                ('ticker','AAPL'),('sha256','b'*64)):
            with self.subTest(field=field):
                existing = {**item['record'],field:value}
                # Even identical bytes at a same-issuer alias must not authorize
                # restoring an outcome for a different reviewed document identity.
                objects = {existing['storage_path']:item['body'],existing['text_path']:item['text']}
                reads = []
                sb = SimpleNamespace(get_object=lambda bucket,path:(reads.append((bucket,path)),objects.get(path))[1])
                state = {'outcomes':{}}
                with self.assertRaisesRegex(ValueError,'identity'):
                    backfill_ir.restore_missing_extraction(sb,item,existing,state)
                self.assertEqual(state['outcomes'],{})
                self.assertEqual(reads,[])


if __name__ == '__main__':
    unittest.main()
