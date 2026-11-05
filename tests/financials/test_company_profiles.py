"""Security and data-integrity contracts for bounded professional enrichment."""
import copy
import importlib.util
import io
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from etl.universe import BY_TICKER

NOW='2026-11-05T14:00:00Z'
QUOTA={'x-totallimit-remaining':'100','x-totallimit-purchased-remaining':'100','x-ratelimit-remaining':'{\"minute\":10}'}
ISSUER={**BY_TICKER['AAPL'],'website':'https://www.apple.com'}
COMPANY={'status':200,'id':'company-apple','name':'apple','display_name':'Apple Inc.','ticker':'AAPL','website':'apple.com','likelihood':10,'matched':['domain','name','ticker'],'summary':'Apple designs technology products and services.','industry':'Consumer Electronics','founded':1976,'employee_count':201335,'location':{'locality':'Cupertino','region':'California','country':'United States','street_address':'Corporate address'},'linkedin_url':'linkedin.com/company/apple','emails':['do-not-publish@example.test']}
PERSON={'full_name':'Example Executive','job_title':'Chief Financial Officer','job_company_id':'company-apple','job_company_name':'Apple','job_company_website':'apple.com','job_start_date':'2024-01','job_last_verified':'2026-10-01','job_title_levels':['cxo'],'linkedin_url':'linkedin.com/in/example-executive','emails':[{'address':'secret@example.test'}],'mobile_phone':'+1-private','location_street_address':'private-home'}


class MemoryStorage:
    def __init__(self):
        self.objects={}; self.public={}
    def get_object(self,bucket,path,**kwargs):
        return self.objects.get((bucket,path))
    def put_object(self,bucket,path,body,*args,**kwargs):
        self.objects[(bucket,path)]=body.encode() if isinstance(body,str) else body
    def get_json(self,path):
        return copy.deepcopy(self.public.get(path))
    def put_json(self,path,value,**kwargs):
        self.public[path]=copy.deepcopy(value)
    def rpc(self,fn,args):
        return True


class ProfileTests(unittest.TestCase):
    def test_flat_company_response_preserves_provenance_and_avoids_false_headcount(self):
        from etl.company_profiles import build_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        self.assertEqual(p['description'],'Apple designs technology products and services.')
        self.assertEqual(p['professional_profile_count'],201335)
        self.assertIsNone(p['employees'])
        self.assertEqual(p['ticker'],'AAPL'); self.assertEqual(p['cik'],320193)
        self.assertEqual(p['description_source']['observed_at'],NOW)
        self.assertEqual(p['provider_record_id'],'company-apple')
        self.assertNotIn('emails',json.dumps(p)); self.assertNotIn('Corporate address',json.dumps(p))

    def test_identity_requires_ticker_and_verified_domain_when_available(self):
        from etl.company_profiles import build_profile
        for change in [{'ticker':'MSFT'},{'website':'apple.evil.example'},{'likelihood':5}]:
            with self.subTest(change=change),self.assertRaises(ValueError):
                build_profile(ISSUER,{**COMPANY,**change},observed_at=NOW)

    def test_domainless_registry_match_requires_strong_exact_issuer_name(self):
        from etl.company_profiles import build_profile
        with self.assertRaises(ValueError):
            build_profile(BY_TICKER['AAPL'],{**COMPANY,'name':'Apple Hospitality','display_name':'Apple Hospitality'},observed_at=NOW)
        self.assertEqual(build_profile(BY_TICKER['AAPL'],COMPANY,observed_at=NOW)['ticker'],'AAPL')

    def test_leadership_filters_wrong_employer_stale_verification_and_loose_cxo(self):
        from etl.company_profiles import sanitize_leadership
        rows=[PERSON,{**PERSON,'job_company_id':'unrelated'},{**PERSON,'job_last_verified':'2024-06-10'},{**PERSON,'job_title':'Product Manager, WW Chief Strategy Officer'},{**PERSON,'job_title':'Global Client Chief Technology Officer'},{**PERSON,'job_last_verified':None}]
        out=sanitize_leadership(rows,'company-apple',NOW,expected_domain='apple.com')
        self.assertEqual(len(out),1)
        self.assertEqual(out[0]['start_date'],'2024-01')
        self.assertEqual(out[0]['provider_verified_at'],'2026-10-01')
        raw=json.dumps(out)
        for forbidden in ['emails','mobile_phone','street_address','secret@example','photo_url','reports_to']:
            self.assertNotIn(forbidden,raw)

    def test_bad_and_future_partial_dates_do_not_become_invented_dates(self):
        from etl.company_profiles import partial_date
        self.assertEqual(partial_date('2015',NOW),'2015')
        self.assertEqual(partial_date('2015-03',NOW),'2015-03')
        for value in ['2027','2024-02-30','2024-13','2015-3','n/a']:
            self.assertIsNone(partial_date(value,NOW))

    def test_official_current_leadership_and_description_win_over_provider(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        p['leadership']=[{'name':'Old CEO','role':'CEO'}]
        official={'ticker':'AAPL','cik':320193,'description':'Official business description','leadership':[{'name':'John Ternus','role':'Chief Executive Officer','source_url':'https://www.apple.com/leadership/','observed_at':NOW}],'updated_at':NOW,'coverage':{'status':'official_sources','leadership_scope':'Selected official leaders; roster incomplete.'},'sources':[{'name':'Apple','url':'https://www.apple.com','observed_at':NOW}]}
        merged=merge_official_profile(p,official)
        self.assertEqual(merged['leadership'][0]['name'],'John Ternus')
        self.assertEqual(merged['description'],'Official business description')
        self.assertEqual(merged['description_source']['name'],'Apple')
        self.assertEqual(merged['coverage']['leadership_scope'],'Selected official leaders; roster incomplete.')
        self.assertEqual(merged['coverage']['field_observed_at']['description'],NOW)
        self.assertEqual(merged['professional_profile_count'],201335)
        bad={**official,'cik':123}
        self.assertEqual(merge_official_profile(p,bad),p)

    def test_official_snapshot_newer_than_provider_still_wins_when_current(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        official={'ticker':'AAPL','cik':320193,'leadership':[{'name':'Current Official CEO','role':'CEO'}],
                  'updated_at':'2026-11-05T14:20:00Z','coverage':{'status':'official_sources'}}
        out=merge_official_profile(p,official,now='2026-11-05T14:30:00Z')
        self.assertEqual(out['leadership'][0]['name'],'Current Official CEO')
        future={**official,'updated_at':'2027-01-01T00:00:00Z'}
        self.assertEqual(merge_official_profile(p,future,now=NOW)['leadership'],[])

    def test_provider_update_does_not_refresh_old_official_leadership(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW,people=[PERSON])
        official={'ticker':'AAPL','cik':320193,'updated_at':NOW,
                  'coverage':{'status':'official_with_provider_metadata','official_updated_at':'2025-01-01T00:00:00Z'},
                  'leadership':[{'name':'Old Executive','role':'CEO','observed_at':'2025-01-01T00:00:00Z'},
                                {'name':'Undated Executive','role':'CFO'}]}
        merged=merge_official_profile(p,official,now=NOW)
        self.assertEqual(merged['leadership'],p['leadership'])
        self.assertEqual(merged['coverage']['leadership'],'partial_provider_records')
        self.assertEqual(merged['coverage']['official_updated_at'],'2025-01-01T00:00:00Z')

    def test_official_freshness_is_checked_for_each_person(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        official={'ticker':'AAPL','cik':320193,'updated_at':NOW,'coverage':{'status':'official_sources'},
                  'leadership':[{'name':'Current Executive','role':'CEO','observed_at':NOW},
                                {'name':'Old Executive','role':'CFO','observed_at':'2025-01-01T00:00:00Z'},
                                {'name':'Future Executive','role':'COO','observed_at':'2027-01-01T00:00:00Z'}]}
        merged=merge_official_profile(p,official,now=NOW)
        self.assertEqual([p['name'] for p in merged['leadership']],['Current Executive'])
        self.assertEqual(merged['coverage']['field_observed_at']['leadership'],NOW)

    def test_newer_live_official_profile_is_not_replaced_by_older_bundle(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        p.update(description='Current official description',description_source={'name':'Apple','url':'https://apple.com/current','observed_at':NOW},
                 leadership=[{'name':'Current Executive','role':'CEO','observed_at':NOW}])
        p['coverage'].update(status='official_with_provider_metadata',leadership='official_sources',official_updated_at=NOW)
        older='2026-10-01T00:00:00Z'
        official={'ticker':'AAPL','cik':320193,'updated_at':older,'coverage':{'status':'official_sources'},
                  'description':'Older official description','description_source':{'name':'Apple','url':'https://apple.com/older','observed_at':older},
                  'leadership':[{'name':'Previous Executive','role':'CEO','observed_at':older}]}
        merged=merge_official_profile(p,official,now=NOW)
        self.assertEqual(merged['description'],'Current official description')
        self.assertEqual(merged['leadership'][0]['name'],'Current Executive')
        self.assertEqual(merged['coverage']['official_updated_at'],NOW)

    def test_zero_person_limit_returns_no_record(self):
        from etl.company_profiles import sanitize_leadership
        self.assertEqual(sanitize_leadership([PERSON],'company-apple',NOW,limit=0),[])

    def test_unverified_private_reservation_prevents_provider_calls(self):
        from etl.company_profiles import PDLClient,refresh_profiles
        class StaleStorage(MemoryStorage):
            def get_object(self,bucket,path,**kwargs): return None
        calls=[]
        def transport(*args):
            calls.append(args)
            return 200,COMPANY,QUOTA
        with patch('etl.company_profiles.time.sleep'),self.assertRaises(RuntimeError):
            refresh_profiles(StaleStorage(),['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        self.assertEqual(calls,[])

    def test_free_credits_are_usable_but_overages_are_not(self):
        from etl.company_profiles import PDLClient,PDLError
        for total,overages,allowed in [(5,0,True),(5,5,False)]:
            paid=[]
            def transport(method,url,headers,payload,timeout):
                if method=='HEAD': return 400,{}, {'x-totallimit-remaining':str(total),'x-totallimit-purchased-remaining':'0','x-totallimit-overages-remaining':str(overages)}
                paid.append(method);return 200,COMPANY,{}
            client=PDLClient('test',transport=transport)
            if allowed: self.assertEqual(client.company(ISSUER)['ticker'],'AAPL')
            else:
                with self.assertRaises(PDLError): client.company(ISSUER)
            self.assertEqual(len(paid),int(allowed))

    def test_old_provider_supplement_in_official_seed_does_not_override_fresh_provider(self):
        from etl.company_profiles import build_profile,merge_official_profile
        p=build_profile(ISSUER,COMPANY,observed_at=NOW)
        old_source={'name':'People Data Labs','url':'https://docs.peopledatalabs.com/docs/company-enrichment-api','observed_at':'2026-01-01T00:00:00Z'}
        official={'ticker':'AAPL','cik':320193,'industry':'Old provider industry','professional_profile_count':999,
                  'field_sources':{'industry':old_source,'professional_profile_count':old_source},
                  'sources':[{'name':'Apple','url':'https://apple.com','observed_at':NOW},old_source],
                  'coverage':{'status':'official_with_provider_metadata'},'updated_at':NOW}
        merged=merge_official_profile(p,official,now=NOW)
        self.assertEqual(merged['industry'],'Consumer Electronics')
        self.assertEqual(merged['professional_profile_count'],201335)
        self.assertEqual(merged['field_sources']['professional_profile_count']['observed_at'],NOW)

    def test_client_sends_only_professional_allowlist_and_stops_after_quota_failure(self):
        from etl.company_profiles import PDLClient,PDLError
        captured=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD': return 400,{},QUOTA
            captured.append((method,url,headers,payload))
            return 402,{},{}
        client=PDLClient('test-secret',transport=transport)
        with self.assertRaises(PDLError): client.company(ISSUER)
        with self.assertRaises(PDLError): client.company(ISSUER)
        self.assertEqual(len(captured),1)
        self.assertNotIn('test-secret',captured[0][1])
        self.assertIn('data_include=',captured[0][1])

    def test_missing_or_exhausted_preflight_blocks_all_billable_requests(self):
        from etl.company_profiles import PDLClient,PDLError
        for quota in [{},{'x-totallimit-remaining':'0','x-totallimit-purchased-remaining':'100'}, {'x-totallimit-remaining':'100','x-totallimit-purchased-remaining':'0'}]:
            methods=[]
            def transport(method,url,headers,payload,timeout):
                methods.append(method)
                return (400 if method=='HEAD' else 200),COMPANY,quota
            with self.subTest(quota=quota),self.assertRaises(PDLError):
                PDLClient('test',transport=transport).company(ISSUER)
            self.assertEqual(methods,['HEAD'])

    def test_response_credit_balance_caps_the_next_person_request(self):
        from etl.company_profiles import PDLClient,PDLError
        paid=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD':return 400,{}, {'x-totallimit-remaining':'110','x-totallimit-overages-remaining':'100'}
            paid.append(payload['size'])
            return 200,{'data':[]},{'x-totallimit-remaining':'101','x-totallimit-overages-remaining':'100'}
        client=PDLClient('test',transport=transport)
        client.people(COMPANY,size=2)
        with self.assertRaises(PDLError):client.people(COMPANY,size=2)
        self.assertEqual(paid,[2])

    def test_nonfinite_negative_and_malformed_quota_never_authorize_spend(self):
        from etl.company_profiles import PDLClient,PDLError
        invalid=[{'x-totallimit-remaining':value} for value in ['NaN','Infinity','-1']]
        invalid += [{'x-totallimit-overages-remaining':value} for value in ['NaN','Infinity','-1']]
        invalid += [{'x-totallimit-purchased-remaining':value} for value in ['NaN','Infinity','-1']]
        invalid += [{'x-ratelimit-remaining':value} for value in ['{"minute":NaN}','{"minute":-1}','[]','"invalid"']]
        for change in invalid:
            methods=[]
            def transport(method,*args):
                methods.append(method);return 400,{}, {**QUOTA,**change}
            with self.subTest(change=change),self.assertRaises(PDLError):PDLClient('test',transport=transport).company(ISSUER)
            self.assertEqual(methods,['HEAD'])

    def test_bad_response_quota_keeps_result_but_blocks_further_spend(self):
        from etl.company_profiles import PDLClient,PDLError
        methods=[]
        def transport(method,*args):
            methods.append(method)
            return (400,{},QUOTA) if method=='HEAD' else (200,COMPANY,{'x-totallimit-remaining':'NaN','x-totallimit-purchased-remaining':'100'})
        client=PDLClient('test',transport=transport)
        self.assertEqual(client.company(ISSUER)['ticker'],'AAPL')
        with self.assertRaises(PDLError):client.company(ISSUER)
        self.assertEqual(methods,['HEAD','GET'])

    def test_people_request_cannot_exceed_reserved_budget_or_include_contacts(self):
        from etl.company_profiles import PDLClient,PDLError
        payloads=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD': return 400,{},QUOTA
            payloads.append(payload)
            return 200,{'status':200,'data':[]},{'x-call-credits-spent':'0'}
        client=PDLClient('test-secret',people_limit=3,transport=transport)
        client.people(COMPANY,size=2); client.people(COMPANY,size=2)
        with self.assertRaises(PDLError): client.people(COMPANY,size=1)
        self.assertEqual([p['size'] for p in payloads],[2,1])
        self.assertEqual(payloads[0]['dataset'],'resume')
        self.assertNotIn('email',payloads[0]['data_include'])
        self.assertNotIn('phone',payloads[0]['data_include'])
        self.assertIn('job_company_id',json.dumps(payloads[0]['query']))
        self.assertIn('job_title',json.dumps(payloads[0]['query']))

    def test_success_publishes_sanitized_profile_and_positive_cache_prevents_respend(self):
        from etl.company_profiles import PDLClient,refresh_profiles
        paid=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD': return 400,{},QUOTA
            paid.append(method)
            return (200,COMPANY,{}) if method=='GET' else (200,{'data':[PERSON]}, {})
        sb=MemoryStorage()
        first=refresh_profiles(sb,['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        second=refresh_profiles(sb,['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        self.assertEqual(first['published'],['AAPL'])
        self.assertEqual(second['cache_hits'],1)
        self.assertEqual(paid,['GET','POST'])
        profile=sb.public['company-profiles/AAPL.json']
        self.assertEqual(profile['leadership'][0]['role'],'Chief Financial Officer')
        self.assertNotIn('secret@example.test',json.dumps(profile))
        self.assertNotIn('private-home',json.dumps(profile))
        self.assertIsNone(profile['employees'])

    def test_cached_profiles_prevent_repeat_calls_and_negative_cache_stops_404_loops(self):
        from etl.company_profiles import PDLClient,refresh_profiles
        calls=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD': return 400,{},QUOTA
            calls.append(url)
            return 404,{},{}
        sb=MemoryStorage()
        first=refresh_profiles(sb,['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        second=refresh_profiles(sb,['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        self.assertEqual(len(calls),1)
        self.assertEqual(second['cache_hits'],1)
        self.assertEqual(sb.public,{})

    def test_cache_hit_republishes_new_official_context_without_provider_or_budget_change(self):
        from etl.company_profiles import build_profile,refresh_profiles,STATE_PATH
        old='2026-11-04T14:00:00Z'
        sb=MemoryStorage()
        state={'schema_version':1,'companies':{'AAPL':{'status':'matched','attempted_at':old}},
               'budget':{'date':NOW[:10],'company_attempts':5,'people_reserved':10},'paused_until':'2026-11-06T14:00:00Z'}
        sb.put_object('raw',STATE_PATH,json.dumps(state))
        sb.public['company-profiles/AAPL.json']=build_profile(ISSUER,COMPANY,observed_at=old)
        official={'schema_version':1,'ticker':'AAPL','cik':320193,'updated_at':NOW,'coverage':{'status':'official_sources'},
                  'description':'New official description','description_source':{'name':'Apple','url':'https://apple.com','observed_at':NOW},
                  'sources':[{'name':'Apple','url':'https://apple.com','observed_at':NOW}],
                  'leadership':[{'name':'Current Executive','role':'CEO','observed_at':NOW}]}
        with patch('etl.company_profiles._read_official',return_value=official),patch.dict(os.environ,{},clear=True):
            first=refresh_profiles(sb,['AAPL'],now=NOW,official_dir=Path('/nonexistent'))
            second=refresh_profiles(sb,['AAPL'],now=NOW,official_dir=Path('/nonexistent'))
        out=sb.public['company-profiles/AAPL.json']
        self.assertEqual(out['leadership'][0]['name'],'Current Executive')
        self.assertEqual(out['description'],'New official description')
        self.assertEqual(out['professional_profile_count'],201335)
        self.assertEqual(out['professional_profile_count_source']['observed_at'],old)
        self.assertEqual(first['published'],['AAPL'])
        self.assertEqual(second['published'],[])
        self.assertEqual(first['company_requests'],0)
        self.assertEqual(json.loads(sb.get_object('raw',STATE_PATH)),state)

    def test_cache_can_restore_official_only_object_once_without_claiming_provider_data(self):
        from etl.company_profiles import refresh_profiles,STATE_PATH
        sb=MemoryStorage()
        sb.put_object('raw',STATE_PATH,json.dumps({'schema_version':1,'companies':{'AAPL':{'status':'matched','attempted_at':NOW}},
                                                 'budget':{'date':NOW[:10],'company_attempts':0,'people_reserved':0}}))
        official={'schema_version':1,'ticker':'AAPL','cik':320193,'updated_at':NOW,'coverage':{'status':'official_sources'},
                  'description':'Official description','description_source':{'name':'Apple','url':'https://apple.com','observed_at':NOW},
                  'sources':[{'name':'Apple','url':'https://apple.com','observed_at':NOW}],
                  'leadership':[{'name':'Current Executive','role':'CEO','observed_at':NOW}]}
        with patch('etl.company_profiles._read_official',return_value=official),patch.dict(os.environ,{},clear=True):
            first=refresh_profiles(sb,['AAPL'],now=NOW)
            second=refresh_profiles(sb,['AAPL'],now=NOW)
        self.assertEqual(first['published'],['AAPL'])
        self.assertEqual(second['published'],[])
        self.assertEqual(sb.public['company-profiles/AAPL.json']['coverage']['status'],'official_sources')

    def test_lost_lease_during_reservation_or_preflight_blocks_billable_transport(self):
        from etl.company_profiles import PDLClient,refresh_profiles
        for lose_at in ['company_reservation','company_preflight','people_reservation','people_preflight']:
            events=[]
            class Lease:
                current=None
                def __init__(self,*args,**kwargs):self.lost=False;Lease.current=self
                def __enter__(self):return self
                def __exit__(self,*args):return False
                def check(self):
                    if self.lost:raise RuntimeError('Injected lease loss')
            class Storage(MemoryStorage):
                writes=0
                def put_object(self,*args,**kwargs):
                    super().put_object(*args,**kwargs);self.writes+=1
                    if (lose_at=='company_reservation' and self.writes==1) or (lose_at=='people_reservation' and self.writes==2):Lease.current.lost=True
            def transport(method,url,headers,payload,timeout):
                events.append((method,'person/search' in url,Lease.current.lost))
                if method=='HEAD':
                    if (lose_at=='company_preflight' and 'company/enrich' in url) or (lose_at=='people_preflight' and 'person/search' in url):Lease.current.lost=True
                    return 400,{},QUOTA
                return (200,COMPANY,QUOTA) if method=='GET' else (200,{'data':[]},QUOTA)
            with self.subTest(lose_at=lose_at),patch('etl.company_profiles.AggregateLease',Lease),self.assertRaises(RuntimeError):
                refresh_profiles(Storage(),['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
            self.assertFalse(any(lost for method,is_people,lost in events if method!='HEAD'))
            if lose_at.startswith('company'):self.assertFalse(any(method=='GET' for method,_,_ in events))
            else:self.assertFalse(any(method=='POST' for method,_,_ in events))

    def test_deadline_expires_during_preflight_without_sending_paid_request(self):
        from etl.company_profiles import PDLClient,refresh_profiles
        clock=[0];methods=[]
        def transport(method,*args):
            methods.append(method);clock[0]=100
            return 400,{},QUOTA
        with patch('etl.company_profiles.time.monotonic',side_effect=lambda:clock[0]):
            result=refresh_profiles(MemoryStorage(),['AAPL'],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'),time_budget=1)
        self.assertEqual(methods,['HEAD'])
        self.assertEqual(result['status'],'time_budget')

    def test_daily_budget_is_reserved_durably_before_billable_request(self):
        from etl.company_profiles import PDLClient,refresh_profiles,STATE_PATH
        sb=MemoryStorage(); reservations=[]
        def transport(method,url,headers,payload,timeout):
            if method=='HEAD': return 400,{},QUOTA
            state=json.loads(sb.get_object('raw',STATE_PATH))
            reservations.append(state['budget']['company_attempts'])
            return 404,{},{}
        for ticker in ['AAPL','MSFT','NVDA','AMZN','META','GOOGL']:
            refresh_profiles(sb,[ticker],client=PDLClient('test',transport=transport),now=NOW,official_dir=Path('/nonexistent'))
        self.assertEqual(reservations,[1,2,3,4,5])

    def test_empty_allowlist_does_not_construct_client_or_touch_storage(self):
        from etl.company_profiles import refresh_profiles
        class Untouchable:
            def __getattr__(self,name): raise AssertionError(name)
        with patch.dict(os.environ,{},clear=True):
            out=refresh_profiles(Untouchable(),[],now=NOW)
        self.assertEqual(out['status'],'disabled')

    def test_anonymous_cron_is_rejected_before_any_job(self):
        path=Path('api/cron/company_profiles.py')
        spec=importlib.util.spec_from_file_location('company_profile_cron',path)
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        class Request:
            path='/api/cron/company_profiles'
            headers={}
            wfile=io.BytesIO()
            def send_response(self,code): self.code=code
            def send_header(self,*args): pass
            def end_headers(self): pass
        req=Request()
        with patch.dict(os.environ,{'CRON_SECRET':'expected-secret'},clear=True):
            module.handler.do_GET(req)
        self.assertEqual(req.code,401)

if __name__=='__main__': unittest.main()
