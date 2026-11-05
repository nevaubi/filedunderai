"""Read SEC IPO summary statistics as supplementary macro series.

This opt-in collector is not registered in the scheduled macro jobs. Only the
explicit Stats Table periods are normalized; source filenames never establish a
reporting period or release date. No company financial statement is populated.
"""
import calendar
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from .common import request, SEC_UA, sec_throttle

URL = 'https://www.sec.gov/files/sec-stats-ipos-20260922.xlsx'
SOURCE_PAGE = 'https://www.sec.gov/data-research/statistics-data-visualizations/initial-public-offerings-ipos'
REVIEWED_SHA256 = '8499e48223bb72d93cd5919a4f3b2f84776d020b120e3133d992841da5ba5450'
PUBLICATION_DATE = '2026-09-22'
NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
METRICS = (
    ('B', 'COUNT_TOTAL', 'Total number of IPOs'),
    ('C', 'COUNT_US', 'Number of IPOs by U.S. issuers'),
    ('D', 'COUNT_NON_US', 'Number of IPOs by non-U.S. issuers'),
    ('E', 'COUNT_CORPORATE', 'Number of IPOs by corporate issuers'),
    ('F', 'COUNT_SPAC', 'Number of IPOs by blank check/SPAC issuers'),
    ('G', 'COUNT_FUND', 'Number of IPOs by fund issuers (closed-end funds/BDCs)'),
    ('H', 'TOTAL_PROCEEDS_TOTAL', 'IPO total proceeds (US$ Millions)'),
    ('I', 'TOTAL_PROCEEDS_US', 'IPO total proceeds by U.S. issuers (US$ Millions)'),
    ('J', 'TOTAL_PROCEEDS_NON_US', 'IPO total proceeds by non-U.S. issuers (US$ Millions)'),
    ('K', 'TOTAL_PROCEEDS_CORPORATE', 'IPO total proceeds by corporate issuers (US$ Millions)'),
    ('L', 'TOTAL_PROCEEDS_SPAC', 'IPO total proceeds by blank check/SPAC issuers (US$ Millions)'),
    ('M', 'TOTAL_PROCEEDS_FUND', 'IPO total proceeds by fund issuers (closed-end funds/BDCs) (US$ Millions)'),
    ('N', 'AVERAGE_PROCEEDS_TOTAL', 'IPO average proceeds (US$ Millions)'),
    ('O', 'AVERAGE_PROCEEDS_US', 'IPO average proceeds by U.S. issuers (US $ Millions)'),
    ('P', 'AVERAGE_PROCEEDS_NON_US', 'IPO average proceeds by non-U.S. issuers (US$ Millions)'),
    ('Q', 'AVERAGE_PROCEEDS_CORPORATE', 'IPO average proceed by corporate issuers (US$ Millions)'),
    ('R', 'AVERAGE_PROCEEDS_SPAC', 'IPO average proceeds by blank check/SPAC issuers (US$ Millions)'),
    ('S', 'AVERAGE_PROCEEDS_FUND', 'IPO average proceeds by fund issuers (closed-end funds/BDCs) (US$ Millions)'),
    ('T', 'MEDIAN_PROCEEDS_TOTAL', 'IPO median proceeds (US$ Millions)'),
    ('U', 'MEDIAN_PROCEEDS_US', 'IPO median proceeds by U.S. issuers (US$ Millions)'),
    ('V', 'MEDIAN_PROCEEDS_NON_US', 'IPO median proceeds by non-U.S. issuers (US$ Millions)'),
    ('W', 'MEDIAN_PROCEEDS_CORPORATE', 'IPO median proceeds by corporate issuers (US$ Millions)'),
    ('X', 'MEDIAN_PROCEEDS_SPAC', 'IPO median proceeds by blank check/SPAC issuers (US$ Millions)'),
    ('Y', 'MEDIAN_PROCEEDS_FUND', 'IPO median proceeds by fund issuers (closed-end funds/BDCs) (US$ Millions)'),
)
MISSING = {'-', '–', '—', '*', '**', 'N/A', 'NA', '(D)'}


def _sheets(body):
    """Read only original cells/caches/formats; no formula evaluation or edits."""
    if not isinstance(body, bytes) or len(body) > 10 * 1024 * 1024:
        raise ValueError('Unexpected workbook size/type')
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        if sum(item.file_size for item in archive.infolist()) > 32 * 1024 * 1024:
            raise ValueError('Unexpected uncompressed workbook size')
        workbook = ET.fromstring(archive.read('xl/workbook.xml'))
        relations = {r.get('Id'): r.get('Target') for r in ET.fromstring(archive.read('xl/_rels/workbook.xml.rels'))}
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in item.findall('.//s:t', NS))
                       for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', NS)]
        formats = {0: 'General', 1: '0', 2: '0.00', 3: '#,##0', 4: '#,##0.00'}
        styles = []
        if 'xl/styles.xml' in archive.namelist():
            style = ET.fromstring(archive.read('xl/styles.xml'))
            formats.update({int(f.get('numFmtId')): f.get('formatCode') for f in style.findall('s:numFmts/s:numFmt', NS)})
            styles = [formats.get(int(xf.get('numFmtId', '0')), 'unsupported') for xf in style.findall('s:cellXfs/s:xf', NS)]
        result = {}
        for sheet in workbook.findall('s:sheets/s:sheet', NS):
            if sheet.get('name') not in ('Stats Table', 'Data Visual 1'):
                continue
            target = relations[sheet.get(REL)]
            path = target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl', target))
            if not path.startswith('xl/worksheets/'):
                raise ValueError('Unexpected source worksheet path')
            cells = {}
            for cell in ET.fromstring(archive.read(path)).findall('s:sheetData/s:row/s:c', NS):
                kind, address = cell.get('t', 'n'), cell.get('r')
                if address in cells: raise ValueError('Duplicate source cell')
                v = cell.find('s:v', NS)
                raw = v.text if v is not None else None
                if kind == 's': value = strings[int(raw)] if raw is not None else None
                elif kind == 'inlineStr': value = ''.join(t.text or '' for t in cell.findall('s:is//s:t', NS))
                else: value = raw
                cells[address] = {'value': value, 'kind': kind, 'formula': cell.find('s:f', NS) is not None,
                                  'number_format': styles[int(cell.get('s', '0'))] if styles else 'General'}
            result[sheet.get('name')] = cells
        if set(result) != {'Stats Table', 'Data Visual 1'}:
            raise ValueError('Required source sheets are missing')
        return result


def _period(label, as_of):
    annual = re.fullmatch(r'(\d{4})', label)
    quarterly = re.fullmatch(r'(\d{4}):Q([1-4])', label)
    if annual:
        year, month, frequency = int(annual[1]), 12, 'A'
    elif quarterly:
        year, month, frequency = int(quarterly[1]), int(quarterly[2]) * 3, 'Q'
    else:
        raise ValueError('Unreviewed period label: ' + label)
    if not 1900 <= year <= 2100: raise ValueError('Unreviewed period year')
    end = dt.date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    if end > as_of: raise ValueError('Reported period ends after the review date: ' + label)
    return frequency, end


def _printed(cell, count):
    if cell is None: return None, 'blank'
    if cell['formula']: raise ValueError('Source formula requires independent cached-value review')
    if cell['kind'] == 'e': raise ValueError('Workbook contains a source error in the data table')
    raw = cell['value']
    if raw is None or not str(raw).strip(): return None, 'blank'
    if str(raw).strip() in MISSING: return None, 'missing_marker'
    if cell['kind'] != 'n': raise ValueError('Unreviewed nonnumeric metric cell')
    try: value = Decimal(raw)
    except InvalidOperation as exc: raise ValueError('Invalid numeric source value') from exc
    if not value.is_finite() or value < 0: raise ValueError('Invalid negative/nonfinite IPO statistic')
    expected_format = '#,##0' if count else '#,##0.0'
    if cell['number_format'] != expected_format: raise ValueError('Unreviewed source number format')
    if count and value != value.to_integral_value(): raise ValueError('Fractional offering count')
    # This exact source format prints whole counts and one decimal in millions.
    # Preserve the original XML token alongside the printed observation.
    printed = value.quantize(Decimal('1') if count else Decimal('0.1'), rounding=ROUND_HALF_UP)
    return format(printed, 'f'), 'reported'


def parse_workbook(body, source_url=URL, as_of=None, since=2000):
    """Return the existing mstore collector contract, without storage mutations."""
    as_of = as_of or dt.datetime.now(dt.timezone.utc).date().isoformat()
    dt.date.fromisoformat(as_of)
    sheets = _sheets(body)
    table = sheets['Stats Table']
    if table.get('A1', {}).get('value') != 'Time Period': raise ValueError('Period header changed')
    for column, metric, label in METRICS:
        header = table.get(column + '1', {})
        if header.get('value') != label or header.get('formula'):
            raise ValueError('Source metric/unit header changed: ' + column)
    periods = {'A': [], 'Q': []}
    seen = set()
    for address, cell in sorted(table.items(), key=lambda pair: int(re.search(r'\d+', pair[0])[0])):
        if not re.fullmatch(r'A\d+', address) or address == 'A1': continue
        label = str(cell['value'] or '').strip()
        if not label: continue
        if cell['formula']: raise ValueError('Source period formula requires review')
        frequency, end = _period(label, as_of)
        if (frequency, end) in seen: raise ValueError('Duplicate period label')
        seen.add((frequency, end))
        periods[frequency].append((int(address[1:]), label, end))
    heading = sheets['Data Visual 1'].get('A1', {}).get('value') or ''
    match = re.fullmatch(r'IPOs: Number and Proceeds of Offerings \((\d{4}) - (\d{4}) and (\d{4}:Q[1-4]) - (\d{4}:Q[1-4])\)', heading)
    if not match or not all(periods.values()): raise ValueError('Source coverage heading requires review')
    if (periods['A'][0][1], periods['A'][-1][1], periods['Q'][0][1], periods['Q'][-1][1]) != match.groups():
        raise ValueError('Table periods disagree with explicit coverage heading')
    for frequency, rows in periods.items():
        serials = [int(label[:4]) * (4 if frequency == 'Q' else 1) + (int(label[-1]) - 1 if frequency == 'Q' else 0) for _, label, _ in rows]
        if serials != list(range(serials[0], serials[-1] + 1)):
            raise ValueError('Missing or unsorted source period row')
    original_sha = hashlib.sha256(body).hexdigest()
    publication = ({'date': PUBLICATION_DATE, 'source_url': SOURCE_PAGE, 'section': 'Citation',
                    'field': 'Publication Date', 'workbook_url': URL, 'workbook_sha256': REVIEWED_SHA256}
                   if source_url == URL and original_sha == REVIEWED_SHA256 else None)
    published = publication['date'] if publication else None
    if published and published > as_of:
        raise ValueError('Source publication occurs after the review date')
    release_note = ('Publication date 2026-09-22 is stated in the official landing-page Citation and is the ingestion vintage.'
                    if publication else 'No verified publication date for these source bytes; ingestion uses retrieval vintage.')
    out = []
    for column, metric, label in METRICS:
        count = metric.startswith('COUNT_')
        for frequency, rows in periods.items():
            observations, snapshot_rows, keys, cells = [], [], [], []
            for row, period_label, end in rows:
                if int(end[:4]) < since: continue
                address = column + str(row)
                cell = table.get(address)
                value, state = _printed(cell, count)
                if value is not None: observations.append((end, value))
                snapshot_rows.append([period_label, value if value is not None else (cell or {}).get('value') or ''])
                keys.append(end)
                cells.append({'cell': address, 'period_cell': 'A' + str(row), 'period_label': period_label,
                              'period_kind': frequency, 'period_end': end, 'raw_value': (cell or {}).get('value'),
                              'number_format': (cell or {}).get('number_format'), 'state': state})
            if not observations: continue
            meta = {'id': 'SEC_IPO:' + metric + ':' + frequency, 'source': 'SEC IPO summary statistics',
                    'provider': 'U.S. Securities and Exchange Commission',
                    'title': label + (' — annual' if frequency == 'A' else ' — quarterly'),
                    'units': 'Offerings' if count else 'US$ millions', 'frequency': frequency,
                    'seasonal_adj': None, 'geography': 'United States IPO market',
                    'source_url': SOURCE_PAGE, 'data_url': source_url, 'source_key': 'Stats Table!' + column + ':' + frequency,
                    'section': 'credit', 'release': 'SEC:IPO', 'group': 'sec_ipo', 'dp': 0 if count else 1,
                    'original_sha256': original_sha, 'original_bytes': len(body), 'source_column': column,
                    'source_label': label, 'source_range_heading': heading, 'supplementary_statistics': True,
                    'publication_evidence': publication,
                    'notes': 'Source-reported IPO statistics. Annual and quarterly periods remain separate. '
                             'Proceeds remain in the source unit, US$ millions; average and median proceeds are not additive. '
                             'Blank and masked/dash cells are missing, not zero. The workbook filename is not a release or period date. '
                             'U.S.-market IPOs are assigned by pricing date. U.S./non-U.S. issuers are classified by primary offices at offering. '
                             'SEC staff statistics use LSEG SDC Platinum and Dealogic ECM data. ' + release_note}
            snap = {'url': source_url, 'cols': ['Time Period', label], 'rows': snapshot_rows, 'keys': keys, 'hl': 1,
                    'source_sheet': 'Stats Table', 'original_sha256': original_sha, 'original_bytes': len(body),
                    'source_page': SOURCE_PAGE, 'publication_evidence': publication,
                    'cells': cells, 'source_range_heading': heading,
                    'numeric_policy': 'Observation strings follow the exact published number format; original cached XML tokens retained per cell.'}
            out.append({'meta': meta, 'obs': observations, 'published': published, 'snap': snap})
    return out


def collect(url=URL, since=2000, as_of=None, log=print):
    """Explicit fetch only; callers must separately review and authorize storage."""
    if not re.fullmatch(r'https://www\.sec\.gov/files/sec-stats-ipos-\d{8}\.xlsx', url):
        raise ValueError('Expected an explicit official SEC IPO workbook URL')
    sec_throttle()
    body = request(url, headers={'User-Agent': SEC_UA}, raw=True, timeout=90, retries=2)
    out = parse_workbook(body, source_url=url, as_of=as_of, since=since)
    log('sec_ipos: ' + str(len(out)) + ' supplementary series; ' + str(sum(len(row['obs']) for row in out)) + ' reported observations')
    return out
