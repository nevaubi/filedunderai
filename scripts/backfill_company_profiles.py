"""Explicit, resumable company-profile backfill; never invoked by page views.

The fetch phase reserves bounded provider credits in an atomic local journal
before transport, never retries billable requests, and excludes overage credits.
The assemble phase only uses returned records that pass issuer/role verification.
Publication is a separate operation and preserves newer official evidence.
"""
import argparse
import concurrent.futures
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from etl.common import AggregateLease, Supabase, now_iso
from etl import company_profiles as profiles
from etl.universe import BY_TICKER, SP100

COMPANY_KEYS = set(profiles.COMPANY_FIELDS.split(',')) | {
    'location', 'likelihood', 'matched', 'status', 'dataset_version',
}
PERSON_KEYS = set(profiles.PERSON_FIELDS.split(',')) | {'id'}


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def safe_record(record, kind):
    allowed = COMPANY_KEYS if kind == 'company' else PERSON_KEYS
    result = {key: value for key, value in record.items() if key in allowed}
    if kind == 'company' and isinstance(result.get('location'), dict):
        result['location'] = {key: value for key, value in result['location'].items()
                              if key in ('locality', 'region', 'country')}
    return result


def charged_credits(entry):
    """Only a conclusive provider receipt can release an unused reservation."""
    reserved = entry['credits_reserved']
    if entry.get('status') == 'cancelled_before_transport':
        return 0
    if entry.get('status') not in ('200', '400', '404'):
        return reserved
    try:
        spent = float((entry.get('quota') or {})['x-call-credits-spent'])
        return int(spent) if math.isfinite(spent) and spent.is_integer() and 0 <= spent <= reserved else reserved
    except (KeyError, TypeError, ValueError):
        return reserved


class BudgetedFetch:
    """One explicitly bounded run, with serial reservations and parallel transport."""
    def __init__(self, directory, limits, key, transport=None):
        self.directory = Path(directory)
        self.path = self.directory / 'journal.json'
        self.state = read_json(self.path, {'schema_version': 1, 'operations': {}, 'limits': limits})
        if self.state.get('schema_version') != 1 or self.state.get('limits') != limits:
            raise ValueError('Use the original limits when resuming this journal')
        self.lock = threading.Lock()
        self.key = key
        self.transport = transport or profiles._transport
        self.limits = limits
        self.remaining = {}
        self.next_request = {}
        self.receipts = {}
        self.stopped = set()

    def preflight(self, endpoint):
        status, _, headers = self.transport('HEAD', profiles.API_ROOT + endpoint,
                                            {'X-Api-Key': self.key}, None, 15)
        headers = {k.lower(): v for k, v in headers.items()}
        if status not in (200, 400):
            raise profiles.PDLError(status, 'Quota preflight failed')
        available = math.floor(profiles.PDLClient._non_overage_balance(headers))
        reserved = sum(charged_credits(item) for item in self.state['operations'].values()
                       if item['endpoint'] == endpoint)
        self.remaining[endpoint] = max(0, min(available, self.limits[endpoint] - reserved))
        self.receipts[endpoint] = {k: headers[k] for k in profiles.QUOTA_HEADERS if k in headers}
        return self.remaining[endpoint]

    def call(self, operation, endpoint, parameters, cost, method='POST'):
        query_hash = hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()
        with self.lock:
            old = self.state['operations'].get(operation)
            if old:
                if old.get('endpoint') != endpoint or old.get('query_hash') != query_hash:
                    raise ValueError('Operation already belongs to a different endpoint or query')
                return read_json(self.directory / old['result_file']) if old.get('result_file') else None
            if endpoint in self.stopped or cost > self.remaining.get(endpoint, 0):
                return None
            self.remaining[endpoint] -= cost
            entry = {'endpoint': endpoint, 'credits_reserved': cost, 'status': 'reserved',
                     'requested_at': now_iso(), 'query_hash': query_hash}
            self.state['operations'][operation] = entry
            write_json(self.path, self.state)
            # The documented search allowance is 10/minute; leave room for HEAD.
            gap = 7.0 if endpoint.endswith('/search') else 0.8
            when = max(time.monotonic(), self.next_request.get(endpoint, 0))
            self.next_request[endpoint] = when + gap
        delay = when - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        with self.lock:
            if endpoint in self.stopped:
                entry['status'] = 'cancelled_before_transport'
                self.remaining[endpoint] += cost
                write_json(self.path, self.state)
                return None
        url = profiles.API_ROOT + endpoint
        payload = parameters if method == 'POST' else None
        if method == 'GET':
            url += '?' + urllib.parse.urlencode(parameters)
        try:
            status, body, headers = self.transport(method, url, {
                'X-Api-Key': self.key, 'Accept': 'application/json',
                'Content-Type': 'application/json'}, payload, 15)
        except Exception:
            # An uncertain request remains reserved and is not replayed on resume.
            with self.lock:
                entry['status'] = 'transport_uncertain'
                write_json(self.path, self.state)
            return None
        headers = {k.lower(): v for k, v in headers.items()}
        receipt = {k: headers[k] for k in profiles.QUOTA_HEADERS if k in headers}
        result = None
        if status == 200 and isinstance(body, dict):
            kind = 'person' if endpoint.startswith('person/') else 'company'
            if endpoint.endswith('/search'):
                result = {'data': [safe_record(row, kind) for row in body.get('data', [])
                                   if isinstance(row, dict)],
                          'total': body.get('total'), 'scroll_token': body.get('scroll_token')}
            else:
                result = safe_record(body, kind)
            result['_retrieved_at'] = now_iso()
            result['_endpoint'] = endpoint
        with self.lock:
            entry['status'] = str(status)
            entry['quota'] = receipt
            self.remaining[endpoint] += cost - charged_credits(entry)
            self.receipts[endpoint] = receipt
            if status in (401, 402, 403, 429):
                self.stopped.add(endpoint)
            try:
                available = profiles.PDLClient._non_overage_balance(headers)
                self.remaining[endpoint] = min(self.remaining[endpoint], math.floor(available))
            except (KeyError, TypeError, ValueError):
                self.stopped.add(endpoint)
            if result is not None:
                filename = hashlib.sha256(operation.encode()).hexdigest()[:24] + '.json'
                write_json(self.directory / filename, result)
                entry['result_file'] = filename
            write_json(self.path, self.state)
            print(json.dumps({'operation': operation, 'status': status,
                              'records': len(result.get('data', [])) if result and 'data' in result else int(bool(result)),
                              'remaining': self.remaining.get(endpoint)}), flush=True)
        return result


def load_issuers(path):
    rows = read_json(path, [])
    return {row['ticker']: {**BY_TICKER[row['ticker']], 'legal_name': row.get('name'),
                           'website': row.get('website') or None}
            for row in rows if row.get('ticker') in BY_TICKER
            and row.get('cik') == BY_TICKER[row['ticker']]['cik']}


def verified_records(directory, issuers, basics_directory=None):
    records = {}
    paths=list(Path(directory).glob('*.json'))
    if basics_directory:
        paths.extend(Path(basics_directory).glob('*.json'))
    priority={'company/clean': 1, 'company/search': 2, 'company/enrich': 3}
    for path in paths:
        data = read_json(path)
        if not isinstance(data, dict) or not data.get('_endpoint', '').startswith('company/'):
            continue
        for raw in data.get('data', [data]):
            ticker = str(raw.get('ticker', '')).upper().replace('.', '-')
            issuer = issuers.get(ticker)
            if not issuer:
                continue
            stamp = data.get('_retrieved_at') or now_iso()
            try:
                factory = {'company/search': profiles.build_search_profile,
                           'company/enrich': profiles.build_profile,
                           'company/clean': profiles.build_clean_profile}[data['_endpoint']]
                result = factory(issuer, raw, observed_at=stamp)
            except (KeyError, ValueError):
                continue
            rank=priority[data['_endpoint']]
            if ticker not in records or rank>records[ticker]['priority']:
                records[ticker] = {'record': raw, 'profile': result, 'observed_at': stamp, 'priority': rank}
    return records


def fetch_companies(args, requester, issuers):
    plan = read_json(args.plan)
    for offset in range(0, len(plan.get('search', [])), 12):
        tickers = plan['search'][offset:offset + 12]
        requester.call('companies-search-' + str(offset), 'company/search', {
            'query': {'query': {'terms': {'ticker': [t.lower() for t in tickers]}}},
            'size': len(tickers), 'titlecase': True}, len(tickers))
    def enrich(ticker):
        issuer = issuers[ticker]
        params = {'ticker': ticker, 'name': issuer['name'], 'min_likelihood': 6,
                  'include_if_matched': 'true', 'titlecase': 'true',
                  'data_include': profiles.COMPANY_FIELDS}
        domain = profiles._domain(issuer.get('website'))
        if domain:
            params['website'] = domain
        return requester.call('company-' + ticker, 'company/enrich', params, 1, method='GET')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(enrich, plan.get('enrich', [])))


def fetch_people(args, requester, issuers):
    records = verified_records(args.cache, issuers)
    companies = list({row['record']['id']: row['record'] for row in records.values()}.values())
    plan_path = Path(args.cache) / 'people-plan.json'
    plan = read_json(plan_path)
    if plan is not None and 'requests' not in plan and any(
        item['endpoint']=='person/search' and charged_credits(item)>0
        for item in requester.state['operations'].values()
    ):
        raise ValueError('Legacy person plan has paid reservations; automatic replay is disabled')
    if plan is None:
        plan = {'company_ids': sorted(row['id'] for row in companies), 'created_at': now_iso()}
    plan.setdefault('requests', {})
    today = dt.date.fromisoformat(plan['created_at'][:10])
    cutoff = (today - dt.timedelta(days=180)).isoformat()
    titles = sorted(t for t in profiles.CORPORATE_TITLES if t not in ('president',))
    # Each array stays within the provider's documented 100-element limit.
    role_terms = [{'terms': {'job_title': titles[i:i + 100]}} for i in range(0, len(titles), 100)]
    queries = []
    for offset in range(0, len(plan['company_ids']), 35):
        group = plan['company_ids'][offset:offset + 35]
        query = {'query': {'bool': {'filter': [
            {'terms': {'job_company_id': group}},
            {'bool': {'should': role_terms}},
            {'range': {'job_last_verified': {'gte': cutoff, 'lte': today.isoformat()}}},
        ]}}}
        queries.append({'offset': offset, 'query': query, 'token': None})
    plan.setdefault('queries', queries)
    write_json(plan_path, plan)
    queries=copy.deepcopy(plan['queries'])
    # Give every company group a first page before requesting deeper pages.
    for page in range(4):
        for item in queries:
            if page and not item.get('token'):
                continue
            slot=f"{item['offset']}-{page}"
            request=plan['requests'].get(slot)
            if request is None:
                size = min(80, requester.remaining.get('person/search', 0))
                if size < 1:
                    return
                params = {'query': item['query'], 'size': size, 'dataset': 'resume', 'titlecase': True,
                          'data_include': profiles.PERSON_FIELDS}
                if item['token']:
                    params['scroll_token'] = item['token']
                identity = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
                request={'operation':f'people-{identity}', 'parameters':params, 'cost':size}
                plan['requests'][slot]=request
                write_json(plan_path, plan)
            result = requester.call(request['operation'], 'person/search', request['parameters'], request['cost'])
            if not result:
                item['token'] = None
                continue
            item['token'] = result.get('scroll_token') if len(result.get('data', [])) == request['cost'] else None


def assemble(args, issuers):
    records = verified_records(args.cache, issuers, args.basics_cache)
    people = {}
    for path in Path(args.cache).glob('*.json'):
        data = read_json(path)
        if not isinstance(data, dict) or data.get('_endpoint') != 'person/search':
            continue
        for person in data.get('data', []):
            people.setdefault(person.get('job_company_id'), []).append(person)
    output = Path(args.output)
    counts = {'provider_companies': 0, 'provider_candidates_quarantined': 0, 'official_leaders': 0}
    for ticker, item in records.items():
        profile = item['profile']
        candidates = profiles.sanitize_leadership(
            people.get(item['record']['id'], []), item['record']['id'], now_iso(),
            expected_domain=item['record'].get('website'))
        counts['provider_candidates_quarantined'] += len(candidates)
        profile['description_provider'] = 'People Data Labs'
        old = read_json(output / (ticker + '.json'))
        if old:
            profile = profiles.merge_official_profile(profile, old, provider_people=candidates)
        if not profiles._provider_source(profile.get('description_source')):
            profile.pop('description_provider', None)
        write_json(output / (ticker + '.json'), profile)
        counts['provider_companies'] += 1
        counts['official_leaders'] += len(profile.get('leadership', []))
    write_json(Path(args.cache) / 'assembly-summary.json', counts)
    print(json.dumps(counts), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['companies', 'people', 'assemble'])
    parser.add_argument('--issuers', type=Path, required=True)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--cache', type=Path, required=True)
    parser.add_argument('--basics-cache', type=Path, default=ROOT / 'cache/company-basic-backfill')
    parser.add_argument('--output', type=Path, default=ROOT / 'public/data/company-profiles')
    parser.add_argument('--company-enrich-limit', type=int, default=95)
    parser.add_argument('--company-search-limit', type=int, default=96)
    parser.add_argument('--people-limit', type=int, default=498)
    args = parser.parse_args()
    issuers = load_issuers(args.issuers)
    if not issuers:
        raise ValueError('No registry-verified issuers')
    if args.phase == 'assemble':
        assemble(args, issuers)
        return
    limits = {'company/enrich': args.company_enrich_limit,
              'company/search': args.company_search_limit, 'person/search': args.people_limit}
    if any(type(n) is not int or not 0 <= n <= 5000 for n in limits.values()):
        raise ValueError('Explicit limits must be integers between 0 and 5000')
    args.cache.mkdir(parents=True, exist_ok=True)
    with (args.cache / 'run.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        requester = BudgetedFetch(args.cache, limits, os.environ['PEOPLE_DATA_LABS_API_KEY'])
        endpoints = ['company/enrich', 'company/search'] if args.phase == 'companies' else ['person/search']
        for endpoint in endpoints:
            print(json.dumps({'preflight': endpoint, 'available_for_run': requester.preflight(endpoint)}), flush=True)
        if args.phase == 'companies':
            if not args.plan:
                raise ValueError('An explicit company plan is required')
            fetch_companies(args, requester, issuers)
        else:
            fetch_people(args, requester, issuers)


if __name__ == '__main__':
    main()
