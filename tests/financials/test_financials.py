import copy
import json
from pathlib import Path
import datetime as dt
import unittest
from etl import sec, fundamentals


def fact(v, start='2026-07-01', end='2026-09-30', accn='q3', filed='2026-10-29', **kw):
    return dict(val=v, start=start, end=end, accn=accn, filed=filed, form='10-Q', **kw)


def facts(**concepts):
    return {'us-gaap': {c: {'units': {'USD/shares' if 'PerShare' in c else 'USD': rows}} for c, rows in concepts.items()}}


def quarters(label, values=(10,10,10,10), ends=None):
    starts=['2025-10-01','2026-01-01','2026-04-01','2026-07-01']
    ends=ends or ['2025-12-31','2026-03-31','2026-06-30','2026-09-30']
    return {'label':label,'quarterly':[{'v':v,'start':s,'end':e} for v,s,e in zip(values,starts,ends)],'annual':[]}


class FinancialRegressions(unittest.TestCase):
    def test_amt_total_includes_lease_revenue(self):
        source=json.loads((Path(__file__).parent/"fixtures/amt_revenue_source.json").read_text())
        concepts={}
        for x in source["observations"]:
            concepts.setdefault(x["concept"].split(":")[1],[]).append(fact(float(x["value_text"].replace(",",""))*10**int(x["scale"])))
        f=facts(**concepts)
        r=sec.build_series(f,'income')[0]['quarterly'][0]
        self.assertEqual(r['v'],2_785_400_000)
        self.assertIn('lease',r['selection_reason'])

    def test_industrial_contract_revenue(self):
        r=sec.build_series(facts(RevenueFromContractWithCustomerExcludingAssessedTax=[fact(200)]),'income')[0]
        self.assertEqual(r['quarterly'][0]['v'],200)

    def test_bank_preserves_net_interest_revenue_definition(self):
        f=facts(RevenuesNetOfInterestExpense=[fact(100)], RevenueFromContractWithCustomerExcludingAssessedTax=[fact(30)], InterestIncomeExpenseNet=[fact(70)])
        self.assertEqual(sec.build_series(f,'income')[0]['quarterly'][0]['v'],100)

    def test_eps_cannot_be_subtracted(self):
        f=facts(EarningsPerShareDiluted=[fact(1,'2026-01-01','2026-03-31'),fact(4/3,'2026-01-01','2026-06-30')])
        q=sec.build_series(f,'income')[0]['quarterly']
        self.assertEqual(len(q),1)
        f['us-gaap']['EarningsPerShareDiluted']['units']['USD/shares'].append(fact(.5,'2026-04-01','2026-06-30'))
        self.assertEqual(sec.build_series(f,'income')[0]['quarterly'][-1]['v'],.5)

    def test_vintages_and_cutoff(self):
        f=facts(Revenues=[fact(100,accn='original',filed='2026-10-29'),fact(90,accn='revision',filed='2027-02-01')])
        self.assertEqual(sec.build_series(f,'income')[0]['quarterly'][0]['v'],90)
        self.assertEqual(sec.build_series(f,'income',vintage='original')[0]['quarterly'][0]['v'],100)
        self.assertEqual(sec.build_series(f,'income',as_of='2026-11-01')[0]['quarterly'][0]['v'],100)
        self.assertEqual(len(sec.build_series(f,'income')[0]['filing_history']['quarterly']),2)

    def test_derived_start_and_provenance(self):
        f=facts(Revenues=[fact(100,'2026-01-01','2026-03-31'),fact(250,'2026-01-01','2026-06-30')])
        q=sec.build_series(f,'income')[0]['quarterly'][-1]
        self.assertEqual(q['start'],'2026-04-01')
        self.assertEqual(q['v'],150)
        self.assertEqual(q['accessions'],['q3'])
        self.assertEqual(q['available_at'],'2026-10-29')

    def test_missing_capex_is_not_zero(self):
        st={'cashflow':[quarters('Operating cash flow')]}
        self.assertIsNone(fundamentals.compute(st,{},None)['fcf_ttm'])
        st['cashflow'].append(quarters('Capex',(0,0,0,0)))
        self.assertEqual(fundamentals.compute(st,{},None)['fcf_ttm'],40)

    def test_fcf_periods_must_align(self):
        st={'cashflow':[quarters('Operating cash flow'),quarters('Capex',(1,1,1,1),['2025-09-30','2025-12-31','2026-03-31','2026-06-30'])]}
        self.assertIsNone(fundamentals.compute(st,{},None)['fcf_ttm'])

    def test_ttm_rejects_balances_shares_and_gaps(self):
        for lab in ['Diluted shares','Total assets']:
            self.assertIsNone(fundamentals.ttm({'income':[quarters(lab)]},lab))
        q=quarters('Revenue'); q['quarterly'][1]['start']='2026-02-01'
        self.assertIsNone(fundamentals.ttm({'income':[q]},'Revenue'))

    def test_share_duplicates_are_not_summed(self):
        f={'dei':{'EntityCommonStockSharesOutstanding':{'units':{'shares':[
            {'val':100,'end':'2026-09-30','filed':'2026-10-29','accn':'a','form':'10-Q'},
            {'val':100,'end':'2026-09-30','filed':'2026-10-30','accn':'b','form':'10-Q'}]}}}}
        self.assertEqual(sec.profile({'name':'Example'},f)['sharesOutstanding']['val'],100)
        f['dei']['EntityCommonStockSharesOutstanding']['units']['shares'].append({'val':50,'end':'2026-09-30','filed':'2026-10-30','accn':'b','form':'10-Q'})
        self.assertIsNone(sec.profile({'name':'Example'},f)['sharesOutstanding']['val'])

if __name__=='__main__': unittest.main()
