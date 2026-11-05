"""Prepare stored-input screener candidates and explicitly publish reviewed bytes.

No SEC, Yahoo, news, model or enrichment request belongs to this command.
Preparation is read-only remotely. Apply is a separate leased operation.
"""
import argparse
import base64
import contextlib
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit
from unittest.mock import patch

from . import common, fundamentals, jobs, screener as sc

CODE_PATHS = ('etl/screener.py', 'etl/jobs.py', 'etl/fundamentals.py', 'etl/sec.py',
              'etl/source_provenance.py', 'etl/source_quality.py', 'etl/universe.py',
              'etl/common.py', 'etl/repair_screener.py')


def code_paths():
    registry = sorted(Path(__file__).parent.glob('additional_companies*.json'))
    return CODE_PATHS + tuple('etl/' + p.name for p in registry)


def code_signatures():
    repo = Path(__file__).resolve().parents[1]
    return {p: digest((repo / p).read_bytes()) for p in code_paths()}


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('wb') as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


@contextlib.contextmanager
def stored_input_network(url):
    """Permit selected Supabase reads only; redirects cannot escape the guard."""
    expected = urlsplit(url)
    counts = {'GET': 0, 'read_only_list_POST': 0, 'blocked': 0}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            raise RuntimeError('Blocked storage redirect during read-only preparation')
    opener = urllib.request.build_opener(NoRedirect)
    def guarded(req, *args, **kwargs):
        request_url = req.full_url if isinstance(req, urllib.request.Request) else str(req)
        method = req.get_method() if isinstance(req, urllib.request.Request) else 'GET'
        parsed = urlsplit(request_url)
        host_ok = parsed.scheme == expected.scheme == 'https' and parsed.hostname == expected.hostname and parsed.port in (None, 443)
        allowed_get = method == 'GET' and (parsed.path.startswith('/storage/v1/object/authenticated/') or
            parsed.path in {'/rest/v1/' + t for t in ('filings', 'news_items', 'insider_transactions', 'macro_observations')})
        allowed_list = method == 'POST' and parsed.path == '/storage/v1/object/list/public-data'
        if allowed_list:
            data = json.loads(req.data or b'{}')
            allowed_list = data.get('prefix') in {'kpis', 'segments', 'kpistd/t'} and set(data) <= {'prefix', 'limit', 'offset'}
        if not host_ok or not (allowed_get or allowed_list):
            counts['blocked'] += 1
            raise RuntimeError('Blocked network operation: ' + method + ' ' + parsed.path)
        counts['GET' if allowed_get else 'read_only_list_POST'] += 1
        return opener.open(req, *args, **kwargs)
    with patch.object(urllib.request, 'urlopen', guarded):
        yield counts


class StoredScreenerSupabase(common.Supabase):
    """Single-attempt writes cannot continue retrying after lease expiry."""
    def _write(self, url, **kwargs):
        lease = getattr(self, '_aggregate_lease', None)
        if lease is None:
            raise RuntimeError('Screener publication requires the aggregate lease')
        lease.check()
        remaining = lease.valid_until - time.monotonic() - 5
        if remaining < 1:
            raise RuntimeError('Insufficient lease time for publication')
        result = common.request(url, timeout=min(30, remaining), retries=1, **kwargs)
        lease.check()
        return result

    def put_object(self, bucket, path, content, content_type='application/json', cache='max-age=300'):
        content = content.encode() if isinstance(content, str) else content
        self._write(f'{self.url}/storage/v1/object/{bucket}/{quote(path)}', method='POST', data=content,
                    headers=self._h({'Content-Type': content_type, 'x-upsert': 'true', 'cache-control': cache}))
        return len(content)

    def upsert(self, table, rows, on_conflict, batch=500):
        if table != 'company_metrics' or on_conflict != 'ticker':
            raise ValueError('Only company_metrics upserts are permitted')
        rows = list(rows)
        for offset in range(0, len(rows), batch):
            self._write(f'{self.url}/rest/v1/company_metrics?on_conflict=ticker', method='POST', data=rows[offset:offset + batch],
                        headers=self._h({'Prefer': 'resolution=merge-duplicates,return=minimal', 'Content-Type': 'application/json'}))
        return len(rows)

    def rpc(self, fn, args):
        if fn not in ('acquire_etl_lease', 'release_etl_lease'):
            raise ValueError('Only aggregate lease RPCs are permitted')
        return json.loads(common.request(f'{self.url}/rest/v1/rpc/{fn}', method='POST', data=args,
                                        headers=self._h(), timeout=15, retries=1) or 'null')

    def delete_objects(self, bucket, paths):
        paths = list(paths)
        if bucket not in ('raw', 'public-data') or any(not _scope(bucket, path, set(sc.TICKERS)) for path in paths):
            raise ValueError('Object deletion is outside screener rollback scope')
        for offset in range(0, len(paths), 20):
            self._write(f'{self.url}/storage/v1/object/{bucket}', method='DELETE', data={'prefixes': paths[offset:offset+20]},
                        headers=self._h({'Content-Type': 'application/json'}))

    def delete_metrics(self, tickers):
        if not set(tickers) <= set(sc.TICKERS):
            raise ValueError('Metrics deletion is outside the active universe')
        for offset in range(0, len(tickers), 50):
            selected = ','.join(tickers[offset:offset+50])
            self._write(f'{self.url}/rest/v1/company_metrics?ticker=in.({selected})', method='DELETE',
                        headers=self._h({'Prefer': 'return=minimal'}))

    def deny(self, *args, **kwargs):
        raise RuntimeError('This mutation is outside screener publication scope')

    insert = update = delete = put_raw = deny


def _path(root, family, bucket, path):
    value = Path(path)
    if value.is_absolute() or '..' in value.parts or bucket not in ('raw', 'public-data', 'relations'):
        raise ValueError('Artifact path is outside candidate scope')
    result = (root / family / bucket / value).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError('Artifact path escapes candidate directory')
    return result


def _scope(bucket, path, tickers):
    if bucket == 'raw':
        return path in (sc.FUND_PATH, sc.KPI_PATH)
    if bucket == 'relations':
        return path == 'company_metrics.json'
    if bucket != 'public-data':
        return False
    if path in ('index.json', 'screener.json', 'screener/corr.json'):
        return True
    source = re.fullmatch(r'screener/src/([A-Z0-9.-]+)\.json', path)
    proof = re.fullmatch(r'sources/screener/([A-Z0-9.-]+)/[a-f0-9]{64}\.json', path)
    return bool((source or proof) and (source or proof)[1] in tickers)


def _unique(rows, key, expected):
    result = {row[key]: row for row in rows}
    if len(result) != len(rows) or set(result) != set(expected):
        raise ValueError('Candidate ticker membership/uniqueness mismatch')
    return result


def _load_candidate(path, reviewed_sha256):
    path = Path(path)
    raw = path.read_bytes()
    if not re.fullmatch(r'[a-f0-9]{64}', reviewed_sha256 or '') or digest(raw) != reviewed_sha256:
        raise ValueError('Reviewed manifest SHA-256 does not match')
    plan = json.loads(raw)
    if (plan.get('schema_version') != 1 or plan.get('mode') != 'read_only_candidate'
            or plan.get('ready_for_publication_review') is not True or plan.get('fund_cache_version') != sc.FUND_VERSION):
        raise ValueError('Candidate is not ready for publication review')
    tickers = set(sc.TICKERS)
    if set(plan['normalized_tickers']) | set(plan['legacy_tickers']) != tickers or set(plan['normalized_tickers']) & set(plan['legacy_tickers']):
        raise ValueError('Candidate scope must equal the active screener universe')
    repo = Path(__file__).resolve().parents[1]
    if set(plan.get('code_sha256') or {}) != set(code_paths()):
        raise ValueError('Candidate omits required calculation code signatures')
    for name, expected in plan.get('code_sha256', {}).items():
        target = (repo / name).resolve()
        if not target.is_relative_to(repo) or digest(target.read_bytes()) != expected:
            raise ValueError('Calculation code changed since preparation: ' + name)
    for entry in plan['inputs']:
        target = _path(path.parent, 'inputs', entry['bucket'], entry['path'])
        if entry['exists'] and (not target.is_file() or digest(target.read_bytes()) != entry['sha256']):
            raise ValueError('Candidate input hash mismatch: ' + entry['path'])
    outputs = {}
    for entry in plan['outputs']:
        key = (entry['bucket'], entry['path'])
        if not _scope(*key, tickers) or key in outputs:
            raise ValueError('Duplicate or out-of-scope output path')
        content = _path(path.parent, 'outputs', *key).read_bytes()
        if digest(content) != entry['sha256']:
            raise ValueError('Candidate output hash mismatch: ' + entry['path'])
        outputs[key] = (entry, content)
    required = {('public-data', p) for p in ('index.json', 'screener.json', 'screener/corr.json')}
    required |= {('public-data', f'screener/src/{t}.json') for t in tickers}
    required |= {('raw', sc.FUND_PATH), ('relations', 'company_metrics.json')}
    if not required <= set(outputs):
        raise ValueError('Candidate lacks required outputs')
    read = lambda bucket, name: json.loads(outputs[bucket, name][1])
    screen, index = read('public-data', 'screener.json'), read('public-data', 'index.json')
    generation = plan.get('aggregation_id')
    if not re.fullmatch(r'[a-f0-9]{24}', generation or '') or screen.get('aggregation_id') != generation or index.get('aggregation_id') != generation:
        raise ValueError('Candidate aggregation generation mismatch')
    if (screen['cols'] != sc.ID_COLS + ['lq', 'lq_end'] + sc.FIELD_IDS
            or any(len(row) != len(screen['cols']) for row in screen['rows'])
            or [field['id'] for field in screen['fields']] != sc.FIELD_IDS):
        raise ValueError('Candidate drops or changes screener columns')
    sr = _unique([dict(zip(screen['cols'], r), ticker=r[0]) for r in screen['rows']], 'ticker', tickers)
    ir = _unique(index['companies'], 'ticker', tickers)
    metrics = read('relations', 'company_metrics.json')
    mr = _unique(metrics, 'ticker', tickers)
    cache = json.loads(gzip.decompress(outputs['raw', sc.FUND_PATH][1]))
    if cache.get('_v') != sc.FUND_VERSION or set(cache) - {'_v'} != set(plan['normalized_tickers']):
        raise ValueError('Financial cache must contain exactly the normalized active companies')
    stored_history = json.loads(gzip.decompress(_path(path.parent, 'inputs', 'raw', sc.CLOSES_PATH).read_bytes()))
    for t in tickers:
        a, b, m = ir[t], sr[t], mr[t]
        if a.get('price') != b.get('price') or a.get('price') != m.get('price') or a.get('as_of') != m.get('as_of'):
            raise ValueError('Candidate exact price/date parity failed: ' + t)
        if sc.sig(a.get('market_cap')) != b.get('mcap') or a.get('market_cap') != m.get('market_cap'):
            raise ValueError('Candidate market-cap parity failed: ' + t)
        source = read('public-data', f'screener/src/{t}.json')
        if source.get('tk') != t or source.get('generated') != screen.get('generated'):
            raise ValueError('Candidate provenance generation mismatch: ' + t)
        price_points = ((source.get('f') or {}).get('price') or {}).get('p') or []
        closes = stored_history['s'].get(t) or []
        latest = next((i for i in range(len(closes)-1, -1, -1) if closes[i] is not None), None)
        expected = (a.get('as_of'), a.get('price'))
        if latest is None or (stored_history['dates'][latest], closes[latest]) != expected or not price_points or tuple(price_points[-1]) != expected:
            raise ValueError('Candidate source/index exact price/date parity failed: ' + t)
        for provenance in (source.get('source_provenance'), (cache.get(t) or {}).get('source_provenance')):
            if provenance:
                proof_key = ('public-data', provenance.get('proof_path'))
                if proof_key not in outputs:
                    raise ValueError('Candidate provenance references a missing proof: ' + t)
                proof = read(*proof_key)
                if proof.get('status') != 'verified' or proof.get('snapshot_sha256') != provenance.get('snapshot_sha256') or proof.get('cik') != sc.BY_TICKER[t]['cik']:
                    raise ValueError('Candidate proof identity mismatch: ' + t)
    return plan, outputs, metrics, ir


def _material_fund(fund):
    """Legacy synthetic financial values are not read by the guarded index."""
    result = {}
    for t in sc.TICKERS:
        row = dict(fund.get(t) or {})
        if not fundamentals.normalized(row):
            f = row.get('fund') or {}
            row['fund'] = {k: f.get(k) for k in ('latest_quarter', 'latest_quarter_end')}
        result[t] = row
    return result


def _check_snapshots(sb, plan, root):
    original = json.loads(_path(root, 'inputs', 'public-data', 'meta/fundamentals.json').read_bytes())
    current = sb.get_json('meta/fundamentals.json') or {}
    if encoded(_material_fund(current)) != encoded(_material_fund(original)):
        raise ValueError('Fundamental inputs changed since preparation')
    close_entry = next(e for e in plan['inputs'] if (e['bucket'], e['path']) == ('raw', sc.CLOSES_PATH))
    raw = sb.get_object('raw', sc.CLOSES_PATH)
    if raw is None or digest(raw) != close_entry['sha256']:
        raise ValueError('Stored close cache changed since preparation')
    hist = json.loads(gzip.decompress(raw))
    index = _unique((sb.get_json('index.json') or {})['companies'], 'ticker', sc.TICKERS)
    pstats = jobs.load_pricestats(sb)
    for t in sc.TICKERS:
        series = hist['s'].get(t) or []
        last = next((i for i in range(len(series)-1, -1, -1) if series[i] is not None), None)
        if last is None:
            raise ValueError('Stored price missing: ' + t)
        expected = (series[last], hist['dates'][last])
        if (index[t].get('price'), index[t].get('as_of')) != expected or ((pstats.get(t) or {}).get('price'), (pstats.get(t) or {}).get('as_of')) != expected:
            raise ValueError('Current index/price stats changed: ' + t)
    # Any change to a price-stat input (including technical columns) needs review.
    for entry in plan['inputs']:
        if entry['bucket'] == 'public-data' and entry['path'].startswith('meta/pricestats'):
            raw = sb.get_object(entry['bucket'], entry['path'])
            if (raw is not None) != entry['exists'] or (raw is not None and digest(raw) != entry['sha256']):
                raise ValueError('Price statistics changed since preparation')


def _check_sources(sb, plan, workers):
    entries = [e for e in plan['inputs'] if e['path'].startswith(('companies/', 'sec/companyfacts/'))]
    expected = {f'companies/{t}.json' for t in plan['normalized_tickers']}
    if not expected <= {e['path'] for e in entries}:
        raise ValueError('Manifest omits normalized source signatures')
    def check(entry):
        raw = sb.get_object(entry['bucket'], entry['path'])
        if (raw is not None) != entry['exists'] or (raw is not None and digest(raw) != entry['sha256']):
            raise ValueError('Stored normalized source changed since preparation: ' + entry['path'])
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(check, entries))


def _metrics(sb, tickers):
    rows = sb.select('company_metrics', 'select=*&ticker=in.(' + ','.join(sorted(tickers)) + ')&order=ticker.asc')
    selected = [row for row in rows if row['ticker'] in tickers]
    if len({r['ticker'] for r in selected}) != len(selected):
        raise ValueError('Duplicate company_metrics rows')
    return selected


def _verify_metrics(sb, expected):
    rows = {r['ticker']: r for r in _metrics(sb, {r['ticker'] for r in expected})}
    for row in expected:
        current = rows.get(row['ticker']) or {}
        for key, value in row.items():
            actual = current.get(key)
            if key == 'updated_at' and actual and value:
                from datetime import datetime
                actual, value = (datetime.fromisoformat(x.replace('Z', '+00:00')) for x in (actual, value))
            if actual != value:
                raise RuntimeError('Relational metrics read-back mismatch: ' + row['ticker'] + '/' + key)


def apply_candidate(sb, path, reviewed_sha256, workers=8):
    """Apply exactly reviewed local outputs, after uncached leased drift checks."""
    if isinstance(sb, common.Supabase) and not isinstance(sb, StoredScreenerSupabase):
        raise TypeError('Use StoredScreenerSupabase for bounded publication writes')
    if not 1 <= workers <= 8:
        raise ValueError('Workers must be 1–8')
    path = Path(path)
    plan, outputs, metrics, _ = _load_candidate(path, reviewed_sha256)
    if urlsplit(sb.url).hostname != plan['supabase_host']:
        raise ValueError('Manifest belongs to another Supabase project')
    return _apply_leased(sb, path, plan, outputs, metrics, reviewed_sha256, workers)


@common.serialized_aggregates
def _apply_leased(sb, path, plan, outputs, metrics, manifest_sha, workers):
    lease = sb._aggregate_lease
    _check_snapshots(sb, plan, path.parent)
    _check_sources(sb, plan, workers)
    remote = {key: value for key, value in outputs.items() if key[0] != 'relations'}
    def before(key):
        bucket, name = key
        raw = sb.get_object(bucket, name)
        if name.startswith('sources/screener/') and raw is not None and raw != remote[key][1]:
            raise ValueError('Immutable proof path contains different content: ' + name)
        return {'bucket': bucket, 'path': name, 'exists': raw is not None,
                'sha256': digest(raw) if raw is not None else None,
                'body_base64': base64.b64encode(raw).decode() if raw is not None else None,
                'after_sha256': remote[key][0]['sha256']}
    with ThreadPoolExecutor(workers) as ex:
        previous = list(ex.map(before, remote))
    backup = {'schema_version': 1, 'manifest_sha256': manifest_sha, 'created_at': common.now_iso(),
              'supabase_host': plan['supabase_host'],
              'objects': previous, 'company_metrics': _metrics(sb, set(sc.TICKERS)),
              'after_company_metrics': metrics, 'outputs': plan['outputs']}
    backup['absent_company_metrics'] = sorted(set(sc.TICKERS) - {r['ticker'] for r in backup['company_metrics']})
    backup_raw = gzip.compress(encoded(backup), mtime=0)
    backup_path = 'repairs/screener/' + manifest_sha + '/backup.json.gz'
    state_path = path.parent / 'apply-receipt.json'
    if sb.get_object('raw', backup_path) is not None:
        raise ValueError('A publication backup already exists; review the prior receipt before retrying')
    receipt = {'status': 'preparing_backup', 'manifest_sha256': manifest_sha, 'backup_path': backup_path,
               'backup_sha256': digest(backup_raw), 'published': [], 'started_at': common.now_iso()}
    atomic_json(state_path, receipt)
    try:
        lease.check()
        sb.put_object('raw', backup_path, backup_raw, 'application/gzip', cache='no-store')
        if sb.get_object('raw', backup_path) != backup_raw:
            raise RuntimeError('Private backup read-back mismatch; public outputs were not changed')
        receipt['backup_verified'] = True
        lease.check()
        _check_snapshots(sb, plan, path.parent)
    except BaseException as error:
        receipt.update(status='interrupted_before_publication', finished_at=common.now_iso(),
                       error=type(error).__name__ + ': ' + str(error)[:500])
        atomic_json(state_path, receipt)
        raise
    stopped, lock = threading.Event(), threading.Lock()
    existing_proofs = {(e['bucket'], e['path']) for e in previous if e['exists'] and e['path'].startswith('sources/screener/')}

    def publish(key):
        if stopped.is_set():
            raise RuntimeError('Publication stopped after another write failed')
        entry, content = remote[key]
        try:
            lease.check()
            if key not in existing_proofs:
                sb.put_object(*key, content, entry.get('content_type', 'application/json'), cache=entry.get('cache_control', 'max-age=300'))
            lease.check()
            if sb.get_object(*key) != content:
                raise RuntimeError('Published artifact read-back mismatch: ' + key[1])
            with lock:
                receipt['published'].append('/'.join(key))
                atomic_json(state_path, receipt)
        except BaseException:
            stopped.set()
            raise

    try:
        receipt['status'] = 'publishing'
        proofs = [key for key in remote if key[1].startswith('sources/screener/')]
        sources = [key for key in remote if key[1].startswith('screener/src/')]
        middle = [key for key in remote if key not in proofs and key not in sources and key[1] not in ('index.json', 'screener.json')]
        for group in (proofs, sources, middle):
            with ThreadPoolExecutor(workers) as ex:
                list(ex.map(publish, group))
        lease.check()
        sb.upsert('company_metrics', metrics, 'ticker')
        lease.check()
        _verify_metrics(sb, metrics)
        receipt['published'].append('relations/company_metrics')
        publish(('public-data', 'index.json'))
        publish(('public-data', 'screener.json'))
        receipt['status'] = 'published'
    except BaseException as error:
        receipt.update(status='interrupted', error=type(error).__name__ + ': ' + str(error)[:500])
        raise
    finally:
        receipt['finished_at'] = common.now_iso()
        atomic_json(state_path, receipt)
    return receipt


def rollback_candidate(sb, backup_path, backup_sha256, receipt_path, workers=8):
    """Restore only recorded before states; refuse unrelated later edits."""
    if isinstance(sb, common.Supabase) and not isinstance(sb, StoredScreenerSupabase):
        raise TypeError('Use StoredScreenerSupabase for bounded rollback writes')
    if not 1 <= workers <= 8 or not re.fullmatch(r'repairs/screener/[a-f0-9]{64}/backup\.json\.gz', backup_path):
        raise ValueError('Invalid rollback scope or worker count')
    compressed = sb.get_object('raw', backup_path)
    if compressed is None or digest(compressed) != backup_sha256:
        raise ValueError('Reviewed backup SHA-256 does not match')
    backup = json.loads(gzip.decompress(compressed))
    if backup.get('schema_version') != 1 or backup.get('supabase_host') != urlsplit(sb.url).hostname:
        raise ValueError('Backup belongs to another schema/project')
    if backup_path.split('/')[2] != backup.get('manifest_sha256'):
        raise ValueError('Backup manifest identity mismatch')
    seen = set()
    for entry in backup['objects']:
        key = entry['bucket'], entry['path']
        if not _scope(*key, set(sc.TICKERS)) or key in seen:
            raise ValueError('Invalid backup object scope')
        seen.add(key)
        if entry['exists'] and digest(base64.b64decode(entry['body_base64'])) != entry['sha256']:
            raise ValueError('Backup object content hash mismatch')
    _unique(backup['after_company_metrics'], 'ticker', sc.TICKERS)
    if {r['ticker'] for r in backup['company_metrics']} | set(backup['absent_company_metrics']) != set(sc.TICKERS):
        raise ValueError('Backup metrics scope mismatch')
    return _rollback_leased(sb, backup, backup_path, Path(receipt_path), workers)


def _canonical_row(row):
    row = dict(row) if row is not None else None
    if row and row.get('updated_at'):
        row['updated_at'] = dt.datetime.fromisoformat(row['updated_at'].replace('Z', '+00:00')).isoformat()
    return row


@common.serialized_aggregates
def _rollback_leased(sb, backup, backup_path, receipt_path, workers):
    lease = sb._aggregate_lease
    def check(entry):
        raw = sb.get_object(entry['bucket'], entry['path'])
        current = digest(raw) if raw is not None else None
        if current not in (entry['sha256'], entry['after_sha256']):
            raise ValueError('Rollback conflict: artifact changed independently: ' + entry['path'])
    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(check, backup['objects']))
    before = {r['ticker']: r for r in backup['company_metrics']}
    current = {r['ticker']: r for r in _metrics(sb, set(sc.TICKERS))}
    after = {r['ticker']: {**(before.get(r['ticker']) or {}), **r} for r in backup['after_company_metrics']}
    for t in sc.TICKERS:
        if _canonical_row(current.get(t)) not in (_canonical_row(before.get(t)), _canonical_row(after.get(t))):
            raise ValueError('Rollback conflict: metrics changed independently: ' + t)
    receipt = {'status': 'rolling_back', 'backup_path': backup_path, 'restored': [], 'started_at': common.now_iso()}
    atomic_json(receipt_path, receipt)
    stop, lock = threading.Event(), threading.Lock()
    metadata = {(e['bucket'], e['path']): e for e in backup['outputs']}
    def restore(entry):
        if stop.is_set():
            raise RuntimeError('Rollback stopped after another operation failed')
        key = entry['bucket'], entry['path']
        try:
            lease.check()
            check(entry)
            content = base64.b64decode(entry['body_base64']) if entry['exists'] else None
            if content is None:
                sb.delete_objects(key[0], [key[1]])
            else:
                kind = metadata[key].get('content_type', 'application/json')
                cache = metadata[key].get('cache_control', 'max-age=300')
                sb.put_object(*key, content, kind, cache=cache)
            lease.check()
            if sb.get_object(*key) != content:
                raise RuntimeError('Rollback read-back mismatch: ' + key[1])
            with lock:
                receipt['restored'].append('/'.join(key))
                atomic_json(receipt_path, receipt)
        except BaseException:
            stop.set()
            raise
    try:
        # Immutable proofs may safely remain unreferenced. Mutable objects and
        # previously absent pointers are restored byte-for-byte/by absence.
        mutable = [e for e in backup['objects'] if not e['path'].startswith('sources/screener/')]
        first = [e for e in mutable if e['path'] not in ('index.json', 'screener.json')]
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(restore, first))
        lease.check()
        if before:
            sb.upsert('company_metrics', list(before.values()), 'ticker')
        if backup['absent_company_metrics']:
            sb.delete_metrics(backup['absent_company_metrics'])
        _verify_metrics(sb, list(before.values()))
        if {r['ticker'] for r in _metrics(sb, set(sc.TICKERS))} != set(before):
            raise RuntimeError('Rollback metrics membership mismatch')
        for name in ('index.json', 'screener.json'):
            restore(next(e for e in mutable if e['path'] == name))
        receipt['status'] = 'rolled_back'
    except BaseException as error:
        receipt.update(status='interrupted', error=type(error).__name__ + ': ' + str(error)[:500])
        raise
    finally:
        receipt['finished_at'] = common.now_iso()
        atomic_json(receipt_path, receipt)
    return receipt


class CaptureStore:
    """Explicit read adapter; storage and metrics writes stay on local disk."""
    def __init__(self, source, destination, workers):
        self.source, self.url = source, source.url
        self.destination = destination
        self.inputs, self.outputs, self.relations = {}, {}, {}
        self.guard = threading.RLock()
        self.read_slots = threading.BoundedSemaphore(workers)
        self.network_objects = 0

    def _h(self, extra=None):
        return self.source._h(extra)

    def location(self, family, bucket, path):
        components = Path(path).parts
        if not path or Path(path).is_absolute() or ".." in components:
            raise ValueError("Invalid artifact path")
        p = self.destination / family / bucket / path
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def get_object(self, bucket, path, public=False):
        key = bucket + "/" + path
        with self.guard:
            if key in self.outputs:
                return self.location("outputs", bucket, path).read_bytes()
            if key in self.inputs:
                return self.location("inputs", bucket, path).read_bytes() if self.inputs[key]["exists"] else None
        with self.read_slots:
            body = self.source.get_object(bucket, path, public=False)
        entry = {"bucket": bucket, "path": path, "exists": body is not None,
                 "bytes": len(body) if body is not None else 0,
                 "sha256": digest(body) if body is not None else None,
                 "observed_at": stamp()}
        with self.guard:
            if body is not None:
                self.location("inputs", bucket, path).write_bytes(body)
            self.inputs[key] = entry
            self.network_objects += 1
            if self.network_objects % 50 == 0:
                print(stamp(), "stored objects read:", self.network_objects, flush=True)
        return body

    def get_json(self, path):
        body = self.get_object("public-data", path)
        return json.loads(body) if body else None

    def select(self, table, query="select=*"):
        if table not in {"filings", "news_items", "insider_transactions", "macro_observations"}:
            raise RuntimeError("Unexpected relation read: " + table)
        key = table + "/" + digest(query.encode()) + ".json"
        p = self.location("inputs", "relations", key)
        if key in self.relations:
            return json.loads(p.read_bytes())
        rows = self.source.select(table, query)
        body = encoded(rows)
        p.write_bytes(body)
        self.relations[key] = {"table": table, "query": query, "rows": len(rows),
                               "sha256": digest(body), "observed_at": stamp()}
        return rows

    def put_object(self, bucket, path, content, content_type="application/json", cache="max-age=300"):
        if isinstance(content, str):
            content = content.encode()
        self.location("outputs", bucket, path).write_bytes(content)
        with self.guard:
            self.outputs[bucket + "/" + path] = {"bucket": bucket, "path": path,
                "bytes": len(content), "sha256": digest(content),
                "content_type": content_type, "cache_control": cache}
        return len(content)

    def put_json(self, path, obj, cache="max-age=300"):
        return self.put_object("public-data", path,
                               json.dumps(obj, separators=(",", ":"), default=str),
                               "application/json", cache)

    def upsert(self, table, rows, on_conflict, batch=500):
        if table != "company_metrics" or on_conflict != "ticker":
            raise RuntimeError("Unexpected proposed relational write")
        rows = list(rows)
        self.put_object("relations", table + ".json", encoded(rows))
        self.outputs["relations/" + table + ".json"].update({"on_conflict": on_conflict, "rows": len(rows)})
        return len(rows)

    def deny(self, *args, **kwargs):
        raise RuntimeError("Production mutation blocked by read-only candidate adapter")

    insert = update = delete = rpc = put_raw = deny

class LocalRun:
    def __init__(self, sb, job):
        self.stats, self.job = {}, job

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        return False


class ReplayInputs:
    """Read a completed capture without credentials or network requests."""
    def __init__(self, root):
        self.root = Path(root)
        self.manifest_bytes = (self.root / 'manifest.json').read_bytes()
        self.manifest = json.loads(self.manifest_bytes)
        self.url = 'https://' + self.manifest['supabase_host']
        self.entries = {(e['bucket'], e['path']): e for e in self.manifest['inputs']}
        self.relations = {(e['table'], e['query']): e for e in self.manifest['relation_inputs']}
        cache_path = self.root / 'outputs/raw' / sc.KPI_PATH
        if not cache_path.exists():
            cache_path = self.root / 'inputs/raw' / sc.KPI_PATH
        raw = cache_path.read_bytes()
        entry = next(e for e in self.manifest['outputs'] + self.manifest['inputs']
                     if e['bucket'] == 'raw' and e['path'] == sc.KPI_PATH and e['sha256'] == digest(raw))
        self.kpis = json.loads(gzip.decompress(raw))

    def get_object(self, bucket, path, **kwargs):
        entry = self.entries.get((bucket, path))
        if entry is None:
            raise ValueError('Replay input was not captured: ' + bucket + '/' + path)
        if not entry['exists']:
            return None
        raw = _path(self.root, 'inputs', bucket, path).read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Replay input hash mismatch: ' + path)
        return raw

    def select(self, table, query='select=*'):
        entry = self.relations.get((table, query))
        if entry is None:
            raise ValueError('Replay relation query was not captured')
        p = self.root / 'inputs/relations' / table / (digest(query.encode()) + '.json')
        raw = p.read_bytes()
        if digest(raw) != entry['sha256']:
            raise ValueError('Replay relation hash mismatch')
        return json.loads(raw)

    def list_objects(self, prefix):
        key = prefix.split('/')[0]
        return [{'name': t + '.json', 'updated_at': entry['stamp'][key]}
                for t, entry in self.kpis.items() if key in (entry.get('stamp') or {})]

    def _h(self, *args, **kwargs):
        raise RuntimeError('Offline replay has no credentials and cannot send HTTP')


def finalize_candidate(output):
    """Seal one aggregation identifier into both local public entry points."""
    output = Path(output)
    manifest_path = output / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    generation = digest(encoded({'started_at': manifest.get('started_at'),
                                 'inputs': manifest['inputs'], 'code': manifest.get('code_sha256')}))[:24]
    screen_path = output / 'outputs/public-data/screener.json'
    if not screen_path.exists():
        return manifest
    generated = json.loads(screen_path.read_bytes())['generated']
    for name in ('index.json', 'screener.json'):
        target = output / 'outputs/public-data' / name
        doc = json.loads(target.read_bytes())
        doc.update(aggregation_id=generation, generated=generated)
        content = encoded(doc)
        target.write_bytes(content)
        entry = next(e for e in manifest['outputs'] if e['bucket'] == 'public-data' and e['path'] == name)
        entry.update(sha256=digest(content), bytes=len(content))
    manifest['aggregation_id'] = generation
    manifest_path.write_text(json.dumps(manifest, indent=2))
    return manifest


def prepare_candidate(sb, output, workers=8):
    """Capture every proposed write locally; reuse stored prices without refresh."""
    if not 1 <= workers <= 8 or sc.FUND_VERSION != 6:
        raise ValueError('Preparation requires 1–8 workers and financial cache version 6')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    store = CaptureStore(sb, output, workers)
    started, t0 = stamp(), time.monotonic()
    repo = Path(__file__).resolve().parents[1]
    manifest = {'schema_version': 1, 'mode': 'read_only_candidate', 'started_at': started,
                'production_writes': 0, 'provider_calls': 0, 'supabase_host': urlsplit(sb.url).hostname,
                'workers': workers, 'fund_cache_version': sc.FUND_VERSION,
                'code_sha256': code_signatures()}
    if isinstance(sb, ReplayInputs):
        manifest['replayed_from'] = {'manifest_sha256': digest(sb.manifest_bytes),
                                    'captured_at': sb.manifest.get('started_at'), 'network_reads': 0}
    log = lambda *items: print(stamp(), *items, flush=True)
    counts = {}
    try:
        with contextlib.ExitStack() as stack:
            counts = stack.enter_context(stored_input_network(sb.url))
            if isinstance(sb, ReplayInputs):
                stack.enter_context(patch.object(urllib.request, 'urlopen', side_effect=RuntimeError('Offline replay cannot use network')))
                stack.enter_context(patch.object(sc, 'list_objects', lambda _store, prefix: sb.list_objects(prefix)))
            stack.enter_context(patch.object(sc, 'Run', LocalRun))
            stack.enter_context(patch.object(sc, 'yahoo_daily', side_effect=RuntimeError('Provider fetch prohibited')))
            fund = store.get_json('meta/fundamentals.json')
            hist = sc.gz_json(store, sc.CLOSES_PATH)
            old_index, old_screen = store.get_json('index.json'), store.get_json('screener.json')
            pstats = jobs.load_pricestats(store)
            normalized = sorted(t for t in sc.TICKERS if fundamentals.normalized(fund.get(t) or {}))
            manifest.update(normalized_tickers=normalized,
                legacy_tickers=sorted(set(sc.TICKERS)-set(normalized)),
                normalized_metadata_sha256={t: digest(encoded(fund[t])) for t in normalized})
            prior_index = _unique(old_index['companies'], 'ticker', sc.TICKERS)
            failures = []
            for t in sc.TICKERS:
                values = hist['s'].get(t) or []
                last = next((i for i in range(len(values)-1, -1, -1) if values[i] is not None), None)
                row, ps = prior_index[t], pstats.get(t) or {}
                if last is None or (row.get('price'), row.get('as_of')) != (values[last], hist['dates'][last]) or (ps.get('price'), ps.get('as_of')) != (values[last], hist['dates'][last]):
                    failures.append(t)
            missing = [s for s in sc.HIST_SYMS if not hist['s'].get(s)]
            manifest['price_input_parity'] = {'exact_matches': len(sc.TICKERS)-len(failures), 'mismatches': failures, 'missing_history_symbols': missing}
            if failures or missing:
                raise ValueError('Stored history/index/price-stat parity failed')
            log('starting stored-only build; normalized', len(normalized), 'legacy', len(sc.TICKERS)-len(normalized))
            stack.enter_context(patch.object(sc, 'update_closes', lambda *a, **kw: hist))
            # Always rebuild from stored bundles so every financial dependency is
            # captured and can be checked even when an existing v6 cache is warm.
            original_update_fund = sc.update_fund
            def reextract(store_, fund_, history_, **kwargs):
                kwargs['full'] = True
                return original_update_fund(store_, fund_, history_, **kwargs)
            stack.enter_context(patch.object(sc, 'update_fund', reextract))
            result = sc.screener_job.__wrapped__(store, full=False, time_budget=None, workers=workers, log=log)
            index = jobs.build_index.__wrapped__(store, pstats=pstats, fundamentals_snapshot=fund, return_snapshot=True)
            screen = store.get_json('screener.json')
            cache = sc.gz_json(store, sc.FUND_PATH)
            sr = _unique([dict(zip(screen['cols'], row), ticker=row[0]) for row in screen['rows']], 'ticker', sc.TICKERS)
            ir = _unique(index['companies'], 'ticker', sc.TICKERS)
            old = _unique([dict(zip(old_screen['cols'], row), ticker=row[0]) for row in old_screen['rows']], 'ticker', sc.TICKERS)
            bad_price = [t for t in sc.TICKERS if sr[t].get('price') != ir[t].get('price')]
            comparisons = {}
            for left, right in (('market_cap', 'mcap'), ('pe_ttm', 'pe')):
                comparisons[right] = [{'ticker': t, 'index': ir[t].get(left), 'screener': sr[t].get(right)}
                    for t in sc.TICKERS if sc.sig(ir[t].get(left)) != sr[t].get(right)]
            missing_cache = sorted(set(normalized)-set(cache))
            legacy_cache = sorted(set(manifest['legacy_tickers']) & set(cache))
            groups = {}
            for group in ('px', 'corr', 'act', 'kpi'):
                ids = [f['id'] for f in screen['fields'] if f['g'] == group]
                groups[group] = {'fields': len(ids),
                    'old_populated': sum(old[t].get(f) is not None for t in sc.TICKERS for f in ids),
                    'candidate_populated': sum(sr[t].get(f) is not None for t in sc.TICKERS for f in ids),
                    'changed_cells': sum(old[t].get(f) != sr[t].get(f) for t in sc.TICKERS for f in ids)}
            validation = {'exact_price_matches': len(sc.TICKERS)-len(bad_price), 'price_mismatches': bad_price,
                'missing_normalized_cache': missing_cache, 'unexpected_legacy_cache': legacy_cache,
                'market_cap_mismatches_after_screener_rounding': comparisons['mcap'],
                'pe_mismatches_after_screener_rounding': comparisons['pe'],
                'same_columns': old_screen['cols'] == screen['cols'], 'same_tickers': set(old) == set(sr), 'groups': groups}
            manifest.update(result=result, candidate_index_rows=len(ir), validation=validation,
                previous_screener_generated=old_screen.get('generated'), candidate_generated=screen['generated'],
                example_rows={t: {'index': ir[t], 'screener': sr[t]} for t in ('MSFT', 'AAL', 'NVDA') if t in ir},
                ready_for_publication_review=not (bad_price or missing_cache or legacy_cache or comparisons['mcap'] or comparisons['pe']) and validation['same_columns'] and validation['same_tickers'])
            if code_signatures() != manifest['code_sha256']:
                raise ValueError('Calculation code changed during preparation; replay the captured inputs with stable code')
    except BaseException as error:
        manifest.update(error=type(error).__name__ + ': ' + str(error)[:500], ready_for_publication_review=False)
        raise
    finally:
        manifest.update(finished_at=stamp(), elapsed_seconds=round(time.monotonic()-t0, 1), network_requests=counts,
                        inputs=list(store.inputs.values()), relation_inputs=list(store.relations.values()), outputs=list(store.outputs.values()))
        (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        log('local outputs', len(store.outputs), 'production writes 0')
    return finalize_candidate(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-json', type=Path, help='Private JSON with Supabase environment values; never printed')
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='Read stored inputs and capture all candidate writes locally')
    prep.add_argument('--out', type=Path, required=True)
    prep.add_argument('--workers', type=int, default=8, choices=range(1, 9))
    prep.add_argument('--replay-inputs', type=Path, help='Replay an existing capture entirely offline; no credentials needed')
    apply = commands.add_parser('apply', help='Publish a reviewed manifest after backup and leased drift checks')
    apply.add_argument('--manifest', type=Path, required=True)
    apply.add_argument('--reviewed-sha256', required=True)
    apply.add_argument('--workers', type=int, default=8, choices=range(1, 9))
    restore = commands.add_parser('rollback', help='Restore verified before states unless unrelated edits conflict')
    restore.add_argument('--backup', required=True)
    restore.add_argument('--reviewed-backup-sha256', required=True)
    restore.add_argument('--receipt', required=True, type=Path)
    restore.add_argument('--workers', type=int, default=8, choices=range(1, 9))
    args = parser.parse_args()
    if args.env_json:
        values = json.loads(args.env_json.read_text())
        for name in ('SUPABASE_URL', 'SUPABASE_SERVICE_ROLE_KEY'):
            os.environ[name] = values[name]
        del values
    if args.command == 'prepare':
        source = ReplayInputs(args.replay_inputs) if args.replay_inputs else common.Supabase()
        result = prepare_candidate(source, args.out, args.workers)
        print(json.dumps({k: result[k] for k in ('ready_for_publication_review', 'elapsed_seconds', 'validation')}, indent=2))
        print('Manifest SHA-256:', digest((args.out / 'manifest.json').read_bytes()))
    elif args.command == 'apply':
        result = apply_candidate(StoredScreenerSupabase(), args.manifest, args.reviewed_sha256, args.workers)
        print(json.dumps(result, indent=2))
    else:
        result = rollback_candidate(StoredScreenerSupabase(), args.backup, args.reviewed_backup_sha256, args.receipt, args.workers)
        print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
