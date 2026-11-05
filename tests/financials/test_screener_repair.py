import base64
import gzip
import hashlib
import importlib
import json
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import patch

from etl import fundamentals, screener


def body(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value).hexdigest()


class Store:
    url = 'https://apxfeteebnspwyajlxfh.supabase.co'

    def __init__(self):
        self.objects, self.metrics, self.writes = {}, [], []
        self.lose_after_backup = False
        self.change_price_after_backup = False

    def rpc(self, fn, args):
        return True

    def get_object(self, bucket, path, **kw):
        return self.objects.get((bucket, path))

    def get_json(self, path):
        raw = self.get_object('public-data', path)
        return json.loads(raw) if raw else None

    def select(self, table, query='select=*'):
        if table != 'company_metrics':
            raise AssertionError('Unexpected table read')
        return self.metrics.copy()

    def put_object(self, bucket, path, raw, *args, **kw):
        self.writes.append((bucket, path))
        self.objects[bucket, path] = raw.encode() if isinstance(raw, str) else raw
        if self.lose_after_backup and path.endswith('backup.json.gz'):
            self._aggregate_lease.lost = 'test: ownership revoked after backup'
        if self.change_price_after_backup and path.endswith('backup.json.gz'):
            self.objects['raw', 'screener/closes.json.gz'] = gzip.compress(body({'dates': ['2026-11-05'], 's': {}}))

    def upsert(self, table, rows, keys, **kw):
        self.writes.append(('relations', table))
        self.metrics = list(rows)

    def delete_objects(self, bucket, paths):
        for path in paths:
            self.writes.append(('delete', bucket + '/' + path))
            self.objects.pop((bucket, path), None)

    def delete_metrics(self, tickers):
        self.writes.append(('delete', 'company_metrics'))
        self.metrics = [r for r in self.metrics if r['ticker'] not in tickers]


class StoredScreenerRepairTests(unittest.TestCase):
    def setUp(self):
        try:
            self.repair = importlib.import_module('etl.repair_screener')
        except ModuleNotFoundError:
            self.fail('Stored-input screener publication safeguards are not implemented')
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sb = Store()
        self.tickers = ['AAPL', 'MSFT']
        self.scope = patch.object(screener, 'TICKERS', self.tickers)
        self.scope.start()
        self.addCleanup(self.scope.stop)
        self.plan = self.fixture()

    def fixture(self):
        fund = {t: {'pipeline_version': 2, 'name': t, 'sector': 'Technology', 'fund': {}}
                for t in self.tickers}
        index = {'generated': '2026-11-05T14:40:00Z', 'aggregation_id': 'a' * 24, 'companies': [
            {'ticker': 'AAPL', 'price': 20, 'as_of': '2026-11-04', 'market_cap': 100},
            {'ticker': 'MSFT', 'price': 30, 'as_of': '2026-11-04', 'market_cap': 200}]}
        cols = screener.ID_COLS + ['lq', 'lq_end'] + screener.FIELD_IDS
        rows = []
        for t, price, cap in [('AAPL', 20, 100), ('MSFT', 30, 200)]:
            values = {'ticker': t, 'price': price, 'mcap': cap}
            row = [values.get(c) for c in cols]
            row[0] = t
            rows.append(row)
        screen = {'generated': index['generated'], 'aggregation_id': 'a' * 24, 'cols': cols, 'rows': rows,
                  'fields': screener.F, 'asof': {'prices': '2026-11-04'}}
        hist = {'dates': ['2026-11-04'], 's': {'AAPL': [20], 'MSFT': [30]}}
        pstats = {r['ticker']: {'price': r['price'], 'as_of': r['as_of']} for r in index['companies']}
        plan = {'schema_version': 1, 'mode': 'read_only_candidate', 'ready_for_publication_review': True, 'aggregation_id': 'a' * 24,
                'fund_cache_version': 6, 'supabase_host': 'apxfeteebnspwyajlxfh.supabase.co',
                'normalized_tickers': self.tickers, 'legacy_tickers': [],
                'normalized_metadata_sha256': {t: sha(body(fund[t])) for t in self.tickers},
                'inputs': [], 'outputs': [], 'code_sha256': {p: sha((Path(__file__).resolve().parents[2] / p).read_bytes()) for p in
                    ('etl/screener.py', 'etl/jobs.py', 'etl/fundamentals.py', 'etl/sec.py', 'etl/source_provenance.py',
                     'etl/source_quality.py', 'etl/universe.py', 'etl/common.py', 'etl/kpi_taxonomy.py', 'etl/repair_screener.py',
                     'etl/additional_companies.json', 'etl/additional_companies_mdy-spsm-2026-11-04.json')},
                'validation': {'exact_price_matches': 2, 'price_mismatches': [],
                    'missing_normalized_cache': [], 'unexpected_legacy_cache': [],
                    'market_cap_mismatches_after_screener_rounding': [], 'same_columns': True, 'same_tickers': True}}
        for bucket, path, raw in [
            ('public-data', 'meta/fundamentals.json', body(fund)),
            ('public-data', 'meta/pricestats.json', body(pstats)),
            ('public-data', 'index.json', body(index)),
            ('public-data', 'screener.json', body(screen)),
            ('raw', 'screener/closes.json.gz', gzip.compress(body(hist))),
            ('public-data', 'companies/AAPL.json', body({'pipeline_version': 2, 'ticker': 'AAPL'})),
            ('public-data', 'companies/MSFT.json', body({'pipeline_version': 2, 'ticker': 'MSFT'})),
            ('raw', 'sec/companyfacts/CIK0000320193.json.gz', b'stored source AAPL'),
            ('raw', 'sec/companyfacts/CIK0000789019.json.gz', b'stored source MSFT')]:
            self.sb.objects[bucket, path] = raw
            p = self.root / 'inputs' / bucket / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            plan['inputs'].append({'bucket': bucket, 'path': path, 'exists': True, 'sha256': sha(raw)})
        corrected = {t: {**fund[t], 'fund': fundamentals.compute({}, {}, None, ticker=t), 'div_ttm': None, 'bb_ttm': None} for t in self.tickers}
        plan['fundamental_changes'] = {t: {'fund.' + key: {'before': None, 'after': value, 'before_present': False, 'after_present': True}
            for key, value in corrected[t]['fund'].items()} for t in self.tickers}
        for t in self.tickers:
            for field in ('div_ttm', 'bb_ttm'):
                plan['fundamental_changes'][t][field] = {'before': None, 'after': None, 'before_present': False, 'after_present': True}
        outputs = [('public-data', 'meta/fundamentals.json', body(corrected)),
                   ('public-data', 'index.json', body(index)), ('public-data', 'screener.json', body(screen)),
                   ('public-data', 'screener/corr.json', body({'tickers': self.tickers})),
                   ('raw', 'screener/fund.json.gz', gzip.compress(body({'_v': 6, **fund}))),
                   ('relations', 'company_metrics.json', body(index['companies']))]
        for t in self.tickers:
            price = 20 if t == 'AAPL' else 30
            outputs.append(('public-data', f'screener/src/{t}.json', body({'tk': t, 'generated': screen['generated'], 'asof': '2026-11-04',
                'f': {'price': {'p': [['2026-11-04', price]]}}})))
        for bucket, path, raw in outputs:
            p = self.root / 'outputs' / bucket / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            plan['outputs'].append({'bucket': bucket, 'path': path, 'sha256': sha(raw), 'bytes': len(raw),
                                    'content_type': 'application/json', 'cache_control': 'max-age=300'})
        self.sb.metrics = [{'ticker': 'AAPL', 'price': 19}, {'ticker': 'MSFT', 'price': 29}]
        return plan

    def save(self):
        p = self.root / 'manifest.json'
        p.write_bytes(body(self.plan))
        return p, sha(p.read_bytes())

    def apply(self):
        path, reviewed = self.save()
        return self.repair.apply_candidate(self.sb, path, reviewed_sha256=reviewed, workers=2)

    def test_changed_normalized_source_blocks_all_replacements_and_backups(self):
        self.sb.objects['raw', 'sec/companyfacts/CIK0000320193.json.gz'] = b'newer source'
        with self.assertRaisesRegex(ValueError, 'changed|drift'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_modified_output_is_rejected_even_when_manifest_itself_is_reviewed(self):
        (self.root / 'outputs/public-data/screener.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_unreviewed_manifest_is_rejected_without_mutation(self):
        path, _ = self.save()
        with self.assertRaisesRegex(ValueError, 'review|manifest'):
            self.repair.apply_candidate(self.sb, path, reviewed_sha256='0' * 64)
        self.assertEqual(self.sb.writes, [])

    def test_loss_of_lease_after_backup_stops_publication(self):
        self.sb.lose_after_backup = True
        with self.assertRaisesRegex(RuntimeError, 'lease lost|lease|Lease'):
            self.apply()
        self.assertTrue(self.sb.writes)
        self.assertTrue(all(b == 'raw' and p.startswith('repairs/screener/') for b, p in self.sb.writes))

    def test_price_drift_during_backup_blocks_publication_and_keeps_recovery_receipt(self):
        self.sb.change_price_after_backup = True
        with self.assertRaisesRegex(ValueError, 'close cache changed'):
            self.apply()
        self.assertTrue(all(b == 'raw' and p.startswith('repairs/screener/') for b, p in self.sb.writes))
        receipt = json.loads((self.root / 'apply-receipt.json').read_bytes())
        self.assertEqual(receipt['status'], 'interrupted_before_publication')
        self.assertTrue(receipt['backup_path'].startswith('repairs/screener/'))

    def test_success_preserves_complete_before_state_and_publishes_main_last(self):
        result = self.apply()
        self.assertEqual(result['status'], 'published')
        self.assertEqual(self.sb.writes[-1], ('public-data', 'screener.json'))
        self.assertLess(self.sb.writes.index(('public-data', 'screener/src/AAPL.json')),
                        self.sb.writes.index(('public-data', 'index.json')))
        backup = json.loads(gzip.decompress(self.sb.get_object('raw', result['backup_path'])))
        before = {(x['bucket'], x['path']): x for x in backup['objects']}
        self.assertFalse(before['public-data', 'screener/src/AAPL.json']['exists'])
        self.assertEqual(backup['company_metrics'], [{'ticker': 'AAPL', 'price': 19}, {'ticker': 'MSFT', 'price': 29}])
        old = base64.b64decode(before['public-data', 'index.json']['body_base64'])
        self.assertEqual(sha(old), before['public-data', 'index.json']['sha256'])
        self.assertEqual({r['ticker']: r['price'] for r in self.sb.metrics}, {'AAPL': 20, 'MSFT': 30})

    def test_foreign_output_path_is_rejected_before_writes(self):
        raw = b'{}'
        p = self.root / 'outputs/public-data/companies/GOOG.json'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        self.plan['outputs'].append({'bucket': 'public-data', 'path': 'companies/GOOG.json', 'sha256': sha(raw), 'bytes': 2})
        with self.assertRaisesRegex(ValueError, 'path|scope|output'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_modified_local_source_signature_is_rejected_without_remote_mutation(self):
        original = self.root / 'inputs/public-data/meta/fundamentals.json'
        changed = json.loads(original.read_bytes())
        changed['AAPL']['name'] = 'unreviewed local change'
        original.write_bytes(body(changed))
        self.sb.objects['public-data', 'meta/fundamentals.json'] = original.read_bytes()
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_read_only_transport_blocks_providers_and_mutating_supabase_calls(self):
        guard = getattr(self.repair, 'stored_input_network', None)
        self.assertTrue(callable(guard), 'Read-only transport boundary is not implemented')
        with guard(self.sb.url):
            for url, method in [
                ('https://query2.finance.yahoo.com/v8/finance/chart/MSFT', 'GET'),
                (self.sb.url + '/rest/v1/rpc/acquire_etl_lease', 'POST'),
                (self.sb.url + '/storage/v1/object/public-data/screener.json', 'POST')]:
                with self.subTest(method=method, path=url):
                    with self.assertRaisesRegex(RuntimeError, 'Blocked'):
                        urllib.request.urlopen(urllib.request.Request(url, method=method))

    def test_existing_immutable_proof_cannot_be_overwritten(self):
        name = 'sources/screener/AAPL/' + 'a' * 64 + '.json'
        raw = body({'status': 'verified', 'proof': 'new'})
        p = self.root / 'outputs/public-data' / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)
        self.plan['outputs'].append({'bucket': 'public-data', 'path': name, 'sha256': sha(raw), 'bytes': len(raw)})
        self.sb.objects['public-data', name] = body({'status': 'verified', 'proof': 'different'})
        with self.assertRaisesRegex(ValueError, 'Immutable'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_rollback_restores_absent_artifacts_and_previous_metric_rows(self):
        result = self.apply()
        rollback = getattr(self.repair, 'rollback_candidate', None)
        self.assertTrue(callable(rollback), 'Guarded rollback is not implemented')
        restored = rollback(self.sb, result['backup_path'], result['backup_sha256'], self.root / 'rollback.json', workers=2)
        self.assertEqual(restored['status'], 'rolled_back')
        self.assertNotIn(('public-data', 'screener/src/AAPL.json'), self.sb.objects)
        self.assertEqual(self.sb.metrics, [{'ticker': 'AAPL', 'price': 19}, {'ticker': 'MSFT', 'price': 29}])

    def test_rollback_refuses_unrelated_newer_artifact_changes(self):
        result = self.apply()
        rollback = getattr(self.repair, 'rollback_candidate', None)
        self.assertTrue(callable(rollback), 'Guarded rollback is not implemented')
        self.sb.objects['public-data', 'index.json'] = b'newer unrelated publication'
        self.sb.writes.clear()
        with self.assertRaisesRegex(ValueError, 'conflict|changed'):
            rollback(self.sb, result['backup_path'], result['backup_sha256'], self.root / 'rollback.json', workers=2)
        self.assertEqual(self.sb.writes, [])

    def test_candidate_provenance_cannot_reference_a_missing_proof(self):
        name = 'screener/src/AAPL.json'
        p = self.root / 'outputs/public-data' / name
        doc = json.loads(p.read_bytes())
        doc['source_provenance'] = {'status': 'verified', 'proof_path': 'sources/screener/AAPL/' + 'a' * 64 + '.json', 'snapshot_sha256': 'a' * 64}
        raw = body(doc)
        p.write_bytes(raw)
        next(e for e in self.plan['outputs'] if e['path'] == name).update(sha256=sha(raw), bytes=len(raw))
        with self.assertRaisesRegex(ValueError, 'proof'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_candidate_source_price_date_must_equal_serialized_index_date(self):
        name = 'screener/src/AAPL.json'
        p = self.root / 'outputs/public-data' / name
        doc = json.loads(p.read_bytes())
        doc['f']['price']['p'][0][0] = '2026-11-03'
        raw = body(doc)
        p.write_bytes(raw)
        next(e for e in self.plan['outputs'] if e['path'] == name).update(sha256=sha(raw), bytes=len(raw))
        with self.assertRaisesRegex(ValueError, 'price/date'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_missing_source_quality_signature_is_rejected_without_writes(self):
        self.plan['code_sha256'].pop('etl/source_quality.py', None)
        with self.assertRaisesRegex(ValueError, 'code signatures'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_corrected_metadata_cannot_change_company_identity(self):
        name = 'meta/fundamentals.json'
        p = self.root / 'outputs/public-data' / name
        doc = json.loads(p.read_bytes())
        doc['AAPL']['name'] = 'Unreviewed company rename'
        raw = body(doc)
        p.write_bytes(raw)
        next(e for e in self.plan['outputs'] if e['path'] == name).update(sha256=sha(raw), bytes=len(raw))
        with self.assertRaisesRegex(ValueError, 'fundamentals|metadata'):
            self.apply()
        self.assertEqual(self.sb.writes, [])

    def test_rollback_restores_original_fundamental_metadata(self):
        before = self.sb.objects['public-data', 'meta/fundamentals.json']
        result = self.apply()
        self.assertNotEqual(self.sb.objects['public-data', 'meta/fundamentals.json'], before)
        self.repair.rollback_candidate(self.sb, result['backup_path'], result['backup_sha256'], self.root / 'rollback.json', workers=2)
        self.assertEqual(self.sb.objects['public-data', 'meta/fundamentals.json'], before)

    def test_recomputation_updates_distribution_inputs_without_changing_identity(self):
        original = {t: {'pipeline_version': 2, 'name': t, 'fund': {}, 'div_ttm': 100, 'bb_ttm': 200,
                         'shares_metadata': {'val': 300}} for t in self.tickers}
        corrected, changes = self.repair.recompute_fund_snapshot(original,
            lambda t: {'ticker': t, 'pipeline_version': 2, 'statements': {}, 'profile': {}}, workers=2)
        for t in self.tickers:
            self.assertIsNone(corrected[t]['div_ttm'])
            self.assertIsNone(corrected[t]['bb_ttm'])
            self.assertEqual(corrected[t]['name'], t)
            self.assertEqual(corrected[t]['shares_metadata'], {'val': 300})
            self.assertEqual(changes[t]['div_ttm']['before'], 100)
            self.assertEqual(changes[t]['bb_ttm']['before'], 200)
            self.assertEqual(original[t]['div_ttm'], 100)

    def test_shared_current_financial_ratio_mismatch_blocks_publication(self):
        name = 'index.json'
        p = self.root / 'outputs/public-data' / name
        doc = json.loads(p.read_bytes())
        doc['companies'][0]['ps_ttm'] = 1.5
        raw = body(doc)
        p.write_bytes(raw)
        next(e for e in self.plan['outputs'] if e['path'] == name).update(sha256=sha(raw), bytes=len(raw))
        with self.assertRaisesRegex(ValueError, 'shared financial'):
            self.apply()
        self.assertEqual(self.sb.writes, [])


if __name__ == '__main__':
    unittest.main()
