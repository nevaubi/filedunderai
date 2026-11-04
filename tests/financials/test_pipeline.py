import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from etl import common, jobs, repair, screener, sec
from test_financials import fact, facts, quarters


class MemoryStore:
    def __init__(self):
        self.data={}; self.writes=[]; self.db=[]; self.leases={}; self.updates=[]
    def rpc(self, fn, args):
        key=args['lock_name']; owner=args['owner']
        if fn=='acquire_etl_lease':
            if key in self.leases and self.leases[key]!=owner: return False
            self.leases[key]=owner; return True
        if self.leases.get(key)==owner: self.leases.pop(key); return True
        return False
    def get_json(self,path): return copy.deepcopy(self.data.get(path))
    def put_json(self,path,obj,**kw): self.data[path]=copy.deepcopy(obj); self.writes.append(path)
    def upsert(self,table,rows,*args,**kw): self.db.append((table,list(rows)))
    def insert(self,*args,**kw): return [{'id':1}]
    def update(self,*args): self.updates.append(args)
    def select(self,*args): return []


class PipelineRegressions(unittest.TestCase):
    def test_health_distinguishes_outcomes(self):
        for stats,want in [({'done':2},'ok'),({'done':1,'failed':{'B':'timeout'}},'partial'),({'done':0,'failed':{'B':'timeout'}},'error'),({'done':0,'skipped':{'B':'deadline'}},'skipped'),({'symbols':1,'errors':{'B':'skipped: deadline'}},'partial')]:
            self.assertEqual(common.run_outcome(stats),want)

    def test_stale_running_is_detectable(self):
        sb=MemoryStore()
        sb.select=lambda *a:[{'job':'prices','status':'running','started_at':'2000-01-01T00:00:00Z','finished_at':None,'stats':{},'error':None}]
        common.publish_status(sb)
        self.assertEqual(sb.data['meta/status.json']['latest']['prices']['status'],'stale')
        self.assertEqual(sb.data['meta/status.json']['last_ok'],{})

    def test_distributed_lease_excludes_overlapping_publishers(self):
        sb=MemoryStore()
        with common.AggregateLease(sb):
            with self.assertRaises(common.LeaseBusy):
                with common.AggregateLease(sb): pass
        self.assertEqual(sb.leases,{})

    def test_missing_migration_fails_closed(self):
        sb=MemoryStore(); sb.rpc=lambda *a: (_ for _ in ()).throw(RuntimeError('missing RPC'))
        with self.assertRaisesRegex(RuntimeError,'migration'):
            with common.AggregateLease(sb): pass
        self.assertEqual(sb.writes,[])

    def test_lost_or_expired_lease_refuses_write(self):
        sb=MemoryStore()
        with self.assertRaisesRegex(RuntimeError,'lease lost'):
            with common.AggregateLease(sb) as lease:
                lease.valid_until=0
                lease.check()

    def test_price_partial_preserves_missing_symbols_and_revises_entire_window(self):
        sb=MemoryStore()
        sb.data['market.json']={'symbols':{'QQQ':{'bars':[[1,20]]}}}
        sb.data['meta/pricestats/all.json']={'stats':{'QQQ':{'price':20,'as_of':'2026-01-01'}}}
        bars=[[1735689600+i*86400,10,11,9,10+i,100] for i in range(10)]
        fetched={'SPY':{'bars':bars,'meta':{'symbol':'SPY'},'dv':[]}}
        with patch.object(jobs,'UNIVERSE',[]),patch.object(jobs,'MARKET_SYMBOLS',['SPY','QQQ']),patch.object(jobs.prices,'fetch_many',return_value=(fetched,{'QQQ':'skipped: deadline'})),patch.object(jobs,'build_index'):
            jobs.prices_job(sb,intraday=False)
        rows=next(rows for table,rows in sb.db if table=='prices_daily')
        self.assertEqual(len(rows),10)
        self.assertEqual(sb.data['market.json']['symbols']['QQQ']['bars'],[[1,20]])
        self.assertEqual(sb.data['meta/pricestats/all.json']['stats']['QQQ']['price'],20)
        self.assertEqual(sb.updates[-1][2]['status'],'partial')

    def test_strict_cache_read_failure_never_becomes_empty_replacement(self):
        sb=MemoryStore(); sb.get_json=lambda path: (_ for _ in ()).throw(RuntimeError('read failed'))
        with self.assertRaisesRegex(RuntimeError,'read failed'):
            jobs.build_index(sb)
        self.assertEqual(sb.writes,[])

    def test_repair_is_offline_preserves_input_and_writes_review(self):
        f=facts(Revenues=[fact(2_785_400_000)],RevenueFromContractWithCustomerExcludingAssessedTax=[fact(202_500_000)],OperatingLeaseLeaseIncome=[fact(2_582_900_000)])
        bundle={'ticker':'AMT','profile':{},'statements':{'income':[{'label':'Revenue','concept':'RevenueFromContractWithCustomerExcludingAssessedTax','unit':'USD','annual':[],'quarterly':[{'v':202_500_000,'start':'2026-07-01','end':'2026-09-30','accn':'q3','filed':'2026-10-29','c':'RevenueFromContractWithCustomerExcludingAssessedTax','form':'10-Q'}]}]}}
        original=copy.deepcopy(bundle)
        with tempfile.TemporaryDirectory() as td:
            d=Path(td); (d/'bundle.json').write_text(json.dumps(bundle)); (d/'facts.json').write_text(json.dumps({'facts':f}))
            with patch.object(common,'request',side_effect=AssertionError('network prohibited')):
                repair.main(['--bundle',str(d/'bundle.json'),'--companyfacts',str(d/'facts.json'),'--output',str(d/'review')])
            candidate=json.loads((d/'review/candidate-company.json').read_text()); review=json.loads((d/'review/review.json').read_text())
            self.assertEqual(candidate['statements']['income'][0]['quarterly'][0]['v'],2_785_400_000)
            self.assertEqual(review['mode'],'offline_candidate_only')
            self.assertEqual(len(review['inputs']),2)
            self.assertEqual(json.loads((d/'bundle.json').read_text()),original)

    def test_screener_eps_and_instant_semantics(self):
        q=quarters('EPS diluted')['quarterly']
        for r in q: r.update(accn='a',filed='2026-10-30',form='10-Q')
        records=[screener._rec(r,'EarningsPerShareDiluted') for r in q]
        fx=screener.Facts({'q':{'EPS diluted':records,'Total assets':records}},[])
        self.assertEqual(fx.ttm('EPS diluted','2026-09-30')[0],40)
        self.assertIsNone(fx.ttm('Total assets','2026-09-30')[0])
        records[-1][7]=1
        fx=screener.Facts({'q':{'EPS diluted':records}},[])
        self.assertIsNone(fx.ttm('EPS diluted','2026-09-30')[0])

    def test_screener_uses_asof_history(self):
        series=sec.build_series(facts(Revenues=[fact(100,filed='2026-10-29'),fact(90,filed='2027-02-01',accn='revision')]),'income')[0]
        data={'q':{'Revenue':[screener._rec(r,'Revenues') for r in series['quarterly']]},'filing_history':{'Revenue':[screener._rec(r,'Revenues') for r in series['filing_history']['quarterly']]}}
        self.assertEqual(screener.Facts(data,[],as_of='2026-11-01').at('Revenue','2026-09-30')[2],100)
        self.assertEqual(screener.Facts(data,[]).at('Revenue','2026-09-30')[2],90)

    def test_derived_history_survives_later_incompatible_revision(self):
        f=facts(Revenues=[fact(100,'2026-01-01','2026-03-31',accn='q1',filed='2026-05-01'),fact(250,'2026-01-01','2026-06-30',accn='q2',filed='2026-08-01'),fact(90,'2026-01-01','2026-03-31',accn='restated',filed='2026-11-01')])
        r=sec.build_series(f,'income')[0]
        self.assertFalse(any(x['end']=='2026-06-30' for x in r['quarterly']))
        historical=next(x for x in r['filing_history']['quarterly'] if x['end']=='2026-06-30')
        self.assertEqual(historical['v'],150)
        self.assertEqual(historical['superseded_at'],'2026-11-01')
        self.assertEqual(sec.build_series(f,'income',as_of='2026-09-01')[0]['quarterly'][-1]['v'],150)

    def test_insurer_consolidated_scope_not_largest_alias(self):
        f=facts(Revenues=[fact(400)],RevenueFromContractWithCustomerExcludingAssessedTax=[fact(50)],PremiumsEarnedNet=[fact(350)])
        r=sec.build_series(f,'income')[0]['quarterly'][0]
        self.assertEqual(r['v'],400)
        self.assertIn('insurance',r['selection_reason'])

    def test_distribution_yields_require_reported_components(self):
        # Exercise the real screener valuation branch with a profitable company whose
        # statement omits dividends, rather than treating absence as a reported zero.
        import inspect, textwrap
        src=inspect.getsource(screener.screener_job.__wrapped__)
        code=textwrap.dedent(src[src.index('            price = out.get("price")'):src.index('            # correlations')])
        for div,bb,expected in [(None,10,None),(10,None,None),(0,0,0),(10,20,.03)]:
            out={'price':10};prov=screener.Prov()
            inp={key:None for key in ('eps','oi','rev','fcf','eq','ta','tl','tca','tcl','re','debt','cash')}
            inp.update(fin=True,ni=5,div=div,bb=bb,recs={key:[] for key in ('eps','oi','rev','fcf','eq','div','bb','debt','cash')})
            data={'T':{'q':{},'shares':{'v':100,'end':'2026-10-20','basis':'reported_outstanding'}}}
            scope=dict(screener.__dict__,out=out,prov=prov,inp=inp,fundc=data,tk='T',dates=['2026-11-03'],c=[10],fx=None,valuation_history=lambda *a:{})
            exec(compile(code,'production_valuation_branch','exec'),scope)
            self.assertEqual(out.get('sh_yield'),expected)
            self.assertEqual(out.get('div_yield'),None if div is None else div/1000)

    def test_unchanged_legacy_companies_migrate_in_bounded_rotation(self):
        sb=MemoryStore()
        sb.data['meta/fundamentals.json']={
            'OLD':{'ticker':'OLD','material_accession':'same','latest_accession':'same'},
            'CURRENT':{'ticker':'CURRENT','material_accession':'same','latest_accession':'same','pipeline_version':2},
            'OLD2':{'ticker':'OLD2','material_accession':'same','latest_accession':'same','pipeline_version':1}}
        filing={'f':'10-Q','a':'same','d':'2026-10-27','r':'2026-09-30','p':'report.htm','i':'','x':''}
        visited=[]
        def process(store,tk,**kwargs):
            visited.append(tk)
            return dict(store.data['meta/fundamentals.json'][tk],pipeline_version=2)
        universe={tk:{'cik':i+1} for i,tk in enumerate(sb.data['meta/fundamentals.json'])}
        with patch.object(jobs,'BY_TICKER',universe),patch.object(jobs.sec,'submissions',return_value={}),patch.object(jobs.sec,'filing_list',return_value=[filing]),patch.object(jobs,'process_company',side_effect=process),patch.object(jobs,'build_index'),patch.object(jobs,'build_feed'):
            for expected in (['OLD'],['OLD2'],[]):
                done,failed=jobs.companies_job(sb,list(universe),only_changed=True,max_companies=1,time_budget=240,workers=1)
                self.assertEqual(done,expected);self.assertEqual(failed,{})
        self.assertEqual(visited,['OLD','OLD2'])
        self.assertEqual(sb.data['meta/fundamentals.json']['OLD2']['pipeline_version'],2)

    def test_market_cap_uses_same_unknown_and_reported_rules(self):
        from etl.fundamentals import market_cap
        self.assertIsNone(market_cap(10,{'val':100,'basis':'weighted_diluted_proxy'}))
        self.assertEqual(market_cap(10,{'val':100,'basis':'reported_outstanding','end':'2026-09-30'},'2026-11-01'),1000)
        self.assertIsNone(market_cap(10,{'val':100,'basis':'reported_outstanding','end':'2020-09-30'},'2026-11-01'))


class FactStore(MemoryStore):
    def __init__(self, rows, backup_ok=True, delete_ok=True):
        super().__init__(); self.rows=copy.deepcopy(rows); self.raw={}; self.ops=[]; self.backup_ok=backup_ok; self.delete_ok=delete_ok
    def upsert(self, table, rows, keys, **kw):
        self.ops.append('upsert')
        keys=keys.split(','); key=lambda r:tuple(r[k] for k in keys)
        current={key(r):r for r in self.rows}
        current.update({key(r):copy.deepcopy(r) for r in rows});self.rows=list(current.values())
    def select(self, table, query):
        from urllib.parse import parse_qs
        def matches(r):
            for key,items in parse_qs(query).items():
                if key in ('select','order'):continue
                value=items[0]
                if value=='is.true':
                    if r.get(key) is not True:return False
                elif value.startswith('eq.') and str(r.get(key))!=value[3:]:return False
            return True
        return copy.deepcopy([r for r in self.rows if matches(r)])
    def put_object(self, bucket, path, raw, **kw):
        self.ops.append('backup'); self.raw[(bucket,path)]=raw
    def get_object(self, bucket, path):
        self.ops.append('verify_backup');return self.raw[(bucket,path)] if self.backup_ok else b'corrupt backup'
    def delete(self, table, query):
        self.ops.append('delete')
        if self.delete_ok:
            doomed=self.select(table,query);self.rows=[r for r in self.rows if r not in doomed]


class RelationalCleanupRegressions(unittest.TestCase):
    def fixture(self):
        def row(label,end,value,derived,tk='T',concept=None):
            return dict(ticker=tk,statement='income',label=label,kind='Q',period_end=end,period_start='2025-01-01',concept=concept or label,unit='USD/shares',value=value,accession='old',form='10-Q',filed='2025-11-01',derived=derived)
        omitted=row('EPS diluted','2025-12-31',1/3,True,concept='EarningsPerShareDiluted')
        replaced=row('EPS diluted','2025-09-30',.7,True,concept='EarningsPerShareDiluted')
        shares=row('Diluted shares','2025-12-31',100,True,concept='WeightedAverageNumberOfDilutedSharesOutstanding')
        good=row('EPS basic','2025-06-30',.5,False,concept='EarningsPerShareBasic')
        revenue=row('Revenue','2025-12-31',100,True,concept='Revenues')
        other=row('EPS diluted','2025-12-31',.3,True,tk='OTHER',concept='EarningsPerShareDiluted')
        replacement=dict(replaced,value=1.64,derived=False,accession='new')
        return [omitted,replaced,shares,good,revenue,other],replacement,[good,revenue,other]
    def test_obsolete_derived_facts_removed_after_safe_reported_replacement(self):
        rows,replacement,untouched=self.fixture();sb=FactStore(rows)
        result=jobs.persist_normalized_facts(sb,'T',[replacement])
        self.assertEqual(result['deleted'],2)
        self.assertIn(replacement,sb.rows)
        for row in untouched:self.assertIn(row,sb.rows)
        self.assertEqual(len(sb.rows),4)
        self.assertEqual(sb.ops[:3],['upsert','backup','verify_backup'])
        saved=json.loads(next(iter(sb.raw.values())))
        self.assertEqual({r['label'] for r in saved['rows']},{'EPS diluted','Diluted shares'})
        self.assertTrue(all(r['ticker']=='T' and r['derived'] for r in saved['rows']))
    def test_unverified_backup_blocks_every_deletion(self):
        rows,replacement,_=self.fixture();sb=FactStore(rows,backup_ok=False)
        with self.assertRaisesRegex(RuntimeError,'backup verification'):jobs.persist_normalized_facts(sb,'T',[replacement])
        self.assertNotIn('delete',sb.ops);self.assertIn(replacement,sb.rows);self.assertEqual(len(sb.rows),6)
    def test_silent_delete_failure_is_detected_by_exact_key_readback(self):
        rows,replacement,_=self.fixture();sb=FactStore(rows,delete_ok=False)
        with self.assertRaisesRegex(RuntimeError,'still present'):jobs.persist_normalized_facts(sb,'T',[replacement])
        self.assertIn(replacement,sb.rows)


if __name__=='__main__': unittest.main()
