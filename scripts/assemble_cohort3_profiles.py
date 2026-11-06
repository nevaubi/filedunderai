"""Assemble public company profiles for cohort additional-399-spsm-2026-11-04-r1 from archived SEC EDGAR
submissions (official headquarters and SIC industry) and Data Legion company records retrieved through
Firecrawl Alexandria (companies/enrich-base, by ticker symbol).

Adapted from scripts/assemble_sec_provider_profiles.py: same profile schema, same exact/high-confidence
ticker gate and the same issuer-name check, with this cohort's own reviewed accept/reject sets. Leadership,
descriptions and images stay unavailable; provider people are never used.

Run from the repository root:
  python scripts/assemble_cohort3_profiles.py <sec_manifest_dir> <enrich_dir> <output_dir>

<sec_manifest_dir>/<TICKER>.json points at the archived (gzip) submissions JSON and records its URL,
SHA-256 and retrieval time. <enrich_dir>/group*.results.jsonl holds one raw provider response per ticker
({ticker, retrieved_at, response}). A name review is written next to the output directory.
"""
import glob
import gzip
import hashlib
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
from etl.universe import BY_TICKER, registry_by_id  # noqa: E402

COHORT_ID = 'additional-399-spsm-2026-11-04-r1'

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
SUFFIXES = {'inc', 'incorporated', 'corp', 'corporation', 'co', 'company', 'ltd', 'limited', 'plc', 'llc',
            'lp', 'l', 'p', 'sa', 'nv', 'ag', 'se', 'the', 'holdings', 'holding', 'group', 'cos', 'companies',
            'class', 'a', 'b', 'new', 'de', 'n', 'v', 'trust', 'international', 'intl'}

# Reviewed 2026-11-06, one by one, against the registry name, the SEC conformed and former names and the
# returned domain (see docs/audits/2026-11-06-cohort3-logos-profiles.json for each decision).
# Accepted: brand, bank-subsidiary or short-form names of the same issuer whose domain is the issuer's own.
REVIEWED_ACCEPT = set('''
SFNC NMIH PFS UNFI APAM CUBI FBK REZI FBNC NTCT HURN BANR BHE MCY WAFD VECO FCF KWR WLY CHCO ALGT PAYO
LMAT HOPE CBRL LKFN SNDR TFIN HE STC WABC ACT ICHR JBLU UFCS NX HAFC CFFN CPF SBSI GTM EGBN WINA CRSR WRLD
PRSU
'''.split())
# PRSU: pursuit.com verified 2026-11-06 as the issuer's site (pursuitcollection.com redirects there; page title
# "Pursuit Collection"), so the brand-name record "pursuit" is the same issuer.
# Rejected: provider record is a predecessor, a different entity, a subsidiary or a sub-unit, or it carries a
# name that no longer belongs to the issuer (reason recorded per ticker in REJECT_REASONS).
REJECT_REASONS = {
    'SBCF': 'provider record is "seacoast investor services", a 1-10 person unit, not the bank holding company',
    'MBC': 'provider record is "master brand nhb", a 1-10 person unit, not MasterBrand, Inc.',
    'EFC': 'provider record is "pm financial llc", a 1-10 person entity that only shares the issuer domain',
    'BANC': 'provider record is named "pacific western bank" (the merged predecessor bank), not Banc of California',
    'MD': 'provider record carries the pre-rename identity (mednax.com); current Pediatrix website not established',
    'PRDO': 'provider record carries the pre-rename identity Career Education Corporation (careered.com)',
    'CASH': 'provider record carries the pre-rename identity Meta Financial Group (metafinancialgroup.com)',
    'HTO': 'provider record carries the pre-rename identity SJW Group (sjwgroup.com)',
    'SAFE': 'provider record is iStar (istar.com), the merged predecessor, not Safehold Inc.',
    'INVX': 'provider record is named "dril-quip" with the Dril-Quip LinkedIn page (merged predecessor)',
    'PRG': 'provider record is the operating subsidiary Progressive Leasing (progleasing.com), not PROG Holdings',
    'USLM': 'provider record is the operating subsidiary Arkansas Lime Co, not United States Lime & Minerals',
    'UVV': 'provider record is the operating subsidiary Universal Leaf Tobacco Company, not Universal Corporation',
}
REVIEWED_REJECT = set(REJECT_REASONS)
NAME_REVIEW = []


def title(text):
    words = re.split(r'(\s+|-)', str(text).strip().lower())
    out = []
    for i, w in enumerate(words):
        if w.strip() and w not in ('-',) and (i == 0 or w not in SMALL):
            w = w[:1].upper() + w[1:]
        out.append(w)
    return ''.join(out)


def sec_source(cik, observed):
    return {'name': 'SEC EDGAR submissions', 'url': f'https://data.sec.gov/submissions/CIK{cik:010d}.json',
            'observed_at': observed, 'kind': 'official_filing_metadata'}


def headquarters(sec):
    b = (sec.get('addresses') or {}).get('business') or {}
    city = (b.get('city') or '').strip()
    code = (b.get('stateOrCountry') or '').strip().upper()
    if not city:
        return None
    if code in US_STATES and not b.get('isForeignLocation'):
        return f'{title(city)}, {US_STATES[code]}, United States'
    desc = (b.get('stateOrCountryDescription') or b.get('country') or '').strip()
    return f'{title(city)}, {title(desc)}' if desc else title(city)


def norm_tokens(name):
    words = re.sub(r'[^a-z0-9 ]+', ' ', str(name or '').lower().replace('&', ' and ')).split()
    return [w for w in words if w not in SUFFIXES]


def name_ok(provider_name, issuer_names, domain):
    """Provider identity must agree with the issuer's registry or SEC names, not just the ticker."""
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


def safe_domain(domain):
    domain = str(domain or '').strip().lower()
    return domain if re.fullmatch(r'[a-z0-9-]+(\.[a-z0-9-]+)+', domain) else None


def verified_match(ticker, response, issuer_names=()):
    """Exact, high-confidence ticker match to this public issuer whose name agrees with the issuer."""
    data = (response or {}).get('data') or {}
    for m in data.get('matches') or []:
        company, meta = m.get('company') or {}, m.get('match_metadata') or {}
        symbols = {(t.get('symbol') or '').upper() for t in company.get('tickers') or []}
        cname = (company.get('name') or {}).get('cleaned') if isinstance(company.get('name'), dict) else company.get('name')
        gate = (meta.get('match_type') == 'exact' and meta.get('match_confidence') == 'high'
                and ticker.upper() in symbols and company.get('type') in ('public', None))
        if not gate:
            NAME_REVIEW.append({'ticker': ticker, 'provider': cname, 'issuer': list(issuer_names)[:1],
                                'domain': company.get('domain'), 'type': company.get('type'), 'accepted': False,
                                'why': 'identity gate: requires exact/high ticker match of a public company record'})
            continue
        ok, why = name_ok(cname, issuer_names, safe_domain(company.get('domain')))
        auto = ok
        if ticker in REVIEWED_REJECT:
            ok, why = False, 'manual review: ' + REJECT_REASONS[ticker]
        elif not ok and ticker in REVIEWED_ACCEPT:
            ok, why = True, 'manual review: brand, short-form or bank-subsidiary name of the same issuer, matching domain'
        if not auto or ticker in REVIEWED_REJECT:
            NAME_REVIEW.append({'ticker': ticker, 'provider': cname, 'issuer': list(issuer_names)[:1],
                                'domain': company.get('domain'), 'type': company.get('type'), 'accepted': ok, 'why': why})
        if ok:
            return company, dict(meta, name_check=why)
    return None, None


def load_sec(manifest_dir, ticker):
    manifest = json.loads((Path(manifest_dir) / f'{ticker}.json').read_text())
    src = manifest['sources']['submissions']
    blob = Path(src['path']).read_bytes()
    raw = gzip.decompress(blob) if blob[:2] == b'\x1f\x8b' else blob
    # The manifest hash covers the archived source body (the decompressed SEC response).
    if src['sha256'] not in (hashlib.sha256(raw).hexdigest(), hashlib.sha256(blob).hexdigest()):
        raise ValueError(f'Archived submissions hash mismatch for {ticker}')
    return json.loads(raw), src


def build(ticker, sec, sec_src_meta, row):
    issuer = BY_TICKER[ticker]
    cik = issuer['cik']
    if int(sec.get('cik') or 0) != cik:
        raise ValueError(f'SEC CIK mismatch for {ticker}')
    observed_sec = sec_src_meta['retrieved_at']
    sec_src = sec_source(cik, observed_sec)
    profile = {'schema_version': 1, 'ticker': ticker, 'cik': cik, 'name': issuer['name']}
    field_sources, sources, observed = {}, [sec_src], {}
    hq = headquarters(sec)
    if hq:
        profile['headquarters'] = hq
        field_sources['headquarters'] = sec_src
        observed['headquarters'] = observed_sec
    if sec.get('sicDescription'):
        profile['industry'] = sec['sicDescription'].strip()
        field_sources['industry'] = sec_src
        observed['industry'] = observed_sec
    names = [issuer['name'], sec.get('name')] + [f.get('name') for f in sec.get('formerNames') or [] if isinstance(f, dict)][:3]
    company, meta = verified_match(ticker, (row or {}).get('response'), [n for n in names if n])
    updated = observed_sec
    if company:
        observed_dl = row['retrieved_at']
        updated = max(updated, observed_dl)
        dl = {'name': 'Data Legion (via Firecrawl)', 'url': 'https://www.firecrawl.dev/',
              'endpoint': 'datalegion/companies/enrich-base', 'record_id': company.get('legion_id'),
              'observed_at': observed_dl,
              'match': {'ticker': ticker, 'basis': 'exact_ticker_symbol; ' + meta.get('name_check', ''),
                        'confidence': meta.get('match_confidence'), 'matched_on': meta.get('matched_on')}}
        sources.append(dl)
        domain = safe_domain(company.get('domain'))
        if domain:
            profile['website'] = 'https://' + domain
            field_sources['website'] = dl
            observed['website'] = observed_dl
        li = company.get('linkedin_url')
        if isinstance(li, str) and re.match(r'^https://(www\.)?linkedin\.com/company/[A-Za-z0-9_.%-]+/?$', li):
            profile['linkedin_url'] = li
            field_sources['linkedin_url'] = dl
            observed['linkedin_url'] = observed_dl
        founded = company.get('founded')
        if isinstance(founded, int) and 1700 <= founded <= 2026:
            profile['founded'] = founded
            field_sources['founded'] = dl
            observed['founded'] = observed_dl
        size = company.get('size')
        if isinstance(size, str) and re.fullmatch(r'\d+(-\d+|\+)', size):
            profile['employee_size_range'] = size
            profile['employee_size_range_basis'] = 'Provider-reported size band; not company-reported workforce'
            field_sources['employee_size_range'] = dl
            observed['employee_size_range'] = observed_dl
    profile.update({
        'employees': None,
        'leadership': [],
        'images': [],
        'sources': sources,
        'updated_at': updated,
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
    manifests, enrich, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    rows = {}
    for path in sorted(glob.glob(str(enrich / 'group*.results.jsonl'))):
        for line in open(path):
            if line.strip():
                row = json.loads(line)
                rows[row['ticker']] = row
    tickers = [c['ticker'] for c in registry_by_id(COHORT_ID)['companies']]
    stats = {'profiles': 0, 'with_provider': 0, 'with_website': 0, 'no_response': [], 'not_found': [],
             'rejected': []}
    for ticker in tickers:
        sec, src = load_sec(manifests, ticker)
        row = rows.get(ticker)
        if row is None:
            stats['no_response'].append(ticker)
        elif not ((row.get('response') or {}).get('data') or {}).get('matches'):
            stats['not_found'].append(ticker)
        profile, enriched = build(ticker, sec, src, row)
        stats['profiles'] += 1
        stats['with_provider'] += enriched
        stats['with_website'] += 'website' in profile
        if row and not enriched and ticker not in stats['not_found']:
            stats['rejected'].append(ticker)
        (out / f'{ticker}.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2) + '\n')
    (out.parent / 'name_review.json').write_text(json.dumps(NAME_REVIEW, indent=1) + '\n')
    print(json.dumps(stats))


if __name__ == '__main__':
    main()
