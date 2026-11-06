"""Reviewed issuers publish the reported consolidated total when contract revenue is only a component."""
import unittest

from etl import sec


def obs(concept, value, start='2025-01-01', end='2025-12-31', accn='0000000000-26-000001'):
    return {'val': value, 'start': start, 'end': end, 'accn': accn, 'filed': '2026-02-20', 'form': '10-K', 'fy': 2025, 'fp': 'FY'}


def facts(contract, total):
    return {'us-gaap': {'RevenueFromContractWithCustomerExcludingAssessedTax': {'units': {'USD': [obs('c', contract)]}},
                        'Revenues': {'units': {'USD': [obs('r', total)]}}}}


class ReportedTotalRevenueTests(unittest.TestCase):
    def revenue(self, f, scope):
        row = next(r for r in sec.build_series(f, 'income', total_revenue_scope=scope) if r['label'] == 'Revenue')
        return row['annual'][-1]

    def test_default_keeps_contract_revenue_priority(self):
        self.assertEqual(self.revenue(facts(58_704_000, 907_093_000), False)['v'], 58_704_000)

    def test_reviewed_scope_prefers_larger_same_context_total(self):
        point = self.revenue(facts(58_704_000, 907_093_000), True)
        self.assertEqual((point['v'], point['c']), (907_093_000, 'Revenues'))

    def test_reviewed_scope_never_prefers_a_smaller_total(self):
        self.assertEqual(self.revenue(facts(100, 90), True)['c'], 'RevenueFromContractWithCustomerExcludingAssessedTax')

    def test_reviewed_issuers_are_registered_ciks(self):
        from etl.universe import BY_TICKER
        ciks = {v['cik'] for v in BY_TICKER.values()}
        self.assertTrue(set(sec.REPORTED_TOTAL_REVENUE_CIKS) <= ciks)


if __name__ == '__main__':
    unittest.main()


class RevenueScopeReplacementTests(unittest.TestCase):
    def check(self, ticker, prior_concept, new_concept, label='Revenue'):
        from etl import jobs
        prior = {'ticker': ticker, 'statement': 'income', 'label': label, 'kind': 'A', 'period_end': '2025-12-31',
                 'period_start': '2025-01-01', 'concept': prior_concept, 'unit': 'USD', 'value': 1, 'filed': '2026-02-20', 'derived': False}
        candidate = dict(prior, concept=new_concept, value=5)
        class Store:
            def select(self, table, query):
                return [prior]
        jobs.check_reported_fact_replacements(Store(), ticker, [candidate])

    def test_reviewed_issuer_may_move_component_to_reported_total(self):
        self.check('ADM', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues')

    def test_unreviewed_issuer_scope_change_still_blocks(self):
        with self.assertRaisesRegex(ValueError, 'reported_accounting_scope_changed'):
            self.check('AAPL', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues')

    def test_reviewed_issuer_reverse_or_other_label_still_blocks(self):
        with self.assertRaisesRegex(ValueError, 'reported_accounting_scope_changed'):
            self.check('ADM', 'Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax')
        with self.assertRaisesRegex(ValueError, 'reported_accounting_scope_changed'):
            self.check('ADM', 'CostOfRevenue', 'CostOfGoodsAndServicesSold', label='Cost of revenue')


class PublishedConceptPinTests(unittest.TestCase):
    def facts(self):
        return {'us-gaap': {
            'OtherNonoperatingIncomeExpense': {'units': {'USD': [obs('a', 10)]}},
            'NonoperatingIncomeExpense': {'units': {'USD': [obs('b', 12, accn='0000000000-26-000002')]}}}}

    def row(self, pinned):
        from etl import sec as s
        names = [('Other income', ['NonoperatingIncomeExpense', 'OtherNonoperatingIncomeExpense'])]
        return next(r for r in s.build_series(self.facts(), 'income', concepts=names, pinned=pinned) if r['label'] == 'Other income')['annual'][-1]

    def test_default_uses_concept_priority(self):
        self.assertEqual(self.row(None)['c'], 'NonoperatingIncomeExpense')

    def test_published_concept_keeps_its_period(self):
        point = self.row({('Other income', '2025-01-01', '2025-12-31'): 'OtherNonoperatingIncomeExpense'})
        self.assertEqual((point['c'], point['v']), ('OtherNonoperatingIncomeExpense', 10))
        self.assertEqual(point['selection_reason'], 'published concept retained for this period')

    def test_pin_for_another_period_is_ignored(self):
        self.assertEqual(self.row({('Other income', '2024-01-01', '2024-12-31'): 'OtherNonoperatingIncomeExpense'})['c'], 'NonoperatingIncomeExpense')

    def test_reviewed_total_revenue_is_not_pinned_back(self):
        from etl import sec as s
        row = next(r for r in s.build_series(facts(58, 907), 'income', total_revenue_scope=True,
                   pinned={('Revenue', '2025-01-01', '2025-12-31'): 'RevenueFromContractWithCustomerExcludingAssessedTax'}) if r['label'] == 'Revenue')
        self.assertEqual(row['annual'][-1]['c'], 'Revenues')
