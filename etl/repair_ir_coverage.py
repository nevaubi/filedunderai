"""Reconcile one existing IR coverage aggregate from a reviewed SQL snapshot.

Preparation reads current manifests and writes a sealed local plan. Application
writes only immutable evidence and the exact reviewed meta/coverage_ir.json.
It does not collect documents, scan KPI rows, change numeric observations, or
activate companies. Counts retain their actual SQL snapshot timestamp.
"""
import argparse
import base64
import copy
import datetime as dt
import gzip
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote

from .backfill_coverage import summarize
from .common import AggregateLease, Supabase, now_iso, request
from .ir import _stored_object
from .universe import UNIVERSE

PROJECT_URL = 'https://apxfeteebnspwyajlxfh.supabase.co'
PUBLIC_PATH = 'meta/coverage_ir.json'
FILES = ('before.json', 'after.json', 'counts.json', 'manifests.json.gz')
COUNT_FIELDS = ('docs', 'bytes', 'kpis', 'kpi_values')


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def stamp(value):
    if not isinstance(value, str) or not value:
        raise ValueError('Missing timestamp')
    result = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include its timezone')
    return result


def integer(value):
    if type(value) is not int or value < 0:
        raise ValueError('Counts must be nonnegative integers')
    return value


def check_company_counts(row):
    for name in COUNT_FIELDS:
        integer(row.get(name))
    types = row.get('types')
    if (not isinstance(types, dict) or any(not isinstance(k, str) or not k for k in types)
            or sum(integer(n) for n in types.values()) != row['docs']):
        raise ValueError('Invalid document type counts')
    for name in ('segment_tables', 'segment_ties'):
        if row.get(name) is not None:
            integer(row[name])


def validate_counts(snapshot, expected_tickers):
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('companies'), list):
        raise ValueError('Invalid SQL count snapshot')
    stamp(snapshot.get('snapshot_started_at'))
    rows = snapshot['companies']
    tickers = [row.get('ticker') for row in rows]
    if len(tickers) != len(set(tickers)) or set(tickers) != set(expected_tickers):
        raise ValueError('SQL count company scope differs from reviewed active universe')
    for row in rows:
        check_company_counts(row)
        for name in ('ir_definitions', 'other_definitions', 'ir_ytd_values'):
            integer(row.get(name))
        if row['other_definitions']:
            raise ValueError('Unexpected non-IR definitions require separate review')
        if row['kpis'] != row['ir_definitions'] or row['ir_ytd_values'] > row['kpi_values']:
            raise ValueError('Inconsistent source-kind count totals')
    summary = {'company_count': len(rows), 'documents': sum(r['docs'] for r in rows),
               'bytes': sum(r['bytes'] for r in rows), 'definitions': sum(r['kpis'] for r in rows),
               'ir_definitions': sum(r['ir_definitions'] for r in rows), 'other_definitions': 0,
               'ir_values': sum(r['kpi_values'] for r in rows),
               'ir_ytd_values': sum(r['ir_ytd_values'] for r in rows)}
    if any(integer(snapshot.get(k)) != v for k, v in summary.items()):
        raise ValueError('SQL count summary disagrees with its company rows')
    return {r['ticker']: r for r in rows}


def manifest_coverage(ticker, manifest, counts):
    if not isinstance(manifest, dict) or manifest.get('ticker') != ticker:
        raise ValueError(f'Manifest issuer differs for {ticker}')
    stamp(manifest.get('generated'))
    docs = manifest.get('documents')
    if not isinstance(docs, list) or any(not isinstance(row, dict) for row in docs):
        raise ValueError(f'Missing manifest documents for {ticker}')
    ids = [row.get('id') for row in docs]
    if any(not isinstance(did, str) or not did for did in ids) or len(set(ids)) != len(ids):
        raise ValueError(f'Missing or duplicate manifest document ID for {ticker}')
    value = copy.deepcopy(manifest.get('coverage'))
    if not isinstance(value, dict) or value.get('status') not in ('complete', 'partial', 'unverified'):
        raise ValueError(f'Invalid manifest coverage status for {ticker}')
    if value.get('total_documents') is not None:
        integer(value['total_documents'])
    types = dict(Counter(row.get('doc_type') or 'other' for row in docs))
    sizes = sum(integer(row.get('bytes')) for row in docs)
    consistent = (len(docs) == counts['docs'] and sizes == counts['bytes'] and types == counts['types']
                  and value.get('total_documents') == len(docs))
    if not consistent:
        value['status'] = 'unverified'
        limits = list(value.get('limits') or [])
        reason = 'manifest counts differ from the database count snapshot'
        if reason not in limits:
            limits.append(reason)
        value['limits'] = limits
    return value


def build_candidate(before, snapshot, manifests, expected_tickers, generated):
    current = stamp(generated)
    counts = validate_counts(snapshot, expected_tickers)
    if stamp(snapshot['snapshot_started_at']) > current:
        raise ValueError('Count snapshot is after publication time')
    if (not isinstance(before, dict) or not isinstance(before.get('companies'), dict)
            or set(before['companies']) != set(expected_tickers) or set(manifests) != set(expected_tickers)):
        raise ValueError('Prior or manifest company scope differs from reviewed active universe')
    for row in before['companies'].values():
        check_company_counts(row)
    expected_summary = summarize(before['companies'])
    if not isinstance(before.get('summary'), dict) or any(before['summary'].get(k) != v for k, v in expected_summary.items()):
        raise ValueError('Inconsistent prior summary')
    stamp(before.get('segment_counts_as_of'))
    result = copy.deepcopy(before)
    for ticker, row in result['companies'].items():
        observed = counts[ticker]
        row.update({key: copy.deepcopy(observed[key]) for key in (*COUNT_FIELDS, 'types')})
        row['documents_as_of'] = snapshot['snapshot_started_at']
        row['coverage'] = manifest_coverage(ticker, manifests[ticker], observed)
        row['coverage_as_of'] = manifests[ticker]['generated']
    result['summary'] = {**before['summary'], **summarize(result['companies'])}
    result['generated'] = generated
    result['kpi_counts_as_of'] = snapshot['snapshot_started_at']
    result['documents_counts_as_of'] = snapshot['snapshot_started_at']
    return result


def code_hashes():
    root = Path(__file__).parent
    names = ('repair_ir_coverage.py', 'backfill_coverage.py', 'common.py', 'ir.py', 'universe.py')
    paths = [root / name for name in names] + sorted(root.glob('additional_companies*.json'))
    return {p.name: digest(p.read_bytes()) for p in paths}


def count_data(body):
    value = json.loads(body)
    return value['data'] if isinstance(value, dict) and 'data' in value else value


def _read_manifest(sb, ticker):
    raw = _stored_object(sb, 'public-data', f'ir/{ticker}.json')
    if raw is None:
        raise ValueError(f'Missing current manifest for {ticker}')
    return ticker, raw


def prepare(sb, counts_path, root, workers=8):
    if sb.url.rstrip('/') != PROJECT_URL:
        raise ValueError('Unexpected production destination')
    root = Path(root)
    if root.exists():
        raise FileExistsError('Plan directory exists; preserve the sealed original')
    expected = {ticker for ticker, *_ in UNIVERSE}
    counts_raw = Path(counts_path).read_bytes()
    snapshot = count_data(counts_raw)
    validate_counts(snapshot, expected)
    before = _stored_object(sb, 'public-data', PUBLIC_PATH)
    if before is None:
        raise ValueError('Missing existing coverage aggregate')
    manifest_raw = {}
    with ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
        pending = [pool.submit(_read_manifest, sb, ticker) for ticker in sorted(expected)]
        for future in as_completed(pending):
            ticker, raw = future.result()
            manifest_raw[ticker] = raw
            if len(manifest_raw) % 50 == 0:
                print(json.dumps({'manifests_read': len(manifest_raw), 'expected': len(expected)}), flush=True)
    manifests = {ticker: json.loads(raw) for ticker, raw in manifest_raw.items()}
    generated = now_iso()
    after = encoded(build_candidate(json.loads(before), snapshot, manifests, expected, generated))
    manifest_archive = gzip.compress(encoded({ticker: base64.b64encode(raw).decode() for ticker, raw in manifest_raw.items()}), mtime=0)
    payloads = dict(zip(FILES, (before, after, counts_raw, manifest_archive)))
    root.mkdir(parents=True)
    for name, body in payloads.items():
        (root / name).write_bytes(body)
    plan = {'version': 1, 'destination': PROJECT_URL, 'public_path': PUBLIC_PATH,
            'generated': generated, 'tickers': sorted(expected), 'code_sha256': code_hashes(),
            'files': {name: {'sha256': digest(body), 'bytes': len(body)} for name, body in payloads.items()},
            'manifest_sha256': {ticker: digest(raw) for ticker, raw in manifest_raw.items()},
            'summary_before': json.loads(before)['summary'], 'summary_after': json.loads(after)['summary'],
            'kpi_counts_as_of': snapshot['snapshot_started_at']}
    plan['seal'] = digest(encoded(plan))
    (root / 'plan.json').write_bytes(encoded(plan))
    return plan


def load_plan(root):
    root = Path(root)
    raw = (root / 'plan.json').read_bytes()
    plan = json.loads(raw)
    if (plan.get('version') != 1 or plan.get('destination') != PROJECT_URL or plan.get('public_path') != PUBLIC_PATH
            or plan.get('seal') != digest(encoded({k: v for k, v in plan.items() if k != 'seal'}))
            or plan.get('code_sha256') != code_hashes() or set(plan.get('files', {})) != set(FILES)
            or plan.get('tickers') != sorted(t for t, *_ in UNIVERSE)):
        raise ValueError('Invalid plan seal, code, destination, or scope')
    payloads = {name: (root / name).read_bytes() for name in FILES}
    for name, body in payloads.items():
        if plan['files'][name] != {'sha256': digest(body), 'bytes': len(body)}:
            raise ValueError('Sealed payload changed: ' + name)
    manifest_raw = {ticker: base64.b64decode(body, validate=True) for ticker, body in
                    json.loads(gzip.decompress(payloads['manifests.json.gz'])).items()}
    if {ticker: digest(body) for ticker, body in manifest_raw.items()} != plan['manifest_sha256']:
        raise ValueError('Sealed manifest evidence changed')
    recomputed = build_candidate(json.loads(payloads['before.json']), count_data(payloads['counts.json']),
        {ticker: json.loads(body) for ticker, body in manifest_raw.items()}, set(plan['tickers']), plan['generated'])
    if encoded(recomputed) != payloads['after.json']:
        raise ValueError('Sealed after-state differs from its reviewed count/manifest evidence')
    return plan, raw, payloads


def _write_once(sb, bucket, path, body, lease, immutable):
    lease.check()
    request(f'{sb.url}/storage/v1/object/{bucket}/{quote(path)}', method='POST', data=body,
            headers=sb._h({'Content-Type': 'application/gzip' if path.endswith('.gz') else 'application/json',
                           'x-upsert': 'false' if immutable else 'true',
                           'cache-control': 'no-store' if immutable else 'max-age=300'}),
            timeout=30, retries=1)
    lease.check()


def apply(sb, root, workers=8):
    if sb.url.rstrip('/') != PROJECT_URL:
        raise ValueError('Unexpected production destination')
    root = Path(root)
    plan, plan_raw, payloads = load_plan(root)
    prefix = f"repairs/ir-coverage/{plan['seal']}/"
    receipt = {'status': 'started', 'plan_sha256': digest(plan_raw), 'seal': plan['seal'],
               'archive_prefix': prefix, 'started_at': now_iso()}
    def save():
        path = root / 'apply-receipt.json'
        temporary = path.with_suffix('.json.part')
        temporary.write_bytes(encoded(receipt)); temporary.replace(path)
    with AggregateLease(sb) as lease:
        old = getattr(sb, '_aggregate_lease', None)
        sb._aggregate_lease = lease
        try:
            current = _stored_object(sb, 'public-data', PUBLIC_PATH)
            if current not in (payloads['before.json'], payloads['after.json']):
                raise ValueError('Current coverage aggregate differs from both sealed states')
            advanced = current == payloads['after.json']
            for name, body in {**payloads, 'plan.json': plan_raw}.items():
                stored = _stored_object(sb, 'raw', prefix + name)
                if stored is None and not advanced:
                    _write_once(sb, 'raw', prefix + name, body, lease, immutable=True)
                    stored = _stored_object(sb, 'raw', prefix + name)
                if stored != body:
                    raise ValueError('Missing or conflicting immutable evidence: ' + name)
            receipt['archive_verified'] = True; save()
            if not advanced:
                with ThreadPoolExecutor(max_workers=max(1, min(12, workers))) as pool:
                    for ticker, body in pool.map(lambda ticker: _read_manifest(sb, ticker), plan['tickers']):
                        if digest(body) != plan['manifest_sha256'][ticker]:
                            raise ValueError('Company manifest changed after review: ' + ticker)
                if _stored_object(sb, 'public-data', PUBLIC_PATH) != payloads['before.json']:
                    raise ValueError('Coverage aggregate changed during verification')
                _write_once(sb, 'public-data', PUBLIC_PATH, payloads['after.json'], lease, immutable=False)
            if _stored_object(sb, 'public-data', PUBLIC_PATH) != payloads['after.json']:
                raise RuntimeError('Published coverage exact readback failed; resume the same plan')
            lease.check()
            receipt.update(status='verified', completed_at=now_iso(),
                           public_sha256=digest(payloads['after.json']), summary=plan['summary_after'],
                           counts_as_of=plan['kpi_counts_as_of'], resumed_after_state=advanced)
            save()
            return receipt
        except Exception as error:
            receipt.update(status='failed', error=f'{type(error).__name__}: {error}'[:500]); save()
            raise
        finally:
            sb._aggregate_lease = old


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'apply'))
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--counts', type=Path)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--execute-reviewed', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'prepare':
        if args.counts is None:
            parser.error('prepare requires --counts from the reviewed read-only SQL snapshot')
        result = prepare(Supabase(), args.counts, args.root, args.workers)
        print(json.dumps({k: result[k] for k in ('seal', 'summary_before', 'summary_after', 'kpi_counts_as_of')}))
    else:
        if not args.execute_reviewed:
            parser.error('apply requires --execute-reviewed')
        print(json.dumps(apply(Supabase(), args.root, args.workers)))


if __name__ == '__main__':
    main()
