import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from etl import repair_ir_coverage as repair
from etl.repair_ir_coverage import build_candidate, validate_counts


STAMP = '2026-11-05T18:00:00Z'


def fixture():
    companies = {
        'AAA': {'docs': 1, 'bytes': 7, 'types': {'release': 1}, 'kpis': 1,
                'kpi_values': 2, 'segment_tables': 3, 'segment_ties': 2,
                'custom': {'retain': True}, 'coverage': {'status': 'unverified', 'total_documents': 1}},
        'BBB': {'docs': 1, 'bytes': 9, 'types': {'other': 1}, 'kpis': 0,
                'kpi_values': 0, 'segment_tables': 0,
                'coverage': {'status': 'partial', 'total_documents': 1}},
    }
    before = {'generated': '2026-11-05T09:00:00Z', 'kpi_counts_as_of': '2026-11-05T08:00:00Z',
              'segment_counts_as_of': '2026-11-04T00:00:00Z', 'custom_top': 'retain',
              'companies': companies, 'summary': {'companies': 2, 'companies_with_docs': 2,
              'documents': 2, 'bytes': 16, 'companies_with_kpis': 1, 'kpis': 1,
              'kpi_values': 2, 'companies_with_segments': 1, 'segment_tables': 3,
              'coverage': {'unverified': 1, 'partial': 1}, 'custom_summary': 42}}
    counts = {'snapshot_started_at': '2026-11-05T17:59:00Z', 'company_count': 2,
              'documents': 3, 'bytes': 25, 'definitions': 2, 'ir_definitions': 2,
              'other_definitions': 0, 'ir_values': 5, 'ir_ytd_values': 1,
              'companies': [
                  {'ticker': 'AAA', 'docs': 2, 'bytes': 16, 'types': {'release': 2},
                   'kpis': 2, 'ir_definitions': 2, 'other_definitions': 0,
                   'kpi_values': 5, 'ir_ytd_values': 1},
                  {'ticker': 'BBB', 'docs': 1, 'bytes': 9, 'types': {'other': 1},
                   'kpis': 0, 'ir_definitions': 0, 'other_definitions': 0,
                   'kpi_values': 0, 'ir_ytd_values': 0}]}
    manifests = {
        'AAA': {'ticker': 'AAA', 'generated': '2026-11-05T17:58:00Z',
                'documents': [{'id': 'a', 'bytes': 7, 'doc_type': 'release'},
                              {'id': 'b', 'bytes': 9, 'doc_type': 'release'}],
                'coverage': {'status': 'partial', 'total_documents': 2,
                             'extraction': {'verified': 1, 'limited': 1}}},
        'BBB': {'ticker': 'BBB', 'generated': '2026-11-05T17:57:00Z',
                'documents': [{'id': 'c', 'bytes': 9, 'doc_type': 'other'}],
                'coverage': {'status': 'partial', 'total_documents': 1,
                             'extraction': {'unverified': 1}}},
    }
    return before, counts, manifests


class CoverageReconciliationTests(unittest.TestCase):
    def test_current_counts_and_manifest_quality_preserve_unrelated_fields(self):
        before, counts, manifests = fixture()
        old = copy.deepcopy((before, counts, manifests))
        result = build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)
        self.assertEqual((before, counts, manifests), old)
        self.assertEqual(result['summary']['kpi_values'], 5)
        self.assertEqual(result['summary']['documents'], 3)
        self.assertEqual(result['companies']['AAA']['coverage'], manifests['AAA']['coverage'])
        self.assertEqual(result['companies']['AAA']['custom'], {'retain': True})
        self.assertEqual(result['companies']['AAA']['segment_tables'], 3)
        self.assertEqual(result['companies']['AAA']['segment_ties'], 2)
        self.assertEqual(result['custom_top'], 'retain')
        self.assertEqual(result['summary']['custom_summary'], 42)
        self.assertEqual(result['segment_counts_as_of'], before['segment_counts_as_of'])
        self.assertEqual(result['kpi_counts_as_of'], counts['snapshot_started_at'])
        self.assertEqual(result['documents_counts_as_of'], counts['snapshot_started_at'])

    def test_summary_disagreement_is_rejected(self):
        before, counts, manifests = fixture()
        counts['ir_values'] += 1
        with self.assertRaisesRegex(ValueError, 'count summary'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)

    def test_missing_or_extra_company_is_rejected(self):
        before, counts, manifests = fixture()
        for expected in ({'AAA'}, {'AAA', 'BBB', 'CCC'}):
            with self.assertRaisesRegex(ValueError, 'company scope'):
                build_candidate(before, counts, manifests, expected, STAMP)

    def test_non_ir_definitions_require_separate_review(self):
        before, counts, manifests = fixture()
        counts['companies'][0]['ir_definitions'] = 1
        counts['companies'][0]['other_definitions'] = 1
        counts['ir_definitions'] = 1
        counts['other_definitions'] = 1
        with self.assertRaisesRegex(ValueError, 'non-IR'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)

    def test_stale_manifest_cannot_claim_complete_coverage(self):
        before, counts, manifests = fixture()
        manifests['AAA']['documents'].pop()
        manifests['AAA']['coverage'].update(status='complete', total_documents=1)
        manifests['AAA']['coverage']['extraction'] = {'verified': 1}
        result = build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)
        value = result['companies']['AAA']
        self.assertEqual(value['docs'], 2)
        self.assertEqual(value['coverage']['status'], 'unverified')
        self.assertIn('manifest counts differ from the database count snapshot', value['coverage']['limits'])
        self.assertEqual(value['coverage']['total_documents'], 1)

    def test_duplicate_manifest_id_and_wrong_issuer_are_rejected(self):
        before, counts, manifests = fixture()
        manifests['AAA']['documents'][1]['id'] = 'a'
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)
        _, _, manifests = fixture()
        manifests['AAA']['ticker'] = 'BBB'
        with self.assertRaisesRegex(ValueError, 'issuer'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)

    def test_future_snapshot_and_noninteger_counts_are_rejected(self):
        before, counts, manifests = fixture()
        counts['snapshot_started_at'] = '2026-11-06T00:00:00Z'
        with self.assertRaisesRegex(ValueError, 'after publication'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)
        _, counts, _ = fixture()
        counts['companies'][0]['docs'] = True
        with self.assertRaisesRegex(ValueError, 'integer'):
            validate_counts(counts, {'AAA', 'BBB'})

    def test_inconsistent_prior_summary_is_rejected(self):
        before, counts, manifests = fixture()
        before['summary']['documents'] = 999
        with self.assertRaisesRegex(ValueError, 'prior summary'):
            build_candidate(before, counts, manifests, {'AAA', 'BBB'}, STAMP)


class FakeLease:
    def __init__(self, sb): self.sb = sb
    def check(self): pass
    def __enter__(self): return self
    def __exit__(self, *args): return False


class CoveragePublicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        before, counts, manifests = fixture()
        self.sb = SimpleNamespace(url=repair.PROJECT_URL)
        self.objects = {('public-data', repair.PUBLIC_PATH): repair.encoded(before)}
        self.objects.update({('public-data', f'ir/{ticker}.json'): repair.encoded(value)
                             for ticker, value in manifests.items()})
        self.writes = []
        self.stale_next_after_write = False
        self.before = self.objects[('public-data', repair.PUBLIC_PATH)]
        counts_path = self.root / 'counts-source.json'
        counts_path.write_bytes(repair.encoded({'status': 'read_only', 'data': counts}))
        self.patches = [patch.object(repair, 'UNIVERSE', [('AAA',), ('BBB',)]),
                        patch.object(repair, 'now_iso', return_value=STAMP),
                        patch.object(repair, 'AggregateLease', FakeLease),
                        patch.object(repair, '_stored_object', side_effect=self.read),
                        patch.object(repair, '_write_once', side_effect=self.write)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.plan = repair.prepare(self.sb, counts_path, self.root / 'plan', workers=2)
        self.plan_root = self.root / 'plan'

    def read(self, sb, bucket, path):
        if (bucket, path) == ('public-data', repair.PUBLIC_PATH) and self.stale_next_after_write:
            if self.objects[(bucket, path)] != self.before:
                self.stale_next_after_write = False
                return self.before
        return self.objects.get((bucket, path))

    def write(self, sb, bucket, path, body, lease, immutable):
        lease.check()
        if immutable and (bucket, path) in self.objects:
            raise ValueError('Immutable collision')
        self.writes.append((bucket, path, body))
        self.objects[(bucket, path)] = body

    def test_exact_publication_and_backups_without_other_writes(self):
        manifests = {key: body for key, body in self.objects.items() if key[1].startswith('ir/')}
        result = repair.apply(self.sb, self.plan_root, workers=2)
        self.assertEqual(result['status'], 'verified')
        self.assertEqual(result['summary']['kpi_values'], 5)
        public_writes = [item for item in self.writes if item[0] == 'public-data']
        self.assertEqual(len(public_writes), 1)
        self.assertEqual(public_writes[0][1], repair.PUBLIC_PATH)
        self.assertEqual(len([item for item in self.writes if item[0] == 'raw']), 5)
        self.assertEqual(manifests, {key: self.objects[key] for key in manifests})

    def test_drifted_global_before_state_refuses_all_writes(self):
        self.objects[('public-data', repair.PUBLIC_PATH)] += b' '
        with self.assertRaisesRegex(ValueError, 'both sealed states'):
            repair.apply(self.sb, self.plan_root)
        self.assertEqual(self.writes, [])

    def test_drifted_company_manifest_refuses_publication(self):
        self.objects[('public-data', 'ir/AAA.json')] += b' '
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            repair.apply(self.sb, self.plan_root)
        self.assertFalse(any(item[0] == 'public-data' for item in self.writes))
        self.assertEqual(self.objects[('public-data', repair.PUBLIC_PATH)], self.before)

    def test_same_plan_recovers_after_stale_readback_without_rebuilding(self):
        self.stale_next_after_write = True
        with self.assertRaisesRegex(RuntimeError, 'exact readback failed'):
            repair.apply(self.sb, self.plan_root)
        exact_after = (self.plan_root / 'after.json').read_bytes()
        self.assertEqual(self.objects[('public-data', repair.PUBLIC_PATH)], exact_after)
        result = repair.apply(self.sb, self.plan_root)
        self.assertTrue(result['resumed_after_state'])
        self.assertEqual(len([item for item in self.writes if item[0] == 'public-data']), 1)
        self.assertEqual(self.objects[('public-data', repair.PUBLIC_PATH)], exact_after)

    def test_advanced_state_requires_existing_complete_backup(self):
        repair.apply(self.sb, self.plan_root)
        raw_path = next(key for key in self.objects if key[0] == 'raw' and key[1].endswith('before.json'))
        del self.objects[raw_path]
        prior_writes = len(self.writes)
        with self.assertRaisesRegex(ValueError, 'Missing or conflicting immutable evidence'):
            repair.apply(self.sb, self.plan_root)
        self.assertEqual(len(self.writes), prior_writes)

    def test_sealed_candidate_tampering_is_rejected(self):
        after = self.plan_root / 'after.json'
        value = json.loads(after.read_bytes()); value['summary']['kpi_values'] = 100
        after.write_bytes(repair.encoded(value))
        with self.assertRaisesRegex(ValueError, 'Sealed payload changed'):
            repair.apply(self.sb, self.plan_root)
        self.assertEqual(self.writes, [])


if __name__ == '__main__':
    unittest.main()
