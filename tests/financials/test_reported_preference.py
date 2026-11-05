"""A later comparative cumulative total cannot silently replace a reported quarter."""
import copy
import hashlib
import json
import gzip
from pathlib import Path
import tempfile
import unittest

from etl import sec, screener, jobs, repair_normalization
from etl import source_quality, source_provenance
from etl.source_provenance import ProvenanceError, validate_statement_provenance
from etl.source_quality import partition_supported_periods


CIK = 320193
Q1 = '0000320193-26-000001'
Q2 = '0000320193-26-000002'
LATER = '0000320193-26-000003'
REVISION = '0000320193-26-000004'


def observation(value, start, end, accession, filed):
    return {'val': value, 'start': start, 'end': end, 'accn': accession,
            'filed': filed, 'form': '10-Q'}


def source(direct_start='2026-04-01', direct_filed='2026-08-01', total=160):
    return {'cik': CIK, 'facts': {'us-gaap': {'Revenues': {'units': {'USD': [
        observation(50, '2026-01-01', '2026-03-31', Q1, '2026-05-01'),
        observation(100, direct_start, '2026-06-30', Q2, direct_filed),
        observation(150, '2026-01-01', '2026-06-30', Q2, '2026-08-01'),
        observation(total, '2026-01-01', '2026-06-30', LATER, '2026-11-01'),
    ]}}}}}


def normalize(raw, **kwargs):
    return {'income': sec.build_series(raw['facts'], 'income', **kwargs)}


def prove(raw, statements):
    body = json.dumps(raw, separators=(',', ':')).encode()
    return validate_statement_provenance(body, statements, CIK, hashlib.sha256(body).hexdigest())


class ReportedPreferenceTests(unittest.TestCase):
    def test_later_conflicting_derivation_preserves_reported_value_and_availability(self):
        raw = source()
        original = copy.deepcopy(raw)
        statements = normalize(raw)
        row = statements['income'][0]
        point = row['quarterly'][-1]
        self.assertEqual((point['v'], point['accn'], point['filed']), (100, Q2, '2026-08-01'))
        self.assertFalse(point.get('derived'))
        self.assertIn('selection_conflict', point)
        alternative = point['selection_conflict']['alternative']
        self.assertEqual((alternative['v'], alternative['filed']), (110, '2026-11-01'))
        self.assertEqual(alternative['derived']['from']['v'], 160)
        self.assertEqual(alternative['derived']['minus']['v'], 50)
        self.assertTrue(any(p['v'] == 110 and p.get('derived') for p in row['filing_history']['quarterly']))
        proof = prove(raw, statements)
        checked = proof['points']['/income/0/quarterly/1']
        self.assertEqual(checked['kind'], 'reported')
        self.assertEqual(proof['records'][checked['source']]['val'], '100')
        self.assertEqual(checked['selection_conflict']['alternative']['kind'], 'derived')
        self.assertEqual(raw, original)

    def test_intervening_direct_revision_is_preferred_over_older_equal_value(self):
        raw = source(total=150)
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(105, '2026-04-01', '2026-06-30', REVISION, '2026-09-01'))
        point = normalize(raw)['income'][0]['quarterly'][-1]
        self.assertEqual((point['v'], point['accn'], point['filed']), (105, REVISION, '2026-09-01'))
        self.assertFalse(point.get('derived'))
        self.assertEqual(point['selection_conflict']['alternative']['v'], 100)

    def test_equal_reported_and_derived_values_do_not_create_a_conflict(self):
        point = normalize(source(total=150))['income'][0]['quarterly'][-1]
        self.assertEqual((point['v'], point['accn']), (100, Q2))
        self.assertFalse(point.get('derived'))
        self.assertNotIn('selection_conflict', point)

    def test_future_dated_reported_context_cannot_be_preferred(self):
        statements = normalize(source(direct_filed='2026-05-01'))
        point = statements['income'][0]['quarterly'][-1]
        self.assertEqual(point['v'], 110)
        self.assertTrue(point.get('derived'))
        clean, excluded = partition_supported_periods(statements)
        self.assertTrue(any(p['point'].get('accn') == Q2 and p['point']['filed'] == '2026-05-01' for p in excluded))
        prove(source(direct_filed='2026-05-01'), clean)

    def test_different_reporting_start_stays_held_for_explicit_review(self):
        raw = source(direct_start='2026-04-02')
        statements = normalize(raw)
        point = statements['income'][0]['quarterly'][-1]
        self.assertEqual((point['start'], point['v']), ('2026-04-01', 110))
        self.assertTrue(point.get('derived'))
        self.assertEqual(point['selection_conflict']['reason'], 'reported_period_or_value_ambiguity')
        self.assertEqual(point['selection_conflict']['reported_observations'][0]['start'], '2026-04-02')
        self.assertEqual(len(source_quality.comparability_review(statements)['current_conflicts']), 1)
        prove(raw, statements)

    def test_different_metric_concept_is_not_treated_as_the_reported_equivalent(self):
        raw = source()
        direct = raw['facts']['us-gaap']['Revenues']['units']['USD'].pop(1)
        raw['facts']['us-gaap']['SalesRevenueNet'] = {'units': {'USD': [direct]}}
        point = normalize(raw)['income'][0]['quarterly'][-1]
        self.assertEqual((point['v'], point['c']), (110, 'Revenues'))
        self.assertTrue(point.get('derived'))
        self.assertNotIn('selection_conflict', point)

    def test_asof_cutoff_cannot_reveal_a_later_conflicting_alternative(self):
        statements = normalize(source(), as_of='2026-09-01')
        point = statements['income'][0]['quarterly'][-1]
        self.assertEqual((point['v'], point['accn']), (100, Q2))
        self.assertNotIn('selection_conflict', point)
        prove(source(), statements)

    def test_disagreeing_same_date_direct_observations_are_not_arbitrarily_preferred(self):
        raw = source()
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(101, '2026-04-01', '2026-06-30', REVISION, '2026-08-01'))
        point = normalize(raw)['income'][0]['quarterly'][-1]
        self.assertTrue(point.get('derived'))
        self.assertEqual(point['selection_conflict']['reason'], 'reported_period_or_value_ambiguity')
        self.assertEqual({p['v'] for p in point['selection_conflict']['reported_observations']}, {100, 101})
        prove(raw, normalize(raw))

    def test_proof_checks_conflicting_alternative_instead_of_trusting_metadata(self):
        raw = source()
        statements = normalize(raw)
        row = statements['income'][0]
        reported = next(p for p in row['filing_history']['quarterly'] if p['accn'] == Q2 and not p.get('derived'))
        alternative = next(p for p in row['filing_history']['quarterly'] if p['accn'] == LATER and p.get('derived'))
        row['quarterly'][-1] = dict(reported, selection_conflict={
            'reason': 'later_derived_value_differs_from_reported', 'alternative': copy.deepcopy(alternative)})
        prove(raw, statements)
        for field, value in [('v', 999), ('start', '2026-04-02'), ('c', 'SalesRevenueNet'), ('filed', '2026-12-01')]:
            changed = copy.deepcopy(statements)
            changed['income'][0]['quarterly'][-1]['selection_conflict']['alternative'][field] = value
            with self.subTest(field=field), self.assertRaises(ProvenanceError):
                prove(raw, changed)

    def test_recent_conflict_requires_review_before_current_calculations(self):
        statements = normalize(source())
        review = source_quality.comparability_review(statements, as_of='2026-11-05')
        self.assertEqual(len(review['conflicts']), 1)
        self.assertEqual(review['current_conflicts'], review['conflicts'])
        self.assertEqual(review['current_conflicts'][0]['point']['v'], 100)
        self.assertEqual(review['current_conflicts'][0]['alternative']['v'], 110)

    def test_old_conflict_is_retained_but_does_not_block_current_window(self):
        raw = source()
        for record in raw['facts']['us-gaap']['Revenues']['units']['USD']:
            for key in ('start', 'end', 'filed'):
                record[key] = record[key].replace('2026', '2017')
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(200, '2026-04-01', '2026-06-30', REVISION, '2026-08-01'))
        statements = normalize(raw)
        review = source_quality.comparability_review(statements, as_of='2026-11-05')
        self.assertEqual(len(review['conflicts']), 1)
        self.assertEqual(review['current_conflicts'], [])
        self.assertIn('selection_conflict', statements['income'][0]['quarterly'][1])

    def test_stale_company_cannot_escape_current_guard_by_calendar_age(self):
        raw = source()
        for record in raw['facts']['us-gaap']['Revenues']['units']['USD']:
            for key in ('start', 'end', 'filed'):
                record[key] = record[key].replace('2026', '2017')
        review = source_quality.comparability_review(normalize(raw), as_of='2026-11-05')
        self.assertEqual(len(review['current_conflicts']), 1)

    def test_asof_screener_holds_conflicting_math_without_changing_source_history(self):
        row = normalize(source())['income'][0]
        data = {'ticker': 'AAPL', 'q': {'Revenue': [screener._rec(p, 'Revenues') for p in row['quarterly']]},
                'filing_history': {'Revenue': [screener._rec(p, 'Revenues') for p in row['filing_history']['quarterly']]}}
        before = copy.deepcopy(data)
        earlier = screener.Facts(data, [], as_of='2026-09-01')
        self.assertEqual(earlier.at('Revenue', '2026-06-30')[2], 100)
        later = screener.Facts(data, [], as_of='2026-11-05')
        self.assertIsNone(later.at('Revenue', '2026-06-30'))
        self.assertEqual(later.comparability_holds[0]['point']['v'], 100)
        self.assertEqual(later.comparability_holds[0]['alternative']['v'], 110)
        current = screener.Facts(data, [])
        self.assertIsNone(current.at('Revenue', '2026-06-30'))
        self.assertEqual(data, before)

    def test_proof_object_identity_changes_when_selection_changes_on_same_snapshot(self):
        raw = source()
        selected = normalize(raw)
        first = prove(raw, selected)
        changed = copy.deepcopy(selected)
        changed['income'][0]['quarterly'][-1] = copy.deepcopy(
            changed['income'][0]['quarterly'][-1]['selection_conflict']['alternative'])
        second = prove(raw, changed)
        self.assertEqual(first['snapshot_sha256'], second['snapshot_sha256'])
        self.assertNotEqual(source_provenance.proof_object_path('AAPL', first),
                            source_provenance.proof_object_path('AAPL', second))

    def test_plan_retains_recent_conflict_evidence_and_cannot_override_hold(self):
        from test_normalization_repair import RepairStore
        store = RepairStore()
        raw = source()
        statements = normalize(raw)
        profile = sec.profile({'cik': CIK, 'name': 'Apple', 'tickers': ['AAPL'], 'fiscalYearEnd': '0930'}, raw['facts'])
        store.data['companies/AAPL.json'] = {'ticker': 'AAPL', 'cik': CIK, 'profile': profile,
                                             'statements': statements, 'filings': []}
        store.data['meta/fundamentals.json'] = {'AAPL': {'name': 'Apple', 'fund': {}}}
        store.objects['raw', f'sec/companyfacts/CIK{CIK:010d}.json.gz'] = gzip.compress(json.dumps(raw).encode())
        store.tables['facts'] = jobs.statement_fact_rows('AAPL', statements)
        with tempfile.TemporaryDirectory() as td:
            plan = repair_normalization.plan_repair(store, ['AAPL'], Path(td), as_of='2026-11-05')
            entry = plan['companies'][0]
            self.assertEqual(entry['status'], 'review_required')
            self.assertEqual(len(entry['review']['comparability']['current_conflicts']), 1)
            self.assertEqual(store.events, [])
            entry['status'] = 'ready'
            Path(td, 'plan.json').write_text(json.dumps(plan))
            with self.assertRaisesRegex(ValueError, 'comparability review'):
                repair_normalization._load_plan(Path(td, 'plan.json'))

    def test_new_issuer_with_no_before_fact_cannot_publish_ambiguous_direct_period(self):
        from test_company_fact_safety import CompanyStore, ScheduledFactSafety
        raw = source(direct_start='2026-04-02')
        store = CompanyStore()
        with self.assertRaisesRegex(ValueError, 'comparability review'):
            ScheduledFactSafety().process(store, raw['facts']['us-gaap']['Revenues']['units']['USD'])
        self.assertEqual((store.writes, store.db, store.facts), ([], [], []))

    def test_ambiguous_reported_alternatives_are_independently_source_checked(self):
        raw = source(direct_start='2026-04-02')
        statements = normalize(raw)
        statements['income'][0]['quarterly'][-1]['selection_conflict']['reported_observations'][0]['v'] = 999
        with self.assertRaises(ProvenanceError):
            prove(raw, statements)

    def test_same_filing_direct_and_derived_disagreement_keeps_reported_history(self):
        # EPR's2010Q4 reproducer: lease scope ranks the cumulative Revenues
        # context above a direct quarter in the same annual filing.
        raw = source()
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'))
        raw['facts']['us-gaap']['OperatingLeaseLeaseIncome'] = {'units': {'USD': [
            observation(10, '2026-01-01', '2026-06-30', LATER, '2026-11-01')]}}
        statements = normalize(raw)
        row = statements['income'][0]
        point = row['quarterly'][-1]
        self.assertEqual((point['v'], point['filed'], point['accn']), (100, '2026-11-01', LATER))
        self.assertEqual(point['selection_conflict']['reason'], 'same_date_derived_value_differs_from_reported')
        self.assertEqual(point['selection_conflict']['alternative']['v'], 110)
        history = row['filing_history']['quarterly']
        self.assertTrue(any(p['accn'] == LATER and not p.get('derived') and p['v'] == 100 for p in history))
        self.assertTrue(any(p['accn'] == LATER and p.get('derived') and p['v'] == 110 for p in history))
        prove(raw, statements)
        data = {'ticker': 'AAPL', 'filing_history': {'Revenue': [screener._rec(p, 'Revenues') for p in history]}}
        self.assertIsNone(screener.Facts(data, [], as_of='2026-11-01').at('Revenue', '2026-06-30'))
        self.assertEqual(screener.Facts(data, [], as_of='2026-10-31').at('Revenue', '2026-06-30')[2], 100)

    def test_same_date_direct_first_conflict_is_order_independent_and_source_proved(self):
        raw = source()
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'))
        original = copy.deepcopy(raw)
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                candidate = copy.deepcopy(raw)
                if reverse:
                    candidate['facts']['us-gaap']['Revenues']['units']['USD'].reverse()
                statements = normalize(candidate)
                row = statements['income'][0]
                point = row['quarterly'][-1]
                self.assertEqual((point['v'], point['accn'], point['filed']), (100, LATER, '2026-11-01'))
                self.assertEqual(point['selection_conflict']['reason'], 'same_date_derived_value_differs_from_reported')
                self.assertEqual(point['selection_conflict']['alternative']['v'], 110)
                history = row['filing_history']['quarterly']
                self.assertTrue(any(p['accn'] == LATER and p.get('derived') and p['v'] == 110 for p in history))
                self.assertTrue(any(p['accn'] == LATER and not p.get('derived') and p['v'] == 100 for p in history))
                prove(candidate, statements)
                data = {'ticker': 'AAPL', 'filing_history': {'Revenue': [screener._rec(p, 'Revenues') for p in history]}}
                self.assertIsNone(screener.Facts(data, [], as_of='2026-11-01').at('Revenue', '2026-06-30'))
                self.assertEqual(screener.Facts(data, [], as_of='2026-10-31').at('Revenue', '2026-06-30')[2], 100)
        self.assertEqual(raw, original)

    def test_all_same_date_derived_alternatives_are_retained_and_independently_checked(self):
        raw = source()
        raw['facts']['us-gaap']['Revenues']['units']['USD'].extend([
            observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'),
            observation(170, '2026-01-01', '2026-06-30', REVISION, '2026-11-01'),
        ])
        statements = normalize(raw)
        point = statements['income'][0]['quarterly'][-1]
        conflict = point['selection_conflict']
        alternatives = [conflict['alternative'], *conflict['additional_alternatives']]
        self.assertEqual({p['v'] for p in alternatives}, {110, 120})
        history = statements['income'][0]['filing_history']['quarterly']
        self.assertTrue(all(any(h['accn'] == p['accn'] and h['v'] == p['v'] and h.get('derived')
                                for h in history) for p in alternatives))
        proof = prove(raw, statements)
        self.assertEqual(len(proof['points']['/income/0/quarterly/1']['selection_conflict']['additional_alternatives']), 1)
        changed = copy.deepcopy(statements)
        changed['income'][0]['quarterly'][-1]['selection_conflict']['additional_alternatives'][0]['v'] = 999
        with self.assertRaises(ProvenanceError):
            prove(raw, changed)

    def test_later_reported_revision_is_not_held_by_an_older_derived_disagreement(self):
        raw = source()
        raw['facts']['us-gaap']['Revenues']['units']['USD'].extend([
            observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'),
            observation(105, '2026-04-01', '2026-06-30', REVISION, '2026-12-01'),
        ])
        statements = normalize(raw)
        point = statements['income'][0]['quarterly'][-1]
        self.assertEqual((point['v'], point['filed']), (105, '2026-12-01'))
        self.assertNotIn('selection_conflict', point)
        prove(raw, statements)
        history = statements['income'][0]['filing_history']['quarterly']
        data = {'ticker': 'AAPL', 'filing_history': {'Revenue': [screener._rec(p, 'Revenues') for p in history]}}
        self.assertIsNone(screener.Facts(data, [], as_of='2026-11-01').at('Revenue', '2026-06-30'))
        self.assertEqual(screener.Facts(data, [], as_of='2026-12-01').at('Revenue', '2026-06-30')[2], 105)

    def test_same_date_alternative_with_future_source_leg_cannot_hold_a_valid_reported_point(self):
        raw = source()
        values = raw['facts']['us-gaap']['Revenues']['units']['USD']
        values[0]['filed'] = '2026-02-01'
        values.append(observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'))
        statements, excluded = partition_supported_periods(normalize(raw))
        point = statements['income'][0]['quarterly'][-1]
        self.assertEqual(point['v'], 100)
        self.assertNotIn('selection_conflict', point)
        self.assertTrue(excluded)
        prove(raw, statements)

    def test_exact_decimal_subtraction_rounding_does_not_become_a_value_correction(self):
        for same_date in (False, True):
            with self.subTest(same_date=same_date):
                raw = source(total=300.1)
                values = raw['facts']['us-gaap']['Revenues']['units']['USD']
                values[0]['val'], values[1]['val'], values[2]['val'] = 100.2, 199.9, 300.1
                if same_date:
                    values.append(observation(199.9, '2026-04-01', '2026-06-30', LATER, '2026-11-01'))
                statements = normalize(raw)
                point = statements['income'][0]['quarterly'][-1]
                self.assertEqual(point['v'], 199.9)
                self.assertFalse(point.get('derived'))
                self.assertNotIn('selection_conflict', point)
                prove(raw, statements)

    def test_new_issuer_same_date_direct_first_conflict_fails_before_any_write(self):
        from test_company_fact_safety import CompanyStore, ScheduledFactSafety
        raw = source()
        values = raw['facts']['us-gaap']['Revenues']['units']['USD']
        values.append(observation(100, '2026-04-01', '2026-06-30', LATER, '2026-11-01'))
        store = CompanyStore()
        with self.assertRaisesRegex(ValueError, 'comparability review'):
            ScheduledFactSafety().process(store, values)
        self.assertEqual((store.writes, store.db, store.facts), ([], [], []))

    def test_repair_equivalence_helper_cannot_erase_an_explicit_source_ambiguity(self):
        raw = source(direct_start='2026-04-02')
        raw['facts']['us-gaap']['Revenues']['units']['USD'].append(
            observation(110, '2026-04-01', '2026-06-30', REVISION, '2026-07-01'))
        statements = normalize(raw)
        selected = copy.deepcopy(statements['income'][0]['quarterly'][-1])
        self.assertEqual(selected['selection_conflict']['reason'], 'reported_period_or_value_ambiguity')
        prior = {'ticker': 'AAPL', 'statement': 'income', 'label': 'Revenue', 'kind': 'Q',
                 'period_end': '2026-06-30', 'period_start': '2026-04-01', 'concept': 'Revenues',
                 'unit': 'USD', 'value': 110, 'accession': REVISION, 'form': '10-Q',
                 'filed': '2026-07-01', 'derived': False}
        bundle = {'ticker': 'AAPL', 'statements': statements}
        count = repair_normalization.retain_proved_reported_observations(bundle, [prior], raw['facts'], '2026-11-05')
        self.assertEqual(count, 0)
        self.assertEqual(statements['income'][0]['quarterly'][-1], selected)
        self.assertEqual(len(source_quality.comparability_review(statements, as_of='2026-11-05')['current_conflicts']), 1)
        self.assertEqual(repair_normalization.review_database_replacements([prior], repair_normalization.rows_for(bundle))['blockers'][0]['reason'], 'reported_to_derived')
        prove(raw, statements)

    def test_repair_equivalence_helper_cannot_hide_a_different_accounting_concept(self):
        raw = source(total=150)
        direct = raw['facts']['us-gaap']['Revenues']['units']['USD'].pop(1)
        raw['facts']['us-gaap']['SalesRevenueNet'] = {'units': {'USD': [direct]}}
        statements = normalize(raw)
        selected = copy.deepcopy(statements['income'][0]['quarterly'][-1])
        self.assertEqual((selected['v'], selected['c']), (100, 'Revenues'))
        self.assertTrue(selected.get('derived'))
        prior = {'ticker': 'AAPL', 'statement': 'income', 'label': 'Revenue', 'kind': 'Q',
                 'period_end': '2026-06-30', 'period_start': '2026-04-01', 'concept': 'SalesRevenueNet',
                 'unit': 'USD', 'value': 100, 'accession': Q2, 'form': '10-Q',
                 'filed': '2026-08-01', 'derived': False}
        bundle = {'ticker': 'AAPL', 'statements': statements}
        count = repair_normalization.retain_proved_reported_observations(bundle, [prior], raw['facts'], '2026-11-05')
        self.assertEqual(count, 0)
        self.assertEqual(statements['income'][0]['quarterly'][-1], selected)
        self.assertEqual(repair_normalization.review_database_replacements([prior], repair_normalization.rows_for(bundle))['blockers'][0]['reason'], 'reported_to_derived')
        prove(raw, statements)


if __name__ == '__main__':
    unittest.main()
