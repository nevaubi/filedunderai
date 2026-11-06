"""Scan tracked issuers: latest annual contract revenue vs same-context reported Revenues total (SEC companyfacts)."""
import json, sys, time, threading, urllib.request, gzip
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from etl.universe import UNIVERSE
from etl import sec
lock = threading.Lock(); last = [0.0]; out = []
def get(url):
    with lock:
        w = last[0] + 0.13 - time.monotonic()
        if w > 0: time.sleep(w)
        last[0] = time.monotonic()
    req = urllib.request.Request(url, headers={'User-Agent': 'Filed Under firasshaher99@gmail.com', 'Accept-Encoding': 'gzip'})
    with urllib.request.urlopen(req, timeout=120) as r:
        b = r.read(); return gzip.decompress(b) if r.headers.get('Content-Encoding') == 'gzip' else b
def one(row):
    t, cik = row[0], row[1]
    if cik in sec.REPORTED_TOTAL_REVENUE_CIKS: return
    try:
        cf = json.loads(get(f'https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json'))
        rev = next((r for r in sec.build_series(cf['facts'], 'income') if r['label'] == 'Revenue'), None)
        if not rev or not rev['annual']: return
        p = rev['annual'][-1]
        if p['c'] != 'RevenueFromContractWithCustomerExcludingAssessedTax': return
        tot = [x['val'] for x in cf['facts'].get('us-gaap', {}).get('Revenues', {}).get('units', {}).get('USD', [])
               if (x.get('start'), x['end'], x['accn']) == (p.get('start'), p['end'], p['accn'])]
        if tot and max(tot) > p['v'] * 1.05:
            with lock: out.append((t, cik, p['end'], p['v'], max(tot), round(max(tot) / p['v'], 2) if p['v'] else None))
    except Exception as e:
        with lock: out.append((t, cik, 'error', str(e)[:80]))
with ThreadPoolExecutor(6) as pool: list(pool.map(one, UNIVERSE))
json.dump(out, open(sys.argv[1], 'w'), indent=0); print(len(out))
