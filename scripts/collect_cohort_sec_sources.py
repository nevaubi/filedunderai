"""Collect immutable SEC submissions + companyfacts snapshots per candidate into <root>, writing sec-manifests."""
import gzip, hashlib, json, sys, time, threading, urllib.request, urllib.error
from datetime import datetime, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
SEL, ROOT = Path(sys.argv[1]), Path(sys.argv[2])
UA = 'Filed Under firasshaher99@gmail.com'
lock = threading.Lock(); last = [0.0]
def get(url):
    for attempt in range(6):
        with lock:
            wait = last[0] + 0.13 - time.monotonic()
            if wait > 0: time.sleep(wait)
            last[0] = time.monotonic()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept-Encoding': 'gzip'})
            with urllib.request.urlopen(req, timeout=120) as r:
                body = r.read()
                if r.headers.get('Content-Encoding') == 'gzip': body = gzip.decompress(body)
                return body
        except urllib.error.HTTPError as e:
            if e.code == 404: raise
            time.sleep(2 ** attempt)
        except Exception:
            time.sleep(2 ** attempt)
    raise RuntimeError('retries exhausted: ' + url)
def one(c):
    cik, tk = c['cik'], c['ticker']
    mpath = ROOT / 'sec-manifests' / f'{tk}.json'
    if mpath.exists() and json.loads(mpath.read_text()).get('status') == 'collected': return tk, 'cached'
    sources = {}
    try:
        for name, url in (('submissions', f'https://data.sec.gov/submissions/CIK{cik:010d}.json'),
                          ('companyfacts', f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json')):
            body = get(url)
            doc = json.loads(body)
            if int(doc.get('cik')) != cik: raise ValueError(f'{name} CIK mismatch')
            p = ROOT / 'raw' / 'sec' / name / f'CIK{cik:010d}.json.gz'
            p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(gzip.compress(body, 6))
            sources[name] = {'url': url, 'path': str(p), 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
                             'retrieved_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}
        status = 'collected'
    except Exception as e:
        status = 'failed'; sources['error'] = str(e)[:300]
    mpath.parent.mkdir(parents=True, exist_ok=True)
    mpath.write_text(json.dumps({'ticker': tk, 'cik': cik, 'status': status, 'sources': sources}))
    return tk, status
cands = json.load(open(SEL / 'candidates.json'))['candidates']
with ThreadPoolExecutor(6) as pool:
    res = list(pool.map(one, cands))
import collections; print(collections.Counter(s for _, s in res)); print([t for t, s in res if s == 'failed'])
