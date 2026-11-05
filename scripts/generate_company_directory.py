"""Build the public registry manifest independently of screener publication.

Run from the repository root: python scripts/generate_company_directory.py
Optional reviewed profiles supply known website/industry only; their issuer identity
must match both ticker and CIK before any metadata can be copied.
"""
import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from etl.universe import BY_TICKER, UNIVERSE, COHORT_OF


def build_directory(profiles=None, generated_at=None):
    active = {t for t, *_ in UNIVERSE}
    profiles = profiles or {}
    rows = []
    for ticker, issuer in sorted(BY_TICKER.items()):
        row = dict(issuer)
        row['screener_eligible'] = ticker in active
        row['coverage'] = {
            'directory': 'registered',
            'screener': 'eligible' if ticker in active else 'pending_publication',
            'profile': 'unavailable',
        }
        cohort = COHORT_OF.get(ticker)
        if cohort:
            row['cohort_id'] = cohort['cohort_id']
        profile = profiles.get(ticker) or {}
        if profile.get('ticker') == ticker and profile.get('cik') == issuer['cik']:
            if isinstance(profile.get('industry'), str) and profile['industry'].strip():
                row['industry'] = profile['industry'].strip()
            website = profile.get('website')
            if isinstance(website, str):
                parsed = urlparse(website if '://' in website else 'https://' + website)
                host = (parsed.hostname or '').lower().removeprefix('www.')
                if parsed.scheme in ('https','http') and '.' in host and not parsed.username:
                    row['domain'] = host
                    row['website'] = parsed.geturl()
            row['coverage']['profile'] = 'available'
        rows.append(row)
    return {
        'schema_version': 1,
        'generated_at': generated_at or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Filed Under validated issuer registry (etl/universe.py and cohort registries)',
        'coverage_note': 'Registry discovery is independent of screener publication. Pending publication does not imply that source documents or prices are absent.',
        'counts': {'registered':len(rows), 'screener_eligible':sum(c['screener_eligible'] for c in rows)},
        'companies': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'public/data/company-directory.json')
    parser.add_argument('--profiles',type=Path,default=ROOT/'public/data/company-profiles')
    args=parser.parse_args()
    profiles={}
    if args.profiles.exists():
        for path in args.profiles.glob('*.json'):
            if path.name!='index.json':
                data=json.loads(path.read_text())
                profiles[path.stem]=data
    manifest=build_directory(profiles=profiles)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(manifest,separators=(',',':'),ensure_ascii=False)+'\n')
    print(json.dumps({'output':str(args.output.relative_to(ROOT)) if args.output.is_relative_to(ROOT) else str(args.output),**manifest['counts']}))

if __name__=='__main__':
    main()
