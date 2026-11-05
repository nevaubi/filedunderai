"""Standardized KPI sources and bounded publication regressions."""
import copy
import json
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

from etl import common, kpistd


class SourceStore:
    def select(self, table, query):
        if table != 'ir_documents':
            raise AssertionError(table)
        columns = parse_qs(query)['select'][0].split(',')
        source = {'id': 'workbook', 'title': 'Quarterly workbook', 'storage_path': 'TEST/workbook.xlsx',
                  'ext': '.xlsx', 'url': 'https://ir.example/data.xlsx', 'doc_type': 'supplement',
                  'fiscal_label': 'Q3 FY26', 'published': '2026-10-20', 'text_path': 'TEST/text/workbook.json'}
        return [{key: value for key, value in source.items() if key in columns}]


class StandardizedKpiSources(unittest.TestCase):
    def test_workbook_source_retains_extracted_text_path_for_the_public_viewer(self):
        source = kpistd.fetch_docs(SourceStore(), {'workbook'})['workbook']
        self.assertEqual(source, ['Quarterly workbook', 'TEST/workbook.xlsx', '.xlsx',
                                 'https://ir.example/data.xlsx', 'supplement', 'Q3 FY26',
                                 'TEST/text/workbook.json'])


class Clock:
    def __init__(self):
        self.now = 100.0

    def time(self):
        return self.now


def proposal_items(count=1):
    return [(f'key-{i}', {'ticker': 'TEST', 'kpi_id': f'uncertain-{i}',
                         'name': 'Adjusted recurring revenue', 'unit': 'USD'}, None,
             [kpistd.K.BY_ID['arr']]) for i in range(count)]


def model_reply():
    return json.dumps({'choices': [{'message': {'content': json.dumps({'items': [
        {'i': 0, 'id': 'arr', 'scope': 'variant', 'reason': 'Adjusted basis'},
    ]})}}]})


class PublicationStore(SourceStore):
    def __init__(self):
        self.public = {}
        self.objects = {}
        self.writes = []
        self.db_writes = []
        self.lease_calls = []
        self.lease_available = True

    def rpc(self, name, args):
        self.lease_calls.append((name, copy.deepcopy(args)))
        if name == 'acquire_etl_lease':
            return self.lease_available
        if name == 'release_etl_lease':
            return True
        raise AssertionError(name)

    def select(self, table, query):
        if table == 'companies':
            return [{'ticker': 'TEST', 'name': 'Example', 'display_name': 'Example', 'fiscal_year_end': '1231'}]
        if table == 'kpi_values':
            return [{'kpi_id': 'arr', 'period_kind': 'Q', 'period_end': '2026-09-30', 'fiscal_label': 'Q3 FY26',
                     'value': 12500000, 'source_id': 'workbook', 'anchor': {'sheet': 'Revenue', 'cell': 'A1'}, 'derived': None}]
        return super().select(table, query)

    def get_json(self, path):
        return copy.deepcopy(self.public.get(path))

    def put_json(self, path, value, **kwargs):
        self.public[path] = copy.deepcopy(value)
        self.writes.append(path)

    def get_object(self, bucket, path):
        return self.objects.get((bucket, path))

    def put_object(self, bucket, path, value, *args, **kwargs):
        self.objects[bucket, path] = value

    def upsert(self, table, rows, *args, **kwargs):
        self.db_writes.append((table, copy.deepcopy(rows)))

    def delete(self, table, query):
        self.db_writes.append((table, query))


class StandardizedKpiPublicationLease(unittest.TestCase):
    definitions = [{'ticker': 'TEST', 'kpi_id': 'arr', 'name': 'Annual recurring revenue',
                    'unit': 'USD', 'updated_at': '2026-11-05T00:00:00Z'}]

    def run_mapping(self, store):
        with patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST', 'name': 'Example'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=copy.deepcopy(self.definitions)):
            return kpistd.map_job(store, tickers=['TEST'], use_llm=False, log=lambda *args: None)

    def test_busy_mapper_refuses_before_reading_or_writing(self):
        store = PublicationStore()
        store.lease_available = False
        with patch.object(kpistd, '_raw_get') as read:
            with self.assertRaises(common.LeaseBusy):
                self.run_mapping(store)
        read.assert_not_called()
        self.assertEqual(store.writes, [])
        self.assertEqual(store.db_writes, [])

    def test_direct_publisher_uses_the_shared_financial_aggregate_lease(self):
        store = PublicationStore()
        kpistd.publish(store, {}, {}, {}, set(), [], '2026-11-05T00:00:00Z')
        self.assertEqual([name for name, _ in store.lease_calls], ['acquire_etl_lease', 'release_etl_lease'])
        self.assertTrue(all(args['lock_name'] == 'financial-aggregates' for _, args in store.lease_calls))
        self.assertIsNone(getattr(store, '_aggregate_lease', None))

    def test_mapper_and_nested_publisher_share_one_lease(self):
        store = PublicationStore()
        result = self.run_mapping(store)
        self.assertEqual(result['companies_mapped'], 1)
        self.assertEqual([name for name, _ in store.lease_calls], ['acquire_etl_lease', 'release_etl_lease'])
        self.assertIn('kpistd/map.json', store.writes)

    def test_lost_lease_stops_stale_mapping_deletion(self):
        class LosingStore(PublicationStore):
            def upsert(self, table, rows, *args, **kwargs):
                super().upsert(table, rows, *args, **kwargs)
                if table == 'kpi_std_map' and getattr(self, '_aggregate_lease', None):
                    self._aggregate_lease.lost = 'offline regression: lease ownership lost'

        store = LosingStore()
        with self.assertRaisesRegex(RuntimeError, 'Aggregate lease lost'):
            self.run_mapping(store)
        self.assertFalse(any(isinstance(rows, str) for _, rows in store.db_writes), 'stale DELETE must check the lease first')
        self.assertNotIn('kpistd/map.json', store.writes)


class StandardizedKpiPublicationPreservation(unittest.TestCase):
    def fixture(self):
        point = ['2026-09-30', 12500000, 'Q3 FY26', 'workbook', {'sheet': 'Revenue', 'cell': 'A1'}]
        series = {'kpi_id': 'arr', 'name': 'Annual recurring revenue', 'unit': 'USD',
                  'factor': 1, 'Q': [point], 'A': [], 'alts': [], 'current': True}
        return {'ticker': 'TEST', 'name': 'Example', 'fye': '1231', 'map': [],
                'series': {'arr': series}, 'docs': {}}

    def test_required_peer_failure_does_not_publish_incomplete_comparison(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                class UnreadableStore(PublicationStore):
                    def get_json(self, path):
                        if path == 'kpistd/t/PEER.json':
                            if missing:
                                return None
                            raise TimeoutError('peer read failed')
                        return super().get_json(path)

                store = UnreadableStore()
                old = {'primary': {'PEER': {'arr': 'arr'}}}
                store.public['kpistd/latest.json'] = {'kpis': {'arr': {'PEER': {'kpi_id': 'arr'}}}}
                with self.assertRaises((RuntimeError, TimeoutError)):
                    kpistd.publish(store, {}, old, {'TEST': self.fixture()}, {'arr'}, [], '2026-11-05T00:00:00Z')
                self.assertEqual(store.writes, [], 'required peer reads must finish before aggregate writes')

    def test_unreadable_existing_global_map_does_not_erase_other_companies(self):
        class UnreadableStore(PublicationStore):
            def get_json(self, path):
                if path == 'kpistd/map.json':
                    raise TimeoutError('global map read failed')
                return super().get_json(path)

        store = UnreadableStore()
        with patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=copy.deepcopy(StandardizedKpiPublicationLease.definitions)):
            with self.assertRaisesRegex(TimeoutError, 'global map read failed'):
                kpistd.map_job(store, tickers=['TEST'], use_llm=False, log=lambda *args: None)
        self.assertEqual(store.writes, [])
        self.assertEqual(store.db_writes, [])

    def test_unreadable_latest_snapshot_does_not_start_aggregate_publication(self):
        class UnreadableStore(PublicationStore):
            def get_json(self, path):
                if path == 'kpistd/latest.json':
                    raise TimeoutError('latest read failed')
                return super().get_json(path)

        store = UnreadableStore()
        with self.assertRaisesRegex(TimeoutError, 'latest read failed'):
            kpistd.publish(store, {}, {'primary': {'TEST': {'arr': 'arr'}}},
                           {'TEST': self.fixture()}, {'arr'}, [], '2026-11-05T00:00:00Z')
        self.assertEqual(store.writes, [])

    def test_failed_aggregate_publication_does_not_mark_company_signature_complete(self):
        store = PublicationStore()
        with patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=copy.deepcopy(StandardizedKpiPublicationLease.definitions)), \
             patch.object(kpistd, 'publish', side_effect=RuntimeError('publication interrupted')):
            with self.assertRaisesRegex(RuntimeError, 'publication interrupted'):
                kpistd.map_job(store, tickers=['TEST'], use_llm=False, log=lambda *args: None)
        self.assertNotIn(('raw', kpistd.STATE_PATH), store.objects, 'an interrupted company must remain retryable')

    def test_mapping_upsert_failure_preserves_prior_rows_and_retry_signature(self):
        class FailedStore(PublicationStore):
            def upsert(self, table, rows, *args, **kwargs):
                if table == 'kpi_std_map':
                    raise common.HTTPError(503, 'Unavailable')
                return super().upsert(table, rows, *args, **kwargs)

        store = FailedStore()
        with patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=copy.deepcopy(StandardizedKpiPublicationLease.definitions)):
            with self.assertRaises(common.HTTPError):
                kpistd.map_job(store, tickers=['TEST'], use_llm=False, log=lambda *args: None)
        self.assertFalse(any(isinstance(rows, str) for _, rows in store.db_writes))
        self.assertNotIn(('raw', kpistd.STATE_PATH), store.objects)

    def run_definitions(self, store, definitions):
        with patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=definitions):
            return kpistd.map_job(store, tickers=['TEST'], use_llm=False, log=lambda *args: None)

    def test_no_applicable_definition_does_not_create_empty_coverage(self):
        store = PublicationStore()
        result = self.run_definitions(store, [{'ticker': 'TEST', 'kpi_id': 'cash',
                                              'name': 'Cash and cash equivalents', 'unit': 'USD'}])
        self.assertEqual(store.writes, [])
        self.assertEqual(result['companies_mapped'], 0)
        self.assertEqual(result['companies_examined'], 1)
        self.assertEqual(result['companies_no_applicable'], 1)
        self.assertEqual(result['new_series'], 0)
        self.assertEqual(result['companies_left'], 0)
        self.assertEqual(common.run_outcome(result), 'ok')

    def test_unanswered_new_company_has_no_empty_file_and_remains_partial(self):
        store = PublicationStore()
        result = self.run_definitions(store, [{'ticker': 'TEST', 'kpi_id': 'arr',
                                              'name': 'Contractually committed recurring revenue', 'unit': 'USD'}])
        self.assertEqual(store.writes, [])
        self.assertEqual(result['companies_mapped'], 0)
        self.assertEqual(result['companies_no_applicable'], 0)
        self.assertEqual(result['unanswered_companies'], 1)
        self.assertEqual(common.run_outcome(result), 'partial')
        self.assertNotIn('TEST', kpistd._raw_get(store, kpistd.STATE_PATH)['sig'])

    def test_unanswered_prior_mapping_is_held_without_erasing_reviewed_series(self):
        store = PublicationStore()
        before = self.fixture()
        before['map'] = [{'kpi_id': 'arr', 'name': 'Contractually committed recurring revenue', 'unit': 'USD',
                          'canonical': 'arr', 'scope': 'total', 'method': 'llm+rules'}]
        store.public = {'kpistd/t/TEST.json': before,
                        'kpistd/map.json': {'primary': {'TEST': {'arr': 'arr'}}},
                        'kpistd/latest.json': {'kpis': {'arr': {'TEST': {'kpi_id': 'arr'}}}}}
        snapshot = copy.deepcopy(store.public)
        result = self.run_definitions(store, [
            {'ticker': 'TEST', 'kpi_id': 'arr', 'name': 'Contractually committed recurring revenue', 'unit': 'USD'},
            {'ticker': 'TEST', 'kpi_id': 'fcf', 'name': 'Free cash flow', 'unit': 'USD'},
        ])
        self.assertEqual(store.public, snapshot)
        self.assertEqual(store.db_writes, [])
        self.assertEqual(result['companies_held'], 1)
        self.assertEqual(result['companies_mapped'], 0)
        self.assertEqual(common.run_outcome(result), 'partial')


class StandardizedKpiDeadlines(unittest.TestCase):
    def test_fallback_uses_only_remaining_model_budget_without_helper_retries(self):
        clock, calls, cache = Clock(), [], {}

        def transport(url, **kwargs):
            calls.append((clock.now, kwargs['timeout'], kwargs['retries']))
            if len(calls) == 1:
                clock.now += 2
                raise TimeoutError('Primary failed')
            return model_reply()

        with patch.object(kpistd.time, 'time', clock.time), patch.object(kpistd, 'request', side_effect=transport), \
             patch.dict(os.environ, {'FIREWORKS_API_KEY': 'offline-test-key'}):
            self.assertEqual(kpistd.propose(proposal_items(), cache, deadline=105, workers=1), 1)
        self.assertEqual(calls, [(100, 5, 1), (102, 3, 1)])
        self.assertEqual(cache['key-0']['id'], 'arr')

    def test_expired_budget_starts_no_model_request(self):
        clock, calls, cache = Clock(), [], {}

        def transport(url, **kwargs):
            calls.append(kwargs)
            return model_reply()

        with patch.object(kpistd.time, 'time', clock.time), patch.object(kpistd, 'request', side_effect=transport), \
             patch.dict(os.environ, {'FIREWORKS_API_KEY': 'offline-test-key'}):
            result = kpistd.propose(proposal_items(), cache, deadline=100, workers=1)
        self.assertEqual(result, 0)
        self.assertEqual(calls, [])
        self.assertEqual(cache, {})

    def test_exhausted_request_skips_fallback_and_later_batches_without_caching_no_match(self):
        clock, calls, cache = Clock(), [], {}

        def transport(url, **kwargs):
            calls.append((clock.now, kwargs['timeout'], kwargs['retries']))
            clock.now += kwargs['timeout']
            raise TimeoutError('Transport timed out')

        with patch.object(kpistd.time, 'time', clock.time), patch.object(kpistd, 'request', side_effect=transport), \
             patch.dict(os.environ, {'FIREWORKS_API_KEY': 'offline-test-key'}):
            result = kpistd.propose(proposal_items(61), cache, deadline=105, workers=1)
        self.assertEqual(calls, [(100, 5, 1)])
        self.assertEqual(clock.now, 105)
        self.assertEqual(result, 0)
        self.assertEqual(cache, {}, 'unanswered definitions must remain retryable')

    def test_slow_model_leaves_reserved_time_to_publish_rule_checked_company_values(self):
        clock, calls, store = Clock(), [], PublicationStore()
        definitions = [
            {'ticker': 'TEST', 'kpi_id': 'arr', 'name': 'Annual recurring revenue', 'unit': 'USD', 'updated_at': '2026-11-05T00:00:00Z'},
            {'ticker': 'TEST', 'kpi_id': 'uncertain', 'name': 'Adjusted recurring revenue', 'unit': 'USD', 'updated_at': '2026-11-05T00:00:00Z'},
        ]

        def transport(url, **kwargs):
            calls.append((clock.now, kwargs['timeout'], kwargs['retries']))
            clock.now += kwargs['timeout']
            raise TimeoutError('Transport timed out')

        with patch.object(kpistd.time, 'time', clock.time), patch.object(kpistd, 'request', side_effect=transport), \
             patch.object(kpistd, 'load_universe', return_value=({'TEST': {'tk': 'TEST', 'name': 'Example'}}, {})), \
             patch.object(kpistd, 'fetch_all', return_value=definitions), \
             patch.dict(os.environ, {'FIREWORKS_API_KEY': 'offline-test-key'}):
            result = kpistd.map_job(store, time_budget=230, log=lambda *args: None)
        self.assertEqual(result['companies_mapped'], 1)
        self.assertEqual(result['unanswered_companies'], 1)
        self.assertEqual(calls, [(100, 140, 1)])
        self.assertEqual(store.public['kpistd/t/TEST.json']['series']['arr']['Q'][0][1], 12500000)
        self.assertEqual(store.public['kpistd/map.json']['primary']['TEST']['arr'], 'arr')
        self.assertEqual(common.run_outcome(result), 'partial', 'unanswered mapping work is not a completed run')

    def test_no_publication_work_preserves_existing_aggregate_timestamps_and_maps(self):
        store = PublicationStore()
        old = {'generated': '2026-11-04T00:00:00Z', 'primary': {'TEST': {'arr': 'arr'}}}
        store.public = {'kpistd/map.json': copy.deepcopy(old),
                        'kpistd/latest.json': {'generated': old['generated'], 'kpis': {'arr': {'TEST': {'q': ['2026-09-30', 12500000]}}}},
                        'kpistd/taxonomy.json': {'generated': old['generated'], 'version': kpistd.K.VERSION}}
        before = copy.deepcopy(store.public)
        result = kpistd.publish(store, {}, old, {}, set(), [], '2026-11-05T00:00:00Z')
        self.assertEqual(store.public, before)
        self.assertEqual(store.writes, [])
        self.assertEqual(store.db_writes, [])
        self.assertEqual(result['series_files'], 0)
        self.assertEqual(result['companies_with_series'], 1)


if __name__ == '__main__':
    unittest.main()
