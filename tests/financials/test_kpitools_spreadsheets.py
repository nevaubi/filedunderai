"""Source-cell and additive publication regressions; all persistence is in memory."""
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import patch

from etl import ir, kpitools


class CellValidationTests(unittest.TestCase):
    def check(self, cell, value, scale=1, address='A1', unit='USD', metadata=None):
        text = {'kind': 'sheets', 'sheets': {'S': [[cell]]},
                'cell_metadata': {'S': {'A1': metadata or {}}}}
        point = {'doc': 'd', 'sheet': 'S', 'cell': address, 'value': value}
        if scale is not None:
            point['scale'] = scale
        with patch.object(kpitools, 'text_of', return_value=text):
            return kpitools.check_value(None, {'d': {'id': 'd'}}, point, unit=unit)

    def test_invalid_zero_row_cannot_cite_the_last_row(self):
        self.assertTrue(self.check(42, 42, address='A0'))

    def test_boolean_values_and_scales_are_not_financial_numbers(self):
        for cell, value, scale in [(True, 1, 1), (1, True, 1), (1, 1, True)]:
            with self.subTest(cell=cell, value=value, scale=scale):
                self.assertTrue(self.check(cell, value, scale))

    def test_explicit_scale_is_applied_without_unconditional_percent_guessing(self):
        self.assertTrue(self.check(12.5, 1250, 1))
        self.assertTrue(self.check(12.5, 12.5, 1_000_000))
        self.assertEqual(self.check(12.5, 12_500_000, 1_000_000), [])
        self.assertTrue(self.check(12.5, 12_500_001, 1_000_000))

    def test_percent_conversion_requires_source_format_and_matching_unit(self):
        percent = {'number_format': '0.0%', 'data_type': 'n'}
        self.assertEqual(self.check(.125, 12.5, None, unit='%', metadata=percent), [])
        self.assertEqual(self.check(.125, 12.5, 100, unit='%', metadata=percent), [])
        self.assertTrue(self.check(.125, 12.5, 1, unit='%', metadata=percent))
        self.assertTrue(self.check(.125, .125, 1, unit='%', metadata=percent))
        self.assertTrue(self.check(.125, 125, 1000, unit='%', metadata=percent))
        self.assertTrue(self.check(.125, 12.5, None, unit='%'))
        self.assertTrue(self.check(.125, 12.5, None, unit='USD', metadata=percent))

    def test_literal_percent_suffix_keeps_source_percent_points(self):
        literal = {'number_format': '0" %";\\(0\\)" %"', 'data_type': 'n'}
        escaped = {'number_format': '0.0\\%', 'data_type': 'n'}
        for metadata in (literal, escaped):
            self.assertEqual(self.check(5.9884, 5.9884, None, unit='%', metadata=metadata), [])
            self.assertTrue(self.check(5.9884, 598.84, None, unit='%', metadata=metadata))
            self.assertTrue(self.check(5.9884, 598.84, 100, unit='%', metadata=metadata))

    def test_csv_numeric_literals_validate_without_rewriting_source_text(self):
        text = ir.extract_text(b'Metric,Value\nRevenue,42.125\n', '.csv')
        before = copy.deepcopy(text)
        with patch.object(kpitools, 'text_of', return_value=text):
            errors = kpitools.check_value(None, {'d': {'id': 'd', 'ext': '.csv'}},
                {'doc': 'd', 'sheet': 'csv', 'cell': 'B2', 'value': 42125, 'scale': 1000}, unit='USD')
        self.assertEqual(errors, [])
        self.assertEqual(text, before)
        self.assertEqual(text['sheets']['csv'][1][1], '42.125')

    def test_ambiguous_strings_and_nonfinite_numbers_are_rejected(self):
        for cell in ['1,234', '1.234,56', '00123', 'Revenue 42', 'NaN', 'Infinity', float('nan'), float('inf')]:
            with self.subTest(cell=cell):
                self.assertTrue(self.check(cell, 42))

    def test_per_barrel_rates_cannot_be_derived_by_period_subtraction(self):
        data = {'ticker':'TEST', 'kpis':[{'id':'rate','unit':'USD_per_barrel','values':[
            {'kind':'A','period_end':'2025-12-31','value':99},
            {'kind':'Q','period_end':'2025-03-31','value':42}]}],
            'derived':[{'kpi':'rate','kind':'Q','period_end':'2025-12-31','value':57,
                        'formula':'FY - Q1','inputs':[{'kind':'A','period_end':'2025-12-31'},
                                                     {'kind':'Q','period_end':'2025-03-31'}]}]}
        with patch.object(kpitools, 'docs_for', return_value=[]), patch.object(kpitools, 'check_value', return_value=[]):
            report, _ = kpitools.validate(None, data)
        self.assertEqual(len(report['derived_bad']), 1)
        self.assertIn('not additive', report['derived_bad'][0]['error'])

    def test_refreshed_text_pointer_invalidates_process_memory_cache(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(kpitools, 'TEXT_CACHE_DIR', folder), \
                patch.dict(kpitools._TEXT_CACHE, {}, clear=True):
            previous = {'id':'same-original', 'text_path':'TEST/text/previous.json'}
            refreshed = {**previous, 'text_path':'TEST/text/refreshed.json'}
            for doc, value in ((previous, 42), (refreshed, 99)):
                Path(kpitools._cache_file(doc)).write_text(json.dumps({'kind':'sheets','sheets':{'S':[[value]]}}))
            self.assertEqual(kpitools.text_of(None, previous)['sheets']['S'][0][0], 42)
            self.assertEqual(kpitools.text_of(None, refreshed)['sheets']['S'][0][0], 99)

    def test_uncached_formula_is_limited_and_keeps_cell_provenance(self):
        import openpyxl
        book = openpyxl.Workbook()
        sheet = book.active
        sheet.title = 'Financials'
        sheet['A1'], sheet['B1'] = 'Revenue', 12.5
        sheet['A2'], sheet['B2'] = 'Calculated revenue', '=SUM(B1,2)'
        sheet['B1'].number_format = '0.0%'
        hidden = book.create_sheet('Supporting data')
        hidden.sheet_state = 'hidden'
        hidden['C3'] = 99
        raw = io.BytesIO(); book.save(raw)
        original = raw.getvalue()
        text = ir.extract_text(original, '.xlsx')
        self.assertEqual(text['extraction']['status'], 'limited')
        self.assertIn('Financials!B2:formula cache missing', text['extraction']['errors'])
        self.assertEqual(text['cell_metadata']['Financials']['B2']['formula'], '=SUM(B1,2)')
        self.assertFalse(text['cell_metadata']['Financials']['B2']['cached'])
        self.assertEqual(text['cell_metadata']['Financials']['B1']['number_format'], '0.0%')
        self.assertEqual(text['sheet_metadata']['Supporting data']['state'], 'hidden')
        self.assertEqual(text['sheets']['Supporting data'][2][2], 99)
        self.assertEqual(raw.getvalue(), original)

    def test_cells_beyond_old_sheet_rectangle_are_retained_within_global_budget(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active['A1'] = 'Revenue'
        book.active.cell(602, 61, 123.75)
        raw = io.BytesIO(); book.save(raw)
        text = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(text['extraction']['status'], 'verified')
        self.assertEqual(text['sheets']['Sheet'][601][60], 123.75)
        self.assertEqual(text['cell_metadata']['Sheet']['BI602']['data_type'], 'n')

    def test_source_cell_errors_are_preserved_and_mark_extraction_limited(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active['A1'], book.active['B1'] = 'Revenue', 42
        book.active['C3'] = '#DIV/0!'
        raw = io.BytesIO(); book.save(raw)
        text = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(text['extraction']['status'], 'limited')
        self.assertEqual(text['extraction']['source_cell_errors'], 1)
        self.assertEqual(text['sheets']['Sheet'][2][2], '#DIV/0!')
        self.assertEqual(text['cell_metadata']['Sheet']['C3']['source_error'], '#DIV/0!')

    def test_style_only_trailing_rectangle_does_not_starve_later_sheets(self):
        import openpyxl
        book = openpyxl.Workbook()
        book.active['A1'], book.active['B2'] = 'Revenue', 42
        book.active.cell(1000000, 16384).number_format = '0.0%'
        book.create_sheet('Other financials')['C3'] = 99
        raw = io.BytesIO(); book.save(raw)
        with patch.object(ir, 'WORKBOOK_MAX_CELLS', 64):
            text = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(text['extraction']['status'], 'verified')
        self.assertEqual(text['sheets']['Sheet'][1][1], 42)
        self.assertEqual(text['sheets']['Other financials'][2][2], 99)
        self.assertEqual(text['sheet_metadata']['Sheet']['rows'], 1000000)

    def test_large_drawing_allowance_does_not_relax_cell_or_total_archive_limits(self):
        import openpyxl
        book = openpyxl.Workbook(); book.active['A1'] = 42
        source = io.BytesIO(); book.save(source)
        def with_members(members):
            raw = io.BytesIO()
            with zipfile.ZipFile(io.BytesIO(source.getvalue())) as old, zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as new:
                for info in old.infolist():
                    new.writestr(info.filename, old.read(info))
                for name, size in members:
                    new.writestr(name, b'<' + b' ' * (size - 2) + b'>')
            return raw.getvalue()
        with patch.object(ir, 'WORKBOOK_ZIP_MAX_MEMBER_BYTES', 16384), \
                patch.object(ir, 'WORKBOOK_ZIP_MAX_DRAWING_BYTES', 32768, create=True), \
                patch.object(ir, 'WORKBOOK_ZIP_MAX_BYTES', 65536):
            raw = with_members([('xl/drawings/drawing99.xml', 24000)])
            self.assertEqual(ir.extract_text(raw, '.xlsx')['sheets']['Sheet'][0][0], 42)
            for members in [[('xl/worksheets/extra.xml', 24000)], [('xl/drawings/drawing99.xml', 33000)],
                            [('xl/drawings/drawing97.xml', 24000), ('xl/drawings/drawing98.xml', 24000), ('xl/drawings/drawing99.xml', 24000)]]:
                with self.subTest(members=members), self.assertRaisesRegex(ValueError, 'size limit'):
                    ir.extract_text(with_members(members), '.xlsx')


class MemoryStore:
    def __init__(self):
        self.tables = {'ir_documents': [
            {'id': 'd', 'ticker': 'TEST', 'title': 'Quarterly workbook', 'ext': '.xlsx',
             'url': 'https://issuer.test/results.xlsx', 'storage_path': 'TEST/results.xlsx',
             'text_path': 'TEST/text/results-v2.json'},
            {'id': 'd2', 'ticker': 'TEST', 'title': 'New quarterly workbook', 'ext': '.xlsx',
             'url': 'https://issuer.test/new.xlsx', 'storage_path': 'TEST/new.xlsx',
             'text_path': 'TEST/text/new-v2.json'}],
            'companies': [{'ticker': 'TEST', 'fiscal_year_end': '1231'}],
            'kpi_definitions': [], 'kpi_values': []}
        self.objects, self.writes, self.deletes = {}, [], []

    def rpc(self, name, args):
        return True

    def select(self, table, query=''):
        return copy.deepcopy(self.tables.get(table, []))

    def get_json(self, path):
        return copy.deepcopy(self.objects.get(path))

    def put_json(self, path, value, **kwargs):
        self.objects[path] = copy.deepcopy(value)
        self.writes.append(('object', path))

    def upsert(self, table, rows, on_conflict, **kwargs):
        keys = on_conflict.split(',')
        for row in rows:
            existing = next((r for r in self.tables[table] if all(r.get(k) == row.get(k) for k in keys)), None)
            if existing is None:
                self.tables[table].append(copy.deepcopy(row))
            else:
                existing.update(copy.deepcopy(row))
        self.writes.append(('upsert', table))

    def insert(self, table, rows, returning=True):
        rows = [rows] if isinstance(rows, dict) else rows
        keys = ('ticker', 'kpi_id') if table == 'kpi_definitions' else ('ticker', 'kpi_id', 'period_kind', 'period_end', 'source_kind')
        for row in rows:
            if any(all(old.get(k) == row.get(k) for k in keys) for old in self.tables[table]):
                raise ValueError('duplicate key')
        self.tables[table].extend(copy.deepcopy(rows))
        self.writes.append(('insert', table))
        return copy.deepcopy(rows) if returning else None

    def delete(self, table, query):
        self.deletes.append((table, query))
        raise AssertionError('additive publication must never delete existing data')


def extraction(kid='revenue', value=42, end='2026-03-31', doc='d', kind='Q'):
    return {'ticker': 'TEST', 'kpis': [{'id': kid, 'name': kid.title(), 'category': 'Operating metrics',
        'unit': 'USD', 'values': [{'kind': kind, 'period_end': end, 'value': value, 'doc': doc,
                                 'sheet': 'Financials', 'cell': 'B2', 'scale': 1}]}]}


class AdditivePublicationTests(unittest.TestCase):
    def setUp(self):
        self.sb = MemoryStore()
        self.text = patch.object(kpitools, 'text_of', side_effect=lambda sb, d:
            {'kind': 'sheets', 'sheets': {'Financials': [['Revenue', 'Value'], ['Source value', 42 if d['id'] == 'd' else 99]]}})
        self.text.start()
        self.addCleanup(self.text.stop)
        kpitools.publish(self.sb, extraction())
        self.sb.writes.clear()

    def test_addition_keeps_existing_definitions_values_and_public_source_points(self):
        before_defs = copy.deepcopy(self.sb.tables['kpi_definitions'])
        before_rows = copy.deepcopy(self.sb.tables['kpi_values'])
        old_point = copy.deepcopy(self.sb.objects['kpis/TEST.json']['groups'][0]['items'][0]['values']['Q']['2026-03-31'])
        kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        self.assertEqual(self.sb.tables['kpi_definitions'], before_defs)
        self.assertEqual(self.sb.tables['kpi_values'][0], before_rows[0])
        self.assertEqual(len(self.sb.tables['kpi_values']), 2)
        points = self.sb.objects['kpis/TEST.json']['groups'][0]['items'][0]['values']['Q']
        self.assertEqual(points['2026-03-31'], old_point)
        self.assertEqual(points['2026-06-30']['v'], 99)
        self.assertEqual(points['2026-06-30']['text_path'], 'TEST/text/new-v2.json')
        self.assertEqual(self.sb.deletes, [])

    def test_conflicting_existing_point_fails_before_any_write(self):
        before = copy.deepcopy((self.sb.tables, self.sb.objects))
        with self.assertRaisesRegex(ValueError, 'conflict'):
            kpitools.publish(self.sb, extraction(value=99, doc='d2'), additive=True)
        self.assertEqual((self.sb.tables, self.sb.objects), before)
        self.assertEqual(self.sb.writes, [])

    def test_different_source_for_equal_value_is_a_conflict(self):
        data = extraction(doc='d2')
        with patch.object(kpitools, 'text_of', return_value={'kind':'sheets','sheets':{'Financials':[['', ''], ['', 42]]}}):
            with self.assertRaisesRegex(ValueError, 'conflict'):
                kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_invalid_new_value_does_not_publish_a_partial_candidate(self):
        data = extraction(value=999, end='2026-06-30')
        with self.assertRaisesRegex(ValueError, 'validation'):
            kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_exact_retry_does_not_rewrite_existing_rows_or_objects(self):
        kpitools.publish(self.sb, extraction(), additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_new_definition_and_ytd_only_group_are_kept(self):
        kpitools.publish(self.sb, extraction(kid='cash_flow', value=99, end='2026-06-30', doc='d2', kind='Y'), additive=True)
        items = {it['id']: it for g in self.sb.objects['kpis/TEST.json']['groups'] for it in g['items']}
        self.assertEqual(set(items), {'revenue', 'cash_flow'})
        self.assertEqual(items['cash_flow']['values']['Y']['2026-06-30']['v'], 99)

    def test_definition_conflict_and_public_database_drift_stop_before_writes(self):
        data = extraction(value=99, end='2026-06-30', doc='d2')
        data['kpis'][0]['unit'] = 'count'
        with self.assertRaisesRegex(ValueError, 'conflict'):
            kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.writes, [])
        self.sb.objects['kpis/TEST.json']['groups'][0]['items'][0]['values']['Q']['2026-03-31']['v'] = 7
        with self.assertRaisesRegex(ValueError, 'conflict'):
            kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_interrupted_publication_resumes_without_rewriting_inserted_values(self):
        data = extraction(value=99, end='2026-06-30', doc='d2')
        old_bundle = copy.deepcopy(self.sb.objects['kpis/TEST.json'])
        with patch.object(self.sb, 'put_json', side_effect=RuntimeError('storage unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'storage unavailable'):
                kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.objects['kpis/TEST.json'], old_bundle)
        self.assertEqual(len(self.sb.tables['kpi_values']), 2)
        stored = copy.deepcopy(self.sb.tables['kpi_values'])
        self.sb.writes.clear()
        report = kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.tables['kpi_values'], stored)
        self.assertEqual(report['added_values'], 0)
        self.assertEqual(report['restored_public_values'], 1)
        self.assertEqual(self.sb.writes, [('object', 'kpis/TEST.json')])

    def test_missing_insert_readback_cannot_publish_public_values(self):
        old_bundle = copy.deepcopy(self.sb.objects['kpis/TEST.json'])
        with patch.object(self.sb, 'insert', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'readback'):
                kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        self.assertEqual(self.sb.objects['kpis/TEST.json'], old_bundle)

    def test_lost_lease_stops_even_unguarded_plain_inserts(self):
        read = self.sb.get_json
        def lose_lease(path):
            self.sb._aggregate_lease.lost = 'lost before insert'
            return read(path)
        with patch.object(self.sb, 'get_json', side_effect=lose_lease):
            with self.assertRaisesRegex(RuntimeError, 'lease lost'):
                kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_unrelated_database_only_point_is_never_republished(self):
        orphan = copy.deepcopy(self.sb.tables['kpi_values'][0])
        orphan.update(period_end='2025-06-30', fiscal_label='Q2 FY25', value=999999,
                      source_id='missing-unreviewed-source')
        self.sb.tables['kpi_values'].append(orphan)
        kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        points = self.sb.objects['kpis/TEST.json']['groups'][0]['items'][0]['values']['Q']
        self.assertEqual(set(points), {'2026-03-31', '2026-06-30'})
        self.assertIn(orphan, self.sb.tables['kpi_values'])

    def test_omitted_unit_cannot_publish_under_a_different_stored_definition(self):
        data = extraction(value=99, end='2026-06-30', doc='d2')
        del data['kpis'][0]['unit']
        with self.assertRaisesRegex(ValueError, 'unit'):
            kpitools.publish(self.sb, data, additive=True)
        self.assertEqual(self.sb.writes, [])

    def test_initial_and_readback_snapshots_have_unique_pagination_order(self):
        select = self.sb.select
        def ordered(table, query=''):
            if table == 'kpi_definitions':
                self.assertIn('order=kpi_id', query)
            if table == 'kpi_values':
                self.assertIn('order=kpi_id,period_kind,period_end', query)
            return select(table, query)
        with patch.object(self.sb, 'select', side_effect=ordered):
            kpitools.publish(self.sb, extraction(value=99, end='2026-06-30', doc='d2'), additive=True)
        self.assertEqual(len(self.sb.tables['kpi_values']), 2)


if __name__ == '__main__':
    unittest.main()
