"""Assemble public company profiles for issuers without one, from SEC EDGAR submissions
(official headquarters) and verified Data Legion company records retrieved through Firecrawl.

Run from the repository root:
  python <this file> <scratch_dir> <output_dir>
"""
import glob
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from etl.universe import BY_TICKER  # noqa: E402

SCRATCH, OUT = Path(sys.argv[1]), Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)
OBSERVED = sys.argv[3] if len(sys.argv) > 3 else '2026-11-05T22:30:00Z'

US_STATES = {
    'AL': 'Alabama', 'AK': 'Alaska', 'AZ': 'Arizona', 'AR': 'Arkansas', 'CA': 'California', 'CO': 'Colorado',
    'CT': 'Connecticut', 'DE': 'Delaware', 'DC': 'District of Columbia', 'FL': 'Florida', 'GA': 'Georgia',
    'HI': 'Hawaii', 'ID': 'Idaho', 'IL': 'Illinois', 'IN': 'Indiana', 'IA': 'Iowa', 'KS': 'Kansas',
    'KY': 'Kentucky', 'LA': 'Louisiana', 'ME': 'Maine', 'MD': 'Maryland', 'MA': 'Massachusetts',
    'MI': 'Michigan', 'MN': 'Minnesota', 'MS': 'Mississippi', 'MO': 'Missouri', 'MT': 'Montana',
    'NE': 'Nebraska', 'NV': 'Nevada', 'NH': 'New Hampshire', 'NJ': 'New Jersey', 'NM': 'New Mexico',
    'NY': 'New York', 'NC': 'North Carolina', 'ND': 'North Dakota', 'OH': 'Ohio', 'OK': 'Oklahoma',
    'OR': 'Oregon', 'PA': 'Pennsylvania', 'RI': 'Rhode Island', 'SC': 'South Carolina', 'SD': 'South Dakota',
    'TN': 'Tennessee', 'TX': 'Texas', 'UT': 'Utah', 'VT': 'Vermont', 'VA': 'Virginia', 'WA': 'Washington',
    'WV': 'West Virginia', 'WI': 'Wisconsin', 'WY': 'Wyoming', 'PR': 'Puerto Rico',
}
SMALL = {'and', 'of', 'the', 'for', 'in', 'on', 'at', 'to'}


def title(text):
    words = re.split(r'(\s+|-)', str(text).strip().lower())
    out = []
    for i, w in enumerate(words):
        if w.strip() and w not in ('-',) and (i == 0 or w not in SMALL):
            w = w[:1].upper() + w[1:]
        out.append(w)
    return ''.join(out)


def sec_source(cik):
    return {'name': 'SEC EDGAR submissions', 'url': f'https://data.sec.gov/submissions/CIK{cik:010d}.json',
            'observed_at': OBSERVED, 'kind': 'official_filing_metadata'}


def headquarters(sec):
    b = sec.get('business') or {}
    city = (b.get('city') or '').strip()
    code = (b.get('stateOrCountry') or '').strip().upper()
    if not city:
        return None
    if code in US_STATES and not b.get('isForeignLocation'):
        return f'{title(city)}, {US_STATES[code]}, United States'
    desc = (b.get('stateOrCountryDescription') or b.get('country') or '').strip()
    return f'{title(city)}, {title(desc)}' if desc else title(city)


SUFFIXES = {'inc', 'incorporated', 'corp', 'corporation', 'co', 'company', 'ltd', 'limited', 'plc', 'llc',
            'lp', 'l', 'p', 'sa', 'nv', 'ag', 'se', 'the', 'holdings', 'holding', 'group', 'cos', 'companies',
            'class', 'a', 'b', 'new', 'de', 'n', 'v', 'trust', 'international', 'intl'}
NAME_REVIEW = []
# Reviewed 2026-11-05 against registry/SEC names and the returned domain.
REVIEWED_ACCEPT = set('''ABCB ADM ADP AJG ALLY AMG APA ATR AXON AXTA BDX BG BR BWXT CARR CART CBSH CFG CFR CINF CMC CNH
CNX COLB COO CVBF CVLT DRI ED EEFT ELAN EMN EWBC EXLS FBP FERG FFBC FFIN FITB FLG FNB FOUR FULT GNW GTES H HALO HBAN
HOOD IBOC IFF INDB IRDM ITW J JEF JKHY JLL JXN KEY KKR LSTR MAA MIR MSM MTG MTSI NSIT NVT ON OTIS PAYC PB PEG POOL
POWL PPC PPG RELY RF RLI ROIV SAIC SFBS SLAB SLM SON SYF TCBI TDS TFC UHS UMBF URBN USB VFC VLO WDC WSFS YOU'''.split())
REVIEWED_REJECT = set('ENSG ERIE GPC HII LNC MNST OMC PG UBSI'.split())


def norm_tokens(name):
    words = re.sub(r'[^a-z0-9 ]+', ' ', str(name or '').lower().replace('&', ' and ')).split()
    return [w for w in words if w not in SUFFIXES]


def name_ok(provider_name, issuer_names, domain):
    """Provider identity must agree with the issuer's registry or SEC names, not just the ticker."""
    from difflib import SequenceMatcher
    p = norm_tokens(provider_name)
    if not p:
        return False, 'empty provider name'
    pj = ''.join(p)
    label = (domain or '').split('.')[-2] if domain and '.' in domain else ''
    for issuer in issuer_names:
        q = norm_tokens(issuer)
        if not q:
            continue
        qj = ''.join(q)
        if pj == qj:
            return True, 'exact normalized name'
        if SequenceMatcher(None, pj, qj).ratio() >= 0.88:
            return True, 'near-identical name'
        shared = set(p) & set(q)
        if len(shared) >= 2 and len(shared) / len(set(p) | set(q)) >= 0.6:
            return True, 'name tokens'
        if label and len(label) >= 4 and (label == qj or (label in qj and len(label) >= 0.6 * len(qj))) and (set(p) & set(q)):
            return True, 'issuer name matches domain and provider name'
    return False, 'name mismatch'


def verified_match(ticker, response, issuer_names=()):
    """Return the Data Legion company only when it is an exact, high-confidence ticker match to
    this public issuer AND its name agrees with the issuer (ticker matches alone were wrong for
    several issuers, e.g. a French real-estate firm for PG)."""
    data = (response or {}).get('data') or {}
    for m in data.get('matches') or []:
        company, meta = m.get('company') or {}, m.get('match_metadata') or {}
        symbols = {(t.get('symbol') or '').upper() for t in company.get('tickers') or []}
        if not (meta.get('match_type') == 'exact' and meta.get('match_confidence') == 'high'
                and ticker.upper() in symbols and company.get('type') in ('public', None)):
            continue
        cname = (company.get('name') or {}).get('cleaned') if isinstance(company.get('name'), dict) else company.get('name')
        ok, why = name_ok(cname, issuer_names, safe_domain(company.get('domain')))
        if ticker in REVIEWED_REJECT:
            ok, why = False, 'manual review: different or subsidiary entity'
        elif not ok and ticker in REVIEWED_ACCEPT:
            ok, why = True, 'manual review: brand or bank-subsidiary name of the same issuer, matching domain'
        NAME_REVIEW.append({'ticker': ticker, 'provider': cname, 'issuer': list(issuer_names)[:1],
                            'domain': company.get('domain'), 'accepted': ok, 'why': why})
        if ok:
            meta = dict(meta, name_check=why)
            return company, meta
    return None, None


def safe_domain(domain):
    domain = str(domain or '').strip().lower()
    return domain if re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', domain) else None


def build(ticker, sec, response):
    issuer = BY_TICKER[ticker]
    cik = issuer['cik']
    if int(sec.get('cik') or 0) != cik:
        raise ValueError(f'SEC CIK mismatch for {ticker}')
    sec_src = sec_source(cik)
    profile = {'schema_version': 1, 'ticker': ticker, 'cik': cik, 'name': issuer['name']}
    field_sources, sources, observed = {}, [sec_src], {}
    hq = headquarters(sec)
    if hq:
        profile['headquarters'] = hq
        field_sources['headquarters'] = sec_src
        observed['headquarters'] = OBSERVED
    if sec.get('sicDescription'):
        profile['industry'] = sec['sicDescription'].strip()
        field_sources['industry'] = sec_src
        observed['industry'] = OBSERVED
    names = [issuer['name'], sec.get('name')] + [f.get('name') for f in sec.get('formerNames') or [] if isinstance(f, dict)][:3]
    company, meta = verified_match(ticker, response, [n for n in names if n])
    if company:
        dl = {'name': 'Data Legion (via Firecrawl)', 'url': 'https://www.firecrawl.dev/',
              'endpoint': 'datalegion/companies/enrich-base', 'record_id': company.get('legion_id'),
              'observed_at': OBSERVED,
              'match': {'ticker': ticker, 'basis': 'exact_ticker_symbol; ' + meta.get('name_check', ''), 'confidence': meta.get('match_confidence'),
                        'matched_on': meta.get('matched_on')}}
        sources.append(dl)
        domain = safe_domain(company.get('domain'))
        if domain:
            profile['website'] = 'https://' + domain
            field_sources['website'] = dl
            observed['website'] = OBSERVED
        li = company.get('linkedin_url')
        if isinstance(li, str) and re.match(r'^https://(www\.)?linkedin\.com/company/[A-Za-z0-9_.%-]+/?$', li):
            profile['linkedin_url'] = li
            field_sources['linkedin_url'] = dl
            observed['linkedin_url'] = OBSERVED
        founded = company.get('founded')
        if isinstance(founded, int) and 1700 <= founded <= 2026:
            profile['founded'] = founded
            field_sources['founded'] = dl
            observed['founded'] = OBSERVED
        size = company.get('size')
        if isinstance(size, str) and re.fullmatch(r'\d+(-\d+|\+)', size):
            profile['employee_size_range'] = size
            profile['employee_size_range_basis'] = 'Provider-reported size band; not company-reported workforce'
            field_sources['employee_size_range'] = dl
            observed['employee_size_range'] = OBSERVED
    profile.update({
        'employees': None,
        'leadership': [],
        'images': [],
        'sources': sources,
        'updated_at': OBSERVED,
        'field_sources': field_sources,
        'coverage': {
            'status': 'provider_enriched' if company else 'filing_metadata',
            'description': 'unavailable',
            'leadership': 'unavailable',
            'images': 'unavailable',
            'leadership_scope': 'No sourced leadership roster yet; officers are not inferred from provider data.',
            'field_observed_at': observed,
        },
    })
    return profile, bool(company)


def main():
    responses = {}
    for path in glob.glob(str(SCRATCH / 'enrich' / 'group*.results.jsonl')):
        for line in open(path):
            line = line.strip()
            if line:
                row = json.loads(line)
                responses[row['ticker']] = row.get('response')
    missing = json.load(open(SCRATCH / 'profiles_missing.json'))
    stats = {'profiles': 0, 'with_provider': 0, 'with_website': 0, 'no_response': []}
    for ticker in missing:
        sec = json.load(open(SCRATCH / 'sec' / f'{ticker}.json'))
        if ticker not in responses:
            stats['no_response'].append(ticker)
        profile, enriched = build(ticker, sec, responses.get(ticker))
        stats['profiles'] += 1
        stats['with_provider'] += enriched
        stats['with_website'] += 'website' in profile
        (OUT / f'{ticker}.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
    (SCRATCH / 'name_review.json').write_text(json.dumps(NAME_REVIEW, indent=1))
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
