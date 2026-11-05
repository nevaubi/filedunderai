"""Build the public registry manifest independently of screener publication.

Run from the repository root: python scripts/generate_company_directory.py
Optional reviewed profiles supply website/industry context; their issuer identity
must match both ticker and CIK. A stored fundamentals snapshot may also supply
the SEC industry description when ticker, registry name and sector agree.
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


def build_directory(profiles=None, generated_at=None, fundamentals=None):
    active = {t for t, *_ in UNIVERSE}
    profiles = profiles or {}
    fundamentals = fundamentals or {}
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
        filed = fundamentals.get(ticker) or {}
        sic = filed.get('sic')
        if (filed.get('ticker') == ticker and filed.get('name') == issuer['name']
                and filed.get('sector') == issuer['sector']
                and isinstance(sic, str) and sic.strip() and not sic.strip().isdigit()):
            row['sic_description'] = sic.strip()
            row['industry'] = sic.strip()
            row['industry_source'] = 'SEC Standard Industrial Classification'
        profile = profiles.get(ticker) or {}
        if profile.get('ticker') == ticker and profile.get('cik') == issuer['cik']:
            if isinstance(profile.get('industry'), str) and profile['industry'].strip():
                row['industry'] = profile['industry'].strip()
                row['industry_source'] = 'Sourced company profile'
            website = profile.get('website')
            if isinstance(website, str):
                parsed = urlparse(website if '://' in website else 'https://' + website)
                host = (parsed.hostname or '').lower().removeprefix('www.')
                if parsed.scheme in ('https','http') and '.' in host and not parsed.username:
                    row['domain'] = host
                    row['website'] = parsed.geturl()
            row['coverage']['profile'] = 'available'
            leaders = [person for person in profile.get('leadership', [])
                       if isinstance(person, dict) and person.get('name') and (person.get('role') or person.get('title'))]
            row['leadership_count'] = len(leaders)
            row['portrait_count'] = sum(bool(person.get('photo_url')) for person in leaders)
            row['coverage']['leadership'] = 'available' if leaders else 'unavailable'
            row['profile_updated_at'] = profile.get('updated_at')
            if profile.get('ir_url'):
                row['ir_url'] = profile['ir_url']
            if profile.get('headquarters'):
                row['headquarters'] = profile['headquarters']
        rows.append(row)
    return {
        'schema_version': 1,
        'generated_at': generated_at or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'source': 'Filed Under validated issuer registry (etl/universe.py and cohort registries)',
        'coverage_note': 'Registry discovery is independent of screener publication. Pending publication does not imply that source documents or prices are absent.',
        'counts': {'registered':len(rows), 'screener_eligible':sum(c['screener_eligible'] for c in rows),
                   'with_metadata':sum(c['coverage']['profile'] == 'available' for c in rows),
                   'with_leadership':sum(c.get('leadership_count', 0) > 0 for c in rows),
                   'leaders':sum(c.get('leadership_count', 0) for c in rows),
                   'portraits':sum(c.get('portrait_count', 0) for c in rows)},
        'companies': rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'public/data/company-directory.json')
    parser.add_argument('--profiles',type=Path,default=ROOT/'public/data/company-profiles')
    parser.add_argument('--fundamentals',type=Path,help='Previously retrieved meta/fundamentals.json; no network requests are made')
    args=parser.parse_args()
    profiles={}
    if args.profiles.exists():
        for path in args.profiles.glob('*.json'):
            if path.name!='index.json':
                data=json.loads(path.read_text())
                profiles[path.stem]=data
    fundamentals=json.loads(args.fundamentals.read_text()) if args.fundamentals else {}
    manifest=build_directory(profiles=profiles, fundamentals=fundamentals)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(manifest,separators=(',',':'),ensure_ascii=False)+'\n')
    print(json.dumps({'output':str(args.output.relative_to(ROOT)) if args.output.is_relative_to(ROOT) else str(args.output),**manifest['counts']}))

if __name__=='__main__':
    main()
