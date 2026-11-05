"""Bounded, server-only professional company profiles.

The browser only reads sanitized JSON. It never calls PDL or starts enrichment.
This module performs no I/O on import. Live refresh is explicit, authenticated by
its cron caller, serialized with an ETL lease, and capped per UTC day.
"""
import copy
import datetime as dt
import ipaddress
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import unicodedata
from pathlib import Path

from .common import AggregateLease
from .universe import BY_TICKER

API_ROOT = 'https://api.peopledatalabs.com/v5/'
COMPANY_DOCS = 'https://docs.peopledatalabs.com/docs/company-enrichment-api'
COMPANY_SEARCH_DOCS = 'https://docs.peopledatalabs.com/docs/company-search-api'
COMPANY_CLEAN_DOCS = 'https://docs.peopledatalabs.com/docs/cleaner-apis-reference'
PEOPLE_DOCS = 'https://docs.peopledatalabs.com/docs/person-search-api'
STATE_PATH = 'company-profiles/state.json'
MAX_COMPANIES = 5
MAX_PEOPLE = 10
COMPANY_FIELDS = 'id,name,display_name,ticker,website,industry,summary,headline,founded,employee_count,size,location.locality,location.region,location.country,linkedin_url'
PERSON_FIELDS = 'full_name,job_title,job_company_id,job_company_name,job_company_website,job_start_date,job_last_verified,linkedin_url'
# Exact titles, not PDL's broad cxo classification (which includes divisional roles).
CORPORATE_TITLES = {
    'chief executive officer', 'chief financial officer', 'chief operating officer',
    'chief technology officer', 'chief information officer', 'chief legal officer',
    'chief people officer', 'chief accounting officer', 'chief marketing officer',
    'chief compliance officer', 'chief human resources officer', 'general counsel',
    'president', 'ceo', 'cfo', 'coo', 'cto', 'cio',
    'president and chief executive officer', 'chairman and chief executive officer',
    'chair and chief executive officer', 'founder and chief executive officer',
    'co-founder and chief executive officer', 'cofounder and chief executive officer',
    'executive vice president and chief financial officer',
}
# Include common corporate title combinations without admitting division or region
# qualifiers. Exact query terms reduce paid records that would fail validation.
for _prefix in ('executive vice president', 'senior vice president', 'evp', 'svp', 'president'):
    for _role in ('chief financial officer', 'chief operating officer', 'chief technology officer',
                  'chief information officer', 'chief legal officer', 'general counsel', 'cfo', 'coo', 'cto'):
        for _separator in (' and ', ', ', ' & '):
            CORPORATE_TITLES.add(_prefix + _separator + _role)
for _prefix in ('chairman', 'chair', 'president', 'founder', 'co-founder', 'cofounder'):
    for _role in ('chief executive officer', 'ceo'):
        for _separator in (' and ', ', ', ' & '):
            CORPORATE_TITLES.add(_prefix + _separator + _role)
QUOTA_HEADERS = ('x-call-credits-spent','x-call-credits-type','x-totallimit-remaining',
                 'x-totallimit-purchased-remaining','x-totallimit-overages-remaining',
                 'x-ratelimit-remaining','x-ratelimit-reset')


def _utc(value=None):
    if isinstance(value, dt.datetime):
        return value.astimezone(dt.timezone.utc)
    if value:
        return dt.datetime.fromisoformat(str(value).replace('Z','+00:00')).astimezone(dt.timezone.utc)
    return dt.datetime.now(dt.timezone.utc)


def _iso(value):
    return _utc(value).strftime('%Y-%m-%dT%H:%M:%SZ')


def _text(value, maximum=4000):
    if not isinstance(value,str):
        return None
    value=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',value).strip()
    return value[:maximum] or None


def safe_url(value):
    value=_text(value,2000)
    if not value: return None
    if '://' not in value: value='https://'+value
    try:
        p=urllib.parse.urlsplit(value)
        host=(p.hostname or '').lower()
        if p.scheme not in ('https','http') or p.username or p.password or '.' not in host:
            return None
        if host.endswith(('.localhost','.local','.internal')): return None
        try:
            if not ipaddress.ip_address(host).is_global: return None
        except ValueError:
            pass
        return urllib.parse.urlunsplit((p.scheme,p.netloc,p.path,p.query,''))
    except ValueError:
        return None


def _domain(value):
    url=safe_url(value)
    return urllib.parse.urlsplit(url).hostname.lower().removeprefix('www.') if url else None


def _name(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(c))
    value=re.sub(r'\s*(?:/+[a-z]{2}/*|\([a-z]{2}\))\s*$','',value,flags=re.I)
    value=re.sub(r'\((?:nasdaq|nyse)\s*:[^)]*\)','',value,flags=re.I)
    value=value.replace("'",'').replace('’','')
    words=re.findall(r'[a-z0-9]+',value.lower())
    suffixes={'inc','incorporated','corp','corporation','co','company','companies','limited','ltd','plc','the','and'}
    return ' '.join(w for w in words if w not in suffixes)


def _title(value):
    value=re.sub(r'\s+',' ',str(value or '').casefold()).strip()
    value=re.sub(r'\s*(?:,|&)\s*',' and ',value)
    aliases={'ceo':'chief executive officer','cfo':'chief financial officer','coo':'chief operating officer',
             'cto':'chief technology officer','cio':'chief information officer','evp':'executive vice president',
             'svp':'senior vice president'}
    for short,full in aliases.items(): value=re.sub(r'\b'+short+r'\b',full,value)
    return re.sub(r'\s+',' ',value).strip()


def _corporate_title(value):
    return _title(value) in {_title(title) for title in CORPORATE_TITLES}


def _person_name(value):
    value=''.join(c for c in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+',value.casefold()))


def _leader_rank(person):
    role=_title(person.get('job_title') or person.get('role'))
    return next((i for i,title in enumerate(('chief executive officer','chief financial officer','chief operating officer',
                                             'chief technology officer','chief legal officer')) if title in role),10)


def _role_family(person):
    role=_title(person.get('job_title') or person.get('role'))
    titles=('chief executive officer','chief financial officer','chief operating officer',
            'chief technology officer','chief information officer','chief legal officer',
            'chief people officer','chief accounting officer','chief marketing officer',
            'chief compliance officer','chief human resources officer','general counsel')
    return next((title for title in titles if title in role),role)


def _linkedin_url(value,kind):
    url=safe_url(value)
    if not url: return None
    parsed=urllib.parse.urlsplit(url)
    return url if re.fullmatch(r'(?:www\.)?linkedin\.com',parsed.hostname or '') and parsed.path.startswith('/'+kind+'/') else None


def partial_date(value, observed_at=None):
    """Keep source precision; never complete missing months/days or accept future dates."""
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?',value): return None
    try:
        parsed=dt.date.fromisoformat(value+('-01-01' if len(value)==4 else '-01' if len(value)==7 else ''))
    except ValueError:
        return None
    return value if dt.date(1800,1,1)<=parsed<=_utc(observed_at).date() else None


def _matches(issuer,record):
    if not isinstance(record,dict) or record.get('status',200)!=200: return False
    ticker=lambda v: str(v or '').strip().upper().replace('.','-')
    if ticker(record.get('ticker'))!=ticker(issuer['ticker']): return False
    likelihood=record.get('likelihood')
    if type(likelihood) not in (int,float) or not math.isfinite(likelihood) or not 6<=likelihood<=10: return False
    expected=_domain(issuer.get('website') or issuer.get('domain'))
    if expected: return _domain(record.get('website'))==expected
    # Without a verified domain, require both a stronger match and the issuer name.
    expected_names={_name(issuer.get(key)) for key in ('name','display_name','legal_name')} - {''}
    return likelihood>=8 and bool(expected_names & {_name(record.get('name')),_name(record.get('display_name'))})


def _search_matches(issuer,record):
    if not isinstance(record,dict) or record.get('status',200)!=200 or not record.get('id'): return False
    ticker=lambda v: str(v or '').strip().upper().replace('.','-')
    if ticker(record.get('ticker'))!=ticker(issuer['ticker']): return False
    expected=_domain(issuer.get('website') or issuer.get('domain'))
    if expected: return _domain(record.get('website'))==expected
    expected_names={_name(issuer.get(key)) for key in ('name','display_name','legal_name')} - {''}
    return bool(expected_names & {_name(record.get('name')),_name(record.get('display_name'))})


def sanitize_leadership(records, company_id, observed_at, expected_domain=None, limit=MAX_PEOPLE):
    out=[]; seen=set(); today=_utc(observed_at).date()
    if limit<=0 or not company_id: return out
    rows=sorted([p for p in records if isinstance(p,dict)],key=_leader_rank) if isinstance(records,list) else []
    for person in rows:
        if not isinstance(person,dict) or person.get('job_company_id')!=company_id: continue
        if expected_domain and _domain(person.get('job_company_website'))!=_domain(expected_domain): continue
        name=_text(person.get('full_name'),160); role=_text(person.get('job_title'),200)
        if not name or not role or not _corporate_title(role): continue
        verified=partial_date(person.get('job_last_verified'),observed_at)
        if not verified or len(verified)!=10 or (today-dt.date.fromisoformat(verified)).days>180: continue
        employment_start=partial_date(person.get('job_start_date'),observed_at)
        if person.get('job_start_date') and not employment_start: continue
        url=_linkedin_url(person.get('linkedin_url'),'in')
        identity=(name.casefold(),role.casefold())
        if identity in seen: continue
        seen.add(identity)
        out.append({'name':name,'role':role,'start_date':None,'employment_start_date':employment_start,
                    'tenure_note':'Provider employment start; the appointment date for this specific role is not verified.' if employment_start else None,
                    'profile_url':url,'linkedin_url':url,'source_url':PEOPLE_DOCS,'source_name':'People Data Labs',
                    'observed_at':_iso(observed_at),'provider_verified_at':verified,
                    'group':'Executive leadership' if 'chief executive officer' in _title(role) else 'Senior leadership'})
        if len(out)>=max(0,min(MAX_PEOPLE,limit)): break
    return out


def _field_observed_at(profile):
    dates={}
    sources={**profile.get('field_sources',{}),'description':profile.get('description_source')}
    for field,source in sources.items():
        if profile.get(field) is not None and isinstance(source,dict) and source.get('observed_at'):
            dates[field]=source['observed_at']
    observed=[person.get('observed_at') for person in profile.get('leadership',[]) if person.get('observed_at')]
    if observed: dates['leadership']=max(observed)
    return dates


def _timestamp(value):
    if not value: return None
    try: return _utc(value)
    except (TypeError,ValueError,OverflowError): return None


def _provider_source(source):
    name=source.get('name','') if isinstance(source,dict) else ''
    url=source.get('url') if isinstance(source,dict) else source
    return 'people data labs' in str(name).lower() or _domain(url) in ('docs.peopledatalabs.com','api.peopledatalabs.com','peopledatalabs.com')


def _official_observed_at(profile):
    coverage=profile.get('coverage') or {}
    if coverage.get('official_updated_at'):
        return coverage['official_updated_at']
    # A mixed record's update time may describe only a new provider supplement.
    return profile.get('updated_at') if coverage.get('status')=='official_sources' else None


def _current_official_leadership(profile,now):
    if not isinstance(profile,dict) or (profile.get('coverage') or {}).get('status') not in ('official_sources','official_with_provider_metadata'):
        return []
    fallback=_official_observed_at(profile); current=[]; now=_utc(now)
    for person in profile.get('leadership') or []:
        if not isinstance(person,dict) or not person.get('name') or not (person.get('role') or person.get('title')): continue
        if _provider_source({'name':person.get('source_name'),'url':person.get('source_url')}): continue
        observed=person.get('observed_at') or fallback
        when=_timestamp(observed)
        if when is None or not 0<=(now-when).total_seconds()<=180*86400: continue
        row=copy.deepcopy(person); row['observed_at']=_iso(when); current.append(row)
    return current


def _field_source(profile,field):
    if field=='description' and profile.get('description_source'):
        return profile['description_source']
    return (profile.get('field_sources') or {}).get(field) or profile.get(field+'_source')


def _source_time(profile,field,source):
    if isinstance(source,dict) and source.get('observed_at'):
        return _timestamp(source['observed_at'])
    if field=='images':
        dates=[_timestamp(image.get('observed_at')) for image in profile.get('images',[]) if isinstance(image,dict)]
        if any(dates): return max(date for date in dates if date)
    return _timestamp(_official_observed_at(profile))


def _unique_sources(sources):
    out=[]; seen=set()
    for source in sources:
        identity=json.dumps(source,sort_keys=True,separators=(',',':'))
        if identity not in seen: seen.add(identity); out.append(copy.deepcopy(source))
    return out


def build_profile(issuer,record,observed_at=None,people=None,_endpoint='company/enrich'):
    """Convert only allowed public business/professional fields from the flat API response."""
    matcher=_matches if _endpoint=='company/enrich' else _search_matches
    if _endpoint not in ('company/search','company/enrich','company/clean') or not matcher(issuer,record): raise ValueError('Company identity was not verified')
    observed_at=_iso(observed_at)
    docs={'company/search':COMPANY_SEARCH_DOCS,'company/enrich':COMPANY_DOCS,'company/clean':COMPANY_CLEAN_DOCS}[_endpoint]
    match={'ticker':issuer['ticker'],'domain':_domain(record.get('website')),
           'basis':'ticker_and_verified_domain' if _domain(issuer.get('website') or issuer.get('domain')) else 'ticker_and_exact_issuer_name'}
    if _endpoint=='company/enrich': match['likelihood']=record.get('likelihood')
    if _endpoint=='company/clean':
        # Cleaner scores/fuzzy flags are provider diagnostics, not probabilities.
        match['provider_fuzzy_match']=record.get('fuzzy_match')
        match['provider_score']=record.get('score')
    source={'name':'People Data Labs','url':docs,'endpoint':API_ROOT+_endpoint,
            'record_id':_text(record.get('id'),120),'observed_at':observed_at,
            'match':match}
    description=_text(record.get('summary')) or _text(record.get('headline'))
    loc=record.get('location') if isinstance(record.get('location'),dict) else {}
    hq=', '.join(str(loc[k]).strip() for k in ['locality','region','country'] if isinstance(loc.get(k),str) and loc[k].strip()) or None
    founded=record.get('founded')
    founded=founded if type(founded) is int and 1800<=founded<=_utc(observed_at).year else None
    count=record.get('employee_count')
    count=count if type(count) is int and count>=0 else None
    # Even fresh provider employment records can misidentify a division's chief
    # or an outdated role as corporate leadership. Only an official roster may
    # establish a name and role; provider candidates can supplement its links.
    leadership=[]
    profile={
        'schema_version':1,'ticker':issuer['ticker'],'cik':issuer['cik'],'name':issuer['name'],
        'description':description,'description_source':source if description else None,'source_url':docs,
        'website':safe_url(record.get('website')),'industry':_text(record.get('industry'),150),'headquarters':hq,
        'linkedin_url':_linkedin_url(record.get('linkedin_url'),'company'),
        'founded':founded,'employees':None,'professional_profile_count':count,
        'professional_profile_count_basis':'People Data Labs profiles associated with the company; not company-reported headcount',
        'professional_profile_count_source':source,'provider_record_id':source['record_id'],
        'leadership':leadership,'images':[],'sources':[source],'updated_at':observed_at,
        'field_sources':{k:source for k in ['website','industry','headquarters','founded','professional_profile_count','linkedin_url']},
        'coverage':{'status':'provider_enriched','description':'available' if description else 'unavailable',
                    'leadership':'partial_provider_records' if leadership else 'unavailable','images':'unavailable',
                    'leadership_scope':'Selected leadership profiles; this is not a complete executive or board roster.'},
    }
    if leadership: profile['sources'].append({'name':'People Data Labs professional records','url':PEOPLE_DOCS,'endpoint':API_ROOT+'person/search','observed_at':observed_at,'company_id':source['record_id']})
    profile['coverage']['field_observed_at']=_field_observed_at(profile)
    return profile


def build_search_profile(issuer,record,observed_at=None,people=None):
    """Search has no likelihood score; verify its ticker plus name/domain explicitly."""
    return build_profile(issuer,record,observed_at=observed_at,people=people,_endpoint='company/search')


def build_clean_profile(issuer,record,observed_at=None):
    """Cleaner identity needs the same exact issuer evidence as company search."""
    return build_profile(issuer,record,observed_at=observed_at,_endpoint='company/clean')


def merge_official_profile(profile,official,now=None,provider_people=None):
    """Curated official data wins; provider facts remain separately attributed."""
    if not isinstance(official,dict) or official.get('ticker')!=profile.get('ticker') or official.get('cik')!=profile.get('cik'):
        return profile
    if (official.get('coverage') or {}).get('status') not in ('official_sources','official_with_provider_metadata'):
        return profile
    now=_utc(now); merged=copy.deepcopy(profile)
    official_source=(official.get('sources') or [None])[0]
    official_keys=('description','website','industry','headquarters','founded','employees','images')
    copied=set()
    evidence_dates=[]
    for key in official_keys:
        field_source=_field_source(official,key) or official_source
        # Bundled snapshots may contain old provider supplements. They do not
        # become official evidence merely by sharing a file with curated facts.
        if _provider_source(field_source): continue
        incoming_time=_source_time(official,key,field_source)
        if incoming_time and incoming_time>now: continue
        previous_source=_field_source(profile,key)
        previous_time=_source_time(profile,key,previous_source)
        if profile.get(key) not in (None,'',[],{}) and previous_time and not _provider_source(previous_source):
            if incoming_time is None or previous_time>incoming_time: continue
        if official.get(key) not in (None,'',[],{}):
            merged[key]=copy.deepcopy(official[key]); copied.add(key)
            if incoming_time: evidence_dates.append(incoming_time)
            if field_source and key in ('description','website','industry','headquarters','founded','employees'):
                merged.setdefault('field_sources',{})[key]=copy.deepcopy(field_source)
    if 'description' in copied:
        merged['description_source']=copy.deepcopy(official.get('description_source') or official_source or {'name':'Official company source','url':official.get('source_url'),'observed_at':official.get('updated_at')})
        for key in ('source_url','description_provider'):
            if official.get(key): merged[key]=copy.deepcopy(official[key])
    if 'employees' in copied and official.get('employees_source'):
        merged['employees_source']=copy.deepcopy(official['employees_source'])
    incoming_leaders=_current_official_leadership(official,now)
    previous_leaders=_current_official_leadership(profile,now)
    leaders=previous_leaders; using_incoming=False
    if incoming_leaders and (not previous_leaders or max(p['observed_at'] for p in incoming_leaders)>=max(p['observed_at'] for p in previous_leaders)):
        leaders=incoming_leaders; using_incoming=True
    if leaders:
        for leader in leaders:
            candidates=[person for person in list(provider_people or [])+profile.get('leadership',[]) if
                        _provider_source({'name':person.get('source_name'),'url':person.get('source_url')})
                        and _person_name(person.get('name'))==_person_name(leader.get('name'))
                        and _role_family(person)==_role_family(leader)]
            if not leader.get('linkedin_url') and len(candidates)==1:
                person=candidates[0]
                verified=partial_date(person.get('provider_verified_at'),now)
                url=_linkedin_url(person.get('linkedin_url') or person.get('profile_url'),'in')
                if url and verified and len(verified)==10 and (now.date()-dt.date.fromisoformat(verified)).days<=180:
                    leader['linkedin_url']=url
                    leader['linkedin_source']={'name':'People Data Labs','url':PEOPLE_DOCS,
                                               'observed_at':person.get('observed_at'),'provider_verified_at':verified}
        merged['leadership']=leaders
        evidence_dates.extend(_timestamp(p['observed_at']) for p in leaders)
    else:
        # Provider-only rows and expired official rosters never become current
        # corporate leadership simply because metadata was refreshed.
        merged['leadership']=[]
    previous_update=_timestamp(profile.get('updated_at'))
    if previous_update and previous_update<=now: evidence_dates.append(previous_update)
    if evidence_dates: merged['updated_at']=_iso(max(evidence_dates))
    official_dates=[_timestamp(_official_observed_at(record)) for record in (profile,official)]
    official_dates=[date for date in official_dates if date and date<=now]
    merged['sources']=_unique_sources(list(official.get('sources') or [])+list(merged.get('sources') or []))
    has_provider=any(_provider_source(source) for source in merged['sources'])
    scope_profile=official if using_incoming else profile
    merged['coverage']={**copy.deepcopy(official.get('coverage') or {}),**merged['coverage'],
                        'status':'official_with_provider_metadata' if has_provider else 'official_sources',
                        'official_updated_at':_iso(max(official_dates)) if official_dates else None,
                        'description':'available' if merged.get('description') else 'unavailable',
                        'leadership':'official_sources' if leaders else 'unavailable',
                        'images':'available' if merged.get('images') else 'unavailable',
                        'leadership_scope':(scope_profile.get('coverage') or {}).get('leadership_scope') or 'Selected leadership profiles; this is not a complete executive or board roster.'}
    merged['coverage']['field_observed_at']=_field_observed_at(merged)
    return merged


class PDLError(RuntimeError):
    def __init__(self,status,reason):
        self.status=status
        super().__init__('PDL '+str(status)+': '+reason)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None


def _transport(method,url,headers,payload,timeout):
    """One request, no billable retries or redirects; bodies/errors never enter logs."""
    body=json.dumps(payload).encode() if payload is not None else None
    request=urllib.request.Request(url,data=body,headers=headers,method=method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request,timeout=timeout) as r:
            content=r.read(1_048_577)
            if len(content)>1_048_576: raise PDLError(502,'Response exceeded size limit')
            return r.status,json.loads(content) if content else {},dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code,{},dict(e.headers)
    except (OSError,ValueError):
        raise PDLError(503,'Provider request failed') from None


class PDLClient:
    def __init__(self,api_key,company_limit=MAX_COMPANIES,people_limit=MAX_PEOPLE,transport=None):
        if not api_key: raise ValueError('PEOPLE_DATA_LABS_API_KEY is not configured')
        self._key=api_key; self.transport=transport or _transport
        self.company_limit=max(0,min(MAX_COMPANIES,company_limit)); self.people_limit=max(0,min(MAX_PEOPLE,people_limit))
        self.company_requests=0; self.people_reserved=0; self.stopped=False; self.quota={}; self._credits={}
        self._request_guard=None

    @staticmethod
    def _non_overage_balance(headers):
        values={}
        for key in ('x-totallimit-remaining','x-totallimit-purchased-remaining','x-totallimit-overages-remaining'):
            if key not in headers: continue
            value=float(headers[key])
            if not math.isfinite(value) or value<0: raise ValueError('Invalid credit balance')
            values[key]=value
        total=values['x-totallimit-remaining']
        if 'x-totallimit-overages-remaining' in values:
            # Total may include free credits as well as purchased credits.
            return max(0,total-values['x-totallimit-overages-remaining'])
        return min(total,values['x-totallimit-purchased-remaining'])

    def _check_rate(self,headers):
        try:
            rate=json.loads(headers.get('x-ratelimit-remaining','{}'))
            if not isinstance(rate,dict): raise ValueError('Invalid rate metadata')
            values=[float(value) for value in rate.values() if value is not None]
            if any(not math.isfinite(value) or value<0 for value in values): raise ValueError('Invalid rate metadata')
        except (ValueError,TypeError):
            self.stopped=True; raise PDLError(503,'Rate-limit metadata invalid') from None
        if any(value==0 for value in values):
            self.stopped=True; raise PDLError(429,'Provider rate limit reached')

    def _request(self,method,url,headers,payload,timeout):
        # The refresh installs its lease/deadline guard here, after quota HEAD
        # requests and directly before every actual transport invocation.
        if self._request_guard: self._request_guard()
        if method=='GET': self.company_requests+=1
        return self.transport(method,url,headers,payload,timeout)

    def _reserve_credits(self,endpoint,needed):
        if self.stopped: raise PDLError(429,'Further requests are paused')
        if endpoint not in self._credits:
            # PDL documents HEAD as a no-credit quota check. Missing input normally
            # returns 400 with quota headers; no paid request follows unknown quota.
            status,_,headers=self._request('HEAD',API_ROOT+endpoint,{'X-Api-Key':self._key,'Accept':'application/json'},None,15)
            headers={k.lower():v for k,v in headers.items()}
            self.quota[endpoint]={k:headers[k] for k in QUOTA_HEADERS if k in headers}
            if status not in (200,400):
                self.stopped=True; raise PDLError(status,'Quota preflight failed')
            try:
                available=self._non_overage_balance(headers)
            except (KeyError,TypeError,ValueError):
                self.stopped=True; raise PDLError(503,'Non-overage credit balance unavailable') from None
            self._check_rate(headers)
            self._credits[endpoint]=available
        if self._credits[endpoint]<needed:
            self.stopped=True; raise PDLError(402,'Insufficient non-overage credits; refusing request')
        self._credits[endpoint]-=needed

    def _call(self,endpoint,params,method='GET'):
        if self.stopped: raise PDLError(429,'Further requests are paused')
        url=API_ROOT+endpoint
        payload=params if method=='POST' else None
        if method=='GET': url+='?'+urllib.parse.urlencode(params)
        status,data,headers=self._request(method,url,{'X-Api-Key':self._key,'Accept':'application/json','Content-Type':'application/json'},payload,15)
        headers={k.lower():v for k,v in headers.items()}
        self.quota[endpoint]={k:headers[k] for k in QUOTA_HEADERS if k in headers}
        if status in (401,402,403,429): self.stopped=True
        if any(key.startswith('x-totallimit-') for key in headers):
            try:
                available=self._non_overage_balance(headers)
                self._credits[endpoint]=min(self._credits.get(endpoint,available),available)
                if available<1: self.stopped=True
            except (KeyError,ValueError,TypeError):
                self.stopped=True; self._credits.pop(endpoint,None)
        else:
            # An omitted balance requires a fresh no-credit preflight next time.
            self._credits.pop(endpoint,None)
        try: self._check_rate(headers)
        except PDLError: pass  # Keep this paid result; stop subsequent spending.
        if status==404: return None
        if status!=200: raise PDLError(status,'Provider request was not successful')
        if not isinstance(data,dict): raise PDLError(502,'Invalid response shape')
        return data

    def company(self,issuer):
        if self.stopped or self.company_requests>=self.company_limit: raise PDLError(429,'Company request budget reached')
        self._reserve_credits('company/enrich',1)
        params={'ticker':issuer['ticker'],'name':issuer['name'],'min_likelihood':6,
                'include_if_matched':'true','titlecase':'true','data_include':COMPANY_FIELDS}
        domain=_domain(issuer.get('website') or issuer.get('domain'))
        if domain: params['website']=domain
        return self._call('company/enrich',params)

    def people(self,company,size=2):
        allowed=min(max(0,int(size)),self.people_limit-self.people_reserved)
        if self.stopped or allowed<=0: raise PDLError(429,'Professional record budget reached')
        if not company.get('id'): return []
        self._reserve_credits('person/search',allowed)
        self.people_reserved+=allowed
        today=_utc().date(); titles=sorted(CORPORATE_TITLES)
        roles=[{'terms':{'job_title':titles[i:i+100]}} for i in range(0,len(titles),100)]
        query={'query':{'bool':{'filter':[{'term':{'job_company_id':company['id']}},
                                         {'bool':{'should':roles}},
                                         {'range':{'job_last_verified':{'gte':(today-dt.timedelta(days=180)).isoformat(),'lte':today.isoformat()}}}]}}}
        data=self._call('person/search',{'query':query,'size':allowed,'dataset':'resume','titlecase':True,'data_include':PERSON_FIELDS},method='POST')
        return (data.get('data') or [])[:allowed] if data else []


def _read_official(ticker,directory):
    path=directory/(ticker+'.json')
    if not path.exists(): return None
    data=json.loads(path.read_text())
    return data if isinstance(data,dict) else None


def _save_state(sb,state,guard=None):
    # New revision also distinguishes identical consecutive writes from stale reads.
    if guard: guard()
    state['revision']=int(state.get('revision',0))+1
    body=json.dumps(state,separators=(',',':')).encode()
    expected=hashlib.sha256(body).digest()
    sb.put_object('raw',STATE_PATH,body,'application/json',cache='no-store')
    for attempt in range(3):
        if guard: guard()
        observed=sb.get_object('raw',STATE_PATH)
        if guard: guard()
        if isinstance(observed,str): observed=observed.encode()
        if isinstance(observed,bytes) and hashlib.sha256(observed).digest()==expected:
            return
        if attempt<2: time.sleep(0.25*(attempt+1))
    raise RuntimeError('Profile reservation read-back failed; refusing provider requests')


class _RefreshDeadline(RuntimeError):
    pass


def _publish_official_update(sb,issuer,official,now,guard):
    """Reconcile curated evidence without spending or resetting the provider TTL."""
    if not official: return False
    observed=_timestamp(official.get('updated_at'))
    if observed and observed>now: return False
    guard()
    path='company-profiles/'+issuer['ticker']+'.json'
    previous=sb.get_json(path)
    guard()
    if (isinstance(previous,dict) and previous.get('schema_version')==1 and isinstance(previous.get('coverage'),dict)
            and previous.get('ticker')==issuer['ticker'] and previous.get('cik')==issuer['cik']):
        merged=merge_official_profile(previous,official,now=now)
    else:
        merged=merge_official_profile(official,official,now=now)
    if merged==previous: return False
    guard(); sb.put_json(path,merged,cache='max-age=3600')
    return True


def refresh_profiles(sb,tickers,client=None,now=None,official_dir=None,time_budget=180):
    """Explicit allowlist only; hard daily ceilings survive invocations and failures."""
    tickers=list(dict.fromkeys(str(t).strip().upper() for t in tickers if str(t).strip()))
    if not tickers: return {'status':'disabled','reason':'No configured ticker allowlist','published':[],'cache_hits':0}
    if len(tickers)>MAX_COMPANIES or any(t not in BY_TICKER for t in tickers):
        raise ValueError('Configure at most five registered tickers')
    now=_utc(now); stamp=_iso(now); deadline=time.monotonic()+min(210,max(1,time_budget))
    official_dir=Path(official_dir or Path(__file__).resolve().parents[1]/'public/data/company-profiles')
    summary={'status':'ok','published':[],'official_updates':[],'cache_hits':0,'failures':[],'company_requests':0,'people_reserved':0}
    budget={}; guard_installed=False; previous_guard=None
    with AggregateLease(sb,name='company-profiles',seconds=300) as lease:
        def guard():
            lease.check()
            if time.monotonic()>=deadline: raise _RefreshDeadline('Profile refresh time budget reached')
        try:
            guard()
            raw=sb.get_object('raw',STATE_PATH)
            state=json.loads(raw) if raw else {'schema_version':1,'companies':{},'budget':{}}
            if not isinstance(state,dict) or state.get('schema_version')!=1 or not isinstance(state.get('companies'),dict):
                raise ValueError('Invalid profile refresh state; refusing requests')
            if not isinstance(state.get('budget'),dict):
                raise ValueError('Invalid profile refresh budget; refusing requests')
            if state['budget'].get('date')!=stamp[:10]:
                state['budget']={'date':stamp[:10],'company_attempts':0,'people_reserved':0}
            budget=state['budget']
            if any(type(budget.get(key)) is not int or budget[key]<0 for key in ('company_attempts','people_reserved')):
                raise ValueError('Invalid profile refresh budget; refusing requests')

            # Curated corrections are independent of paid refresh eligibility,
            # including a positive cache hit, exhausted budget or provider pause.
            officials={}
            for ticker in tickers:
                guard(); issuer=BY_TICKER[ticker]
                official=_read_official(ticker,official_dir)
                if (not isinstance(official,dict) or official.get('ticker')!=ticker or official.get('cik')!=issuer['cik']
                        or (official.get('coverage') or {}).get('status') not in ('official_sources','official_with_provider_metadata')):
                    official=None
                officials[ticker]=official
                if _publish_official_update(sb,issuer,official,now,guard):
                    summary['published'].append(ticker); summary['official_updates'].append(ticker)

            paused=state.get('paused_until') and _utc(state['paused_until'])>now
            if paused:
                summary.update(status='paused',paused_until=state['paused_until'])
            else:
                for ticker in tickers:
                    guard()
                    cached=state['companies'].get(ticker,{})
                    ttl=30 if cached.get('status')=='matched' else 7
                    if cached.get('attempted_at') and 0<=(now-_utc(cached['attempted_at'])).total_seconds()<ttl*86400:
                        summary['cache_hits']+=1; continue
                    if budget['company_attempts']>=MAX_COMPANIES:
                        summary['status']='daily_budget'; break
                    official=officials[ticker]; issuer=dict(BY_TICKER[ticker])
                    if official: issuer['website']=official.get('website')
                    # Cache-only official publication does not need a provider key.
                    if client is None: client=PDLClient(os.environ.get('PEOPLE_DATA_LABS_API_KEY'))
                    if not guard_installed:
                        previous_guard=client._request_guard
                        def request_guard():
                            if previous_guard: previous_guard()
                            guard()
                        client._request_guard=request_guard; guard_installed=True
                    budget['company_attempts']+=1
                    state['companies'][ticker]={'status':'reserved','attempted_at':stamp}
                    # A crash after a charged request cannot reset this reservation.
                    _save_state(sb,state,guard)
                    try:
                        record=client.company(issuer)
                        if record is None:
                            state['companies'][ticker]['status']='no_match'
                        else:
                            build_profile(issuer,record,observed_at=stamp)
                            people=[]
                            official_leadership=bool(_current_official_leadership(official,now))
                            capacity=min(2,MAX_PEOPLE-budget['people_reserved'])
                            needs_links=official_leadership and any(not person.get('linkedin_url') for person in _current_official_leadership(official,now))
                            if capacity>0 and needs_links and not client.stopped:
                                guard(); budget['people_reserved']+=capacity; _save_state(sb,state,guard)
                                try:
                                    people=client.people(record,size=capacity)
                                except PDLError as exc:
                                    summary['failures'].append({'ticker':ticker,'stage':'professional_records','status':exc.status})
                            profile=build_profile(issuer,record,observed_at=stamp,people=people)
                            candidates=sanitize_leadership(people,record.get('id'),stamp,record.get('website'))
                            profile=merge_official_profile(profile,official,now=now,provider_people=candidates)
                            guard(); sb.put_json('company-profiles/'+ticker+'.json',profile,cache='max-age=3600')
                            state['companies'][ticker]={'status':'matched','attempted_at':stamp,'provider_record_id':record.get('id')}
                            if ticker not in summary['published']: summary['published'].append(ticker)
                    except (PDLError,ValueError) as exc:
                        status=exc.status if isinstance(exc,PDLError) else 'identity_mismatch'
                        state['companies'][ticker]['status']=str(status)
                        summary['failures'].append({'ticker':ticker,'stage':'company','status':status})
                    if client.stopped:
                        state['paused_until']=_iso(now+dt.timedelta(days=1)); summary['status']='provider_paused'
                    _save_state(sb,state,guard)
                    if client.stopped: break
        except _RefreshDeadline:
            summary['status']='time_budget'
        finally:
            if guard_installed: client._request_guard=previous_guard
        if client is not None:
            summary['company_requests']=client.company_requests
            summary['people_reserved']=client.people_reserved
            summary['quota']=client.quota
        summary['daily_reserved']=dict(budget)
    return summary
