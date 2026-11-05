"""Offline financial repair preview. Reads archived/public files and writes candidates only.

Example:
  python -m etl.repair --bundle AMT.json --companyfacts CIK0001053507.json.gz \
      --output /tmp/amt-review

No production client, network request, database mutation, or upload is performed.
Inspect review.json and candidate-company.json before any separately authorized publish.
"""
import argparse
import copy
import gzip
import hashlib
import json
from pathlib import Path

from . import fundamentals, sec


def read_json(path):
    raw = Path(path).read_bytes()
    return json.loads(gzip.decompress(raw) if raw[:2] == b'\x1f\x8b' else raw)


def public_facts(bundle):
    """Recover reported observations only. Cannot recreate concepts discarded upstream."""
    facts = {}
    def add(c, unit, r):
        if c and unit:
            facts.setdefault('us-gaap', {}).setdefault(c, {'units': {}})['units'].setdefault(unit, []).append(r)
    for stmt, rows in bundle.get('statements', {}).items():
        for row in rows:
            history = row.get('filing_history') or row
            for kind in ('annual', 'quarterly'):
                for r in history.get(kind, []):
                    if r.get('derived'):
                        continue
                    obs = {k:r[k] for k in ('start','end','accn','form','filed') if k in r}
                    obs['val'] = r['v']
                    if kind == 'annual':
                        obs['fp'] = 'FY'
                    add(r.get('c') or row.get('concept'), row.get('unit'), obs)
    for accn, filing in bundle.get('filingTables', {}).items():
        if not filing.get('filed') or not filing.get('form'):
            continue
        for stmt, table in filing.get('statements', {}).items():
            for row in table.get('rows', []):
                for period, value in row.get('vals', {}).items():
                    parts = period.split('|')
                    obs = {'end':parts[-1], 'val':value, 'accn':accn, 'form':filing['form'], 'filed':filing['filed']}
                    if len(parts)==2:
                        obs['start']=parts[0]
                    if obs['form'].startswith('10-K'):
                        obs['fp']='FY'
                    add(row.get('concept'), row.get('unit'), obs)
    return facts


def value_index(bundle):
    out = {}
    for stmt, rows in bundle.get('statements', {}).items():
        for row in rows:
            for kind in ('annual','quarterly'):
                for r in row.get(kind, []):
                    out[f'{stmt}/{row["label"]}/{kind}/{r["end"]}'] = {k:r.get(k) for k in ('v','start','c','accn','filed','available_at','accessions')}
    return out


def rebuild(bundle, facts, vintage='latest', as_of=None, price=None, archived=True):
    candidate = copy.deepcopy(bundle)
    candidate['pipeline_version'] = 2
    candidate['statements'] = {k:sec.build_series(facts,k,vintage=vintage,as_of=as_of) for k in ('income','balance','cashflow')}
    if not any(candidate['statements'].values()):
        raise ValueError('No usable source facts; refusing to produce an empty replacement')
    profile = candidate.setdefault('profile', {})
    # Public reconstruction cannot prove cover-page class identity.
    profile['sharesOutstanding'] = sec.shares_outstanding(facts,as_of=as_of) if archived else None
    candidate['normalization'] = {'version':2, 'vintage':vintage, 'as_of':as_of, 'source':'archived_companyfacts' if archived else 'public_observations', 'review_required':True}
    prior, after = value_index(bundle), value_index(candidate)
    changes = [{'path':k,'before':prior.get(k),'after':after.get(k)} for k in sorted(prior.keys()|after.keys()) if prior.get(k)!=after.get(k)]
    fund = fundamentals.compute(candidate['statements'], profile, price, ticker=candidate.get('ticker'))
    report = {'ticker':bundle.get('ticker'), 'mode':'offline_candidate_only', 'vintage':vintage,'as_of':as_of,
              'changed_periods':len(changes), 'changes':changes, 'fundamentals':fund,
              'shares_before':bundle.get('profile',{}).get('sharesOutstanding'), 'shares_after':profile['sharesOutstanding'],
              'limitations':[]}
    if not archived:
        report['limitations'].append('Public-only reconstruction cannot recover discarded concepts, missing original vintages, or cover-page share-class identity. Obtain archived companyfacts before approving a complete repair.')
    if as_of:
        report['limitations'].append('Filing-date cutoff is day-granular; intraday availability and next-session execution require a separate market calendar policy.')
    if profile.get('sharesOutstanding') is None or profile['sharesOutstanding'].get('val') is None:
        report['limitations'].append('Exact outstanding shares unavailable or ambiguous; market capitalization remains unknown.')
    if fund.get('fcf_ttm') is None:
        report['limitations'].append('FCF unavailable without four aligned consecutive OCF and capex quarters.')
    candidate['fundamentals'] = fund
    return candidate, report


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', required=True, type=Path)
    p.add_argument('--companyfacts', type=Path)
    p.add_argument('--prices', type=Path, help='Optional price-stat JSON with price/as_of, or public daily price bundle')
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--vintage', choices=['latest','original'], default='latest')
    p.add_argument('--as-of')
    a=p.parse_args(argv)
    bundle=read_json(a.bundle)
    cf=read_json(a.companyfacts) if a.companyfacts else None
    facts=(cf.get('facts',cf) if cf else public_facts(bundle))
    price=read_json(a.prices) if a.prices else None
    if price and 'd' in price:
        from .prices import stats
        price=stats(price['d'])
    candidate, review=rebuild(bundle,facts,a.vintage,a.as_of,price,archived=bool(cf))
    review['inputs']=[{'file':str(path.resolve()),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()} for path in (a.bundle,a.companyfacts,a.prices) if path]
    a.output.mkdir(parents=True,exist_ok=True)
    for name in ('candidate-company.json','review.json'):
        if (a.output/name).exists():
            raise FileExistsError(f'Refusing to overwrite existing review output: {a.output/name}')
    for name,obj in (('candidate-company.json',candidate),('review.json',review)):
        (a.output/name).write_text(json.dumps(obj,indent=2,sort_keys=True)+'\n')
    print(json.dumps({'candidate':str(a.output/'candidate-company.json'),'review':str(a.output/'review.json'),'changed_periods':review['changed_periods'],'limitations':review['limitations']}))


if __name__=='__main__':
    main()
