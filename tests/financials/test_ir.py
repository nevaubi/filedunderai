"""Offline IR failure regressions. Provider boundaries use deterministic fixtures."""
import copy
import datetime as dt
import hashlib
import io
import json
import struct
import time
import os
import unittest
import zipfile
from contextlib import ExitStack
from unittest.mock import patch

from etl import common, ir, jobs, kpitools


class Store:
    def __init__(self):
        self.rows = {}
        self.objects = {}
        self.json = {}
        self.sites = [{"ticker": "TEST", "ir_url": "https://ir.example.test", "discovered_at": "2000-01-01"}]
        self.writes = []
        self.leases = {}
        self.updates = []

    def rpc(self, name, args):
        key, owner = args['lock_name'], args['owner']
        if name == 'acquire_etl_lease':
            if key in self.leases and self.leases[key] != owner:
                return False
            self.leases[key] = owner
            return True
        if self.leases.get(key) == owner:
            self.leases.pop(key)
            return True
        return False

    def select(self, table, query=''):
        if table == 'ir_documents':
            rows = list(self.rows.values())
            if 'ticker=eq.' in query:
                tk = query.split('ticker=eq.')[1].split('&')[0]
                rows = [r for r in rows if r['ticker'] == tk]
            return copy.deepcopy(rows)
        if table == 'ir_sites':
            return copy.deepcopy(self.sites)
        return []

    def upsert(self, table, rows, *args, **kw):
        self.writes.append(table)
        if table == 'ir_documents':
            for row in rows:
                self.rows.setdefault(row['id'], {}).update(copy.deepcopy(row))
        elif table == 'ir_sites':
            for row in rows:
                if self.sites:
                    self.sites[0].update(row)
                else:
                    self.sites.append(copy.deepcopy(row))

    def put_object(self, bucket, path, content, *args, **kw):
        self.objects[bucket, path] = content.encode() if isinstance(content, str) else content
        self.writes.append((bucket, path))

    def get_object(self, bucket, path):
        return self.objects.get((bucket, path))

    def put_json(self, path, obj, **kw):
        self.json[path] = copy.deepcopy(obj)
        self.writes.append(path)

    def get_json(self, path):
        return copy.deepcopy(self.json.get(path))

    def insert(self, *args, **kw):
        return [{'id': 1}]

    def update(self, *args):
        self.updates.append(args)


def sub(filings=None, files=None):
    filings = filings or []
    keys = {'accessionNumber':'a', 'filingDate':'d', 'reportDate':'r', 'form':'f', 'primaryDocument':'p', 'primaryDocDescription':'x', 'items':'i'}
    recent = {key:[f.get(short, '') for f in filings] for key, short in keys.items()}
    return {'filings': {'recent': recent, 'files': files or []}}


def existing(sb, url, text=True):
    did = ir.doc_id(url)
    sb.rows[did] = {'id': did, 'ticker': 'TEST', 'url': url, 'title': 'Q1 2026 earnings', 'doc_type': 'release',
                    'ext': '.csv', 'storage_path': 'TEST/' + did + '.csv', 'text_path': 'TEST/text/' + did + '.json' if text else None,
                    'fetched_at': '2026-01-01T00:00:00Z', 'sha256': None}
    sb.objects['ir', sb.rows[did]['storage_path']] = b'Label,Value\nRevenue,42\n'
    if text:
        sb.objects['ir', sb.rows[did]['text_path']] = b'{"kind":"sheets","sheets":{"csv":[["Revenue",42]]}}'
    return did


def pdf_document(lines):
    """A tiny real PDF, keeping parser regressions independent of PDF writer libraries."""
    stream = b'BT /F1 12 Tf 50 750 Td 16 TL\n'
    for line in lines:
        escaped = line.replace('\\', '\\\\').replace('(', '\\(').replace(')', '\\)')
        stream += b'(' + escaped.encode('ascii') + b') Tj T*\n'
    stream += b'ET\n'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        f'<< /Length {len(stream)} >>\nstream\n'.encode() + stream + b'endstream',
    ]
    body, offsets = b'%PDF-1.4\n', [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(body))
        body += f'{number} 0 obj\n'.encode() + obj + b'\nendobj\n'
    start = len(body)
    body += f'xref\n0 {len(offsets)}\n0000000000 65535 f \n'.encode()
    body += b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return body + f'trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n'.encode()


class ExtractionTests(unittest.TestCase):
    def test_password_placeholder_pdf_is_not_verified_or_published_as_text(self):
        raw = pdf_document(['News Release Archive', 'This site is not available for public viewing.',
                            'Password Submit', 'https://vylor.mediaroom.com/index.php?s=2469&item=122500'])
        with self.assertRaisesRegex(ValueError, 'access-restricted placeholder'):
            ir.extract_text(raw, '.pdf')
        sb, outcomes = Store(), {}
        url = 'https://ir.example.test/earnings.pdf'
        ir.store_document(sb, 'TEST', url, raw, '.pdf', 'Earnings', '', set(), outcomes=outcomes, log=lambda _: None)
        row = sb.rows[ir.doc_id(url)]
        self.assertIsNone(row['text_path'])
        self.assertIsNone(row['extracted_at'])
        self.assertEqual(outcomes[row['id']]['status'], 'failed')
        self.assertFalse(outcomes[row['id']]['nonempty'])
        self.assertEqual(sb.objects['ir', row['storage_path']], raw)

    def test_pdf_article_quoting_access_notice_remains_valid(self):
        raw = pdf_document(['Quarterly results: Revenue was $42 million.',
                            'This site is not available for public viewing.', 'Password Submit',
                            'The old archive displayed the notice above before launch.'])
        result = ir.extract_text(raw, '.pdf')
        self.assertEqual(result['extraction']['status'], 'verified')
        self.assertIn('Revenue was $42 million.', result['pages'][0])

    def test_csv_quoted_multiline_coordinates_and_precision(self):
        result = ir.extract_text(b'Name,Value,Note\n"Revenue, net",1.234567890123,"line one\nline two"\n,,\n', '.csv')
        self.assertEqual(result['sheets']['csv'][1], ['Revenue, net', '1.234567890123', 'line one\nline two'])
        self.assertEqual(result['sheets']['csv'][2], ['', '', ''])
        self.assertEqual(result['extraction']['status'], 'verified')

    def test_empty_and_unsupported_are_not_successful_text(self):
        sb = Store()
        ir.store_document(sb, 'TEST', 'https://example.test/q1.pdf', b'%PDF-invalid', '.pdf', 'Q1 earnings', '', set())
        row = next(iter(sb.rows.values()))
        self.assertIsNone(row.get('text_path'))
        self.assertIsNone(row.get('extracted_at'))
        self.assertEqual(ir.extract_text(b'', '.csv')['extraction']['status'], 'empty')
        self.assertEqual(ir.extract_text(b'binary', '.doc')['extraction']['status'], 'unsupported')

    def test_sheet_rectangle_does_not_truncate_cells_inside_global_budget(self):
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.cell(1, 1, 1.234567890123)
        ws.cell(602, 61, 'formerly truncated')
        raw = io.BytesIO(); wb.save(raw)
        result = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(result['sheets']['Sheet'][0][0], 1.234567890123)
        self.assertEqual(result['extraction']['status'], 'verified')
        self.assertEqual(result['sheets']['Sheet'][601][60], 'formerly truncated')
        self.assertEqual(result['extraction']['limits'], [])
    def test_eighty_small_worksheets_retain_every_sheet_and_coordinate(self):
        import openpyxl
        wb = openpyxl.Workbook(); wb.remove(wb.active)
        for i in range(80):
            ws = wb.create_sheet(f'Sheet {i + 1}')
            ws.cell(3, 2, i + 0.125)
        raw = io.BytesIO(); wb.save(raw)
        result = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(len(result['sheets']), 80)
        self.assertEqual(result['sheets']['Sheet 80'][2][1], 79.125)
        self.assertEqual(result['extraction']['status'], 'verified')

    def test_total_workbook_cell_budget_is_explicit_and_preserves_coordinates(self):
        import openpyxl
        wb = openpyxl.Workbook(); ws = wb.active
        ws.cell(1, 3, 42); ws.cell(2, 3, 99)
        raw = io.BytesIO(); wb.save(raw)
        with patch.object(ir, 'WORKBOOK_MAX_CELLS', 5, create=True):
            result = ir.extract_text(raw.getvalue(), '.xlsx')
        self.assertEqual(result['sheets']['Sheet'], [['', '', 42]])
        self.assertEqual(result['extraction']['status'], 'limited')
        self.assertIn('cells:5', result['extraction']['limits'])

    def test_windows_zip_member_names_extract_without_changing_original_bytes(self):
        import openpyxl
        wb = openpyxl.Workbook(); wb.active.title = 'Financial data'
        wb.active.cell(3, 2, 123.4567890123)
        wb.active.cell(4, 1, 'Revenue, net')
        raw = io.BytesIO(); wb.save(raw)
        altered = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw.getvalue())) as source, zipfile.ZipFile(altered, 'w', zipfile.ZIP_DEFLATED) as target:
            for entry in source.infolist():
                target.writestr(entry.filename.replace('/', chr(92)), source.read(entry))
        original = altered.getvalue(); digest = hashlib.sha256(original).hexdigest()
        result = ir.extract_text(original, '.xlsx')
        self.assertEqual(result['sheets']['Financial data'][2][1], 123.4567890123)
        self.assertEqual(result['sheets']['Financial data'][3][0], 'Revenue, net')
        self.assertEqual(result['extraction']['status'], 'verified')
        self.assertEqual(hashlib.sha256(original).hexdigest(), digest)

    def test_zip_normalization_rejects_collisions_traversal_and_excess_size(self):
        import openpyxl
        wb = openpyxl.Workbook(); wb.active.cell(1, 1, 42)
        raw = io.BytesIO(); wb.save(raw)
        for extra in ('xl' + chr(92) + 'workbook.xml', '../outside.xml', '/absolute.xml'):
            with self.subTest(extra=extra):
                altered = io.BytesIO(raw.getvalue())
                with zipfile.ZipFile(altered, 'a') as archive:
                    archive.writestr(extra, '<workbook/>')
                with self.assertRaises(ValueError):
                    ir.extract_text(altered.getvalue(), '.xlsx')
        with patch.object(ir, 'WORKBOOK_ZIP_MAX_BYTES', 100, create=True), self.assertRaises(ValueError):
            ir.extract_text(raw.getvalue(), '.xlsx')

    def test_html_article_loader_is_empty_until_article_body_is_available(self):
        raw = b'<html><nav>Quarterly results</nav><div class="press-release-details"></div><p>Generic Error Unable to load press releases</p></html>'
        result = ir.extract_text(raw, '.html')
        self.assertEqual(result['extraction']['status'], 'empty')
        self.assertFalse(result['extraction']['nonempty'])
        rendered = b'<html><div class="press-release-details"><div class="press-release-details-body">Revenue was $42 million.</div></div></html>'
        good = ir.extract_text(rendered, '.html')
        self.assertEqual(good['extraction']['status'], 'verified')
        self.assertIn('Revenue was $42 million.', good['pages'][0])

    def test_xls_preserves_numeric_values_and_cell_errors(self):
        # Minimal BIFF2 workbook: A1=42, A2='Revenue', B3=#DIV/0!.
        def record(code, body=b''):
            return struct.pack('<HH', code, len(body)) + body
        raw = (record(0x0009, struct.pack('<HH', 2, 0x10)) + record(0x0042, struct.pack('<H', 1252))
               + record(0x0002, struct.pack('<HH', 0, 0) + b'\0\0\0' + struct.pack('<H', 42))
               + record(0x0004, struct.pack('<HH', 1, 0) + b'\0\0\0' + bytes([7]) + b'Revenue')
               + record(0x0005, struct.pack('<HH', 2, 1) + b'\0\0\0' + bytes([7, 1]))
               + record(0x000a))
        result = ir.extract_text(raw, '.xls')
        self.assertEqual(result['sheets']['Sheet 1'][0][0], 42)
        self.assertEqual(result['sheets']['Sheet 1'][2][1], '#DIV/0!')
        self.assertEqual(result['extraction']['status'], 'limited')
        self.assertEqual(result['extraction']['source_cell_errors'], 1)


class PipelineTests(unittest.TestCase):
    def providers(self, docs=None, scrape=None, secdata=None):
        stack = ExitStack()
        stack.enter_context(patch.object(ir, 'firecrawl_map', return_value=[{'url':u, 'title':t} for u,t in (docs or {}).items()]))
        stack.enter_context(patch.object(ir, '_scrape', side_effect=scrape or (lambda *a, **k: {'html':'<html><a href="/results">Results</a></html>'}), create=True))
        stack.enter_context(patch.object(ir, '_sec_json', return_value=secdata or sub(), create=True))
        # Old code calls this helper; new code owns resumable submissions itself.
        stack.enter_context(patch.object(jobs, 'periodic_filings', return_value=[]))
        stack.enter_context(patch.object(ir, 'download', side_effect=lambda url, **kw: ('Label,Value\n' + url + ',42\n').encode()))
        return stack

    def test_existing_documents_do_not_consume_work_budget(self):
        sb = Store()
        urls = [f'https://ir.example.test/q1-2026-earnings-{i}.csv' for i in range(3)]
        existing(sb, urls[0])
        with self.providers(dict.fromkeys(urls, 'Q1 2026 earnings')):
            result = ir.process_company(sb, 'TEST', 'Test', 1, max_docs=1)
        self.assertEqual(len(sb.rows), 2)
        self.assertEqual(result['web'], 1)
        self.assertGreater(result['deferred'], 0)
        self.assertNotEqual(result['coverage']['status'], 'complete')

    def test_queue_resumes_without_successes_starving_later_documents(self):
        sb = Store()
        urls = [f'https://ir.example.test/q1-2026-earnings-{i}.csv' for i in range(3)]
        with self.providers(dict.fromkeys(urls, 'Q1 2026 earnings')):
            for _ in range(3):
                result = ir.process_company(sb, 'TEST', 'Test', 1, max_docs=1)
        self.assertEqual(len(sb.rows), 3)
        self.assertIn(('raw', 'ir/checkpoints/TEST.json'), sb.objects)
        self.assertEqual(result['coverage']['pending']['documents'], 0)
        self.assertEqual(result['coverage']['status'], 'unverified')

    def test_checkpoint_read_failure_has_no_writes(self):
        sb = Store()
        sb.get_object = lambda *a: (_ for _ in ()).throw(RuntimeError('checkpoint read failed'))
        with self.providers(), self.assertRaisesRegex(RuntimeError, 'checkpoint read failed'):
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(sb.writes, [])

    def test_malformed_checkpoint_has_no_writes(self):
        sb = Store(); sb.objects['raw', 'ir/checkpoints/TEST.json'] = b'{"version":1,"ticker":"WRONG"}'
        with self.providers(), self.assertRaises(ValueError):
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(sb.writes, [])

    def test_sec_only_never_discovers_site(self):
        sb = Store(); sb.sites = []
        with self.providers(), patch.object(ir, 'ir_candidates', side_effect=AssertionError('web provider invoked')):
            result = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(result['failed'], 0)
        self.assertFalse(result['coverage']['scope']['web'])

    def test_expired_deadline_defers_without_provider_calls(self):
        sb = Store()
        with patch.object(ir, 'request', side_effect=AssertionError('deadline ignored')):
            result = ir.process_company(sb, 'TEST', 'Test', 1, deadline=time.monotonic() - 1)
        self.assertGreater(result['deferred'], 0)
        self.assertEqual(result['status'], 'partial')

    def test_sec_error_is_reported_and_retried_next_run(self):
        sb = Store()
        with self.providers(), patch.object(ir, '_sec_json', side_effect=RuntimeError('SEC down')):
            first = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertGreater(first['failed'], 0)
        self.assertGreater(first['coverage']['pending']['discovery'], 0)
        with self.providers():
            second = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(second['failed'], 0)
        self.assertEqual(second['coverage']['status'], 'complete')

    def test_repair_uses_original_and_does_not_rewrite_it_or_invent_dates(self):
        sb = Store(); did = existing(sb, 'https://ir.example.test/q1-2026-earnings.csv', text=False)
        raw = copy.deepcopy(sb.objects)
        with patch.object(ir, 'request', side_effect=AssertionError('provider called')):
            result = ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True)
        self.assertEqual(result['repaired'], 1)
        self.assertEqual(sb.objects['ir', sb.rows[did]['storage_path']], raw['ir', sb.rows[did]['storage_path']])
        self.assertIsNone(sb.rows[did].get('published'))
        self.assertEqual(sb.json['ir/TEST.json']['documents'][0]['extraction']['status'], 'verified')
        self.assertEqual(sb.json['ir/TEST.json']['coverage']['enumeration']['sec'], 'unverified')

    def test_legacy_text_path_is_unverified_until_read(self):
        sb = Store(); existing(sb, 'https://ir.example.test/q1-2026-earnings.csv')
        with self.providers():
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        row = sb.json['ir/TEST.json']['documents'][0]
        self.assertEqual(row['extraction']['status'], 'unverified')
        self.assertEqual(sb.json['ir/TEST.json']['coverage']['extraction']['unverified'], 1)

    def test_failed_archive_page_retries_and_discovers_children(self):
        sb = Store(); archive = 'https://ir.example.test/results/2026'
        def first(url, **kw):
            if url == archive:
                raise RuntimeError('archive timeout')
            return {'html': f'<a href="{archive}">2026 results</a>'}
        with self.providers(scrape=first):
            one = ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertGreater(one['failed'], 0)
        def second(url, **kw):
            return {'html':'<a href="/q1-2026-earnings.csv">Q1 earnings</a>'}
        with self.providers(scrape=second):
            two = ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertEqual(two['web'], 1)
        self.assertEqual(two['coverage']['status'], 'unverified')

    def test_publish_only_reconciles_all_rows_without_providers_or_repair(self):
        sb = Store()
        for i in range(205):
            existing(sb, f'https://ir.example.test/q1-2026-earnings-{i}.csv')
        with patch.object(ir, 'request', side_effect=AssertionError('provider called')):
            result = ir.process_company(sb, 'TEST', 'Test', 1, publish_only=True)
        self.assertEqual(len(sb.json['ir/TEST.json']['documents']), 205)
        self.assertEqual(result['coverage']['extraction']['unverified'], 205)
        self.assertEqual(result['repaired'], 0)
        self.assertEqual(sb.objects['ir', next(iter(sb.rows.values()))['storage_path']], b'Label,Value\nRevenue,42\n')

    def test_backfill_scope_never_narrows_on_maintenance(self):
        sb = Store()
        with self.providers():
            ir.process_company(sb, 'TEST', 'Test', 1, backfill=True, web=False)
            result = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(result['coverage']['scope']['history'], 'available_archive')
        self.assertEqual(result['coverage']['scope']['since_year'], 1900)
        self.assertEqual(result['coverage']['scope']['sec_since'], '1900-01-01')

    def test_sec_filing_date_is_published_even_for_item_701(self):
        sb = Store()
        f = {'a':'0000000001-26-000001', 'f':'8-K', 'd':'2026-10-02', 'p':'report.htm', 'i':'7.01'}
        index = '<html><table><tr><td>1</td><td>Slides</td><td><a href="ex99.htm">ex99.htm</a></td><td>EX-99.1</td></tr></table></html>'
        def sec_request(url, deadline=None, raw=False):
            return b'<html>Quarterly earnings presentation revenue 42</html>' if raw else index
        with self.providers(secdata=sub([f])), patch.object(ir, '_sec_request', side_effect=sec_request):
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        row = next(iter(sb.rows.values()))
        self.assertEqual(row['published'], '2026-10-02')
        self.assertIsNone(row['period_end'])

    def test_repair_dates_join_accession_and_keep_unknowns_missing(self):
        sb = Store()
        yes = existing(sb, 'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/ex99.htm')
        no = existing(sb, 'https://ir.example.test/2026-q1-earnings.csv')
        base_select = sb.select
        sb.select = lambda table, query='': [{'accession':'0000000001-26-000001','filed':'2026-10-02'}] if table == 'filings' else base_select(table, query)
        ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True, max_docs=0)
        self.assertEqual(sb.rows[yes]['published'], '2026-10-02')
        self.assertIsNone(sb.rows[no].get('published'))

    def test_repair_dates_accepts_only_parsed_sec_archive_urls(self):
        archive = '/Archives/edgar/data/1/000000000126000001/ex99.htm'
        for url in ('https://www.sec.gov' + archive, 'http://sec.gov' + archive + '?download=1',
                    'https://www.sec.gov:443' + archive, 'http://sec.gov:80' + archive,
                    'https://WWW.SEC.GOV' + archive + '#results'):
            with self.subTest(url=url):
                sb = Store()
                row = {'id': 'document', 'ticker': 'TEST', 'url': url, 'title': 'Earnings'}
                with patch.object(sb, 'select', return_value=[{'accession': '0000000001-26-000001', 'filed': '2026-10-02'}]):
                    self.assertEqual(ir._repair_dates(sb, 'TEST', [row]), 1)
                self.assertEqual(sb.rows['document']['published'], '2026-10-02')
                self.assertEqual(sb.rows['document']['url'], url)

    def test_repair_dates_rejects_lookalikes_embedded_urls_and_invalid_paths(self):
        archive = '/Archives/edgar/data/1/000000000126000001/ex99.htm'
        urls = ('https://notsec.gov' + archive,
                'https://sec.gov.evil.example' + archive,
                'https://evil.example/?x=sec.gov' + archive,
                'https://evil.example/#https://sec.gov' + archive,
                'https://sec.gov@evil.example' + archive,
                'https://evil.example/sec.gov' + archive,
                'https://www.sec.gov/other' + archive,
                'https://www.sec.gov' + archive.replace('/Archives/', '/archives/'),
                'https://www.sec.gov' + archive.replace('/ex99.htm', '/../other.htm'),
                'https://www.sec.gov' + archive.replace('/ex99.htm', '/%2e%2e/other.htm'),
                'https://www.sec.gov' + archive.replace('/ex99.htm', '/' + chr(92) + '..' + chr(92) + 'other.htm'),
                'https://www.sec.gov' + archive.replace('/ex99.htm', '/%5c..%5cother.htm'),
                'https://www.sec.gov:bad' + archive,
                'https://www.sec.gov:8443' + archive,
                'https://www.sec.gov:65536' + archive,
                'https://www.sec.gov:80' + archive,
                'http://www.sec.gov:443' + archive,
                'https://user@www.sec.gov' + archive,
                'ftp://www.sec.gov' + archive,
                '//www.sec.gov' + archive,
                'https://[invalid', '', None)
        for url in urls:
            with self.subTest(url=url):
                sb = Store()
                row = {'id': 'document', 'ticker': 'TEST', 'url': url}
                with patch.object(sb, 'select', return_value=[{'accession': '0000000001-26-000001', 'filed': '2026-10-02'}]):
                    self.assertEqual(ir._repair_dates(sb, 'TEST', [row]), 0)
                self.assertIsNone(row.get('published'))
                self.assertEqual(sb.writes, [])

    def test_shared_client_is_not_mutated_with_worker_lease(self):
        sb = Store()
        with self.providers():
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertFalse(hasattr(sb, '_aggregate_lease'))
        self.assertEqual(sb.leases, {})

    def test_provider_timeout_is_bounded_and_single_attempt(self):
        calls = []
        def request(url, **kw):
            calls.append(kw)
            return b'data'
        with patch.object(ir, 'request', side_effect=request):
            ir.download('https://example.test/file.csv', deadline=time.monotonic() + 0.5)
        self.assertLessEqual(calls[0]['timeout'], 0.5)
        self.assertEqual(calls[0]['retries'], 1)

    def test_failed_tasks_stop_at_retry_bound_and_remain_failed(self):
        sb = Store()
        with self.providers(), patch.object(ir, '_sec_json', side_effect=RuntimeError('SEC down')):
            for _ in range(4):
                result = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(result['failed'], 0)  # no fourth provider attempt
        self.assertEqual(result['coverage']['failed']['discovery'], 1)
        self.assertEqual(result['coverage']['status'], 'partial')

    def test_query_ids_and_trailing_slashes_survive_discovery(self):
        sb = Store(); sb.sites[0]['ir_url'] += '/'
        visited = []
        def scrape(url, **kw):
            visited.append(url)
            return {'html':'<a href="/news/press-release-details?id=122500">Q1 2026 results</a>'}
        with self.providers(scrape=scrape), patch.object(ir, 'download', return_value=b'<html>Quarterly earnings revenue 42</html>'):
            ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertIn('https://ir.example.test/', visited)
        self.assertEqual(next(iter(sb.rows.values()))['url'], 'https://ir.example.test/news/press-release-details?id=122500')

    def test_coverage_index_includes_zero_document_universe_members(self):
        sb = Store(); existing(sb, 'https://ir.example.test/q1.csv')
        with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]):
            summary = kpitools.build_index(sb)
        self.assertIn('EMPTY', sb.json['meta/coverage_ir.json']['companies'])
        self.assertEqual(summary['companies'], 2)
        self.assertEqual(sb.json['meta/coverage_ir.json']['companies']['TEST']['coverage']['status'], 'unverified')

    def test_repair_upserts_preserve_required_columns_and_batch_dates(self):
        sb = Store()
        for i in range(20):
            existing(sb, f'https://www.sec.gov/Archives/edgar/data/1/000000000126000001/ex99-{i}.htm', text=False)
        base_select, base_upsert = sb.select, sb.upsert
        date_batches = []
        def select(table, query=''):
            return [{'accession':'0000000001-26-000001','filed':'2026-10-02'}] if table == 'filings' else base_select(table, query)
        def upsert(table, rows, *args, **kw):
            rows = list(rows)
            if table == 'ir_documents':
                for row in rows:
                    if not row.get('ticker') or not row.get('url'):
                        raise ValueError('NOT NULL ticker/url')
                date_batches.append(len(rows))
            return base_upsert(table, rows, *args, **kw)
        sb.select, sb.upsert = select, upsert
        result = ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True, max_docs=1)
        self.assertEqual(result['dates_repaired'], 20)
        self.assertEqual(result['repaired'], 1)
        self.assertIn(20, date_batches)
        self.assertEqual(result['failed'], 0)

    def test_limited_repair_preserves_previous_text_bytes(self):
        sb = Store(); did = existing(sb, 'https://ir.example.test/q1.csv')
        path = sb.rows[did]['text_path']; original = sb.objects['ir', path]
        partial = {'kind':'sheets','sheets':{'csv':[['partial']]},'extraction':{'status':'limited','nonempty':True,'limits':['deadline'],'errors':[]}}
        with patch.object(ir, 'extract_text', return_value=partial):
            result = ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True)
        self.assertEqual(sb.objects['ir', path], original)
        self.assertEqual(result['coverage']['extraction']['limited'], 1)

    def test_explicit_repair_rechecks_legacy_verified_workbook_once(self):
        import openpyxl
        sb = Store(); did = existing(sb, 'https://ir.example.test/q1.xlsx')
        row = sb.rows[did]; row['ext'] = '.xlsx'
        book = openpyxl.Workbook(); book.active.cell(602, 2, 99)
        raw = io.BytesIO(); book.save(raw)
        sb.objects['ir', row['storage_path']] = raw.getvalue()
        old_path = row['text_path']; old_text = sb.objects['ir', old_path]
        state = ir._read_checkpoint(sb, 'TEST')
        state['outcomes'][did] = {'status':'verified','nonempty':True,'limits':[],'errors':[]}
        state['queues']['extractions'][did] = {'id':did,'status':'done','attempts':0}
        ir._save_checkpoint(sb, state)
        result = ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True)
        self.assertEqual(result['repaired'], 1)
        text = json.loads(sb.objects['ir', sb.rows[did]['text_path']])
        self.assertEqual(text['sheets']['Sheet'][601][1], 99)
        self.assertEqual(sb.objects['ir', old_path], old_text)
        self.assertEqual(sb.objects['ir', row['storage_path']], raw.getvalue())
        repeated = ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True)
        self.assertEqual(repeated['repaired'], 0)

    def test_new_workbook_version_retries_old_block_once_then_keeps_retry_bound(self):
        sb = Store(); did = existing(sb, 'https://ir.example.test/q1.xls')
        sb.rows[did]['ext'] = '.xls'
        state = ir._read_checkpoint(sb, 'TEST')
        state['outcomes'][did] = {'status':'failed','nonempty':False,'limits':[],'errors':['corrupt workbook']}
        state['queues']['extractions'][did] = {'id':did,'status':'blocked','attempts':3}
        ir._save_checkpoint(sb, state)
        attempts = [ir.process_company(sb, 'TEST', 'Test', 1, repair_existing=True)['extraction_failed'] for _ in range(4)]
        self.assertEqual(attempts, [1, 1, 1, 0])

    def test_html_error_page_cannot_be_verified_as_pdf(self):
        sb = Store()
        with self.providers({'https://ir.example.test/q1-2026-results.pdf':'Q1 results'}), patch.object(ir, 'download', return_value=b'<html>Access Denied</html>'):
            result = ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertEqual(len(sb.rows), 0)
        self.assertEqual(result['download_failed'], 1)

    def test_sec_error_html_is_not_a_completed_index(self):
        sb = Store(); f = {'a':'0000000001-26-000001', 'f':'8-K', 'd':'2026-10-02', 'p':'report.htm', 'i':'7.01'}
        with self.providers(secdata=sub([f])), patch.object(ir, '_sec_request', return_value='<html>Rate limited</html>'):
            result = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(result['coverage']['enumeration']['sec'], 'failed')

    def test_strict_checkpoint_read_accepts_only_explicit_missing_object(self):
        sb = object.__new__(common.Supabase); sb.url = 'https://storage.example'; sb.key = 'fake'
        with patch.object(ir, 'request', side_effect=common.HTTPError(400, '{"statusCode":"404","error":"not_found","message":"Object not found"}')):
            self.assertEqual(ir._read_checkpoint(sb, 'TEST')['ticker'], 'TEST')
        with patch.object(ir, 'request', side_effect=common.HTTPError(400, '{"message":"bad credentials"}')):
            with self.assertRaises(common.HTTPError):
                ir._read_checkpoint(sb, 'TEST')

    def test_invalid_task_payload_refuses_checkpoint_replacement(self):
        sb = Store()
        with self.providers():
            ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        state = json.loads(sb.objects['raw','ir/checkpoints/TEST.json'])
        state['queues']['documents']['bad'] = {'status':'pending','attempts':0}
        sb.objects['raw','ir/checkpoints/TEST.json'] = json.dumps(state).encode()
        sb.writes.clear()
        with self.providers(), self.assertRaises(ValueError):
            ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertEqual(sb.writes, [])

    def coverage_snapshot(self):
        return {
            'generated':'2026-11-01T10:00:00Z',
            'kpi_counts_as_of':'2026-10-31T09:00:00Z',
            'segment_counts_as_of':'2026-10-30T08:00:00Z',
            'summary':{'companies':2, 'documents':1, 'bytes':100, 'kpis':227953, 'kpi_values':3944368,
                       'companies_with_docs':1, 'companies_with_kpis':2, 'companies_with_segments':1, 'segment_tables':1},
            'companies':{
                'TEST':{'docs':1, 'bytes':100, 'types':{'release':1}, 'kpis':2, 'kpi_values':3,
                        'segment_tables':1, 'segment_ties':1, 'coverage':{'status':'unverified','total_documents':1}},
                'EMPTY':{'docs':0, 'bytes':0, 'types':{}, 'kpis':227951, 'kpi_values':3944365,
                         'segment_tables':0, 'segment_ties':0, 'coverage':{'status':'complete','total_documents':0},
                         'documents_as_of':'2026-11-01T10:00:00Z', 'custom_note':'preserve this'}
            }
        }

    def test_incremental_coverage_is_independent_of_millions_of_kpi_rows(self):
        sb = Store(); sb.json['meta/coverage_ir.json'] = self.coverage_snapshot()
        before = copy.deepcopy(sb.json['meta/coverage_ir.json']); queries = []
        def select(table, query=''):
            queries.append((table, query))
            if table != 'ir_documents' or 'ticker=eq.TEST' not in query:
                raise AssertionError('No KPI or unfiltered document scan is permitted')
            return [{'ticker':'TEST','doc_type':'release','bytes':200}, {'ticker':'TEST','doc_type':'supplement','bytes':300}]
        def get_json(path):
            if path != 'meta/coverage_ir.json':
                raise AssertionError('No manifest or segment refresh belongs in an incremental IR update')
            return copy.deepcopy(sb.json.get(path))
        sb.select, sb.get_json = select, get_json
        with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]):
            summary = kpitools.build_index(sb, coverage_updates={'TEST':{'status':'partial','total_documents':2}})
        result = sb.json['meta/coverage_ir.json']
        self.assertEqual(queries, [('ir_documents','select=ticker,doc_type,bytes&ticker=eq.TEST')])
        self.assertEqual(result['companies']['TEST']['types'], {'release':1,'supplement':1})
        self.assertEqual(result['companies']['TEST']['docs'], 2)
        self.assertEqual(result['companies']['TEST']['bytes'], 500)
        self.assertEqual(result['companies']['TEST']['kpis'], 2)
        self.assertEqual(result['companies']['TEST']['kpi_values'], 3)
        self.assertEqual(result['companies']['TEST']['segment_tables'], 1)
        self.assertEqual(result['companies']['EMPTY'], before['companies']['EMPTY'])
        self.assertEqual(result['kpi_counts_as_of'], '2026-10-31T09:00:00Z')
        self.assertEqual(result['segment_counts_as_of'], '2026-10-30T08:00:00Z')
        self.assertEqual(summary['kpis'], 227953)
        self.assertEqual(summary['kpi_values'], 3944368)
        self.assertEqual(summary['documents'], 2)
        self.assertEqual(summary['bytes'], 500)

    def test_incremental_coverage_missing_or_incomplete_prior_fails_before_any_scan(self):
        valid = self.coverage_snapshot()
        missing_company = copy.deepcopy(valid); del missing_company['companies']['EMPTY']
        missing_count = copy.deepcopy(valid); del missing_count['companies']['TEST']['kpi_values']
        inconsistent = copy.deepcopy(valid); inconsistent['summary']['kpi_values'] = 0
        for prior in (None, {}, missing_company, missing_count, inconsistent):
            with self.subTest(prior=prior):
                sb = Store(); sb.json['meta/coverage_ir.json'] = prior
                sb.select = lambda *args: (_ for _ in ()).throw(AssertionError('Do not scan to repair an incomplete snapshot'))
                with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]), self.assertRaisesRegex(ValueError, 'prior|snapshot'):
                    kpitools.build_index(sb, coverage_updates={'TEST':{'status':'partial','total_documents':1}})
                self.assertEqual(sb.writes, [])
                self.assertEqual(sb.json['meta/coverage_ir.json'], prior)

    def test_incremental_legacy_asof_fallback_does_not_advance_on_later_runs(self):
        sb = Store(); prior = self.coverage_snapshot()
        del prior['kpi_counts_as_of']; del prior['segment_counts_as_of']
        sb.json['meta/coverage_ir.json'] = prior
        sb.select = lambda *args: (_ for _ in ()).throw(AssertionError('Empty update must issue no relational query'))
        with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]):
            kpitools.build_index(sb, coverage_updates={})
            kpitools.build_index(sb, coverage_updates={})
        result = sb.json['meta/coverage_ir.json']
        self.assertEqual(result['kpi_counts_as_of'], '2026-11-01T10:00:00Z')
        self.assertEqual(result['segment_counts_as_of'], '2026-11-01T10:00:00Z')
        self.assertEqual(result['summary']['kpi_values'], 3944368)

    def test_incremental_coverage_marks_document_count_mismatch_unverified(self):
        sb = Store(); sb.json['meta/coverage_ir.json'] = self.coverage_snapshot()
        sb.select = lambda table, query: []
        with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]):
            kpitools.build_index(sb, coverage_updates={'TEST':{'status':'complete','total_documents':99}})
        company = sb.json['meta/coverage_ir.json']['companies']['TEST']
        self.assertEqual(company['docs'], 0)
        self.assertEqual(company['coverage']['status'], 'unverified')
        self.assertIn('manifest row count differs from stored documents', company['coverage']['limits'])

    def test_explicit_full_coverage_rebuild_still_counts_source_rows(self):
        sb = Store()
        sb.json['meta/segments.json'] = {'generated':'2026-10-30T08:00:00Z','companies':{'TEST':{'tables':2,'recon_ok':1}}}
        rows = {'ir_documents':[{'ticker':'TEST','doc_type':'release','bytes':100}],
                'kpi_definitions':[{'ticker':'TEST','kpi_id':'revenue'}],
                'kpi_values':[{'ticker':'TEST'},{'ticker':'TEST'}]}
        sb.select = lambda table, query='':copy.deepcopy(rows.get(table, []))
        with patch('etl.universe.UNIVERSE', [('TEST',), ('EMPTY',)]):
            summary = kpitools.build_index(sb)
        self.assertEqual(summary['documents'], 1)
        self.assertEqual(summary['kpis'], 1)
        self.assertEqual(summary['kpi_values'], 2)
        self.assertEqual(summary['segment_tables'], 2)
        self.assertEqual(summary['companies'], 2)
        self.assertEqual(sb.json['meta/coverage_ir.json']['segment_counts_as_of'], '2026-10-30T08:00:00Z')
        self.assertTrue(sb.json['meta/coverage_ir.json']['kpi_counts_as_of'])

    def test_rotation_skips_aggregate_work_after_deadline(self):
        sb = Store()
        with patch.object(jobs, 'UNIVERSE', [('TEST',)]), patch.object(jobs, 'ir_job', return_value=({}, {'TEST':'skipped: time budget'})), patch.object(kpitools, 'build_index', side_effect=AssertionError('budget exhausted')):
            done, failed = jobs.ir_rotation_job(sb, per_run=1, time_budget=0)
        self.assertTrue(sb.json['meta/ir_rotation.json']['coverage_pending'])
        self.assertEqual(failed['coverage_index'], 'skipped: time budget')

    def test_provider_free_republication_preserves_existing_scope(self):
        sb = Store()
        with self.providers():
            before = ir.process_company(sb, 'TEST', 'Test', 1, web=False, backfill=True)
        after = ir.process_company(sb, 'TEST', 'Test', 1, publish_only=True)
        self.assertEqual(after['coverage']['scope'], before['coverage']['scope'])

    def test_new_sec_filings_are_polled_while_historical_index_is_blocked(self):
        sb = Store(); actual_date = dt.date
        day = [actual_date(2026, 11, 4)]
        class ClockDate(actual_date):
            @classmethod
            def today(cls):
                return day[0]
        stamp = lambda: day[0].isoformat() + 'T12:00:00Z'
        old = {'a':'0000000001-26-000001','f':'8-K','d':day[0].isoformat(),'p':'report.htm','i':'7.01'}
        with patch.object(ir.dt, 'date', ClockDate), patch.object(ir, 'now_iso', side_effect=stamp):
            with self.providers(secdata=sub([old])), patch.object(ir, '_sec_request', side_effect=RuntimeError('historical index down')):
                for _ in range(3):
                    ir.process_company(sb, 'TEST', 'Test', 1, web=False)
            day[0] += dt.timedelta(days=1)
            new = dict(old, a='0000000001-26-000002', d=day[0].isoformat())
            index = '<table><tr><td>1</td><td>Slides</td><td><a href="ex99.htm">Slides</a></td><td>EX-99.1</td></tr></table>'
            with self.providers(secdata=sub([new, old])), patch.object(ir, '_sec_request', side_effect=lambda url, **kw: b'<html>Revenue 42</html>' if kw.get('raw') else index):
                result = ir.process_company(sb, 'TEST', 'Test', 1, web=False)
        self.assertEqual(result['sec'], 1)
        self.assertEqual(next(iter(sb.rows.values()))['published'], new['d'])
        self.assertEqual(result['coverage']['enumeration']['sec'], 'failed')

    def test_noop_web_visits_do_not_extend_poll_freshness(self):
        sb = Store()
        first_stamp = ir.now_iso()
        later_stamp = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.providers(), patch.object(ir, 'now_iso', return_value=first_stamp):
            ir.process_company(sb, 'TEST', 'Test', 1)
        first = json.loads(sb.objects['raw','ir/checkpoints/TEST.json'])
        with self.providers(), patch.object(ir, 'now_iso', return_value=later_stamp):
            ir.process_company(sb, 'TEST', 'Test', 1)
        second = json.loads(sb.objects['raw','ir/checkpoints/TEST.json'])
        self.assertEqual(second['web_checked_at'], first['web_checked_at'])
        self.assertEqual(second['web_polled_at'], first['web_polled_at'])

    def test_web_root_refreshes_despite_a_blocked_historical_page(self):
        sb = Store(); root = sb.sites[0]['ir_url']; visited = []
        def failing(url, **kw):
            visited.append(url)
            if url != root:
                raise RuntimeError('old archive down')
            return {'html':'<a href="/results/old">old results</a>'}
        with self.providers(scrape=failing), patch.object(ir, 'now_iso', return_value='2000-01-01T00:00:00Z'):
            for _ in range(3):
                ir.process_company(sb, 'TEST', 'Test', 1)
        visited.clear()
        with self.providers(scrape=lambda url, **kw: (visited.append(url) or {'html':'<a href="/q1-2026-results.csv">Q1 results</a>'})):
            result = ir.process_company(sb, 'TEST', 'Test', 1)
        self.assertIn(root, visited)
        self.assertEqual(result['web'], 1)
        self.assertEqual(result['coverage']['enumeration']['web'], 'failed')

    def test_blocked_html_download_falls_back_to_firecrawl_copy(self):
        from etl.common import HTTPError
        with patch.object(ir, 'request', side_effect=HTTPError(403, 'Access Denied')), \
             patch.object(ir, '_scrape', return_value={'html': '<p>Q3 results</p>'}) as sc, \
             patch.dict(os.environ, {'FIRECRAWL_API_KEY': 'k'}):
            body = ir.download('https://ir.example.test/news-releases/q3-results')
        sc.assert_called_once()
        self.assertTrue(body.startswith(b'<!doctype html>'))
        self.assertEqual(ir.sniff_ext(body, 'https://ir.example.test/news-releases/q3-results'), '.html')
        with patch.object(ir, 'request', side_effect=HTTPError(403, 'Access Denied')), patch.object(ir, '_scrape') as sc2, \
             patch.dict(os.environ, {'FIRECRAWL_API_KEY': 'k'}):
            with self.assertRaises(HTTPError):
                ir.download('https://ir.example.test/q3-deck.pdf')
        sc2.assert_not_called()

    def test_refused_pdf_is_stored_as_a_marked_text_only_copy(self):
        from etl.common import HTTPError
        sb = Store()
        url = 'https://ir.example.test/q3-2026-earnings-presentation.pdf'
        text = '<!doctype html>\n<html><body><pre>Q3 2026 results revenue 42</pre></body></html>'.encode()
        with self.providers({url: 'Q3 2026 earnings presentation'}), \
             patch.object(ir, 'download', side_effect=HTTPError(403, 'Access Denied')), \
             patch.object(ir, 'text_copy', return_value=text) as tc:
            result = ir.process_company(sb, 'TEST', 'Test', 1)
        tc.assert_called_once()
        self.assertEqual(result['web'], 1)
        row = next(iter(sb.rows.values()))
        self.assertTrue((row.get('title') or '').startswith(ir.TEXT_COPY_PREFIX))
        self.assertEqual(row.get('ext'), '.html')

    def test_blocked_refusals_get_one_more_attempt_once(self):
        state = {'queues': {'documents': {
            'https://ir.example.test/news/q3-results': {'status': 'blocked', 'source': 'web', 'ext_hint': '.html', 'attempts': 3, 'error': 'HTTP 403: Access Denied'},
            'https://ir.example.test/q3.pdf': {'status': 'blocked', 'source': 'web', 'ext_hint': '.pdf', 'attempts': 3, 'error': 'Downloaded HTML instead of the linked document'},
            'https://ir.example.test/q3.xlsx': {'status': 'blocked', 'source': 'web', 'ext_hint': '.xlsx', 'attempts': 3, 'error': 'HTTP 403: Access Denied'},
            'https://ir.example.test/gone': {'status': 'blocked', 'source': 'web', 'ext_hint': '.html', 'attempts': 3, 'error': 'HTTP 404: Not Found'}}}}
        self.assertEqual(ir.requeue_blocked(state), 2)
        q = state['queues']['documents']
        self.assertEqual([u for u, v in q.items() if v['status'] == 'retry'], ['https://ir.example.test/news/q3-results', 'https://ir.example.test/q3.pdf'])
        self.assertEqual(q['https://ir.example.test/q3.pdf']['attempts'], ir.MAX_ATTEMPTS - 1)
        q['https://ir.example.test/q3.pdf']['status'] = 'blocked'
        self.assertEqual(ir.requeue_blocked(state), 0)

    def test_web_lane_skips_sec_and_leaves_it_open(self):
        sb = Store()
        new = 'https://ir.example.test/q3-2026-results-web.csv'
        with self.providers({new:'Q3 2026 earnings'}), patch.object(ir, '_enumerate_sec') as enum_sec:
            result = ir.process_company(sb, 'TEST', 'Test', 1, sec=False)
        enum_sec.assert_not_called()
        self.assertEqual(result['web'], 1)
        self.assertNotEqual(result['coverage']['enumeration']['sec'], 'complete')

    def test_new_documents_advance_alongside_a_repair_backlog(self):
        sb = Store()
        for i in range(3):
            existing(sb, f'https://ir.example.test/q1-2025-old-{i}.csv', text=False)
        new = 'https://ir.example.test/q3-2026-results-new.csv'
        with self.providers({new:'Q3 2026 earnings'}):
            result = ir.process_company(sb, 'TEST', 'Test', 1, max_docs=2)
        self.assertEqual(result['web'], 1)
        self.assertEqual(result['repaired'], 1)
        self.assertIn(ir.doc_id(new), sb.rows)
        self.assertEqual(result['coverage']['pending']['extractions'], 2)

    def test_fresh_filing_index_precedes_old_archives_and_old_indices(self):
        sb = Store(); state = ir._read_checkpoint(sb, 'TEST')
        state['scope'] = {'web':False,'sec':True,'since_year':1900,'sec_since':'1900-01-01','history':'available_archive'}
        older = {'a':'0000000001-20-000001','f':'8-K','d':'2020-01-01','p':'report.htm','i':'7.01'}
        newer = dict(older, a='0000000001-26-000002', d='2026-11-01')
        ir._enqueue(state, 'filings', 'old', filing=older, cik=1)
        ir._enqueue(state, 'filings', 'new', filing=newer, cik=1)
        ir._enqueue(state, 'submissions', 'https://data.sec.gov/submissions/archive.json', url='https://data.sec.gov/submissions/archive.json', cik=1, root=False)
        ir._save_checkpoint(sb, state)
        index = '<table><tr><td>1</td><td>Slides</td><td><a href="ex99.htm">Slides</a></td><td>EX-99.1</td></tr></table>'
        with self.providers(), patch.object(ir, '_sec_request', side_effect=lambda url, **kw:b'<html>Revenue 42</html>' if kw.get('raw') else index):
            result = ir.process_company(sb, 'TEST', 'Test', 1, web=False, max_pages=2, max_docs=1)
        self.assertEqual(result['sec'], 1)
        self.assertIn('/000000000126000002/', next(iter(sb.rows.values()))['url'])

    def test_job_propagates_partial_and_rotation_retries_skipped(self):
        sb = Store()
        partial = {'failed': 1, 'deferred': 2, 'status':'partial', 'coverage':{'status':'partial'}}
        with patch.object(jobs, 'UNIVERSE', [('TEST', 1, 'Test', '')]), patch.object(jobs, 'BY_TICKER', {'TEST':{'name':'Test','cik':1}}), patch.object(ir, 'process_company', return_value=partial), patch.object(common, 'publish_status'):
            done, failed = jobs.ir_job(sb, ['TEST'], workers=1, time_budget=10)
        self.assertIn('TEST', failed)
        self.assertIn('TEST', done)
        self.assertEqual(sb.updates[-1][2]['status'], 'partial')
        with patch.object(jobs, 'UNIVERSE', [('TEST',), ('NEXT',)]), patch.object(jobs, 'ir_job', return_value=({}, {'TEST':'skipped: time budget'})), patch.object(kpitools, 'build_index'):
            jobs.ir_rotation_job(sb, per_run=1)
        self.assertIn('TEST', sb.json['meta/ir_rotation.json']['retry'])


class DeadlineIOTests(unittest.TestCase):
    def client(self):
        sb = object.__new__(common.Supabase)
        sb.url, sb.key = 'https://storage.example', 'fake'
        return sb

    def test_actual_ir_storage_and_lease_requests_use_remaining_time_once(self):
        calls = []
        def request(url, **kwargs):
            calls.append((url, kwargs))
            return 'true' if '/rpc/' in url else '[]' if '/rest/' in url else b'{}'
        with patch.object(ir, 'request', side_effect=request):
            sb = ir.bounded_client(self.client(), time.monotonic() + 0.5)
            sb.rpc('acquire_etl_lease', {'lock_name':'test'})
            sb.put_object('raw', 'ir/checkpoints/TEST.json', '{}')
            sb.get_object('raw', 'ir/checkpoints/TEST.json')
            sb.put_json('ir/TEST.json', {'documents':[]})
            sb.select('ir_documents')
            sb.upsert('ir_documents', [{'id':'x','ticker':'TEST','url':'https://example.test'}], 'id')
        self.assertEqual(len(calls), 6)
        for url, kwargs in calls:
            self.assertLessEqual(kwargs['timeout'], 0.5, url)
            self.assertEqual(kwargs['retries'], 1, url)

    def test_expired_rotation_budget_starts_no_lease_or_storage_request(self):
        calls = []
        with patch.object(ir, 'request', side_effect=lambda *a, **k: calls.append(a)), patch.object(common, 'request', return_value='true'), self.assertRaises(ir.DeadlineReached):
            jobs.ir_rotation_job(self.client(), time_budget=0)
        self.assertEqual(calls, [])

    def test_long_paginated_select_stops_at_absolute_io_deadline(self):
        clock, calls = [0.0], []
        def request(url, **kwargs):
            calls.append(url); clock[0] = 2.0
            return json.dumps([{'id':i} for i in range(1000)])
        with patch.object(ir.time, 'monotonic', side_effect=lambda:clock[0]), patch.object(ir, 'request', side_effect=request):
            sb = ir.bounded_client(self.client(), 1.0)
            with self.assertRaises(ir.DeadlineReached):
                sb.select('ir_documents')
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()


class SiteDiscoveryTests(unittest.TestCase):
    def test_investor_sites_rank_ahead_of_the_marketing_site(self):
        # Many www.okta.com hits must not outrank a single investor.okta.com hit.
        results = [{"url": "https://www.okta.com/products/"}, {"url": "https://www.okta.com/pricing/"},
                   {"url": "https://okta.com/"}, {"url": "https://www.okta.com/blog/"},
                   {"url": "https://investor.okta.com/news-releases"}]
        with patch.object(ir, "tavily", side_effect=[results, []]):
            self.assertEqual(ir.ir_candidates("Okta, Inc.", "OKTA")[0], "https://investor.okta.com")

    def test_asset_hosts_are_never_sites_and_main_site_is_last_resort(self):
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://image.roku.com/x/4Q25-Shareholder-Letter.pdf"}], []]):
            self.assertEqual(ir.ir_candidates("Roku, Inc.", "ROKU"), [])
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://www.cintas.com/about/"}], []]):
            self.assertEqual(ir.ir_candidates("Cintas Corporation", "CTAS"), ["https://www.cintas.com"])
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://www.mgic.com/about"}, {"url": "https://mtg.mgic.com/financial-information"}], []]):
            self.assertEqual(ir.ir_candidates("MGIC Investment Corp", "MTG"), ["https://mtg.mgic.com", "https://www.mgic.com"])
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://www.pginvestor.com/financial"}, {"url": "https://www.pg.com/"}], []]):
            self.assertEqual(ir.ir_candidates("Procter & Gamble Co", "PG")[0], "https://www.pginvestor.com")
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://www.technipfmc.com/en/investors/reports"}], []]):
            self.assertEqual(ir.ir_candidates("TechnipFMC plc", "FTI"), ["https://www.technipfmc.com/en/investors/"])
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://fiscal.ai/company/NYSE_STE/investor-relations"},
                                                      {"url": "https://steris.org.in/"}, {"url": "https://sterisplc.gcs-web.com/financial"}], []]):
            self.assertEqual(ir.ir_candidates("STERIS plc", "STE"), ["https://sterisplc.gcs-web.com", "https://steris.org.in"])
        with patch.object(ir, "tavily", side_effect=[[{"url": "https://newsroom.intel.com/a"}, {"url": "https://www.intel.com/"},
                                                      {"url": "https://www.intc.com/news-events"}], []]):
            self.assertEqual(ir.ir_candidates("Intel Corporation", "INTC")[0], "https://www.intc.com")

    def test_site_tier(self):
        for host, path, level in (("investor.okta.com", "/", 0), ("www.abbottinvestor.com", "/", 0), ("www.x.com", "/investors/a", 0),
                                  ("mtg.mgic.com", "/", 1), ("www.okta.com", "/", 2), ("okta.com", "/", 2),
                                  ("www.example.co.uk", "/", 2), ("example.co.uk", "/", 2),
                                  ("image.roku.com", "/", None), ("cdn.example.com", "/", None), ("shop.example.com", "/", None)):
            with self.subTest(host=host):
                self.assertEqual(ir._site_tier(host, path), level)
        self.assertEqual(ir._site_tier("www.intc.com", "/", "INTC", ["intel", "corporation"]), 0)
        self.assertEqual(ir._site_tier("www.intel.com", "/", "INTC", ["intel", "corporation"]), 2)
        self.assertEqual(ir._site_tier("www.okta.com", "/", "OKTA", ["okta"]), 2)  # ticker is the name: not evidence
        self.assertIsNone(ir._site_tier("image.roku.com", "/", "ROKU", ["roku"]))
        self.assertEqual(ir._site_tier("www.o.com", "/", "O"), 2)  # one- and two-letter tickers are not domain evidence
    def test_site_change_drops_unfinished_work_from_the_old_site(self):
        state = {"queues": {q: {} for q in ("submissions", "filings", "maps", "pages", "documents", "extractions")}}
        q = state["queues"]
        q["pages"]["https://www.okta.com/blog"] = {"url": "https://www.okta.com/blog", "status": "pending"}
        q["pages"]["https://www.okta.com/done"] = {"url": "https://www.okta.com/done", "status": "done"}
        q["pages"]["https://investor.okta.com/"] = {"url": "https://investor.okta.com/", "status": "pending"}
        q["maps"]["https://www.okta.com quarterly"] = {"url": "https://www.okta.com", "search": "quarterly", "status": "retry"}
        q["maps"]["site-search"] = {"url": "", "search": "site-search", "status": "pending"}
        q["documents"]["https://www.okta.com/a.pdf"] = {"url": "https://www.okta.com/a.pdf", "source": "web",
                                                         "source_page": "https://www.okta.com/blog", "status": "pending"}
        q["documents"]["https://www.sec.gov/x.htm"] = {"url": "https://www.sec.gov/x.htm", "source": "sec",
                                                        "source_page": "https://www.sec.gov/", "status": "pending"}
        self.assertEqual(ir._drop_other_site_work(state, "https://investor.okta.com"), 3)
        self.assertEqual(set(q["pages"]), {"https://www.okta.com/done", "https://investor.okta.com/"})
        self.assertEqual(set(q["maps"]), {"site-search"})
        self.assertEqual(set(q["documents"]), {"https://www.sec.gov/x.htm"})
