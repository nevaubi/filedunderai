import copy
import gzip
import json
import unittest
from unittest.mock import patch

from etl import common, screener
from test_pipeline import MemoryStore


def shares(value, end, filed, accession):
    return {'val': value, 'end': end, 'filed': filed, 'available_at': filed,
            'accn': accession, 'form': '10-Q', 'basis': 'reported_outstanding'}


class OutputStore(MemoryStore):
    def put_json(self, path, obj, **kw):
        super().put_json(path, obj, **kw)
        return len(json.dumps(obj))


class CurrentScreenerMetadataTests(unittest.TestCase):
    def extract(self, profile_shares, raw_shares):
        class Source:
            def get_json(self, path):
                return {'pipeline_version': 2, 'ticker': 'AAPL', 'statements': {},
                        'profile': {'sharesOutstanding': copy.deepcopy(profile_shares)}}

            def get_object(self, bucket, path):
                return gzip.compress(json.dumps({'facts': {'dei': {}, 'us-gaap': {}}}).encode())

        with patch.object(screener.sec, 'shares_outstanding', return_value=raw_shares), \
                patch.object(common, 'request', side_effect=AssertionError('network forbidden')):
            return screener.extract_company(Source(), 'AAPL', '2020-01-01')

    def render(self, current, cached=None, price=20, normalized=True):
        sb = OutputStore()
        sb.data['meta/fundamentals.json'] = {'AAPL': {'name': 'Apple', 'sector': 'Information Technology',
            'pipeline_version': 2 if normalized else None, 'shares_metadata': current, 'fund': {}}}
        cache = {'AAPL': {'pipeline_version': 2, 'q': {}, 'shares': dict(cached, v=cached['val'])}} if cached else {}
        with patch.object(screener, 'UNIVERSE', [('AAPL', 320193, 'Apple', 'Information Technology')]), \
                patch.object(screener, 'TICKERS', ['AAPL']), \
                patch.object(screener, 'update_closes', return_value={'dates': ['2026-11-04'], 's': {'AAPL': [price]}}), \
                patch.object(screener, 'update_fund', return_value=cache), \
                patch.object(screener, 'update_kpis', return_value={}), \
                patch.object(screener, 'activity', return_value=({}, {})), \
                patch.object(screener, 'macro_monthly', return_value={fid: {'chg': {}} for fid, *_ in screener.CORR_M}), \
                patch.object(common, 'request', side_effect=AssertionError('network forbidden')):
            screener.screener_job.__wrapped__(sb, log=lambda *a: None)
        doc = sb.data['screener.json']
        return dict(zip(doc['cols'], doc['rows'][0])), sb.data['screener/src/AAPL.json']

    def test_newer_filing_profile_count_is_not_overwritten_by_older_raw_dei(self):
        newer = shares(1677436783, '2026-06-30', '2026-08-06', 'current-filing')
        older = shares(1749319009, '2026-01-31', '2026-02-20', 'older-filing')
        result = self.extract(newer, older)
        self.assertEqual(result['shares'], dict(newer, v=1677436783))

    def test_newer_reported_raw_count_can_supersede_an_older_profile_count(self):
        older = shares(50, '2026-03-31', '2026-04-23', 'older-filing')
        newer = shares(60, '2026-09-30', '2026-10-21', 'current-filing')
        result = self.extract(older, newer)
        self.assertEqual(result['shares'], dict(newer, v=60))

    def test_current_cap_and_its_reference_use_canonical_reported_metadata(self):
        current = shares(100, '2026-09-30', '2026-10-21', 'current-filing')
        older = shares(90, '2026-03-31', '2026-04-23', 'older-filing')
        row, source = self.render(current, older)
        self.assertEqual(row['mcap'], 2000)
        ref = source['refs'][source['f']['mcap']['i'][0]]
        self.assertEqual((ref[2], ref[4], ref[5], ref[7]), ('2026-09-30', 100, 'current-filing', '2026-10-21'))

    def test_verified_current_count_can_value_a_company_with_legacy_financials(self):
        current = shares(100, '2026-10-26', '2026-10-28', 'current-filing')
        row, source = self.render(current, normalized=False)
        self.assertEqual(row['mcap'], 2000)
        self.assertIsNone(row['pe'])
        self.assertEqual(source['refs'][0][5], 'current-filing')

    def test_invalid_canonical_count_does_not_fall_back_to_an_old_cached_count(self):
        invalid = shares(100, '2027-07-17', '2026-07-23', 'invalid-observation')
        older = shares(90, '2026-03-31', '2026-04-23', 'older-filing')
        row, source = self.render(invalid, older)
        self.assertIsNone(row['mcap'])
        self.assertNotIn('mcap', source['f'])

    def test_serialized_close_keeps_stored_precision_and_matches_its_source(self):
        for price in (3325.5601, 1091.3101, 1069.4399, 6376.8198):
            with self.subTest(price=price):
                row, source = self.render(None, price=price)
                self.assertEqual(row['price'], price)
                self.assertEqual(source['f']['price']['p'], [['2026-11-04', price]])


if __name__ == '__main__':
    unittest.main()
