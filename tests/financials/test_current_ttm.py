"""Current valuations require a common reporting window and a known share basis."""
import copy
import json
import unittest
from unittest.mock import patch

from etl import fundamentals, jobs, repair, repair_normalization, screener
from test_financials import quarters
from test_pipeline import MemoryStore


def statements(eps=(1, 2, 3, 4)):
    result = {'income': [quarters('Revenue', (100, 110, 120, 130)),
                         quarters('Net income', (10, 11, 12, 13)), quarters('EPS diluted', eps)]}
    for row in result['income']:
        for point in row['quarterly']:
            point.update(accn='reported-' + point['end'], filed='2026-10-22', form='10-Q')
    return result


def extracted(rows, ticker='TEST'):
    return {'ticker': ticker, 'q': {row['label']: [screener._rec(p, row['label']) for p in row['quarterly']]
                                    for row in rows['income']}}


def retail_fiscal_data():
    # Captured COST fiscal 2026: three 12-week quarters and a 16-week quarter.
    starts = ['2025-09-01', '2025-11-24', '2026-02-16', '2026-05-11']
    ends = ['2025-11-23', '2026-02-15', '2026-05-10', '2026-08-30']
    filed = ['2025-12-17', '2026-03-11', '2026-06-03', '2026-10-07']
    values = {'Revenue': [67_307_000_000, 69_597_000_000, 70_527_000_000, 95_723_000_000],
              'Operating cash flow': [4_688_000_000, 2_996_000_000, 3_449_000_000, 4_684_000_000],
              'Capex': [1_526_000_000, 1_289_000_000, 1_413_000_000, 2_207_000_000]}
    data = {'ticker': 'COST', 'q': {}}
    for label, nums in values.items():
        data['q'][label] = [screener._rec({'start': start, 'end': end, 'v': value,
            'filed': available, 'accn': 'reported-' + end, 'form': '10-Q',
            'derived': {'basis': 'source_proved_additive_difference'} if (index > 0 and label != 'Revenue') or index == 3 else None}, label)
            for index, (start, end, value, available) in enumerate(zip(starts, ends, nums, filed))]
    data['filing_history'] = copy.deepcopy(data['q'])
    return data


class CurrentTTMTests(unittest.TestCase):
    def test_old_four_quarters_are_not_current_eps_or_income(self):
        st = statements()
        for label, last_year in (('EPS diluted', 2018), ('Net income', 2020)):
            row = next(r for r in st['income'] if r['label'] == label)
            for quarter, point in enumerate(row['quarterly'], 1):
                point.update(start=f'{last_year}-{(quarter - 1) * 3 + 1:02d}-01',
                             end=f'{last_year}-{quarter * 3:02d}-' + ('31' if quarter in (1, 4) else '30'))
        result = fundamentals.compute(st, {}, {'price': 100}, ticker='TEST')
        self.assertEqual(result['latest_quarter_end'], '2026-09-30')
        self.assertIsNone(result['eps_ttm'])
        self.assertIsNone(result['net_income_ttm'])
        self.assertIsNone(result['pe_ttm'])
        self.assertEqual(result['revenue_ttm'], 460)
        self.assertEqual(result['ttm_periods']['eps_ttm'], [])

    def test_both_consumers_choose_the_latest_common_income_window(self):
        st = statements()
        revenue = st['income'][0]
        revenue['quarterly'].pop()  # Revenue is behind the latest reported net income.
        result = fundamentals.compute(st, {}, {'price': 100}, ticker='TEST')
        fx = screener.Facts(extracted(st), [])
        self.assertEqual(result['latest_quarter_end'], '2026-09-30')
        self.assertEqual(fx.ends[-1], result['latest_quarter_end'])
        self.assertIsNone(result['revenue_ttm'])
        self.assertIsNone(fx.ttm('Revenue', fx.ends[-1])[0])
        self.assertEqual(result['eps_ttm'], fx.ttm('EPS diluted', fx.ends[-1])[0])
        self.assertEqual(result['eps_ttm'], 10)

    def test_screener_does_not_use_an_older_income_grid_when_revenue_is_current(self):
        st = statements()
        st['income'][1]['quarterly'].pop()
        fx = screener.Facts(extracted(st), [])
        self.assertEqual(fx.ends[-1], '2026-09-30')
        self.assertIsNone(fx.ttm('Net income', fx.ends[-1])[0])

    def test_historical_calendar_lookup_resolves_to_the_reported_fiscal_window(self):
        starts = ['2024-09-30', '2024-12-30', '2025-03-31', '2025-06-30']
        ends = ['2024-12-29', '2025-03-30', '2025-06-29', '2025-09-28']
        rows = {'income': [{'label': 'Revenue', 'quarterly': [
            {'start': start, 'end': end, 'v': 10, 'accn': 'reported', 'filed': '2025-10-30', 'form': '10-Q'}
            for start, end in zip(starts, ends)]}]}
        fx = screener.Facts(extracted(rows), [])
        value, records = fx.ttm('Revenue', '2025-09-30')
        self.assertEqual(value, 40)
        self.assertEqual(max(r[0] for r in records), '2025-09-28')

    def test_retail_ttm_uses_actual_quarters_including_a_sixteen_week_quarter(self):
        fx = screener.Facts(retail_fiscal_data(), [])
        revenue, records = fx.ttm('Revenue', '2026-08-30')
        self.assertEqual(revenue, 303_154_000_000)
        self.assertEqual([r[0] for r in records], ['2026-08-30', '2026-05-10', '2026-02-15', '2025-11-23'])
        self.assertEqual(fx.fcf_ttm('2026-08-30')[0], 9_382_000_000)

    def test_retail_actual_quarter_selection_keeps_asof_and_gap_guards(self):
        data = retail_fiscal_data()
        before = screener.Facts(data, [], as_of='2026-10-06')
        self.assertIsNone(before.ttm('Revenue', '2026-08-30')[0])
        after = screener.Facts(data, [], as_of='2026-10-07')
        self.assertEqual(after.ttm('Revenue', '2026-08-30')[0], 303_154_000_000)
        data['q']['Revenue'][1][1] = '2025-11-25'
        self.assertIsNone(screener.Facts(data, []).ttm('Revenue', '2026-08-30')[0])

    def test_retail_selection_does_not_admit_derived_eps(self):
        data = retail_fiscal_data()
        data['q']['EPS diluted'] = copy.deepcopy(data['q']['Revenue'])
        self.assertIsNone(screener.Facts(data, []).ttm('EPS diluted', '2026-08-30')[0])

    def test_current_eps_keeps_exact_period_evidence_and_requires_it_when_cached(self):
        result = fundamentals.compute(statements(), {}, {'price': 100}, ticker='TEST')
        self.assertEqual(result['ttm_periods']['eps_ttm'], [
            ['2025-10-01', '2025-12-31'], ['2026-01-01', '2026-03-31'],
            ['2026-04-01', '2026-06-30'], ['2026-07-01', '2026-09-30']])
        self.assertEqual(fundamentals.current_ttm_value(result, 'eps_ttm'), 10)
        for change in ('missing', 'old', 'gap'):
            altered = copy.deepcopy(result)
            if change == 'missing':
                altered.pop('ttm_periods')
            elif change == 'old':
                altered['latest_quarter_end'] = '2026-12-31'
            else:
                altered['ttm_periods']['eps_ttm'][2][0] = '2026-04-02'
            with self.subTest(change=change):
                self.assertIsNone(fundamentals.current_ttm_value(altered, 'eps_ttm'))

    def test_index_does_not_republish_a_stale_cached_eps_scalar(self):
        store = MemoryStore()
        fund = {'TEST': {'pipeline_version': 2, 'name': 'Test', 'sector': 'Industrials',
                         'fund': {'eps_ttm': 3.34, 'latest_quarter_end': '2026-09-30'}}}
        with patch.object(jobs, 'UNIVERSE', [('TEST', 1, 'Test', 'Industrials')]):
            published = jobs.build_index(store, pstats={'TEST': {'price': 100, 'as_of': '2026-11-04'}},
                                         fundamentals_snapshot=fund, return_snapshot=True)
        self.assertIsNone(published['companies'][0]['eps_ttm'])
        self.assertIsNone(published['companies'][0]['pe_ttm'])
        metrics = next(rows for table, rows in store.db if table == 'company_metrics')
        self.assertIsNone(metrics[0]['eps_ttm'])

    def test_honeywell_basis_hold_preserves_reported_history_and_explains_missing_valuation(self):
        st = statements((-0.18, 1.29, 17.83, 2.08))
        eps = st['income'][2]['quarterly']
        for point, filed in zip(eps, ['2026-02-17', '2026-04-23', '2026-07-23', '2026-10-22']):
            point['filed'] = filed
        original = copy.deepcopy(st)
        result = fundamentals.compute(st, {}, {'price': 100, 'as_of': '2026-11-04'}, ticker='HON')
        fx = screener.Facts(extracted(st, 'HON'), [['2026-06-29', 0.9535]])
        self.assertIsNone(result['eps_ttm'])
        self.assertIsNone(result['pe_ttm'])
        self.assertIsNone(fx.ttm('EPS diluted', fx.ends[-1])[0])
        self.assertEqual(fx.at('EPS diluted', '2026-03-31')[2], 1.29)
        self.assertEqual(st, original)
        basis = result['valuation_basis']
        self.assertEqual(basis['status'], 'held')
        self.assertEqual(basis['code'], 'corporate_action_recast_required')
        self.assertIn('pe_ttm', basis['affected_fields'])
        self.assertIn('https://www.sec.gov/', basis['sources'][0]['url'])
        self.assertEqual(result['revenue_ttm'], 460)

    def test_honeywell_hold_applies_to_warm_cache_without_embedded_policy(self):
        data = extracted(statements((-0.18, 1.29, 17.83, 2.08)))
        data.pop('ticker')
        _out, prov, _fx, inputs = screener.fundamentals_for('HON', data, [['2026-06-29', 0.9535]], 100, 'Industrials')
        self.assertIsNone(inputs['eps'])
        self.assertEqual(prov.f['pe']['status'], 'held')
        self.assertEqual(prov.f['earn_yield']['valuation_basis']['code'], 'corporate_action_recast_required')

    def test_declared_basis_holds_are_explicit_and_date_bounded(self):
        declared = {'status': 'held', 'code': 'inconsistent_recast', 'reason': 'Review the share basis',
                    'effective_date': '2026-07-01', 'affected_fields': ['eps_ttm', 'pe_ttm'], 'sources': []}
        self.assertIsNone(fundamentals.valuation_hold('TEST', declared, '2026-06-30'))
        self.assertEqual(fundamentals.valuation_hold('TEST', declared, '2026-07-01'), declared)
        st = statements()
        result = fundamentals.compute(st, {'valuation_basis': declared},
                                      {'price': 100, 'as_of': '2026-11-04'}, ticker='TEST')
        self.assertIsNone(result['eps_ttm'])
        self.assertEqual(result['net_income_ttm'], 46)

    def test_both_offline_repair_paths_preserve_the_issuer_valuation_hold(self):
        st = statements((-0.18, 1.29, 17.83, 2.08))
        concepts = ('Revenues', 'NetIncomeLoss', 'EarningsPerShareDiluted')
        source = {'us-gaap': {}}
        for line, concept in zip(st['income'], concepts):
            observations = []
            for point in line['quarterly']:
                observations.append({'val': point['v'], 'start': point['start'], 'end': point['end'],
                                     'accn': '0000773840-26-000143', 'filed': '2026-10-22', 'form': '10-Q'})
            source['us-gaap'][concept] = {'units': {'USD/shares' if concept == 'EarningsPerShareDiluted' else 'USD': observations}}
        bundle = {'ticker': 'HON', 'cik': jobs.BY_TICKER['HON']['cik'], 'profile': {}, 'statements': {}}
        direct, direct_review = repair.rebuild(bundle, source, price={'price': 100, 'as_of': '2026-11-04'})
        raw = json.dumps({'cik': bundle['cik'], 'facts': source}).encode()
        proved, _proof, proved_review = repair_normalization._candidate(bundle, raw, '2026-11-05', '2026-11-05T15:00:00Z')
        for name, candidate, report in (('offline', direct, direct_review), ('source_proved', proved, proved_review)):
            with self.subTest(path=name):
                self.assertIsNone(candidate['fundamentals']['eps_ttm'])
                self.assertEqual(candidate['fundamentals']['valuation_basis']['code'], 'corporate_action_recast_required')
                self.assertEqual(report['fundamentals'], candidate['fundamentals'])
                self.assertEqual(len(next(r for r in candidate['statements']['income'] if r['label'] == 'EPS diluted')['quarterly']), 4)


if __name__ == '__main__':
    unittest.main()
