"""Exact SEC source fixture; collector does not publish or infer current periods."""
import hashlib
import io
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
import zipfile

from etl import sec_ipos

FIXTURE = Path(__file__).parent / 'fixtures/sec_statistics/sec-stats-ipos-20260922.xlsx'
SHA = '8499e48223bb72d93cd5919a4f3b2f84776d020b120e3133d992841da5ba5450'
NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'


def changed_cell(raw, address, value=None, kind='inlineStr', formula=None):
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(output, 'w') as dest:
        for item in source.infolist():
            body = source.read(item.filename)
            if item.filename == 'xl/worksheets/sheet1.xml':
                sheet = ET.fromstring(body)
                cell = next(c for c in sheet.iter(NS + 'c') if c.get('r') == address)
                for child in list(cell): cell.remove(child)
                cell.set('t', kind)
                if formula is not None: ET.SubElement(cell, NS + 'f').text = formula
                if value is not None:
                    if kind == 'inlineStr': ET.SubElement(ET.SubElement(cell, NS + 'is'), NS + 't').text = value
                    else: ET.SubElement(cell, NS + 'v').text = value
                body = ET.tostring(sheet)
            dest.writestr(item, body)
    return output.getvalue()


class IpoSourceCollector(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.raw = FIXTURE.read_bytes()

    def collect(self, raw=None, **kwargs):
        return sec_ipos.parse_workbook(raw or self.raw, source_url=sec_ipos.URL, as_of='2026-11-05', **kwargs)

    def test_exact_fixture_separates_annual_and_quarterly_from_explicit_periods(self):
        self.assertEqual(hashlib.sha256(self.raw).hexdigest(), SHA)
        out = self.collect()
        self.assertEqual(len(out), 48)
        self.assertEqual(len({r['meta']['id'] for r in out}), 48)
        by = {r['meta']['id']: r for r in out}
        annual = by['SEC_IPO:COUNT_TOTAL:A']
        quarter = by['SEC_IPO:COUNT_TOTAL:Q']
        self.assertEqual((len(annual['obs']), annual['obs'][0], annual['obs'][-1]),
                         (26, ('2000-12-31', '459'), ('2025-12-31', '375')))
        self.assertEqual((len(quarter['obs']), quarter['obs'][-1]), (106, ('2026-06-30', '109')))
        self.assertTrue(all(d <= '2026-06-30' for d,v in quarter['obs']))
        self.assertEqual(quarter['published'], '2026-09-22')
        self.assertEqual(quarter['meta']['source_url'], sec_ipos.SOURCE_PAGE)
        evidence = quarter['snap']['publication_evidence']
        self.assertEqual(evidence['date'], quarter['published'])
        self.assertEqual(evidence['source_url'], 'https://www.sec.gov/data-research/statistics-data-visualizations/initial-public-offerings-ipos')
        self.assertEqual(evidence['section'], 'Citation')
        self.assertEqual(evidence['field'], 'Publication Date')
        self.assertEqual(evidence['workbook_sha256'], SHA)
        self.assertEqual(quarter['meta']['publication_evidence'], evidence)
        self.assertIn('pricing date', quarter['meta']['notes'])
        self.assertIn('primary offices', quarter['meta']['notes'])
        self.assertIn('LSEG SDC Platinum', quarter['meta']['notes'])
        self.assertIn('Dealogic ECM', quarter['meta']['notes'])
        self.assertEqual(quarter['meta']['original_sha256'], SHA)
        self.assertIn('2026:Q2', quarter['meta']['source_range_heading'])
        self.assertEqual(sum(len(r['obs']) for r in out), 3080)

    def test_zero_is_preserved_and_dash_is_missing_with_original_cell_evidence(self):
        by = {r['meta']['id']:r for r in self.collect()}
        self.assertIn(('2000-12-31','0'), by['SEC_IPO:COUNT_SPAC:A']['obs'])
        mean = by['SEC_IPO:AVERAGE_PROCEEDS_SPAC:A']
        self.assertNotIn('2000-12-31', dict(mean['obs']))
        missing = next(r for r in mean['snap']['cells'] if r['cell'] == 'R2')
        self.assertEqual((missing['raw_value'], missing['state']), ('-', 'missing_marker'))
        self.assertEqual(mean['meta']['units'], 'US$ millions')

    def test_reported_number_format_controls_printed_value_and_raw_token_is_preserved(self):
        series = next(r for r in self.collect() if r['meta']['id'] == 'SEC_IPO:AVERAGE_PROCEEDS_TOTAL:Q')
        self.assertEqual(series['obs'][-1], ('2026-06-30', '1061.6'))
        cell = next(r for r in series['snap']['cells'] if r['cell'] == 'N134')
        self.assertEqual(cell['raw_value'], '1061.5999999999999')
        self.assertEqual(cell['number_format'], '#,##0.0')
        self.assertEqual(cell['period_cell'], 'A134')
        self.assertEqual(cell['period_label'], '2026:Q2')
        self.assertEqual(series['snap']['source_sheet'], 'Stats Table')

    def test_future_or_incomplete_period_cannot_be_promoted_from_filename(self):
        with self.assertRaisesRegex(ValueError, 'after the review date'):
            sec_ipos.parse_workbook(self.raw, source_url=sec_ipos.URL, as_of='2026-05-31')

    def test_release_evidence_is_bound_to_exact_source_url_and_bytes(self):
        for body, url in ((self.raw, sec_ipos.URL.replace('20260922', '20260923')),
                          (changed_cell(self.raw, 'B134', '110', 'n'), sec_ipos.URL)):
            series = sec_ipos.parse_workbook(body, source_url=url, as_of='2026-11-05')
            self.assertTrue(all(row['published'] is None for row in series))
            self.assertTrue(all(row['snap']['publication_evidence'] is None for row in series))
        with self.assertRaisesRegex(ValueError, 'publication.*after the review date'):
            sec_ipos.parse_workbook(self.raw, as_of='2026-09-21')

    def test_unknown_period_label_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'period label'):
            self.collect(changed_cell(self.raw, 'A134', '2026 year to date'))

    def test_changed_unit_header_requires_review(self):
        with self.assertRaisesRegex(ValueError, 'header'):
            self.collect(changed_cell(self.raw, 'H1', 'IPO total proceeds (EUR Millions)'))

    def test_masked_and_blank_cells_remain_missing_not_zero(self):
        for value in ('*', None):
            with self.subTest(value=value):
                by = {r['meta']['id']:r for r in self.collect(changed_cell(self.raw, 'B134', value))}
                self.assertNotIn('2026-06-30', dict(by['SEC_IPO:COUNT_TOTAL:Q']['obs']))
                self.assertEqual(by['SEC_IPO:COUNT_TOTAL:Q']['snap']['cells'][-1]['state'],
                                 'blank' if value is None else 'missing_marker')

    def test_uncached_formula_and_error_cells_are_never_values(self):
        for raw in (changed_cell(self.raw, 'H134', None, 'n', '=1+1'),
                    changed_cell(self.raw, 'H134', '#REF!', 'e')):
            with self.assertRaisesRegex(ValueError, 'formula|source error'):
                self.collect(raw)

    def test_nonnumeric_data_and_negative_counts_fail_closed(self):
        for value, kind in (('unreviewed note','inlineStr'), ('-1','n'), ('1.5','n')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.collect(changed_cell(self.raw, 'B134', value, kind))

    def test_visual_dimension_tables_are_not_duplicate_time_series_observations(self):
        out = self.collect()
        self.assertTrue(all(r['snap']['source_sheet'] == 'Stats Table' for r in out))
        self.assertTrue(all(r['meta']['source_column'] in 'BCDEFGHIJKLMNOPQRSTUVWXY' for r in out))
        self.assertEqual({r['meta']['frequency'] for r in out}, {'A','Q'})


if __name__ == '__main__': unittest.main()
