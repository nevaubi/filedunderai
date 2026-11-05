"""Source-proof regressions exercise the real SEC normalizer and archived bytes."""
import copy
import hashlib
import importlib
import json
import math
import unittest

from etl import sec


CIK = 1660134
ACC1 = '0001660134-25-000001'
ACC2 = '0001660134-25-000002'
ACC3 = '0001660134-25-000003'


def fact(value=100, **changes):
    return dict({'val': value, 'start': '2025-01-01', 'end': '2025-03-31',
                 'accn': ACC1, 'form': '10-Q', 'filed': '2025-05-01',
                 'fy': 2025, 'fp': 'Q1', 'frame': 'CY2025Q1'}, **changes)


def snapshot(records=None, concept='Revenues', unit='USD'):
    return {'cik': CIK, 'entityName': 'Example Issuer', 'facts': {'us-gaap': {
        concept: {'units': {unit: records if records is not None else [fact()]}}
    }}}


def normalize(source):
    return {name: sec.build_series(source['facts'], name) for name in sec.CONCEPTS}


def encoded(source):
    return json.dumps(source, separators=(',', ':'), allow_nan=False).encode()


def resolve_pointer(document, pointer):
    node = document
    for token in pointer.split('/')[1:]:
        token = token.replace('~1', '/').replace('~0', '~')
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


class SourceProvenanceTests(unittest.TestCase):
    def setUp(self):
        try:
            module = importlib.import_module('etl.source_provenance')
        except ModuleNotFoundError as error:
            if error.name != 'etl.source_provenance':
                raise
            self.fail('The strict SEC source-proof gate has not been implemented')
        self.validate = module.validate_statement_provenance
        self.Error = module.ProvenanceError

    def prove(self, source, statements=None, expected_cik=CIK, digest=None):
        raw = encoded(source)
        return self.validate(raw, normalize(source) if statements is None else statements,
                             expected_cik, digest or hashlib.sha256(raw).hexdigest())

    def test_actual_normalizer_points_resolve_to_exact_archived_records(self):
        source = snapshot()
        statements = normalize(source)
        before = copy.deepcopy(statements)
        proof = self.prove(source, statements)
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(proof['cik'], CIK)
        self.assertEqual(proof['counts'], {'statement_points': 2, 'selected_points': 1,
            'history_points': 1, 'reported_points': 2, 'derived_points': 0, 'source_records': 1})
        pointer = proof['points']['/income/0/quarterly/0']['source']
        self.assertEqual(pointer, '/facts/us-gaap/Revenues/units/USD/0')
        self.assertEqual(resolve_pointer(source, pointer)['val'], 100)
        self.assertEqual(proof['records'][pointer]['val'], '100')
        self.assertEqual(proof['records'][pointer]['value_encoding'], 'decimal')
        self.assertEqual(proof['records'][pointer]['fy'], 2025)
        self.assertEqual(proof['records'][pointer]['frame'], 'CY2025Q1')
        self.assertEqual(statements, before, 'validation must not mutate normalized output')
        json.dumps(proof, allow_nan=False)

    def test_sixteen_week_fiscal_quarter_remains_supported(self):
        # CAVA explicitly reports a 16-week first quarter, followed by 12-week quarters.
        source = snapshot([fact(start='2025-12-29', end='2026-04-19', filed='2026-05-20')])
        proof = self.prove(source)
        self.assertEqual(proof['counts']['selected_points'],1)

    def test_explicit_screener_taxonomy_keeps_full_source_validation(self):
        source = snapshot(concept='InterestExpense')
        concepts = {'income':[('Interest expense',['InterestExpense'])]}
        statements = {'income':sec.build_series(source['facts'],'income',concepts=concepts['income'])}
        raw = encoded(source)
        sha = hashlib.sha256(raw).hexdigest()
        with self.assertRaises(self.Error):
            self.validate(raw,statements,CIK,sha)
        proof = self.validate(raw,statements,CIK,sha,taxonomy=concepts)
        self.assertEqual(proof['counts']['selected_points'],1)
        statements['income'][0]['quarterly'][0]['v'] = 999
        with self.assertRaises(self.Error):
            self.validate(raw,statements,CIK,sha,taxonomy=concepts)

    def test_raw_hash_and_issuer_identity_are_required(self):
        source = snapshot()
        raw = encoded(source)
        for supplied, expected, digest in [
            (source, CIK, hashlib.sha256(raw).hexdigest()),
            (raw, CIK + 1, hashlib.sha256(raw).hexdigest()),
            (raw, True, hashlib.sha256(raw).hexdigest()),
            (raw, CIK, '0' * 64),
            (raw, CIK, 'not-a-sha256'),
        ]:
            with self.subTest(expected=expected, digest=digest), self.assertRaises(self.Error):
                self.validate(supplied, normalize(source), expected, digest)
        self.assertEqual(self.prove(source, expected_cik='0001660134')['cik'], CIK)

    def test_duplicate_json_keys_and_nonfinite_json_are_rejected(self):
        for raw in [b'{"cik":1,"cik":1660134,"facts":{}}',
                    b'{"cik":1660134,"facts":{},"unused":NaN}']:
            with self.subTest(raw=raw), self.assertRaises(self.Error):
                self.validate(raw, normalize(snapshot()), CIK, hashlib.sha256(raw).hexdigest())

    def test_each_direct_source_identity_field_is_checked(self):
        source = snapshot()
        mutations = {'v': 101, 'c': 'Cash', 'start': '2025-01-02', 'end': '2025-03-30',
                     'accn': ACC2, 'filed': '2025-05-02', 'form': '10-K',
                     'unit': 'EUR', 'namespace': 'dei', 'fy': 2024, 'fp': 'Q2'}
        for field, value in mutations.items():
            statements = normalize(source)
            statements['income'][0]['quarterly'][0][field] = value
            with self.subTest(field=field), self.assertRaises(self.Error):
                self.prove(source, statements)

    def test_matching_raw_number_cannot_be_attached_to_wrong_metric(self):
        source = snapshot()
        source['facts']['us-gaap']['CashAndCashEquivalentsAtCarryingValue'] = {
            'units': {'USD': [fact()]}}
        statements = normalize(source)
        statements['income'][0]['concept'] = 'CashAndCashEquivalentsAtCarryingValue'
        statements['income'][0]['quarterly'][0]['c'] = 'CashAndCashEquivalentsAtCarryingValue'
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_each_point_uses_its_own_legitimate_concept_alias(self):
        source = snapshot([fact(100)])
        source['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax'] = {
            'units': {'USD': [fact(120, start='2025-04-01', end='2025-06-30',
                                 accn=ACC2, filed='2025-08-01', fp='Q2', frame='CY2025Q2')]}}
        proof = self.prove(source)
        pointers = {point['source'] for point in proof['points'].values()}
        self.assertIn('/facts/us-gaap/Revenues/units/USD/0', pointers)
        self.assertIn('/facts/us-gaap/RevenueFromContractWithCustomerExcludingAssessedTax/units/USD/0', pointers)

    def test_units_are_literal_and_json_pointer_escaped(self):
        source = snapshot([fact(1.25)], concept='EarningsPerShareBasic', unit='USD/shares')
        proof = self.prove(source)
        pointer = proof['points']['/income/0/quarterly/0']['source']
        self.assertEqual(pointer, '/facts/us-gaap/EarningsPerShareBasic/units/USD~1shares/0')
        self.assertEqual(resolve_pointer(source, pointer)['val'], 1.25)
        statements = normalize(source)
        statements['income'][0]['unit'] = 'USD'
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_nonfinite_or_boolean_normalized_values_are_rejected(self):
        source = snapshot()
        for value in [float('nan'), float('inf'), float('-inf'), True, '100']:
            statements = normalize(source)
            statements['income'][0]['quarterly'][0]['v'] = value
            with self.subTest(value=value), self.assertRaises(self.Error):
                self.prove(source, statements)

    def test_precise_source_decimal_is_not_silently_rounded(self):
        raw = encoded(snapshot([fact(1.25)])).replace(b'"val":1.25', b'"val":1.234567890123456789')
        source = json.loads(raw)
        with self.assertRaises(self.Error):
            self.validate(raw, normalize(source), CIK, hashlib.sha256(raw).hexdigest())

    def test_history_is_validated_even_when_selected_point_is_correct(self):
        source = snapshot()
        statements = normalize(source)
        statements['income'][0]['filing_history']['quarterly'][0]['v'] = 999
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_malformed_containers_are_not_silently_skipped(self):
        source = snapshot()
        for field, value in [('quarterly', None), ('annual', {}), ('filing_history', [])]:
            statements = normalize(source)
            statements['income'][0][field] = value
            with self.subTest(field=field), self.assertRaises(self.Error):
                self.prove(source, statements)
        with self.assertRaises(self.Error):
            self.prove(source, {'income': [], 'balance': [], 'cashflow': []})

    def test_annual_value_cannot_be_classified_as_one_quarter(self):
        source = snapshot([fact(600, end='2025-12-31', form='10-K', filed='2026-02-01', fp='FY', frame='CY2025')])
        statements = normalize(source)
        statements['income'][0]['quarterly'] = copy.deepcopy(statements['income'][0]['annual'])
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_four_month_transition_period_is_not_labeled_as_a_quarter(self):
        source = snapshot([fact(100, start='2025-02-01', end='2025-05-31', filed='2025-07-01')])
        statements = normalize(source)
        self.assertEqual(len(statements['income'][0]['quarterly']), 1)
        with self.assertRaisesRegex(self.Error, 'quarterly period'):
            self.prove(source, statements)

    def test_fourteen_week_fiscal_quarter_remains_supported(self):
        source = snapshot([fact(100, end='2025-04-08', filed='2025-05-01')])
        proof = self.prove(source)
        pointer = proof['points']['/income/0/quarterly/0']['source']
        self.assertEqual(proof['records'][pointer]['end'], '2025-04-08')

    def test_instant_balance_and_annual_context_are_preserved(self):
        observation = fact(500, end='2025-12-31', form='10-K', filed='2026-02-01', fp='FY', frame='CY2025Q4I')
        del observation['start']
        proof = self.prove(snapshot([observation], concept='Assets'))
        self.assertEqual(proof['counts']['selected_points'], 2)
        self.assertIn('/balance/0/annual/0', proof['points'])
        self.assertIn('/balance/0/quarterly/0', proof['points'])

    def test_derived_quarter_resolves_both_raw_legs_and_arithmetic(self):
        source = snapshot([fact(100), fact(250, end='2025-06-30', accn=ACC2,
                            filed='2025-08-01', fp='Q2', frame='CY2025Q2YTD')])
        proof = self.prove(source)
        point = proof['points']['/income/0/quarterly/1']
        self.assertEqual(point['kind'], 'derived')
        self.assertEqual(point['operation'], 'subtract')
        self.assertEqual(resolve_pointer(source, point['from']['source'])['val'], 250)
        self.assertEqual(resolve_pointer(source, point['minus']['source'])['val'], 100)
        self.assertEqual(proof['counts']['derived_points'], 2)
        self.assertEqual(proof['counts']['source_records'], 2)

    def test_derived_wrong_value_period_source_or_availability_is_rejected(self):
        source = snapshot([fact(100), fact(250, end='2025-06-30', accn=ACC2, filed='2025-08-01', fp='Q2')])
        for field, value in [('v', 151), ('start', '2025-03-31'), ('end', '2025-06-29'),
                             ('available_at', '2025-05-01'), ('accessions', [ACC2]),
                             ('validation_status', 'reported')]:
            statements = normalize(source)
            statements['income'][0]['quarterly'][1][field] = value
            with self.subTest(field=field), self.assertRaises(self.Error):
                self.prove(source, statements)
        statements = normalize(source)
        statements['income'][0]['quarterly'][1]['derived']['minus']['accn'] = ACC3
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_derived_legs_cannot_change_unit_or_concept(self):
        source = snapshot([fact(100), fact(250, end='2025-06-30', accn=ACC2, filed='2025-08-01', fp='Q2')])
        for field, value in [('unit', 'EUR'), ('c', 'SalesRevenueNet')]:
            statements = normalize(source)
            statements['income'][0]['quarterly'][1]['derived']['minus'][field] = value
            with self.subTest(field=field), self.assertRaises(self.Error):
                self.prove(source, statements)

    def test_recursive_derivation_traces_all_legs_and_rejects_cycles(self):
        source = snapshot([fact(100), fact(250, end='2025-06-30', accn=ACC2,
                            filed='2025-08-01', fp='Q2'),
                           fact(450, end='2025-09-30', accn=ACC3,
                            filed='2025-11-01', fp='Q3')])
        statements = normalize(source)
        quarters = statements['income'][0]['quarterly']
        quarters[2] = copy.deepcopy(quarters[2])
        # Q3 = (nine months - Q1) - (six months - Q1). Both inner
        # computations remain independently tied to archived observations.
        first_half = copy.deepcopy(quarters[1])
        last_six_months = copy.deepcopy(quarters[2])
        last_six_months.update(v=350, start='2025-04-01', accessions=[ACC1, ACC3])
        last_six_months['derived']['minus'] = copy.deepcopy(quarters[0])
        quarters[2]['derived'].update({'from': last_six_months, 'minus': first_half})
        quarters[2]['accessions'] = [ACC1, ACC2, ACC3]
        proof = self.prove(source, statements)
        recursive = proof['points']['/income/0/quarterly/2']
        self.assertEqual(recursive['from']['kind'], 'derived')
        self.assertEqual(recursive['minus']['kind'], 'derived')
        self.assertEqual(proof['counts']['source_records'], 3)
        quarters[2]['derived']['from'] = quarters[2]
        with self.assertRaisesRegex(self.Error, 'cyclic'):
            self.prove(source, statements)

    def test_large_integer_derivation_does_not_use_decimal_rounding(self):
        source = snapshot([fact(100), fact(10 ** 40 + 12345, end='2025-06-30',
                            accn=ACC2, filed='2025-08-01', fp='Q2')])
        statements = normalize(source)
        self.assertEqual(statements['income'][0]['quarterly'][1]['v'], 10 ** 40 + 12245)
        self.prove(source, statements)
        statements['income'][0]['quarterly'][1]['v'] += 1
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_per_share_values_cannot_be_derived_by_cumulative_subtraction(self):
        source = snapshot([fact(1.25), fact(2.5, end='2025-06-30', accn=ACC2,
                            filed='2025-08-01', fp='Q2')],
                          concept='EarningsPerShareBasic', unit='USD/shares')
        statements = normalize(source)
        row = statements['income'][0]
        first = copy.deepcopy(row['quarterly'][0])
        cumulative = dict(first, v=2.5, end='2025-06-30', accn=ACC2,
                          filed='2025-08-01', available_at='2025-08-01', accessions=[ACC2])
        derived = dict(cumulative, v=1.25, start='2025-04-01', accessions=[ACC1, ACC2],
                       validation_status='derived', derived={'from': cumulative, 'minus': first})
        row['quarterly'].append(derived)
        with self.assertRaisesRegex(self.Error, 'not additive'):
            self.prove(source, statements)

    def test_minimal_floating_point_subtraction_noise_is_not_a_changed_value(self):
        source = snapshot([fact(100.2), fact(300.1, end='2025-06-30', accn=ACC2, filed='2025-08-01', fp='Q2')])
        statements = normalize(source)
        self.assertEqual(statements['income'][0]['quarterly'][1]['v'], 199.90000000000003)
        self.prove(source, statements)
        statements['income'][0]['quarterly'][1]['v'] += 0.00001
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_floating_point_allowance_cannot_change_the_normalizer_result(self):
        source = snapshot([fact(100.2), fact(300.1, end='2025-06-30', accn=ACC2,
                            filed='2025-08-01', fp='Q2')])
        statements = normalize(source)
        point = statements['income'][0]['quarterly'][1]
        point['v'] = math.nextafter(point['v'], float('inf'))
        with self.assertRaises(self.Error):
            self.prove(source, statements)

    def test_duplicate_equivalent_source_contexts_remain_traceable(self):
        source = snapshot([fact(), fact(frame=None)])
        proof = self.prove(source)
        direct = proof['points']['/income/0/quarterly/0']
        self.assertEqual(direct['source'], '/facts/us-gaap/Revenues/units/USD/0')
        self.assertEqual(direct['equivalent_sources'], ['/facts/us-gaap/Revenues/units/USD/1'])
        self.assertEqual(proof['counts']['source_records'], 2)


if __name__ == '__main__':
    unittest.main()
