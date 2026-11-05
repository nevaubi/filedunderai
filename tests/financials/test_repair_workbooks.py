"""Explicit workbook refresh plans never mutate originals or unrelated state."""
import copy
import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from etl import ir, repair_workbooks as repair


class Store:
    url = 'https://storage.example.test'
    def __init__(self):
        self.rows, self.objects, self.writes = {}, {}, []
    def _h(self, headers=None):
        return headers or {}
    def rpc(self, name, args):
        return True
    def select(self, table, query=''):
        if table != 'ir_documents':
            raise AssertionError('workbook repair must not access KPI or other tables')
        filters = parse_qs(query)
        rows = list(self.rows.values())
        if 'id' in filters:
            ids = filters['id'][0][4:-1].replace('"', '').split(',')
            rows = [r for r in rows if r['id'] in ids]
        if 'ticker' in filters:
            rows = [r for r in rows if r['ticker'] == filters['ticker'][0][3:]]
        return copy.deepcopy(sorted(rows, key=lambda r:r['id']))
    def get_object(self, bucket, path):
        return self.objects.get((bucket, path))
    def put_object(self, bucket, path, content, *args, **kwargs):
        self.objects[bucket, path] = content.encode() if isinstance(content, str) else content
        self.writes.append((bucket, path))
    def put_json(self, path, data, **kwargs):
        self.put_object('public-data', path, json.dumps(data, default=str).encode())
    def request(self, url, method='GET', data=None, **kwargs):
        if method != 'PATCH' or not urlparse(url).path.endswith('/ir_documents'):
            raise AssertionError('unexpected mutation')
        predicates = parse_qs(urlparse(url).query)
        rows = [row for row in self.rows.values() if all(
            (row.get(key) is None if conditions[0] == 'is.null' else str(row.get(key)) == conditions[0][3:])
            for key, conditions in predicates.items())]
        for row in rows:
            row.update(copy.deepcopy(data))
            self.writes.append(('row', row['id']))
        return json.dumps(rows)


class WorkbookRepairTests(unittest.TestCase):
    def setUp(self):
        import openpyxl
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.originals = self.root/'originals'
        self.sb = Store()
        self.ids = ['a'*16, 'b'*16]
        for did in self.ids:
            book = openpyxl.Workbook(); book.active.title = 'Financials'
            book.active['A1'], book.active['B1'] = 'Revenue', 42
            book.active.cell(602, 2, 99)
            raw = io.BytesIO(); book.save(raw); body = raw.getvalue()
            row = {'id': did, 'ticker': 'TEST', 'url': 'https://issuer.test/'+did+'.xlsx',
                'ext': '.xlsx', 'storage_path': 'TEST/'+did+'.xlsx', 'text_path': 'TEST/text/'+did+'.json',
                'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body), 'pages': 1,
                'extracted_at': None, 'published': None, 'title': 'Financial results'}
            self.sb.rows[did] = row
            self.sb.objects['ir', row['storage_path']] = body
            self.sb.objects['ir', row['text_path']] = json.dumps({'kind':'sheets','sheets':{'Financials':[['Revenue',42]]}}).encode()
            folder = self.originals/'TEST'; folder.mkdir(parents=True, exist_ok=True)
            (folder/(did+'.xlsx')).write_bytes(body)
        state = ir._read_checkpoint(self.sb, 'TEST')
        state['scope'] = {'since_year':1900,'sec_since':'1900-01-01','sec':True,'web':True,'history':'available_archive'}
        state['custom_field'] = {'keep': 'unrelated'}
        state['queues']['pages']['https://issuer.test/history'] = {'url':'https://issuer.test/history','status':'blocked','attempts':3}
        for did in self.ids:
            state['outcomes'][did] = {'status':'unverified','limits':[],'errors':[]}
            state['queues']['extractions'][did] = {'id':did,'status':'pending','attempts':0}
        ir._save_checkpoint(self.sb, state)
        self.sb.put_json('ir/TEST.json', {'ticker':'TEST','documents':copy.deepcopy(list(self.sb.rows.values()))})
        self.sb.writes.clear()

    def stage(self, ids=None):
        ids = ids or self.ids
        return repair.stage_plan(self.sb, [copy.deepcopy(self.sb.rows[i]) for i in ids], self.root/'plan', originals=self.originals)

    def apply(self, plan, ids):
        with patch.object(repair, 'request', side_effect=self.sb.request):
            return repair.apply_plan(self.sb, plan, ids)

    def test_stage_is_read_only_and_reports_new_cells_and_exact_snapshots(self):
        path = self.stage()
        plan = json.loads(Path(path).read_text())
        self.assertEqual(self.sb.writes, [])
        self.assertEqual([d['status'] for d in plan['documents']], ['ready','ready'])
        self.assertEqual(plan['documents'][0]['containment']['old_populated_cells'], 2)
        self.assertEqual(plan['documents'][0]['containment']['new_populated_cells'], 3)
        self.assertTrue(plan['companies']['TEST']['checkpoint_sha256'])
        self.assertTrue(plan['companies']['TEST']['manifest_sha256'])

    def test_date_cells_keep_the_existing_json_representation(self):
        import openpyxl
        did = self.ids[0]; row = self.sb.rows[did]
        source = io.BytesIO(self.sb.objects['ir',row['storage_path']])
        book = openpyxl.load_workbook(source); book.active['C1'] = dt.datetime(2026, 6, 27)
        raw = io.BytesIO(); book.save(raw); body = raw.getvalue()
        row.update(bytes=len(body), sha256=repair.digest(body))
        self.sb.objects['ir',row['storage_path']] = body
        (self.originals/'TEST'/(did+'.xlsx')).write_bytes(body)
        self.sb.objects['ir',row['text_path']] = json.dumps({'kind':'sheets','sheets':{'Financials':[['Revenue',42,'2026-06-27 00:00:00']]}}).encode()
        plan = self.stage([did]); entry = json.loads(plan.read_text())['documents'][0]
        self.assertEqual(entry['status'], 'ready')
        self.assertEqual(entry['containment']['changed_cells'], [])

    def test_source_error_cells_are_complete_but_remain_limited_after_apply_and_retry(self):
        import openpyxl
        did = self.ids[0]; row = self.sb.rows[did]
        book = openpyxl.load_workbook(io.BytesIO(self.sb.objects['ir',row['storage_path']]))
        book.active['C1'] = '#DIV/0!'; book.active['D1'] = '=SUM(B1,B602)'
        raw = io.BytesIO(); book.save(raw); body = raw.getvalue()
        row.update(bytes=len(body), sha256=repair.digest(body))
        self.sb.objects['ir',row['storage_path']] = body
        (self.originals/'TEST'/(did+'.xlsx')).write_bytes(body)
        plan = self.stage([did]); entry = json.loads(plan.read_text())['documents'][0]
        self.assertEqual(entry['status'], 'ready')
        self.assertTrue(entry['complete_coordinate_capture'])
        self.assertEqual(entry['quality']['status'], 'limited')
        self.apply(plan, [did]); self.apply(plan, [did])
        state = json.loads(self.sb.objects['raw','ir/checkpoints/TEST.json'])
        self.assertEqual(state['outcomes'][did]['missing_formula_caches'], 1)
        self.assertEqual(state['outcomes'][did]['source_cell_errors'], 1)
        self.assertEqual(state['queues']['extractions'][did]['status'], 'retry')
        self.assertEqual(state['queues']['extractions'][did]['attempts'], 1)

    def test_apply_changes_only_requested_pointer_outcome_and_manifest(self):
        plan = self.stage()
        original_objects = copy.deepcopy(self.sb.objects)
        second = copy.deepcopy(self.sb.rows[self.ids[1]])
        old_state = json.loads(self.sb.objects['raw','ir/checkpoints/TEST.json'])
        self.apply(plan, [self.ids[0]])
        row = self.sb.rows[self.ids[0]]
        self.assertNotEqual(row['text_path'], 'TEST/text/'+self.ids[0]+'.json')
        self.assertEqual(self.sb.rows[self.ids[1]], second)
        for key, body in original_objects.items():
            if key[0] == 'ir': self.assertEqual(self.sb.objects[key], body)
        state = json.loads(self.sb.objects['raw','ir/checkpoints/TEST.json'])
        self.assertEqual(state['outcomes'][self.ids[0]]['status'], 'verified')
        self.assertEqual(state['outcomes'][self.ids[1]], old_state['outcomes'][self.ids[1]])
        self.assertEqual(state['queues']['extractions'][self.ids[1]], old_state['queues']['extractions'][self.ids[1]])
        self.assertEqual(state['queues']['pages'], old_state['queues']['pages'])
        self.assertEqual(state['custom_field'], old_state['custom_field'])
        manifest = json.loads(self.sb.objects['public-data','ir/TEST.json'])
        self.assertEqual(len(manifest['documents']), 2)
        self.assertEqual(next(r for r in manifest['documents'] if r['id']==self.ids[0])['text_path'], row['text_path'])
        self.assertEqual(next(r for r in manifest['documents'] if r['id']==self.ids[1]), second)

    def test_immutable_plan_and_before_snapshots_are_verified_before_pointer_write(self):
        plan = self.stage([self.ids[0]])
        before = copy.deepcopy(self.sb.objects)
        def guarded(*args, **kwargs):
            prefix = 'repairs/workbooks/' + repair.digest(Path(plan).read_bytes()) + '/'
            self.assertEqual(self.sb.objects['raw', prefix+'plan.json'], Path(plan).read_bytes())
            self.assertEqual(self.sb.objects['raw', prefix+'before/TEST/checkpoint.json'], before['raw','ir/checkpoints/TEST.json'])
            self.assertEqual(self.sb.objects['raw', prefix+'before/TEST/manifest.json'], before['public-data','ir/TEST.json'])
            self.assertTrue(any(bucket == 'raw' and name.startswith(prefix+'intents/TEST/') for bucket,name in self.sb.objects))
            return self.sb.request(*args, **kwargs)
        with patch.object(repair, 'request', side_effect=guarded):
            repair.apply_plan(self.sb, plan, [self.ids[0]])

    def test_resume_after_first_pointer_keeps_original_before_snapshot(self):
        plan = self.stage()
        baseline = self.sb.objects['raw','ir/checkpoints/TEST.json']
        original_request = self.sb.request
        calls = []
        def interrupted(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                raise RuntimeError('simulated interruption')
            return original_request(*args, **kwargs)
        with patch.object(repair, 'request', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                repair.apply_plan(self.sb, plan, self.ids)
        self.assertEqual(self.sb.objects['raw','ir/checkpoints/TEST.json'], baseline)
        self.apply(plan, self.ids)
        prefix = 'repairs/workbooks/' + repair.digest(Path(plan).read_bytes()) + '/'
        self.assertEqual(self.sb.objects['raw',prefix+'before/TEST/checkpoint.json'], baseline)
        self.assertEqual(self.sb.writes.count(('row', self.ids[0])), 1)
        self.assertEqual(self.sb.writes.count(('row', self.ids[1])), 1)

    def test_resume_after_checkpoint_before_manifest_and_completed_retry_have_no_extra_changes(self):
        plan = self.stage([self.ids[0]])
        before_manifest = self.sb.objects['public-data','ir/TEST.json']
        put = self.sb.put_object
        def interrupted(bucket, path, *args, **kwargs):
            if bucket == 'public-data' and path == 'ir/TEST.json':
                raise RuntimeError('simulated manifest interruption')
            return put(bucket, path, *args, **kwargs)
        with patch.object(self.sb, 'put_object', side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, 'interruption'):
                self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.objects['public-data','ir/TEST.json'], before_manifest)
        checkpoint = self.sb.objects['raw','ir/checkpoints/TEST.json']
        self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.objects['raw','ir/checkpoints/TEST.json'], checkpoint)
        writes = copy.deepcopy(self.sb.writes)
        self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.writes, writes)

    def test_resume_rejects_unrelated_checkpoint_drift(self):
        plan = self.stage([self.ids[0]])
        self.apply(plan, [self.ids[0]])
        state = json.loads(self.sb.objects['raw','ir/checkpoints/TEST.json'])
        state['custom_field']['keep'] = 'new unrelated work'
        self.sb.objects['raw','ir/checkpoints/TEST.json'] = json.dumps(state).encode()
        self.sb.writes.clear()
        with self.assertRaisesRegex(ValueError, 'precondition'):
            self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.writes, [])

    def test_staging_does_not_overwrite_an_existing_reviewed_plan(self):
        plan = self.stage()
        old = Path(plan).read_bytes()
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.stage()
        self.assertEqual(Path(plan).read_bytes(), old)

    def test_stale_document_or_checkpoint_preconditions_have_no_writes(self):
        plan = self.stage()
        self.sb.rows[self.ids[0]]['text_path'] = 'TEST/text/newer.json'
        with self.assertRaisesRegex(ValueError, 'precondition'):
            self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.writes, [])

    def test_changed_checkpoint_is_not_replaced_by_staged_state(self):
        plan = self.stage()
        state = json.loads(self.sb.objects['raw','ir/checkpoints/TEST.json']); state['custom_field']['keep'] = 'new work'
        self.sb.objects['raw','ir/checkpoints/TEST.json'] = json.dumps(state).encode()
        with self.assertRaisesRegex(ValueError, 'precondition'):
            self.apply(plan, [self.ids[0]])
        self.assertEqual(self.sb.writes, [])

    def test_tampered_payload_and_unlisted_ids_are_rejected_before_writes(self):
        path = self.stage()
        plan = json.loads(Path(path).read_text())
        (Path(path).parent/plan['documents'][0]['payload']).write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash'):
            self.apply(path, [self.ids[0]])
        with self.assertRaisesRegex(ValueError, 'listed'):
            self.apply(path, ['c'*16])
        self.assertEqual(self.sb.writes, [])

    def test_previous_populated_cell_loss_is_held_not_published(self):
        row = self.sb.rows[self.ids[0]]
        self.sb.objects['ir', row['text_path']] = json.dumps({'kind':'sheets','sheets':{'Financials':[['Revenue',777]]}}).encode()
        path = self.stage([self.ids[0]])
        entry = json.loads(Path(path).read_text())['documents'][0]
        self.assertEqual(entry['status'], 'held')
        self.assertTrue(entry['containment']['changed_cells'])
        with self.assertRaisesRegex(ValueError, 'ready'):
            self.apply(path, [self.ids[0]])
        self.assertEqual(self.sb.writes, [])

    def precision_fixture(self, old=12.345679, new=12.3456789):
        import openpyxl
        did = self.ids[0]; row = self.sb.rows[did]
        book = openpyxl.load_workbook(io.BytesIO(self.sb.objects['ir',row['storage_path']]))
        book.active['B1'] = new
        raw = io.BytesIO(); book.save(raw); body = raw.getvalue()
        row.update(bytes=len(body), sha256=repair.digest(body))
        self.sb.objects['ir',row['storage_path']] = body
        (self.originals/'TEST'/(did+'.xlsx')).write_bytes(body)
        self.sb.objects['ir',row['text_path']] = json.dumps({'kind':'sheets','sheets':{'Financials':[['Revenue',old]]}}).encode()
        source = {'document':copy.deepcopy(row), 'sheets':[{'name':'Financials', 'cells':[
            {'cell':'A1','type':'s','value':'Revenue'}, {'cell':'B1','type':'n','value':new},
            {'cell':'B602','type':'n','value':99}]}]}
        cells = self.root/'source-cells'/'TEST'; cells.mkdir(parents=True)
        (cells/(did+'.json')).write_text(json.dumps(source))
        return self.stage([did]), cells.parent

    def test_precision_restoration_requires_explicit_sealed_source_evidence(self):
        plan, cells = self.precision_fixture(); did = self.ids[0]
        with self.assertRaisesRegex(ValueError, 'ready'):
            self.apply(plan, [did])
        approval = repair.prepare_precision_evidence(plan, cells, self.root/'precision')
        proof = json.loads(approval.read_text())['documents'][did]
        self.assertEqual(proof['changes'], [{'cell':['Financials',1,2], 'old':12.345679,
            'new':12.3456789, 'source':12.3456789, 'reason':'exact_six_decimal_rounding'}])
        with patch.object(repair, 'request', side_effect=self.sb.request):
            repair.apply_plan(self.sb, plan, [did], precision_evidence=approval)
        original = self.sb.objects['ir','TEST/text/'+did+'.json']
        self.assertEqual(json.loads(original)['sheets']['Financials'][0][1], 12.345679)
        refreshed = json.loads(self.sb.objects['ir',self.sb.rows[did]['text_path']])
        self.assertEqual(refreshed['sheets']['Financials'][0][1], 12.3456789)

    def test_precision_evidence_rejects_bigger_changes_wrong_source_and_wrong_hash(self):
        for mode in ('larger_change','source_value','source_hash','zero_sign'):
            with self.subTest(mode=mode):
                # Separate fixtures retain each original reviewed plan.
                self.setUp()
                plan, cells = self.precision_fixture(old=0.0, new=-1e-8) if mode=='zero_sign' else self.precision_fixture(
                    old=12.0 if mode=='larger_change' else 12.345679)
                path = cells/'TEST'/(self.ids[0]+'.json')
                source = json.loads(path.read_text())
                if mode=='source_value': source['sheets'][0]['cells'][1]['value'] = 12.3456788
                if mode=='source_hash': source['document']['sha256'] = '0'*64
                path.write_text(json.dumps(source))
                approval = repair.prepare_precision_evidence(plan, cells, self.root/'precision')
                self.assertEqual(json.loads(approval.read_text())['documents'], {})
                with patch.object(repair, 'request', side_effect=self.sb.request):
                    with self.assertRaisesRegex(ValueError, 'ready'):
                        repair.apply_plan(self.sb, plan, [self.ids[0]], precision_evidence=approval)
                self.assertEqual(self.sb.writes, [])

    def test_changed_precision_source_snapshot_cannot_be_applied(self):
        plan, cells = self.precision_fixture()
        approval = repair.prepare_precision_evidence(plan, cells, self.root/'precision')
        proof = json.loads(approval.read_text())['documents'][self.ids[0]]
        (approval.parent/proof['source_cells_snapshot']).write_text('{}')
        with patch.object(repair, 'request', side_effect=self.sb.request):
            with self.assertRaisesRegex(ValueError, 'source evidence'):
                repair.apply_plan(self.sb, plan, [self.ids[0]], precision_evidence=approval)
        self.assertEqual(self.sb.writes, [])

    def test_compare_and_swap_rejection_never_overwrites_a_newer_pointer(self):
        plan = self.stage([self.ids[0]])
        def raced(*args, **kwargs):
            self.sb.rows[self.ids[0]]['text_path'] = 'TEST/text/concurrent.json'
            return '[]'
        with patch.object(repair, 'request', side_effect=raced):
            with self.assertRaisesRegex(RuntimeError, 'conditional'):
                repair.apply_plan(self.sb, plan, [self.ids[0]])
        self.assertEqual(self.sb.rows[self.ids[0]]['text_path'], 'TEST/text/concurrent.json')
        self.assertNotIn(('raw','ir/checkpoints/TEST.json'), self.sb.writes)


if __name__ == '__main__':
    unittest.main()
