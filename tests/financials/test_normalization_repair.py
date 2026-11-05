import copy
import gzip
import importlib
import json
import tempfile
import threading
import time
import unittest
from types import MethodType, SimpleNamespace
from pathlib import Path
from unittest.mock import patch

from etl import common, jobs, sec
import test_backfill_mode
from test_pipeline import MemoryStore


def expression_parts(value):
    parts, start, depth, quoted, escaped = [], 0, 0, False, False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif quoted and char == '\\':
            escaped = True
        elif char == '"':
            quoted = not quoted
        elif not quoted:
            if char == '(':
                depth += 1
            elif char == ')':
                depth -= 1
            elif char == ',' and depth == 0:
                parts.append(value[start:index]); start = index + 1
    return parts + [value[start:]]


def expression_matches(row, expression):
    if expression.startswith('and('):
        return all(expression_matches(row, part) for part in expression_parts(expression[4:-1]))
    key, op, literal = expression.split('.', 2)
    value = json.loads(literal) if literal.startswith('"') or op == 'is' else literal
    return row.get(key) is value if op == 'is' else str(row.get(key)) == str(value)


class RepairStore(MemoryStore):
    def __init__(self):
        super().__init__()
        self.objects, self.tables, self.events = {}, {}, []
        self.corrupt_backup = False

    def get_object(self, bucket, path):
        body = self.objects.get((bucket, path))
        if self.corrupt_backup and path.startswith('repairs/financial-normalization/'):
            return b'corrupted'
        return body

    def put_object(self, bucket, path, body, *args, **kw):
        self.events.append(('object', bucket, path))
        self.objects[bucket, path] = body.encode() if isinstance(body, str) else body

    def put_json(self, path, obj, **kw):
        self.events.append(('json', path))
        super().put_json(path, obj, **kw)

    def select(self, table, query='select=*'):
        from urllib.parse import parse_qs
        rows = self.tables.get(table, [])
        for key, values in parse_qs(query).items():
            value = values[0]
            if key == 'or':
                rows = [r for r in rows if any(expression_matches(r, term) for term in expression_parts(value[1:-1]))]
            elif value.startswith('eq.'):
                # Simple PostgREST equality keeps quote characters literal;
                # nested logical tuples use their own quoted-value grammar.
                expected = value[3:]
                rows = [r for r in rows if str(r.get(key)) == str(expected)]
            elif value == 'is.true':
                rows = [r for r in rows if r.get(key) is True]
        return copy.deepcopy(rows)

    def upsert(self, table, rows, keys, **kw):
        rows = list(rows)
        if not rows:
            return 0
        self.events.append(('upsert', table))
        key = lambda r: tuple(r[k] for k in keys.split(','))
        current = {key(r): r for r in self.tables.get(table, [])}
        current.update({key(r): copy.deepcopy(r) for r in rows})
        self.tables[table] = list(current.values())

    def delete(self, table, query):
        self.events.append(('delete', table))
        doomed = self.select(table, query)
        self.tables[table] = [r for r in self.tables.get(table, []) if r not in doomed]


class NormalizationRepairTests(unittest.TestCase):
    def setUp(self):
        self.repair = importlib.import_module('etl.repair_normalization')
        self.sb = RepairStore()
        sources = test_backfill_mode.FactsOnlyModeTests().sources()
        cf, sub = (json.loads(sources[k]) for k in ('companyfacts', 'submissions'))
        profile = sec.profile(sub, cf['facts'])
        self.sb.data['companies/AAPL.json'] = {'ticker': 'AAPL', 'cik': 320193, 'profile': profile,
            'statements': {name: sec.build_series(cf['facts'], name) for name in sec.CONCEPTS},
            'filings': [], 'news': {'items': [{'title': 'keep existing context'}]}}
        self.sb.data['meta/fundamentals.json'] = {'AAPL': {'name': 'Apple', 'sector': 'Information Technology',
            'fund': {'eps_ttm': 99}, 'material_accession': None}}
        self.sb.objects['raw', 'sec/companyfacts/CIK0000320193.json.gz'] = gzip.compress(sources['companyfacts'])
        self.obsolete = {'ticker': 'AAPL', 'statement': 'income', 'label': 'EPS diluted', 'kind': 'Q',
            'period_end': '2025-12-31', 'period_start': '2025-10-01', 'concept': 'EarningsPerShareDiluted',
            'unit': 'USD/shares', 'value': -3, 'accession': '0000320193-26-000001', 'form': '10-K',
            'filed': '2026-02-01', 'derived': True}
        self.sb.tables['facts'] = [self.obsolete]

    def plan(self, path):
        return self.repair.plan_repair(self.sb, ['AAPL'], Path(path), as_of='2026-11-05')

    def test_planning_uses_archived_sources_without_any_remote_mutation(self):
        with tempfile.TemporaryDirectory() as td, patch.object(common, 'request', side_effect=AssertionError('network forbidden')):
            plan = self.plan(td)
            self.assertEqual(plan['mode'], 'dry_run')
            self.assertEqual(plan['companies'][0]['review']['unsupported_derived_rows'], 1)
            self.assertEqual(plan['companies'][0]['status'], 'ready')
            self.assertEqual(self.sb.events, [])
            self.assertTrue((Path(td) / 'plan.json').exists())

    def test_wrong_source_issuer_is_blocked_before_remote_writes(self):
        self.sb.objects['raw', 'sec/companyfacts/CIK0000320193.json.gz'] = gzip.compress(test_backfill_mode.FactsOnlyModeTests().sources(123)['companyfacts'])
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
        self.assertEqual(plan['companies'][0]['status'], 'blocked')
        self.assertIn('CIK', plan['companies'][0]['error'])
        self.assertEqual(self.sb.events, [])

    def test_corrupt_private_backup_blocks_fact_or_company_changes(self):
        with tempfile.TemporaryDirectory() as td:
            self.plan(td)
            self.sb.corrupt_backup = True
            with self.assertRaisesRegex(RuntimeError, 'backup verification'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
        self.assertFalse(any(e[0] in ('upsert', 'delete', 'json') for e in self.sb.events))
        self.assertEqual(self.sb.tables['facts'], [self.obsolete])

    def test_stale_plan_is_rejected_before_backups_or_replacements(self):
        with tempfile.TemporaryDirectory() as td:
            self.plan(td)
            self.sb.data['meta/fundamentals.json']['AAPL']['fund']['eps_ttm'] = 12
            with self.assertRaisesRegex(ValueError, 'changed since'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
        self.assertEqual(self.sb.events, [])

    def test_duplicate_plan_issuers_are_rejected_before_parallel_dispatch(self):
        with tempfile.TemporaryDirectory() as td:
            plan = self.repair.plan_cleanup(self.sb, ['AAPL'], Path(td))
            plan['companies'].append(copy.deepcopy(plan['companies'][0]))
            path = Path(td) / 'plan.json'
            path.write_bytes(self.repair.encoded(plan))
            with self.assertRaisesRegex(ValueError, 'unique active issuers'):
                self.repair.apply_plan(self.sb, path, workers=2)
        self.assertEqual(self.sb.events, [])

    def test_cleanup_snapshot_uses_full_primary_key_order_for_pagination(self):
        select = self.sb.select
        queries = []
        def record_select(table, query='select=*'):
            queries.append(query)
            return select(table, query)
        self.sb.select = record_select
        jobs.persist_normalized_facts(self.sb, 'AAPL', [])
        self.assertIn('&order=statement.asc,label.asc,kind.asc,period_end.asc', queries[0])

    def test_missing_reported_period_requires_review_instead_of_automatic_replacement(self):
        income = self.sb.data['companies/AAPL.json']['statements']['income'][0]
        older = dict(income['quarterly'][0], start='2026-04-01', end='2026-06-30')
        income['quarterly'].insert(0, older)
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
            self.assertEqual(plan['companies'][0]['status'], 'review_required')
            self.assertEqual(plan['companies'][0]['review']['lost_reported_periods'],
                             [['AAPL', 'income', 'Revenue', 'Q', '2026-06-30']])
            with self.assertRaisesRegex(ValueError, 'every candidate ready'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
        self.assertEqual(self.sb.events, [])

    def test_modified_candidate_file_is_rejected_before_remote_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            self.plan(td)
            (Path(td) / 'AAPL/candidate-fundamental.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
        self.assertEqual(self.sb.events, [])

    def test_archive_cannot_replace_a_newer_directly_reported_database_value(self):
        reported = dict(self.obsolete, label='Revenue', period_end='2026-09-30', period_start='2026-07-01',
                        concept='Revenues', unit='USD', value=123, filed='2026-11-01', derived=False)
        self.sb.tables['facts'].append(reported)
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
            self.assertEqual(plan['companies'][0]['status'], 'blocked')
            self.assertEqual(plan['companies'][0]['review']['database_replacements']['blockers'][0]['reason'], 'older_reported_filing')
            with self.assertRaisesRegex(ValueError, 'every candidate ready'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
        self.assertEqual(self.sb.events, [])

    def test_changed_reported_value_from_the_same_filing_requires_review(self):
        reported = dict(self.obsolete, label='Revenue', period_end='2026-09-30', period_start='2026-07-01',
                        concept='Revenues', unit='USD', value=123, filed='2026-10-30', derived=False)
        self.sb.tables['facts'].append(reported)
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
        self.assertEqual(plan['companies'][0]['status'], 'review_required')
        self.assertEqual(plan['companies'][0]['review']['database_replacements']['review_required'][0]['reason'], 'changed_reported_value_same_date')

    def test_changed_reported_value_with_unknown_prior_filing_date_requires_review(self):
        before = dict(self.obsolete, label='Revenue', unit='USD', derived=False, filed=None)
        after = dict(before, value=100, filed='2026-10-30')
        review = self.repair.review_database_replacements([before], [after])
        self.assertEqual(review['review_required'][0]['reason'], 'changed_reported_value_unknown_date')
        unchanged = dict(before, filed='2026-10-30')
        self.assertEqual(self.repair.review_database_replacements([before], [unchanged]), {'blockers': [], 'review_required': []})

    def test_exact_archived_reported_observation_is_preserved_over_equal_derived_value(self):
        def source(value, start, end, accession, filed):
            return {'val': value, 'start': start, 'end': end, 'accn': accession, 'filed': filed, 'form': '10-Q'}
        a1, a2, a3 = ('0000320193-26-00000' + str(n) for n in (1, 2, 3))
        observations = [source(100, '2026-01-01', '2026-03-31', a1, '2026-05-01'),
                        source(150, '2026-04-01', '2026-06-30', a2, '2026-08-01'),
                        source(250, '2026-01-01', '2026-06-30', a3, '2026-08-02'),
                        source(100, '2026-01-01', '2026-03-31', a3, '2026-08-02')]
        cf = {'cik': 320193, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': observations}}}}}
        self.sb.objects['raw', 'sec/companyfacts/CIK0000320193.json.gz'] = gzip.compress(json.dumps(cf).encode())
        self.sb.data['companies/AAPL.json']['statements'] = {s: sec.build_series(cf['facts'], s, as_of='2026-08-01') for s in sec.CONCEPTS}
        prior = dict(self.obsolete, label='Revenue', period_end='2026-06-30', period_start='2026-04-01',
                     concept='Revenues', unit='USD', value=150, filed='2026-08-01', accession=a2, derived=False, form='10-Q')
        self.sb.tables['facts'].append(prior)
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
            self.assertEqual(plan['companies'][0]['status'], 'ready')
            rows = json.loads((Path(td) / 'AAPL/candidate-facts.json').read_text())
            retained = next(r for r in rows if r['period_end'] == '2026-06-30')
            self.assertEqual(retained, prior)
            self.assertEqual(plan['companies'][0]['review']['preserved_reported_observations'], 1)

    def test_reported_database_rows_missing_from_bundle_are_not_silently_dropped(self):
        reported = dict(self.obsolete, label='Revenue', period_end='2026-06-30', period_start='2026-04-01',
                        concept='Revenues', unit='USD', value=123, filed='2026-08-01', derived=False)
        self.sb.tables['facts'].append(reported)
        with tempfile.TemporaryDirectory() as td:
            plan = self.plan(td)
        self.assertEqual(plan['companies'][0]['status'], 'review_required')
        self.assertEqual(plan['companies'][0]['review']['database_replacements']['review_required'][0]['reason'], 'reported_database_period_missing')

    def test_reported_database_value_cannot_be_replaced_by_a_derived_candidate(self):
        before = dict(self.obsolete, label='Revenue', unit='USD', derived=False)
        after = dict(before, derived=True, filed='2026-11-01')
        review = self.repair.review_database_replacements([before], [after])
        self.assertEqual(review['blockers'][0]['reason'], 'reported_to_derived')

    def test_duplicate_fact_pages_are_rejected_instead_of_silently_collapsed(self):
        self.sb.tables['facts'].append(copy.deepcopy(self.obsolete))
        with self.assertRaisesRegex(ValueError, 'Duplicate fact key'):
            self.repair.company_facts(self.sb, 'AAPL')

    def test_repair_client_uses_one_bounded_attempt_for_each_mutation(self):
        client = object.__new__(self.repair.RepairSupabase)
        client.url, client.key = 'https://example.invalid', 'test-only'
        client._aggregate_lease = SimpleNamespace(valid_until=time.monotonic() + 50, check=lambda: None)
        with patch.object(self.repair, 'request', return_value='') as request:
            client.upsert('facts', [self.obsolete], 'ticker,statement,label,kind,period_end')
            client.put_object('raw', 'backup.json', b'{}')
            client.delete('facts', 'ticker=eq.AAPL')
        self.assertEqual(request.call_count, 3)
        for call in request.call_args_list:
            self.assertEqual(call.kwargs['retries'], 1)
            self.assertGreater(call.kwargs['timeout'], 0)
            self.assertLessEqual(call.kwargs['timeout'], 30)

    def test_repair_client_refuses_mutations_near_lease_expiry(self):
        client = object.__new__(self.repair.RepairSupabase)
        client.url, client.key = 'https://example.invalid', 'test-only'
        client._aggregate_lease = SimpleNamespace(valid_until=time.monotonic() + 1, check=lambda: None)
        with patch.object(self.repair, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'lease'):
                client.put_object('raw', 'backup.json', b'{}')
        request.assert_not_called()

    def test_apply_and_guarded_rollback_preserve_context_and_restore_original_facts(self):
        original = copy.deepcopy(self.sb.data['companies/AAPL.json'])
        with tempfile.TemporaryDirectory() as td, \
                patch.object(jobs, 'UNIVERSE', [('AAPL', 320193, 'Apple', 'Information Technology')]), \
                patch.object(jobs, 'load_pricestats', return_value={'AAPL': {'price': 10, 'as_of': '2026-11-04'}}):
            self.plan(td)
            receipt = self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
            self.assertEqual(receipt['repaired'], ['AAPL'])
            self.assertEqual(self.sb.data['companies/AAPL.json']['news'], original['news'])
            self.assertEqual(self.sb.data['meta/fundamentals.json']['AAPL']['pipeline_version'], 2)
            self.assertFalse(any(r['derived'] and r['label'] == 'EPS diluted' for r in self.sb.tables['facts']))
            self.assertEqual(self.sb.events[0][0:2], ('object', 'raw'))
            self.repair.rollback(self.sb, receipt['backup'])
        self.assertEqual(self.sb.tables['facts'], [self.obsolete])
        self.assertEqual(self.sb.data['companies/AAPL.json'], original)

    def test_rollback_refuses_an_unrelated_edit_after_the_repair(self):
        with tempfile.TemporaryDirectory() as td, \
                patch.object(jobs, 'UNIVERSE', [('AAPL', 320193, 'Apple', 'Information Technology')]), \
                patch.object(jobs, 'load_pricestats', return_value={}):
            self.plan(td)
            receipt = self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
            self.sb.tables['facts'][0]['value'] = 123456
            before = copy.deepcopy(self.sb.tables['facts'])
            event_count = len(self.sb.events)
            with self.assertRaisesRegex(ValueError, 'changed after publication'):
                self.repair.rollback(self.sb, receipt['backup'])
        self.assertEqual(self.sb.tables['facts'], before)
        self.assertEqual(len(self.sb.events), event_count)

    def test_cleanup_only_preserves_additive_and_reported_values_without_upgrading_version(self):
        additive = dict(self.obsolete, label='Revenue', period_end='2026-09-30', period_start='2026-07-01',
                        concept='Revenues', unit='USD', value=123, filed='2026-11-01', derived=False)
        reported_eps = dict(self.obsolete, period_end='2026-03-31', period_start='2026-01-01', value=2, derived=False)
        self.sb.tables['facts'].extend([additive, reported_eps])
        bundle = self.sb.data['companies/AAPL.json']
        bundle['statements']['income'].append({'label': 'EPS diluted', 'concept': 'EarningsPerShareDiluted', 'unit': 'USD/shares',
            'annual': [], 'quarterly': [{'v': -3, 'start': '2025-10-01', 'end': '2025-12-31', 'derived': {'legacy': True}}]})
        before_bundle = copy.deepcopy(bundle)
        with tempfile.TemporaryDirectory() as td, \
                patch.object(jobs, 'UNIVERSE', [('AAPL', 320193, 'Apple', 'Information Technology')]), \
                patch.object(jobs, 'load_pricestats', return_value={}):
            plan = self.repair.plan_cleanup(self.sb, ['AAPL'], Path(td), clean_bundles=True)
            self.assertEqual(plan['mode'], 'cleanup_only')
            self.assertEqual(plan['companies'][0]['status'], 'ready')
            self.assertEqual(plan['companies'][0]['review']['unsupported_deletions'], 1)
            self.assertEqual(plan['companies'][0]['review']['bundle_points_removed'], 1)
            self.assertEqual(self.sb.events, [])
            receipt = self.repair.apply_plan(self.sb, Path(td) / 'plan.json')
            self.assertCountEqual(self.sb.tables['facts'], [additive, reported_eps])
            cleaned = self.sb.data['companies/AAPL.json']
            self.assertNotIn('pipeline_version', cleaned)
            self.assertEqual(cleaned['statements']['income'][0], before_bundle['statements']['income'][0])
            self.assertEqual(cleaned['statements']['income'][1]['quarterly'], [])
            self.assertNotIn('pipeline_version', self.sb.data['meta/fundamentals.json']['AAPL'])
            self.repair.rollback(self.sb, receipt['backup'])
        self.assertCountEqual(self.sb.tables['facts'], [self.obsolete, additive, reported_eps])
        self.assertEqual(self.sb.data['companies/AAPL.json'], before_bundle)

    def test_bundle_cleanup_does_not_keep_a_source_proof_with_shifted_point_indexes(self):
        bundle = self.sb.data['companies/AAPL.json']
        bundle['provenance'] = {'status': 'verified', 'proof_path': 'source-proof.json'}
        bundle['statements']['income'].append({'label': 'EPS diluted', 'concept': 'EarningsPerShareDiluted', 'unit': 'USD/shares',
            'annual': [], 'quarterly': [{'v': -3, 'end': '2025-12-31', 'derived': True}]})
        with tempfile.TemporaryDirectory() as td:
            plan = self.repair.plan_cleanup(self.sb, ['AAPL'], Path(td), clean_bundles=True)
        self.assertEqual(plan['companies'][0]['status'], 'blocked')
        self.assertIn('proof', plan['companies'][0]['error'])
        self.assertEqual(self.sb.events, [])

    def batch_backup(self, rows):
        body = self.repair.encoded({'ticker': 'AAPL', 'rows': rows})
        path = 'repairs/nonadditive-facts/AAPL/' + self.repair.digest(body) + '.json'
        self.sb.objects['raw', path] = body
        self.sb._aggregate_lease = SimpleNamespace(check=lambda: None)
        return 'raw/' + path

    def test_optional_exact_batches_keep_reported_concurrent_replacements_and_quoted_values(self):
        second = dict(self.obsolete, label='EPS basic, "quoted" (test) \\ preserved', concept='EarningsPerShareBasic', value=-4)
        unrelated = dict(self.obsolete, label='Revenue', concept='Revenues', unit='USD')
        self.sb.tables['facts'] = [copy.deepcopy(self.obsolete), second, unrelated]
        backup = self.batch_backup([self.obsolete, second])
        original_delete = self.sb.delete
        def protected_delete(table, query):
            self.sb.tables['facts'][0].update(derived=False, value=2, accession='reported-replacement')
            original_delete(table, query)
        self.sb.delete = protected_delete
        result = self.repair.RepairSupabase.delete_nonadditive_fact_batches(self.sb, [self.obsolete, second], backup, batch_size=20)
        self.assertEqual(result, {'deleted': 1, 'protected_reported': 1, 'delete_requests': 1})
        self.assertEqual(len(self.sb.tables['facts']), 2)
        self.assertFalse(self.sb.tables['facts'][0]['derived'])
        self.assertIn(unrelated, self.sb.tables['facts'])

    def test_optional_batches_reject_unverified_backup_before_deletion(self):
        backup = self.batch_backup([self.obsolete])
        self.sb.objects['raw', backup.removeprefix('raw/')] = b'changed'
        with self.assertRaisesRegex(RuntimeError, 'backup'):
            self.repair.RepairSupabase.delete_nonadditive_fact_batches(self.sb, [self.obsolete], backup)
        self.assertEqual(self.sb.events, [])

    def test_optional_batch_filters_are_bounded_and_do_not_remove_changed_derived_values(self):
        rows = [dict(self.obsolete, period_end=f'2025-01-{n:02d}', value=-n) for n in range(1, 22)]
        self.sb.tables['facts'] = copy.deepcopy(rows)
        backup = self.batch_backup(rows)
        deletes = []
        original_delete = self.sb.delete
        def record_delete(table, query):
            deletes.append(query)
            original_delete(table, query)
        self.sb.delete = record_delete
        result = self.repair.RepairSupabase.delete_nonadditive_fact_batches(self.sb, rows, backup, batch_size=20)
        self.assertEqual(result['deleted'], 21)
        self.assertGreaterEqual(len(deletes), 2)
        self.assertTrue(all(len(q.encode()) <= 6000 for q in deletes))
        self.assertTrue(all('derived=is.true' in q and 'kind=eq.Q' in q for q in deletes))
        self.sb.tables['facts'] = [dict(self.obsolete, value=999)]
        backup = self.batch_backup([self.obsolete])
        with self.assertRaisesRegex(RuntimeError, 'still present'):
            self.repair.RepairSupabase.delete_nonadditive_fact_batches(self.sb, [self.obsolete], backup)
        self.assertEqual(self.sb.tables['facts'][0]['value'], 999)

    def test_bounded_executor_updates_receipts_on_coordinator_thread(self):
        coordinator = threading.get_ident()
        barrier = threading.Barrier(2)
        callbacks = []
        def work(item):
            barrier.wait(timeout=1)
            return item * 10
        result = self.repair.run_bounded([1, 2], work, lambda item, value: callbacks.append((threading.get_ident(), item, value)), workers=2)
        self.assertCountEqual(result, [10, 20])
        self.assertTrue(all(tid == coordinator for tid, _, _ in callbacks))

    def test_bounded_executor_stops_dispatch_and_awaits_inflight_work_on_failure(self):
        stop, second_started, second_finished = threading.Event(), threading.Event(), threading.Event()
        started = []
        def work(item):
            started.append(item)
            if item == 0:
                second_started.wait(timeout=1)
                raise RuntimeError('first issuer failed')
            second_started.set()
            stop.wait(timeout=1)
            second_finished.set()
            return item
        with self.assertRaisesRegex(RuntimeError, 'first issuer failed'):
            self.repair.run_bounded(range(10), work, lambda *_: None, workers=2, stop=stop)
        self.assertEqual(sorted(started), [0, 1])
        self.assertTrue(second_finished.is_set(), 'must finish in-flight work before caller can release its lease')

    def parallel_fixture(self):
        bundle = copy.deepcopy(self.sb.data['companies/AAPL.json'])
        bundle.update(ticker='MSFT', cik=789019)
        self.sb.data['companies/MSFT.json'] = bundle
        self.sb.data['meta/fundamentals.json']['MSFT'] = copy.deepcopy(self.sb.data['meta/fundamentals.json']['AAPL'])
        self.sb.tables['facts'].append(dict(self.obsolete, ticker='MSFT'))
        self.sb.delete_nonadditive_fact_batches = MethodType(self.repair.RepairSupabase.delete_nonadditive_fact_batches, self.sb)
        return [('AAPL', 320193, 'Apple', 'Information Technology'), ('MSFT', 789019, 'Microsoft', 'Information Technology')]

    def test_opt_in_parallel_batched_apply_keeps_one_lease_and_verifies_both_issuers(self):
        universe = self.parallel_fixture()
        with tempfile.TemporaryDirectory() as td, patch.object(jobs, 'UNIVERSE', universe), \
                patch.object(jobs, 'load_pricestats', return_value={}), patch.object(self.sb, 'rpc', wraps=self.sb.rpc) as rpc:
            self.repair.plan_cleanup(self.sb, ['AAPL', 'MSFT'], Path(td))
            receipt = self.repair.apply_plan(self.sb, Path(td) / 'plan.json', workers=2, delete_batch_size=20)
        self.assertEqual(receipt['status'], 'published')
        self.assertCountEqual(receipt['repaired'], ['AAPL', 'MSFT'])
        self.assertEqual(sum(x['deleted'] for x in receipt['fact_cleanup'].values()), 2)
        self.assertEqual(sum(x['delete_requests'] for x in receipt['fact_cleanup'].values()), 2)
        self.assertEqual([c.args[0] for c in rpc.call_args_list], ['acquire_etl_lease', 'release_etl_lease'])
        self.assertEqual(self.sb.tables['facts'], [])

    def test_full_normalization_cannot_defer_aggregate_publication(self):
        with tempfile.TemporaryDirectory() as td:
            self.plan(td)
            with self.assertRaisesRegex(ValueError, 'cleanup-only'):
                self.repair.apply_plan(self.sb, Path(td) / 'plan.json', defer_aggregate=True)
        self.assertEqual(self.sb.events, [])

    def test_deferred_cleanup_verifies_facts_without_any_aggregate_writes(self):
        self.parallel_fixture()
        self.sb.data['meta/fundamentals.json']['NVDA'] = {'unselected': 'preserved'}
        before_fund = copy.deepcopy(self.sb.data['meta/fundamentals.json'])
        self.sb.data['index.json'] = {'previous': 'verified publication'}
        self.sb.tables['company_metrics'] = [{'ticker': 'AAPL', 'price': 10}]
        with tempfile.TemporaryDirectory() as td, \
                patch.object(self.repair, '_publish_aggregate', side_effect=AssertionError('aggregate writes forbidden')):
            self.repair.plan_cleanup(self.sb, ['AAPL', 'MSFT'], Path(td))
            receipt = self.repair.apply_plan(self.sb, Path(td) / 'plan.json', workers=2,
                                            delete_batch_size=20, defer_aggregate=True)
        self.assertEqual(receipt['status'], 'cleanup_complete')
        self.assertEqual(receipt['aggregate_status'], 'deferred')
        self.assertEqual(self.sb.tables['facts'], [])
        self.assertEqual(self.sb.data['meta/fundamentals.json'], before_fund)
        self.assertEqual(self.sb.data['index.json'], {'previous': 'verified publication'})
        self.assertEqual(self.sb.tables['company_metrics'], [{'ticker': 'AAPL', 'price': 10}])
        self.assertFalse(any(e[0] == 'json' and e[1] in ('meta/fundamentals.json', 'index.json')
                             or e[0] == 'upsert' and e[1] == 'company_metrics' for e in self.sb.events))

    def test_deferred_cleanup_fails_on_unselected_fund_drift_and_retains_recovery_pointer(self):
        self.sb.data['meta/fundamentals.json']['NVDA'] = {'unselected': 'before'}
        put = self.sb.put_json
        def drift(path, value, **kwargs):
            put(path, value, **kwargs)
            if path == 'companies/AAPL.json':
                self.sb.data['meta/fundamentals.json']['NVDA'] = {'unselected': 'changed'}
        with tempfile.TemporaryDirectory() as td:
            self.repair.plan_cleanup(self.sb, ['AAPL'], Path(td))
            with patch.object(self.sb, 'put_json', side_effect=drift), \
                    patch.object(self.repair, '_publish_aggregate', side_effect=AssertionError('aggregate writes forbidden')):
                with self.assertRaisesRegex(RuntimeError, 'Fundamentals changed'):
                    self.repair.apply_plan(self.sb, Path(td) / 'plan.json', defer_aggregate=True)
            receipt = json.loads((Path(td) / 'apply-receipt.json').read_bytes())
        self.assertEqual(receipt['status'], 'failed')
        self.assertTrue(receipt['defer_aggregate'])
        self.assertIn(('raw', receipt['backup'].removeprefix('raw/')), self.sb.objects)
        self.assertEqual(self.sb.data['meta/fundamentals.json']['NVDA'], {'unselected': 'changed'})

    def test_failed_parallel_apply_keeps_recovery_pointer_and_can_rollback_partial_facts(self):
        universe = self.parallel_fixture()
        original = copy.deepcopy(self.sb.tables['facts'])
        put = self.sb.put_json
        def failed_put(path, value, **kw):
            if path == 'companies/MSFT.json':
                raise RuntimeError('simulated issuer publication failure')
            return put(path, value, **kw)
        with tempfile.TemporaryDirectory() as td, patch.object(jobs, 'UNIVERSE', universe), patch.object(jobs, 'load_pricestats', return_value={}):
            self.repair.plan_cleanup(self.sb, ['AAPL', 'MSFT'], Path(td))
            with patch.object(self.sb, 'put_json', side_effect=failed_put):
                with self.assertRaisesRegex(RuntimeError, 'simulated issuer publication failure'):
                    self.repair.apply_plan(self.sb, Path(td) / 'plan.json', workers=2, delete_batch_size=20)
            receipt = json.loads((Path(td) / 'apply-receipt.json').read_text())
            self.assertEqual(receipt['status'], 'failed')
            self.repair.rollback(self.sb, receipt['backup'])
        self.assertCountEqual(self.sb.tables['facts'], original)

    def test_repair_client_aborts_new_mutations_after_another_worker_fails(self):
        client = object.__new__(self.repair.RepairSupabase)
        client.url, client.key = 'https://example.invalid', 'test-only'
        client._aggregate_lease = SimpleNamespace(valid_until=time.monotonic() + 50, check=lambda: None)
        client._repair_abort = threading.Event(); client._repair_abort.set()
        with patch.object(self.repair, 'request') as request:
            with self.assertRaisesRegex(RuntimeError, 'stopped'):
                client.put_object('raw', 'backup.json', b'{}')
        request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
