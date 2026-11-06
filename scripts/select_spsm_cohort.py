"""Select cohort 3 candidates: SPSM holdings after the rows cohort 2 used, with SEC identity + listing checks."""
import hashlib, json, sys, collections
from pathlib import Path
import openpyxl
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[1]))
from etl.universe import BY_TICKER, ADDITIONAL_REGISTRIES
SEL = Path(sys.argv[1])
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
tracked_ciks = {v['cik'] for v in BY_TICKER.values()}
cte = json.load(open(SEL/'cte.json')); fields = cte['fields']
by_ticker = {}
for i, row in enumerate(cte['data']):
    r = dict(zip(fields, row)); by_ticker.setdefault(r['ticker'].upper(), (i, r))
listed = {}
for fn in ('nasdaqlisted.txt', 'otherlisted.txt'):
    lines = (SEL/fn).read_text().splitlines()
    head = lines[0].split('|')
    for ln in lines[1:]:
        p = dict(zip(head, ln.split('|')))
        sym = p.get('Symbol') or p.get('ACT Symbol')
        if sym and p.get('Test Issue') == 'N': listed[sym] = fn
cohort2 = ADDITIONAL_REGISTRIES[-1]
used_rows = {c['sources']['selection']['row'] for c in cohort2['companies'] if 'spsm' in c['sources']['selection']['url']}
last = max(used_rows)
wb = openpyxl.load_workbook(SEL/'spsm.xlsx', read_only=True); rows = list(wb.active.iter_rows(values_only=True))
asof = rows[2][1].replace('As of ', '')
out, excl = [], []
for idx, r in enumerate(rows, start=1):
    if idx <= last or not r[1] or not isinstance(r[4], (int, float)): continue
    name, tk, w = r[0], str(r[1]).strip().upper(), r[4]
    sec_tk = tk.replace('.', '-')
    hit = by_ticker.get(sec_tk) or by_ticker.get(tk)
    if not hit: excl.append({'ticker': tk, 'fund': 'spsm', 'reason': 'no SEC ticker/CIK match'}); continue
    i, s = hit
    if s['cik'] in tracked_ciks or sec_tk in BY_TICKER:
        excl.append({'ticker': tk, 'fund': 'spsm', 'reason': 'CIK already tracked or listed under another class'}); continue
    lst = listed.get(tk) or listed.get(tk.replace('-', '.'))
    if not lst: excl.append({'ticker': tk, 'fund': 'spsm', 'reason': 'not in Nasdaq Trader symbol directories'}); continue
    tracked_ciks.add(s['cik'])
    out.append({'ticker': sec_tk, 'cik': s['cik'], 'sec_name': s['name'], 'fund_name': name, 'exchange': s['exchange'],
                'row': idx, 'weight': w, 'cte_index': i, 'listing_file': lst, 'listing_symbol': tk})
json.dump({'holdings_as_of': asof, 'spsm_sha256': sha(SEL/'spsm.xlsx'), 'cte_sha256': sha(SEL/'cte.json'),
           'nasdaqlisted_sha256': sha(SEL/'nasdaqlisted.txt'), 'otherlisted_sha256': sha(SEL/'otherlisted.txt'),
           'after_row': last, 'candidates': out, 'exclusions': excl}, open(SEL/'candidates.json', 'w'), indent=1)
print(len(out), len(excl), excl[:20])
