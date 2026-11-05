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
