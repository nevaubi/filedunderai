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
