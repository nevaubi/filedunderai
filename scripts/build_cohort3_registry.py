"""Build cohort 3 registry (pending) + the cohort identity file whose SHA binds price/IR manifests."""
import hashlib, json, sys
from pathlib import Path
C3 = Path(sys.argv[1]); REPO = Path(sys.argv[2])
cand = json.load(open(C3 / 'sel/candidates.json'))
guess = {r['ticker']: r for r in json.load(open(C3 / 'sectors_guess.json'))}
IT, HC, FIN, RE, IND, CD, CS, EN, UT, MAT, COM = ('Information Technology', 'Health Care', 'Financials', 'Real Estate',
    'Industrials', 'Consumer Discretionary', 'Consumer Staples', 'Energy', 'Utilities', 'Materials', 'Communication Services')
REVIEW = dict(AMTM=IND, CPK=UT, TDW=EN, SHOO=CD, ALRM=IT, HWKN=MAT, MGRC=IND, ABM=IND, BKU=FIN, TDC=IT, GPI=CD, OSW=CD,
    AGYS=IT, VAC=CD, WDFC=CS, SPSC=IT, PFS=FIN, AAP=CD, SXI=IND, VGNT=CD, ACHC=HC, CALM=CS, SHAK=CD, MGEE=UT, FRSH=IT,
    UNFI=CS, KN=IT, ACLS=IT, KSS=CD, PRVA=HC, FUL=MAT, CARG=COM, REZI=IND, PATK=CD, BFAM=CD, BCC=MAT, FIVN=IT, NEOG=HC,
    NHC=HC, JOE=RE, PHIN=CD, NTCT=IT, BBT=FIN, PPLI=COM, AVPT=IT, MD=HC, ACMR=IT, CALY=CD, DAN=CD, NGVT=MAT, TRN=IND,
    LCII=CD, NWL=CD, LOAR=IND, CPRI=CD, VVX=IND, DXC=IT, GOLF=CD, SEZL=FIN, PLUS=IT, PTON=CD, TILE=IND, PGNY=HC, CENX=MAT,
    NEO=HC, VECO=IT, PBI=IND, MAN=IND, WRBY=CD, ROG=IT, KWR=MAT, CENTA=CS, FA=IND, ECPG=FIN, RUN=IND, CC=MAT, COHU=IT,
    BTU=EN, EVTC=FIN, WLY=COM, OLN=MAT, WU=FIN, AIN=IND, MTX=MAT, DXPE=IND, WOR=IND, IOSP=MAT, KMT=IND, GRBK=CD, DBD=IT,
    CALX=IT, MSGR=COM, WKC=EN, PAYO=FIN, WWW=CD, BLKB=IT, ANDE=CS, AMN=HC, NSP=IND, PRGS=IT, HCSG=IND, KALU=MAT, LPG=EN,
    DV=IT, SBH=CD, MFP=IND, EZPW=FIN, ZD=COM, DFIN=FIN, THRM=CD, SAM=CS, SONO=CD, VSTS=IND, BL=IT, ARLO=IT, PRLB=IND,
    PRG=FIN, AMR=MAT, ALG=IND, CNXC=IND, MLKN=IND, AZTA=HC, MBC=IND, EFOR=IT, ENR=CS, SCL=MAT, PDFS=IT, STC=FIN, RSI=CD,
    EYE=CD, ENOV=HC, CCS=CD, REYN=CS, GBX=IND, JJSF=CS, ADIG=IND, USPH=HC, WS=MAT, UPBD=CD, ADNT=CD, LZB=CD, GT=CD,
    VYX=IT, ROCK=IND, UPWK=IND, TNC=IND, CERT=HC, CRI=CD, WEN=CD, GIII=CD, HFWA=FIN, TRIP=COM, SCSC=IT, DCH=CD, NSSC=IT,
    SLVM=MAT, PRSU=CD, YELP=COM, HLIT=IT, LQDT=IND, LNN=IND, UAA=CD, XPEL=CD, DLX=IND, QNST=COM, HZO=CD, NX=IND,
    CFFN=FIN, LGIH=CD, APOG=IND, VTOL=EN, UVV=CS, GTM=IT, QDEL=HC, CNXN=IT, SMP=CD, WGO=CD, DMC=CS, ASTE=IND, OI=MAT,
    WINA=CD, STAA=HC, CXM=IT, KOP=MAT, LZ=IND, NPK=IND, SAH=CD, HSTM=HC, FOXF=CD, MMI=RE, MTUS=MAT, CRSR=IT, PRKS=CD,
    SCHL=COM, SABR=IT, JBSS=CS, AGNT=RE, DFH=CD,
    # non-flagged SIC majorities corrected to the issuer's GICS sector
    XHR=RE, SHO=RE, DNOW=IND, LEU=EN, MARA=IT, CLSK=IT, ADEA=IT, BXMT=FIN, ARR=FIN, EFC=FIN, PMT=FIN, ADAM=FIN,
    OMCL=HC, ASTH=HC, TRUP=FIN, REX=EN, FUN=CD, UNIT=COM, CXW=IND, CRVL=HC)
UNCERTAIN = {'VGNT', 'UPBD', 'LQDT', 'SABR', 'WOR', 'CRVL', 'DFIN', 'UNIT', 'MFP', 'PRSU', 'BCC', 'CXW', 'EFOR'}
companies, excl = [], list(cand['exclusions'])
for c in cand['candidates']:
    m = json.loads((C3 / 'root/sec-manifests' / f"{c['ticker']}.json").read_text())
    if m['status'] != 'collected':
        excl.append({'ticker': c['ticker'], 'fund': 'spsm', 'reason': 'companyfacts: ' + m['sources'].get('error', 'unavailable')})
        continue
    g = guess[c['ticker']]
    if c['ticker'] in REVIEW and REVIEW[c['ticker']] != g['guess']:
        sector, basis = REVIEW[c['ticker']], {'basis': 'reviewed_gics', 'previous': g['guess'], 'sic': g['sic']}
    elif g['guess']:
        sector, basis = g['guess'], {'basis': 'tracked_sic_majority', 'sic': g['sic'], 'share': g['share'], 'tracked': g['n']}
        if c['ticker'] in REVIEW: basis['reviewed'] = True
    else:
        raise SystemExit('no sector for ' + c['ticker'])
    if c['ticker'] in UNCERTAIN: basis['uncertain'] = True
    companies.append({'ticker': c['ticker'], 'cik': c['cik'], 'name': c['sec_name'], 'sector': sector,
        'selection_rank': len(companies) + 1, 'sector_basis': basis, 'sources': {
        'selection': {'url': 'https://www.ssga.com/library-content/products/fund-data/etfs/us/holdings-daily-us-en-spsm.xlsx',
                      'sha256': cand['spsm_sha256'], 'row': c['row'], 'weight': c['weight'], 'holdings_as_of': cand['holdings_as_of']},
        'sec_identity': {'url': 'https://www.sec.gov/files/company_tickers_exchange.json', 'sha256': cand['cte_sha256'],
                         'json_pointer': f"/data/{c['cte_index']}"},
        'exchange_listing': {'url': 'https://www.nasdaqtrader.com/dynamic/symdir/' + c['listing_file'],
                             'sha256': cand['nasdaqlisted_sha256' if c['listing_file'] == 'nasdaqlisted.txt' else 'otherlisted_sha256'],
                             'symbol': c['listing_symbol']}}})
cohort_id = f'additional-{len(companies)}-spsm-2026-11-04-r1'
identity = {'schema_version': 1, 'cohort_id': cohort_id,
            'companies': [{'ticker': c['ticker'], 'cik': c['cik'], 'name': c['name'], 'currency': 'USD'} for c in companies]}
body = json.dumps(identity, indent=1).encode() + b'\n'
(C3 / 'cohort.json').write_bytes(body)
(REPO / 'docs/audits').mkdir(parents=True, exist_ok=True)
(REPO / f'docs/audits/{cohort_id}-identity.json').write_bytes(body)
parent = REPO / 'etl/additional_companies_mdy-spsm-2026-11-04.json'
registry = {'schema_version': 1, 'cohort_id': cohort_id,
    'selection_basis': ('Remaining S&P SmallCap 600 issuers by official SPSM holding weight after the rows used by '
        'additional-300-mdy-spsm-2026-11-04-r1 (same holdings file, SHA-256 checked), with SEC Company Facts snapshots, after '
        'SEC (company_tickers_exchange) and Nasdaq Trader listing identity checks. Fund holdings are a selection basis, not an '
        'index-membership certification. SPSM gives no sectors: each sector is the one held by tracked companies with the same '
        'SEC SIC description, reviewed per company and corrected to the issuer GICS sector where they differ (sector_basis); '
        'uncertain assignments are flagged.'),
    'holdings_as_of': '2026-11-04', 'cohort_sha256': hashlib.sha256(body).hexdigest(),
    'parent_cohort_sha256': json.loads(parent.read_text())['cohort_sha256'], 'publication_revision': None,
    'active_tickers': [], 'exclusions': excl, 'companies': companies}
out = REPO / 'etl/additional_companies_spsm-rest-2026-11-04.json'
out.write_text(json.dumps(registry, indent=1) + '\n')
import collections
print(cohort_id, len(companies), registry['cohort_sha256'], collections.Counter(c['sector'] for c in companies),
      collections.Counter(c['sector_basis']['basis'] for c in companies))
