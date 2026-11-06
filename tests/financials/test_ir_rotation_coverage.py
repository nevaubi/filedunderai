"""Persisted coverage replay through the real rotation and index publication paths."""
import copy
import unittest
from unittest.mock import patch

from etl import jobs, kpitools


class SnapshotStore:
    def __init__(self, tickers):
        self.leases = {}
        self.queries = []
        self.writes = []
        self.control = {'fail_kind': None, 'failures': 0}
        self.json = {'meta/coverage_ir.json': {
            'generated': '2026-11-01T00:00:00Z',
            'kpi_counts_as_of': '2026-11-01T00:00:00Z',
            'segment_counts_as_of': '2026-11-01T00:00:00Z',
            'summary': {'companies': len(tickers), 'documents': 0, 'bytes': 0, 'kpis': 0, 'kpi_values': 0,
                        'companies_with_docs': 0, 'companies_with_kpis': 0, 'companies_with_segments': 0, 'segment_tables': 0},
            'companies': {ticker: {'docs': 0, 'bytes': 0, 'types': {}, 'kpis': 0, 'kpi_values': 0,
                                    'segment_tables': 0, 'segment_ties': 0,
                                    'coverage': {'status': 'unverified', 'total_documents': 0}}
                          for ticker in tickers}}}

    def rpc(self, fn, args):
        key, owner = args['lock_name'], args['owner']
        if fn == 'acquire_etl_lease':
            if key in self.leases and self.leases[key] != owner:
                return False
            self.leases[key] = owner
            return True
        if self.leases.get(key) == owner:
            del self.leases[key]
            return True
        return False

    def get_json(self, path):
        return copy.deepcopy(self.json.get(path))

    def put_json(self, path, value, **kwargs):
        kind = ('aggregate' if path == 'meta/coverage_ir.json' else
                'pending' if value.get('coverage_pending') else 'acknowledgement')
        if self.control['failures'] and self.control['fail_kind'] == kind:
            self.control['failures'] -= 1
            raise RuntimeError(kind + ' storage write failed')
        self.json[path] = copy.deepcopy(value)
        self.writes.append((path, copy.deepcopy(value)))

    def select(self, table, query=''):
        self.queries.append((table, query))
        if table == 'filings':
            return []
        if table != 'ir_documents' or '&ticker=eq.' not in query:
            raise AssertionError('Unexpected unbounded relational query')
        ticker = query.split('&ticker=eq.', 1)[1]
        return [{'ticker': ticker, 'doc_type': 'release', 'bytes': 10}]


def coverage(stamp, marker):
    return {'version': 1, 'status': 'unverified', 'updated_at': stamp,
            'total_documents': 1, 'marker': marker,
            'pending': {'discovery': 0, 'documents': 0, 'extractions': 0}}


class RotationCoverageReplayTests(unittest.TestCase):
    def run_rotation(self, store, tickers, values, calls, per_run=1):
        build = kpitools.build_index
        def aggregate(sb, coverage_updates=None):
            calls.append(copy.deepcopy(coverage_updates))
            return build(sb, coverage_updates=coverage_updates)
        def process(sb, selected, **kwargs):
            return ({ticker: {'status': 'unverified', 'coverage': values[ticker]} for ticker in selected}, {})
        with patch.object(jobs, 'UNIVERSE', [(ticker,) for ticker in tickers]), \
                patch('etl.universe.UNIVERSE', [(ticker,) for ticker in tickers]), \
                patch.object(jobs, 'ir_job', side_effect=process), \
                patch.object(kpitools, 'build_index', side_effect=aggregate):
            return jobs.ir_rotation_job(store, per_run=per_run, time_budget=240)

    def test_uncovered_issuers_take_half_the_open_slots_without_moving_the_cursor(self):
        tickers = ['AAA', 'BBB', 'CCC', 'NEW1', 'NEW2', 'NEW3']
        store = SnapshotStore(tickers[:3])
        selected = []
        def process(sb, chosen, **kwargs):
            selected.extend(chosen)
            return ({t: {'status': 'unverified', 'coverage': coverage('2026-11-02T00:00:00Z', t)} for t in chosen}, {})
        with patch.object(jobs, 'UNIVERSE', [(t,) for t in tickers]), \
                patch('etl.universe.UNIVERSE', [(t,) for t in tickers]), \
                patch.object(jobs, 'ir_job', side_effect=process), \
                patch.object(kpitools, 'build_index', return_value=None):
            jobs.ir_rotation_job(store, per_run=4, time_budget=240)
        self.assertEqual(selected, ['NEW1', 'NEW2', 'AAA', 'BBB'])
        self.assertEqual(store.json['meta/ir_rotation.json']['cursor'], 2)

    def test_workers_leave_time_for_the_checkpoint_and_retries_take_a_quarter(self):
        tickers = [f'T{i}' for i in range(12)] + ['NEW1', 'NEW2', 'NEW3', 'NEW4']
        store = SnapshotStore(tickers[:12])
        store.json['meta/ir_rotation.json'] = {'cursor': 0, 'done_hot': {}, 'retry': tickers[:8]}
        seen = {}
        def process(sb, chosen, **kwargs):
            seen['chosen'], seen['budget'] = list(chosen), kwargs.get('time_budget')
            return ({t: {'status': 'unverified', 'coverage': coverage('2026-11-02T00:00:00Z', t)} for t in chosen}, {})
        with patch.object(jobs, 'UNIVERSE', [(t,) for t in tickers]), \
                patch('etl.universe.UNIVERSE', [(t,) for t in tickers]), \
                patch.object(jobs, 'ir_job', side_effect=process), \
                patch.object(kpitools, 'build_index', return_value=None):
            jobs.ir_rotation_job(store, per_run=8, time_budget=640)
        self.assertEqual(seen['chosen'][:2], ['T0', 'T1'])
        self.assertEqual(seen['chosen'][2:5], ['NEW1', 'NEW2', 'NEW3'])
        self.assertLessEqual(seen['budget'], 640 - jobs.IR_ROTATION_RESERVE + 1)
        self.assertEqual(store.json['meta/ir_rotation.json']['retry'], ['T5', 'T6', 'T7'])

    def test_fresh_only_takes_every_slot_for_uncovered_issuers(self):
        tickers = ['AAA', 'BBB', 'NEW1', 'NEW2', 'NEW3']
        store = SnapshotStore(tickers[:2])
        store.json['meta/ir_rotation.json'] = {'cursor': 0, 'done_hot': {}, 'retry': ['AAA']}
        seen = []
        def process(sb, chosen, **kwargs):
            seen.extend(chosen)
            return ({t: {'status': 'unverified', 'coverage': coverage('2026-11-02T00:00:00Z', t)} for t in chosen}, {})
        with patch.object(jobs, 'UNIVERSE', [(t,) for t in tickers]), \
                patch('etl.universe.UNIVERSE', [(t,) for t in tickers]), \
                patch.object(jobs, 'ir_job', side_effect=process), \
                patch.object(kpitools, 'build_index', return_value=None):
            jobs.ir_rotation_job(store, per_run=4, time_budget=640, fresh_only=True)
        self.assertEqual(seen, ['NEW1', 'NEW2', 'NEW3'])
        self.assertEqual(store.json['meta/ir_rotation.json']['cursor'], 0)
        self.assertEqual(store.json['meta/ir_rotation.json']['retry'], ['AAA'])

    def test_failed_aaa_aggregate_replays_with_bbb_next_rotation(self):
        tickers = ['AAA', 'BBB']; sb = SnapshotStore(tickers); calls = []
        values = {'AAA': coverage('2026-11-04T10:00:00Z', 'first'),
                  'BBB': coverage('2026-11-04T11:00:00Z', 'second')}
        sb.control.update(fail_kind='aggregate', failures=1)
        done, failed = self.run_rotation(sb, tickers, values, calls)
        self.assertIn('coverage_index', failed)
        self.assertEqual(sb.json['meta/ir_rotation.json']['coverage_updates'], {'AAA': values['AAA']})
        done, failed = self.run_rotation(sb, tickers, values, calls)
        self.assertEqual(list(done), ['BBB'])
        self.assertNotIn('coverage_index', failed)
        self.assertEqual(calls, [{'AAA': values['AAA']}, values])
        self.assertEqual(sb.json['meta/coverage_ir.json']['companies']['AAA']['coverage'], values['AAA'])
        self.assertEqual(sb.json['meta/coverage_ir.json']['companies']['BBB']['coverage'], values['BBB'])
        self.assertFalse(sb.json['meta/ir_rotation.json']['coverage_pending'])
        self.assertEqual(sb.json['meta/ir_rotation.json']['coverage_updates'], {})

    def test_failed_acknowledgement_keeps_exact_payload_retryable(self):
        tickers = ['AAA', 'BBB']; sb = SnapshotStore(tickers); calls = []
        values = {'AAA': coverage('2026-11-04T10:00:00Z', 'first'),
                  'BBB': coverage('2026-11-04T11:00:00Z', 'second')}
        sb.control.update(fail_kind='acknowledgement', failures=1)
        _, failed = self.run_rotation(sb, tickers, values, calls)
        self.assertIn('coverage_index', failed)
        self.assertTrue(sb.json['meta/ir_rotation.json']['coverage_pending'])
        self.assertEqual(sb.json['meta/ir_rotation.json']['coverage_updates'], {'AAA': values['AAA']})
        _, failed = self.run_rotation(sb, tickers, values, calls)
        self.assertNotIn('coverage_index', failed)
        self.assertEqual(calls[-1], values)
        self.assertEqual(sb.json['meta/ir_rotation.json']['coverage_updates'], {})

    def test_pending_snapshot_failure_does_not_advance_cursor_or_publish(self):
        sb = SnapshotStore(['AAA']); calls = []
        values = {'AAA': coverage('2026-11-04T10:00:00Z', 'first')}
        sb.control.update(fail_kind='pending', failures=1)
        with self.assertRaisesRegex(RuntimeError, 'pending storage write failed'):
            self.run_rotation(sb, ['AAA'], values, calls)
        self.assertEqual(calls, [])
        self.assertNotIn('meta/ir_rotation.json', sb.json)
        _, failed = self.run_rotation(sb, ['AAA'], values, calls)
        self.assertEqual(failed, {})
        self.assertEqual(calls, [values])

    def test_same_ticker_merge_keeps_the_newer_original_coverage_timestamp(self):
        older = coverage('2026-11-04T10:00:00Z', 'older')
        newer = coverage('2026-11-04T11:00:00Z', 'newer')
        for prior, incoming in ((older, newer), (newer, older)):
            with self.subTest(prior=prior['marker']):
                sb = SnapshotStore(['AAA']); calls = []
                sb.json['meta/ir_rotation.json'] = {'cursor': 0, 'done_hot': {}, 'retry': [],
                    'coverage_pending': True, 'coverage_updates': {'AAA': prior}}
                _, failed = self.run_rotation(sb, ['AAA'], {'AAA': incoming}, calls)
                self.assertEqual(failed, {})
                self.assertEqual(calls, [{'AAA': newer}])
                self.assertEqual(sb.json['meta/coverage_ir.json']['companies']['AAA']['coverage'], newer)

    def test_bounded_batches_retain_unpublished_companies_until_backlog_drains(self):
        tickers = [f'T{i:02d}' for i in range(40)]; sb = SnapshotStore(tickers); calls = []
        values = {ticker: coverage('2026-11-04T10:00:00Z', ticker) for ticker in tickers}
        sb.json['meta/ir_rotation.json'] = {'cursor': 0, 'done_hot': {}, 'retry': [],
            'coverage_pending': True, 'coverage_updates': copy.deepcopy(values)}
        _, failed = self.run_rotation(sb, tickers, values, calls)
        self.assertIn('coverage_index', failed)
        self.assertLess(len(calls[0]), len(tickers))
        self.assertLessEqual(len(calls[0]), 32)
        pending = sb.json['meta/ir_rotation.json']['coverage_updates']
        self.assertTrue(sb.json['meta/ir_rotation.json']['coverage_pending'])
        self.assertEqual(set(pending) | set(calls[0]), set(tickers))
        self.assertFalse(set(pending) & set(calls[0]))
        for ticker, value in pending.items():
            self.assertEqual(value, values[ticker])
        for _ in range(6):
            if not sb.json['meta/ir_rotation.json']['coverage_pending']:
                break
            self.run_rotation(sb, tickers, values, calls)
        self.assertFalse(sb.json['meta/ir_rotation.json']['coverage_pending'])
        self.assertEqual(sb.json['meta/ir_rotation.json']['coverage_updates'], {})
        for ticker in tickers:
            self.assertEqual(sb.json['meta/coverage_ir.json']['companies'][ticker]['coverage'], values[ticker])

    def test_pending_payload_outside_universe_fails_closed(self):
        sb = SnapshotStore(['AAA']); calls = []
        state = {'cursor': 0, 'done_hot': {}, 'retry': [], 'coverage_pending': True,
                 'coverage_updates': {'ZZZ': coverage('2026-11-04T10:00:00Z', 'unknown')}}
        sb.json['meta/ir_rotation.json'] = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_rotation(sb, ['AAA'], {'AAA': coverage('2026-11-04T11:00:00Z', 'new')}, calls)
        self.assertEqual(calls, [])
        self.assertEqual(sb.json['meta/ir_rotation.json'], state)

    def test_legacy_pending_marker_without_payload_is_not_silently_cleared(self):
        sb = SnapshotStore(['AAA']); calls = []
        state = {'cursor': 0, 'done_hot': {}, 'retry': [], 'coverage_pending': True}
        sb.json['meta/ir_rotation.json'] = copy.deepcopy(state)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            self.run_rotation(sb, ['AAA'], {'AAA': coverage('2026-11-04T11:00:00Z', 'new')}, calls)
        self.assertEqual(calls, [])
        self.assertEqual(sb.json['meta/ir_rotation.json'], state)


if __name__ == '__main__':
    unittest.main()
