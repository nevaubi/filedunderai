"""Collect resumable company-clean candidates without publishing company profiles.

The free Cleaner API has a different confidence model from paid enrichment.
Keep its score and fuzzy_match values unchanged, require independent identity
validation before publication, and never manufacture enrichment likelihoods.

Usage:
  FU_ENV_FILE=/path/to/private.env python scripts/backfill_company_basics.py

The private cache journals every request before transport. Completed, uncertain,
and interrupted reservations are never retried when the same cache is resumed.
Only allowlisted business fields are retained; street addresses are discarded.
"""

import argparse
import concurrent.futures
from collections import Counter
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# common loads the optional FU_ENV_FILE; the transport has no retries/redirects.
from etl.common import now_iso
from etl import company_profiles as profiles
from etl.company_profiles import _name, _transport
from etl.universe import BY_TICKER

ENDPOINT = 'https://api.peopledatalabs.com/v5/company/clean'
BUSINESS_FIELDS = {
    'id', 'name', 'size', 'founded', 'industry', 'location', 'linkedin_url',
    'website', 'ticker', 'type', 'score', 'fuzzy_match',
}
LOCATION_FIELDS = {'locality', 'region', 'country', 'continent'}
QUOTA_FIELDS = {
    'x-ratelimit-remaining', 'x-ratelimit-limit', 'x-ratelimit-reset',
    'x-totallimit-remaining', 'x-totallimit-limit', 'x-call-credits-spent',
    'x-call-credits-type', 'retry-after',
}
STOP_STATUSES = {401, 402, 403, 429}
MAX_ATTEMPTS = 1100


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def write_private_json(path, value):
    """Same-filesystem replacement with private permissions and durable content."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, prefix='.pending-',
                                         suffix='.json', delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def safe_candidate(body, observed_at):
    result = {key: body[key] for key in BUSINESS_FIELDS if key in body}
    if isinstance(result.get('location'), dict):
        result['location'] = {key: value for key, value in result['location'].items()
                              if key in LOCATION_FIELDS and isinstance(value, str)}
    else:
        # A free-form location string can contain an address below city level.
        result.pop('location', None)
    result['_retrieved_at'] = observed_at
    result['_endpoint'] = 'company/clean'
    return result


def ticker_key(value):
    return str(value or '').strip().upper().replace('.', '-')


def identity_checks(issuer, record):
    """Diagnostic checks, not a publication decision or invented confidence."""
    names = {_name(issuer.get(key)) for key in ('name', 'legal_name')}
    names.discard('')
    return {
        'ticker_exact': ticker_key(record.get('ticker')) == issuer['ticker'],
        'name_exact_normalized': bool(_name(record.get('name')) in names),
    }


def load_issuers(path):
    result = {ticker: {**value, 'ticker': ticker, 'legal_name': None, 'website': None}
              for ticker, value in BY_TICKER.items()}
    for row in read_json(path, []):
        ticker = ticker_key(row.get('ticker'))
        if ticker in result and row.get('cik') == result[ticker]['cik']:
            result[ticker]['legal_name'] = row.get('name')
            result[ticker]['website'] = row.get('website') or None
    return result


def paid_verified_tickers(directory, issuers):
    verified = set()
    for path in Path(directory).glob('*.json'):
        data = read_json(path)
        if not isinstance(data, dict) or data.get('_endpoint') not in (
                'company/enrich', 'company/search'):
            continue
        rows = data.get('data', [data])
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            ticker = ticker_key(raw.get('ticker'))
            issuer = issuers.get(ticker)
            if issuer and all(identity_checks(issuer, raw).values()):
                # A structurally exact paid match can still fail the paid
                # confidence gate. Leave it eligible for the free cleaner.
                factory = (profiles.build_search_profile if data['_endpoint'] ==
                           'company/search' else profiles.build_profile)
                try:
                    factory(issuer, raw, observed_at=data.get('_retrieved_at') or now_iso())
                except ValueError:
                    continue
                verified.add(ticker)
    return verified


def quota_receipt(headers):
    return {str(key).lower(): value for key, value in headers.items()
            if str(key).lower() in QUOTA_FIELDS}


def quota_number(value):
    try:
        number = float(value)
        return math.floor(number) if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def quota_windows(headers, name='x-ratelimit-remaining'):
    raw = {str(key).lower(): value for key, value in headers.items()}.get(name)
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return {}
    return {key: quota_number(number) for key, number in value.items()
            if key in ('minute', 'month')} if isinstance(value, dict) else {}


def candidate_diagnostics(cache, state):
    """Separate identity gaps without reinterpreting provider score/fuzzy_match."""
    categories = Counter()
    fuzzy_flags = Counter()
    exact_fields = Counter()
    missing_ticker_exact_name = 0
    for entry in state['operations'].values():
        if entry.get('status') != '200' or not entry.get('result_file'):
            continue
        record = read_json(Path(cache) / entry['result_file'])
        checks = entry.get('identity_checks', {})
        if checks.get('ticker_exact') and checks.get('name_exact_normalized'):
            category = 'exact_ticker_and_name'
            for key in BUSINESS_FIELDS:
                if record.get(key) not in (None, '', {}):
                    exact_fields[key] += 1
        elif not ticker_key(record.get('ticker')):
            category = 'missing_returned_ticker'
            missing_ticker_exact_name += int(bool(checks.get('name_exact_normalized')))
        elif not checks.get('ticker_exact'):
            category = 'different_returned_ticker'
        else:
            category = 'ticker_matches_name_differs'
        categories[category] += 1
        flag = record.get('fuzzy_match')
        label = 'true' if flag is True else 'false' if flag is False else 'missing_or_other'
        fuzzy_flags[category + ':' + label] += 1
    return {
        'candidate_categories': dict(categories),
        'missing_ticker_but_exact_name': missing_ticker_exact_name,
        'raw_fuzzy_match_flags_by_category': dict(fuzzy_flags),
        'populated_fields_in_exact_candidates': dict(exact_fields),
        'publication_note': 'Candidates require independent publication validation; these checks do not assign a provider likelihood.',
    }


class CleanerBackfill:
    def __init__(self, cache, key, max_attempts=1100, gap=0.8, timeout=15,
                 transport=None, progress_every=25):
        self.cache = Path(cache)
        self.path = self.cache / 'journal.json'
        self.state = read_json(self.path, {
            'schema_version': 1, 'endpoint': ENDPOINT, 'created_at': now_iso(),
            'operations': {}, 'preflights': [],
        })
        if self.state.get('schema_version') != 1 or self.state.get('endpoint') != ENDPOINT:
            raise ValueError('Cache schema or endpoint does not match this collector')
        self.key = key
        self.max_attempts = min(MAX_ATTEMPTS, max(0, max_attempts))
        self.gap = max(0.8, gap)
        self.timeout = min(15, max(1, timeout))
        self.transport = transport or _transport
        self.progress_every = max(1, progress_every)
        self.guard = threading.Lock()
        self.start_guard = threading.Lock()
        self.stop = threading.Event()
        self.stop_reason = None
        self.month_remaining = None
        self.next_start = time.monotonic()
        self.completed_this_run = 0

    def persist(self):
        self.state['updated_at'] = now_iso()
        write_private_json(self.path, self.state)

    def halt(self, reason):
        self.stop_reason = reason
        self.state['stop_reason'] = reason
        self.stop.set()

    def summary(self):
        operations = list(self.state['operations'].values())
        counts = Counter(item.get('status') for item in operations)
        return {
            'attempts_reserved': len(operations),
            'successful_candidates': counts.get('200', 0),
            'exact_ticker_and_name': sum(
                item.get('status') == '200' and
                all(item.get('identity_checks', {}).get(key) is True for key in
                    ('ticker_exact', 'name_exact_normalized')) for item in operations),
            'not_found': counts.get('404', 0),
            'transport_uncertain': counts.get('transport_uncertain', 0),
            'interrupted_reserved': counts.get('reserved', 0),
            'other_statuses': {key: count for key, count in counts.items()
                               if key not in ('200', '404', 'transport_uncertain', 'reserved')},
            'month_remaining_bound': self.month_remaining,
            'stop_reason': self.stop_reason,
        }

    def preflight(self):
        self.state.pop('completed_at', None)
        self.state.pop('summary', None)
        self.state['stop_reason'] = None
        entry = {'method': 'HEAD', 'requested_at': now_iso(), 'status': 'reserved'}
        self.state['preflights'].append(entry)
        self.persist()
        try:
            status, _, headers = self.transport('HEAD', ENDPOINT,
                                                {'X-Api-Key': self.key, 'Accept': 'application/json'},
                                                None, self.timeout)
        except Exception as error:
            entry.update(status='transport_uncertain', error_type=type(error).__name__)
            self.halt('preflight_transport_uncertain')
            self.persist()
            return False
        entry.update(status=str(status), quota=quota_receipt(headers), observed_at=now_iso())
        remaining = quota_windows(headers)
        limits = quota_windows(headers, 'x-ratelimit-limit')
        month = remaining.get('month')
        minute = remaining.get('minute')
        if status not in (200, 400):
            self.halt('preflight_http_' + str(status))
        elif month is None or minute is None:
            self.halt('preflight_missing_minute_or_month_allowance')
        elif month <= 0 or minute <= 0:
            self.halt('preflight_allowance_exhausted')
        else:
            self.month_remaining = month
            # Honour the actual account allowance, while retaining the 0.8s floor.
            per_minute = limits.get('minute') or minute
            self.gap = max(self.gap, 60.0 / max(1, per_minute))
            self.next_start = time.monotonic() + self.gap
        self.persist()
        print(json.dumps({'phase': 'preflight', 'http_status': status,
                          'remaining': remaining, 'limits': limits,
                          'minimum_start_gap_seconds': self.gap,
                          'stop_reason': self.stop_reason}), flush=True)
        return not self.stop.is_set()

    def call(self, issuer):
        ticker = issuer['ticker']
        parameters = {'name': issuer['name']}
        if issuer.get('website'):
            parameters['website'] = issuer['website']
        # Serialize actual starts, not only scheduled timestamps. A stop response
        # cancels queued work; only already in-flight requests can still finish.
        with self.start_guard:
            wait = max(0, self.next_start - time.monotonic())
            if self.stop.wait(wait):
                return
            with self.guard:
                if ticker in self.state['operations'] or self.stop.is_set():
                    return
                if len(self.state['operations']) >= self.max_attempts:
                    self.halt('maximum_attempts_reached')
                    self.persist()
                    return
                if self.month_remaining is None or self.month_remaining <= 0:
                    self.halt('monthly_allowance_reserved')
                    self.persist()
                    return
                self.month_remaining -= 1
                entry = {
                    'requested_ticker': ticker, 'cik': issuer['cik'],
                    'registry_name': issuer['name'], 'legal_name': issuer.get('legal_name'),
                    'query': parameters, 'requested_at': now_iso(), 'status': 'reserved',
                    'query_hash': hashlib.sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest(),
                }
                self.state['operations'][ticker] = entry
                self.persist()
            self.next_start = time.monotonic() + self.gap
        url = ENDPOINT + '?' + urllib.parse.urlencode(parameters)
        try:
            status, body, headers = self.transport(
                'GET', url, {'X-Api-Key': self.key, 'Accept': 'application/json'},
                None, self.timeout)
        except Exception as error:
            with self.guard:
                entry.update(status='transport_uncertain', error_type=type(error).__name__,
                             completed_at=now_iso())
                self.completed_this_run += 1
                self.persist()
                self.report_progress()
            return
        observed_at = now_iso()
        with self.guard:
            entry.update(status=str(status), quota=quota_receipt(headers), completed_at=observed_at)
            if status in STOP_STATUSES:
                self.halt('http_' + str(status))
            returned_month = quota_windows(headers).get('month')
            if returned_month is not None:
                self.month_remaining = min(self.month_remaining, returned_month)
            if status == 200 and isinstance(body, dict):
                candidate = safe_candidate(body, observed_at)
                filename = ticker + '.json'
                write_private_json(self.cache / filename, candidate)
                entry['result_file'] = filename
                entry['identity_checks'] = identity_checks(issuer, candidate)
            elif status == 200:
                entry['status'] = 'invalid_200_body'
            self.completed_this_run += 1
            self.persist()
            self.report_progress()

    def report_progress(self):
        if self.completed_this_run % self.progress_every == 0 or self.stop.is_set():
            print(json.dumps({'phase': 'progress', **self.summary()}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache', type=Path, default=ROOT / 'cache/company-basic-backfill')
    parser.add_argument('--paid-cache', type=Path, default=ROOT / 'cache/company-profile-backfill')
    parser.add_argument('--issuers', type=Path, default=Path('/tmp/filedunder-enrichment-issuers.json'))
    parser.add_argument('--profiles', type=Path, default=ROOT / 'public/data/company-profiles')
    parser.add_argument('--max-attempts', type=int, default=MAX_ATTEMPTS)
    parser.add_argument('--workers', type=int, choices=range(1, 7), default=5)
    parser.add_argument('--gap', type=float, default=0.8)
    parser.add_argument('--timeout', type=float, default=15)
    parser.add_argument('--progress-every', type=int, default=25)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if not math.isfinite(args.gap) or not math.isfinite(args.timeout):
        parser.error('gap and timeout must be finite')
    if not 0 <= args.max_attempts <= MAX_ATTEMPTS:
        parser.error('max-attempts must be between 0 and 1100')
    os.umask(0o077)
    args.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(args.cache, 0o700)
    with (args.cache / 'run.lock').open('a') as lock:
        os.chmod(args.cache / 'run.lock', 0o600)
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error('Another collector already holds this cache lock')
        issuers = load_issuers(args.issuers)
        paid = paid_verified_tickers(args.paid_cache, issuers)
        published = {ticker for ticker in issuers if (args.profiles / (ticker + '.json')).exists()}
        old = read_json(args.cache / 'journal.json', {'operations': {}})
        attempted = set(old.get('operations', {}))
        targets = [issuer for ticker, issuer in sorted(issuers.items())
                   if ticker not in published and ticker not in paid and ticker not in attempted]
        plan = {'phase': 'plan', 'universe': len(issuers), 'existing_profiles': len(published),
                'verified_paid_tickers': len(paid), 'previous_attempts': len(attempted),
                'pending_targets': len(targets), 'max_attempts': args.max_attempts,
                'workers': args.workers, 'cache': str(args.cache)}
        print(json.dumps(plan), flush=True)
        if args.dry_run or not targets:
            return 0
        key = os.environ.get('PEOPLE_DATA_LABS_API_KEY')
        if not key:
            parser.error('PEOPLE_DATA_LABS_API_KEY is not configured; use FU_ENV_FILE')
        collector = CleanerBackfill(args.cache, key, args.max_attempts, args.gap,
                                    args.timeout, progress_every=args.progress_every)
        collector.state['plan'] = {key: value for key, value in plan.items() if key != 'cache'}
        if not collector.preflight():
            print(json.dumps({'phase': 'complete', **collector.summary()}), flush=True)
            return 2
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            try:
                for _ in pool.map(collector.call, targets):
                    pass
            except KeyboardInterrupt:
                with collector.guard:
                    collector.halt('interrupted')
                    collector.persist()
        with collector.guard:
            collector.state['completed_at'] = now_iso()
            collector.state['summary'] = {
                **collector.summary(),
                'identity_diagnostics': candidate_diagnostics(args.cache, collector.state),
            }
            collector.persist()
            write_private_json(args.cache / 'summary.json', collector.state['summary'])
        print(json.dumps({'phase': 'complete', **collector.state['summary']}), flush=True)
        return 2 if collector.stop.is_set() else 0


if __name__ == '__main__':
    raise SystemExit(main())
