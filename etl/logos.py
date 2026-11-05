"""Validated, additive company logos with a separate renewable publication lease."""
import copy
import datetime as dt
import hashlib
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlsplit

from PIL import Image

from .common import AggregateLease, HTTPError, now_iso, request
from .universe import BY_TICKER

SOURCES = [
    ("parqet", "https://assets.parqet.com/logos/symbol/{t}?format=png&size=256"),
    ("fmp", "https://financialmodelingprep.com/image-stock/{t}.png"),
    ("nvstly", "https://raw.githubusercontent.com/nvstly/icons/main/ticker_icons/{t}.png"),
]
PNG = b"\x89PNG\r\n\x1a\n"
REGISTRY = 'meta/logos.json'


def _encoded(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False).encode()


def _png_size(body):
    if not isinstance(body, bytes) or not body.startswith(PNG) or len(body) > 10 * 1024 * 1024:
        raise ValueError('Logo must be PNG bytes, at most 10 MiB')
    try:
        with Image.open(io.BytesIO(body)) as im:
            if im.format != 'PNG' or not all(48 <= n <= 4096 for n in im.size) or getattr(im, 'n_frames', 1) != 1:
                raise ValueError('Logo must be a static PNG with both dimensions between 48 and 4096 pixels')
            size = im.size
            im.verify()
        with Image.open(io.BytesIO(body)) as im:
            im.load()
            rgba = im.convert('RGBA')
            if rgba.getchannel('A').getbbox() is None or all(lo == hi for lo, hi in rgba.getextrema()):
                raise ValueError('Logo PNG is empty or a uniform placeholder')
        return size
    except (OSError, SyntaxError) as error:
        raise ValueError('Logo PNG does not decode completely') from error


class _LogoStore:
    """Bound only this publisher's requests; never monkeypatch a shared transport."""
    def __init__(self, sb):
        self.sb, self.lease = sb, None

    def check(self):
        if self.lease:
            self.lease.check()
            if self.lease.valid_until - time.monotonic() <= 30:
                raise RuntimeError('Logo lease has insufficient time remaining for a write')

    def rpc(self, name, args):
        if name not in ('acquire_etl_lease', 'release_etl_lease') or args.get('lock_name') != 'company-logos':
            raise ValueError('Unexpected logo lease operation')
        self.check()
        return json.loads(request(f'{self.sb.url}/rest/v1/rpc/{name}', method='POST', data=args,
                                  headers=self.sb._h(), retries=1, timeout=30) or 'null')

    def read(self, bucket, path):
        url, headers = self.sb._storage_read_request(bucket, path)
        try:
            return request(url, headers=headers, raw=True, retries=1, timeout=30)
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code == 400:
                try:
                    body = json.loads(error.body)
                except (TypeError, ValueError):
                    body = {}
                if isinstance(body, dict) and str(body.get('statusCode')) == '404' and body.get('error') == 'not_found' and body.get('message') == 'Object not found':
                    return None
            raise

    def write(self, bucket, path, body, content_type, cache, upsert=False):
        if self.lease is None:
            raise RuntimeError('Logo writes require the company-logos lease')
        self.check()
        request(f'{self.sb.url}/storage/v1/object/{bucket}/{quote(path)}', method='POST', data=body,
                headers=self.sb._h({'Content-Type': content_type, 'x-upsert': 'true' if upsert else 'false',
                                   'cache-control': cache}), retries=1, timeout=30)

    def create(self, bucket, path, body, content_type, cache):
        """Create only; an identical existing object is a safe resumable success."""
        try:
            self.write(bucket, path, body, content_type, cache)
        except HTTPError as error:
            if error.code not in (400, 409):
                raise
            existing = self.read(bucket, path)
            if existing is None:
                raise
            return existing == body
        if self.read(bucket, path) != body:
            raise RuntimeError(f'Logo object readback differs: {bucket}/{path}')
        return True


def _registry(store):
    raw = store.read('public-data', REGISTRY)
    if raw is None:
        raise ValueError('Logo registry is missing; refusing implicit initialization')
    value = json.loads(raw)
    if not isinstance(value, dict) or not isinstance(value.get('tickers'), dict):
        raise ValueError('Malformed logo registry')
    if any(not isinstance(k, str) or not isinstance(v, str) or not v for k, v in value['tickers'].items()):
        raise ValueError('Logo registry ticker values must remain source strings')
    if 'assets' in value and (not isinstance(value['assets'], dict) or any(not isinstance(v, dict) for v in value['assets'].values())):
        raise ValueError('Malformed logo asset metadata')
    if 'missing' in value and (not isinstance(value['missing'], list) or any(not isinstance(t, str) for t in value['missing'])):
        raise ValueError('Malformed logo missing list')
    _encoded(value)  # Reject nonfinite values before preserving the registry.
    return raw, value


def publish_logo_assets(sb, entries):
    """Publish reviewed PNGs without replacing any registered or differing asset.

    Entries are {ticker, source, body: bytes, metadata: {cik, source_url,
    sha256?, ...}}. Source strings remain backward compatible; provenance is
    added under assets. Failures raise: reconcile storage before retrying.
    Only public logos/metadata and a private exact registry backup are written.
    """
    prepared, seen = [], set()
    for entry in entries:
        ticker, source = entry.get('ticker'), entry.get('source')
        if ticker not in BY_TICKER or ticker in seen:
            raise ValueError('Unknown or duplicate logo ticker')
        seen.add(ticker)
        if not isinstance(source, str) or not source.strip():
            raise ValueError('Logo source is required')
        body = entry.get('body')
        width, height = _png_size(body)
        metadata = copy.deepcopy(entry.get('metadata'))
        if not isinstance(metadata, dict) or type(metadata.get('cik')) is not int or metadata['cik'] != BY_TICKER[ticker]['cik']:
            raise ValueError(f'Logo issuer CIK mismatch: {ticker}')
        url = urlsplit(metadata.get('source_url') or '')
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError(f'Logo source URL is required: {ticker}')
        digest = hashlib.sha256(body).hexdigest()
        if metadata.get('sha256', digest) != digest:
            raise ValueError(f'Logo SHA-256 mismatch: {ticker}')
        metadata.update(source=source, sha256=digest, bytes=len(body), width=width, height=height,
                        content_type='image/png', path=f'logos/{ticker}.png')
        _encoded(metadata)
        prepared.append({'ticker': ticker, 'source': source, 'body': body, 'metadata': metadata})

    store = _LogoStore(sb)
    with AggregateLease(store, name='company-logos', seconds=300) as lease:
        store.lease = lease
        before_raw, before = _registry(store)
        after = copy.deepcopy(before)
        skipped = [{'ticker': e['ticker'], 'reason': 'already_registered'} for e in prepared if e['ticker'] in before['tickers']]
        todo = [e for e in prepared if e['ticker'] not in before['tickers']]
        backup, added, conflicts = None, [], []
        if todo:
            stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
            backup = f'logo-backfills/{stamp}/logos-before.json'
            if not store.create('raw', backup, before_raw, 'application/json', 'no-store'):
                raise RuntimeError('Different logo registry backup already exists')

            def upload(entry):
                ok = store.create('public-data', entry['metadata']['path'], entry['body'],
                                  'image/png', 'public, max-age=604800')
                return entry, ok

            pool = ThreadPoolExecutor(max_workers=12)
            try:
                futures = [pool.submit(upload, entry) for entry in todo]
                for future in as_completed(futures):
                    entry, ok = future.result()
                    ticker = entry['ticker']
                    if not ok:
                        conflicts.append({'ticker': ticker, 'reason': 'different_existing_object'})
                        continue
                    after['tickers'][ticker] = entry['source']
                    after.setdefault('assets', {})[ticker] = entry['metadata']
                    added.append(ticker)
            except BaseException:
                pool.shutdown(wait=True, cancel_futures=True)
                raise
            else:
                pool.shutdown(wait=True)

        after_raw = before_raw
        if added:
            after['generated'] = now_iso()
            after['missing'] = sorted(set(BY_TICKER) - set(after['tickers']))
            after_raw = _encoded(after)
            if store.read('public-data', REGISTRY) != before_raw:
                raise RuntimeError('Logo registry changed during publication; refusing to overwrite it')
            store.write('public-data', REGISTRY, after_raw, 'application/json', 'max-age=120', upsert=True)
            if store.read('public-data', REGISTRY) != after_raw:
                raise RuntimeError('Logo registry exact readback failed; reconcile before retrying')
        missing = sorted(set(BY_TICKER) - set(after['tickers']))
        return {'status': 'partial' if conflicts else 'published' if added else 'no_changes',
                'added': sorted(added), 'skipped': sorted(skipped, key=lambda r: r['ticker']),
                'conflicts': sorted(conflicts, key=lambda r: r['ticker']), 'missing': missing,
                'counts': {'requested': len(prepared), 'added': len(added), 'skipped': len(skipped),
                           'conflicts': len(conflicts), 'registered': len(after['tickers']),
                           'active_registered': len(set(BY_TICKER) & set(after['tickers'])), 'missing': len(missing)},
                'backup_path': backup, 'before_sha256': hashlib.sha256(before_raw).hexdigest(),
                'after_sha256': hashlib.sha256(after_raw).hexdigest(), 'verified_at': now_iso()}


def fetch_logo(ticker):
    """Retain existing providers; only a fully decoded nonempty PNG is eligible."""
    for name, url in SOURCES:
        try:
            body = request(url.format(t=ticker), timeout=20, retries=2, raw=True)
            _png_size(body)
            return name, body
        except Exception:
            continue
    return None, None


def logos_job(sb, tickers=None, force=False, workers=6):
    """Fetch candidates across all registered issuers; force never permits overwrite."""
    if type(workers) is not int or workers < 1:
        raise ValueError('Logo workers must be a positive integer')
    selected = set(BY_TICKER) if tickers is None else set(tickers)
    if selected - set(BY_TICKER):
        raise ValueError('Unregistered logo ticker requested')
    _, registry = _registry(_LogoStore(sb))
    todo = sorted(t for t in selected if force or t not in registry['tickers'])
    entries, failed = [], []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for ticker, (source, body) in zip(todo, pool.map(fetch_logo, todo)):
            if body is None:
                failed.append(ticker)
                continue
            entries.append({'ticker': ticker, 'source': source, 'body': body,
                            'metadata': {'cik': BY_TICKER[ticker]['cik'],
                                         'source_url': dict(SOURCES)[source].format(t=ticker)}})
    receipt = publish_logo_assets(sb, entries)
    receipt['fetch_missing'] = failed
    return receipt
