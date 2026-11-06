"""Collect 10y daily Yahoo chart responses for a cohort and write manifests matching etl.backfill_prices."""
import gzip, hashlib, json, sys, time, threading, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from etl import backfill_prices as bp
COHORT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
cohort_body = COHORT.read_bytes(); cohort = json.loads(cohort_body); chash = hashlib.sha256(cohort_body).hexdigest()
def sha(b): return hashlib.sha256(b).hexdigest()
def enc(o): return json.dumps(o, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
lock = threading.Lock(); last = [0.0]
def fetch(url):
    with lock:
        w = last[0] + 0.35 - time.monotonic()
        if w > 0: time.sleep(w)
        last[0] = time.monotonic()
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (compatible; FiledUnder/1.0)', 'Accept-Encoding': 'identity'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.read(), {'content-encoding': r.headers.get('Content-Encoding') or ''}
def one(company):
    t = company['ticker']; mp = OUT / 'manifests' / f'{t}.json'
    if mp.exists():
        return json.loads(mp.read_text())
    url = f'https://query2.finance.yahoo.com/v8/finance/chart/{t}?range=10y&interval=1d&includePrePost=false&events=div,splits'
    attempts, errors = [], []
    for attempt in range(1, 5):
        try:
            status, body, headers = fetch(url)
            retrieved = datetime.now(timezone.utc).isoformat()
            packed = gzip.compress(body, mtime=0)
            rp = OUT / 'raw' / f'{t}.response.gz'; rp.parent.mkdir(parents=True, exist_ok=True); rp.write_bytes(packed)
            raw = {'url': url, 'requested_url': url, 'http_status': status, 'retrieved_at': retrieved,
                   'path': f'raw/{t}.response.gz', 'gzip_sha256': sha(packed), 'gzip_bytes': len(packed),
                   'body_sha256': sha(body), 'body_bytes': len(body), 'headers': headers}
            break
        except Exception as e:
            errors.append(str(e)[:200]); time.sleep(2 ** attempt)
    else:
        m = {'ticker': t, 'cik': company['cik'], 'name': company['name'], 'expected_currency': 'USD', 'cohort_id': cohort['cohort_id'],
             'cohort_sha256': chash, 'source': {'provider': 'Yahoo Finance'}, 'status': 'failed', 'errors': errors}
        mp.parent.mkdir(parents=True, exist_ok=True); mp.write_bytes(enc(m)); return m
    m = {'ticker': t, 'cik': company['cik'], 'name': company['name'], 'expected_currency': 'USD', 'cohort_id': cohort['cohort_id'],
         'cohort_sha256': chash, 'source': {'provider': 'Yahoo Finance'}, 'status': 'ok', 'errors': [], 'raw': raw,
         'url': url, 'retrieved_at': raw['retrieved_at']}
    try:
        public, screen, quality = bp.parse_chart(company, body, raw)
        derived = {}
        for kind, obj in (('public', public), ('screener', screen)):
            data = enc(obj); p = OUT / 'derived' / f'{t}.{kind}.json'; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
            derived[kind] = {'path': f'derived/{t}.{kind}.json', 'sha256': sha(data), 'bytes': len(data)}
        m.update(derived=derived, **quality)
    except Exception as e:
        m.update(status='failed', errors=[str(e)[:300]])
    mp.parent.mkdir(parents=True, exist_ok=True); mp.write_bytes(enc(m))
    return m
with ThreadPoolExecutor(3) as pool:
    ms = list(pool.map(one, cohort['companies']))
agg = {'schema_version': 1, 'cohort_id': cohort['cohort_id'], 'cohort_sha256': chash,
       'status': 'complete' if all(m['status'] == 'ok' for m in ms) else 'incomplete', 'companies': ms, 'created_at': datetime.now(timezone.utc).isoformat()}
(OUT / 'manifest.json').write_bytes(enc(agg))
print(agg['status'], sum(m['status'] == 'ok' for m in ms), [(m['ticker'], m['errors']) for m in ms if m['status'] != 'ok'])
