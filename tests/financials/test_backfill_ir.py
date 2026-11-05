import gzip
import json
import tempfile
import unittest
from pathlib import Path

from etl import backfill_ir
from etl.backfill import digest, encoded
from etl.universe import ADDITIONAL_REGISTRY, BY_TICKER


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


if __name__ == '__main__':
    unittest.main()
