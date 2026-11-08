"""Medicare and Medicaid drug spending by brand and manufacturer (CMS, public data, no key).

Sources (data.cms.gov, CSV distributions found through the catalog at https://data.cms.gov/data.json):
  Medicare Part D Spending by Drug            annual, five years, by brand and manufacturer
  Medicare Quarterly Part D Spending by Drug  latest year-to-date and full prior year, by brand and manufacturer
  Medicaid Spending by Drug                   annual, by brand and manufacturer

CMS truncates manufacturer names to 15 characters ('Merck Sharp & D', 'Neurocrine Bios'), so a manufacturer maps to
a covered company by its first distinctive word (MERCK -> MRK) when exactly one Health Care company starts with
that word, plus a short table of brand-owner names that differ from the parent (BMS, Celgene, Janssen, Allergan...).

Outputs (public-data):
  medicare/{T}.json     yearly Part D and Medicaid spending, the quarterly series, and the top brands with growth
  medicare/index.json   companies ranked by latest Part D spending with year-over-year change"""
import csv
import datetime as dt
import io
import json
import re
import time
from concurrent.futures import ThreadPoolExecutor

from .common import now_iso, request
from .regulatory import GENERIC_WORDS, tokens

CATALOG = "https://data.cms.gov/data.json"
TITLES = {"partd": "Medicare Part D Spending by Drug", "partd_q": "Medicare Quarterly Part D Spending by Drug", "medicaid": "Medicaid Spending by Drug"}
UA = {"User-Agent": "FiledUnder research (CMS drug spending)"}
# Manufacturer labels CMS uses for units of covered parents, keyed by the label's first word.
OWNERS = {"BMS": "BMY", "CELGENE": "BMY", "BRISTOL": "BMY", "JUNO": "BMY", "MYOKARDIA": "BMY", "ALLERGAN": "ABBV", "PHARMACYCLICS": "ABBV",
          "JANSSEN": "JNJ", "ACTELION": "JNJ", "KITE": "GILD", "HORIZON": "AMGN", "SEAGEN": "PFE", "HOSPIRA": "PFE", "GREENSTONE": "VTRS",
          "MYLAN": "VTRS", "LOXO": "LLY", "MERCK": "MRK", "DERMIRA": "LLY", "ORGANON": "OGN", "SANDOZ": None, "GENENTECH": None, "VIIV": None}


def catalog_urls():
    """Latest CSV download URL for each dataset title."""
    d = json.loads(request(CATALOG, headers=UA, timeout=120, retries=3))
    out = {}
    for ds in d.get("dataset") or []:
        title = ds.get("title") or ""
        for key, want in TITLES.items():
            if title.startswith(want + " :") or title == want:
                if key == "partd" and "Quarterly" in title:
                    continue
                csvs = [x for x in ds.get("distribution") or [] if (x.get("format") or "").upper() == "CSV" or (x.get("downloadURL") or "").lower().endswith(".csv")]
                if csvs and key not in out:
                    out[key] = {"title": title, "url": csvs[0].get("downloadURL") or csvs[0].get("accessURL")}
    return out


def read_csv(url):
    raw = request(url, headers=UA, timeout=300, retries=3, raw=True)
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig", "replace"))))


def _f(x):
    try:
        return float(str(x).replace(",", "")) if x not in (None, "", "NA") else None
    except ValueError:
        return None


def first_word(name):
    ts = [w for w in tokens(name) if len(w) >= 3 and w not in GENERIC_WORDS]
    return ts[0] if ts else None


TAIL = ("CORPORATION", "INCORPORATED", "COMPANY", "LIMITED", "PHARMACEUTICALS", "PHARMACEUTICAL", "LABORATORIES", "THERAPEUTICS",
        "BIOSCIENCES", "BIOTHERAPEUTICS", "HOLDINGS", "INTERNATIONAL")


SOFT = GENERIC_WORDS | {"BIOPHARMA", "BIOPHARMACEUTICALS", "BIOPHARMACEUTICAL", "BIO", "BIOTECHNOLOGY", "PHARMACEUTICAL", "PHARMACEUTICALS"}
# Interchangeable in drug-maker names ('Xeris Pharmaceuticals' is 'Xeris Biopharma'); HEALTHCARE is not.
PHARMA = {"PHARMACEUTICALS", "PHARMACEUTICAL", "BIOPHARMA", "BIOPHARMACEUTICALS", "BIOPHARMACEUTICAL", "BIO", "BIOTECHNOLOGY", "THERAPEUTICS",
          "BIOSCIENCES", "LABORATORIES", "BIOTHERAPEUTICS"}


def _pharma(w, last):
    return w in PHARMA or (last and len(w) >= 5 and any(t.startswith(w) for t in PHARMA))


def _soft(w, last):
    return w in SOFT or (last and any(t.startswith(w) for t in TAIL + tuple(SOFT)))


def label_matches(label, phrase):
    """CMS label (truncated to 15 characters) against one company name, word by word: each label word equals the
    name's word at that position, the last may be a truncated prefix, and words past the end of the name must be
    generic ('Pfizer US Pharm' fits PFIZER; 'Edwards Pharm.' does not fit EDWARDS LIFESCIENCES; 'West-Ward' does not
    fit WEST PHARMACEUTICAL SERVICES)."""
    lt, pt = tokens(label), tokens(phrase)
    if not lt or not pt or lt[0] != pt[0]:
        return False
    for i, w in enumerate(lt[1:], start=1):
        last = i == len(lt) - 1
        if i < len(pt):
            if w == pt[i] or (last and pt[i].startswith(w)) or (_pharma(w, last) and pt[i] in PHARMA):
                continue
            return False
        if _soft(w, last):
            continue
        return False
    return True


def build_mapper(companies):
    """companies: {ticker: [names]} -> function(manufacturer label) -> ticker or None (None when two companies fit)."""
    covered = set(companies)
    memo = {}

    def m(label):
        if label not in memo:
            memo[label] = _m(label)
        return memo[label]

    def _m(label):
        w = first_word(label)
        if not w:
            return None
        if w in OWNERS:
            tk = OWNERS[w]
            return tk if tk in covered else None
        fits = {tk for tk, names in companies.items() if any(label_matches(label, n) for n in names)}
        return next(iter(fits)) if len(fits) == 1 else None
    return m


def annual(rows, mapper, kind):
    """{ticker: {"years": {year: spend}, "brands": {brand: {year: spend}}, "mftrs": set}} from an annual by-drug file."""
    out = {}
    for r in rows:
        mf = r.get("Mftr_Name") or ""
        if not mf or mf == "Overall":
            continue
        tk = mapper(mf)
        if not tk:
            continue
        o = out.setdefault(tk, {"years": {}, "brands": {}, "mftrs": set()})
        o["mftrs"].add(mf)
        brand = (r.get("Brnd_Name") or "").strip()
        for k, v in r.items():
            m = re.fullmatch(r"Tot_Spndng_(\d{4})", k or "")
            if m and _f(v):
                y = m.group(1)
                o["years"][y] = o["years"].get(y, 0) + _f(v)
                o["brands"].setdefault(brand, {})[y] = o["brands"].get(brand, {}).get(y, 0) + _f(v)
    return out


def quarterly(rows, mapper):
    """{ticker: {period label: spend, ...}, brands: {brand: {period: spend}}} from the quarterly Part D file."""
    out = {}
    for r in rows:
        mf = r.get("Mftr_Name") or ""
        if not mf or mf == "Overall":
            continue
        tk = mapper(mf)
        v = _f(r.get("Tot_Spndng"))
        if not tk or not v:
            continue
        per = r.get("Year") or ""
        o = out.setdefault(tk, {"periods": {}, "brands": {}})
        o["periods"][per] = o["periods"].get(per, 0) + v
        b = (r.get("Brnd_Name") or "").strip()
        o["brands"].setdefault(b, {})[per] = o["brands"].get(b, {}).get(per, 0) + v
    return out


def top_brands(brands, latest, prior, n=15):
    rows = []
    for b, ys in brands.items():
        cur, prev = ys.get(latest), ys.get(prior)
        if not cur:
            continue
        rows.append({"brand": b, "spend": round(cur), "prior": round(prev) if prev else None, "yoy": round(cur / prev - 1, 4) if prev else None})
    rows.sort(key=lambda r: -r["spend"])
    return rows[:n]


def medicare_job(sb, log=print):
    from .regulatory import company_phrases, targets
    t0 = time.time()
    urls = catalog_urls()
    log("cms files:", {k: v["title"] for k, v in urls.items()})
    with ThreadPoolExecutor(8) as ex:
        companies = dict(zip(targets(), ex.map(lambda t: company_phrases(sb, t), targets())))
    mapper = build_mapper(companies)
    data = {k: read_csv(v["url"]) for k, v in urls.items()}
    pd = annual(data.get("partd") or [], mapper, "partd")
    md = annual(data.get("medicaid") or [], mapper, "medicaid")
    pq = quarterly(data.get("partd_q") or [], mapper)
    index, written = [], 0
    try:
        old = {r["ticker"] for r in (sb.get_json("medicare/index.json") or {}).get("rows") or []}
    except Exception:
        old = set()
    for tk in sorted(set(pd) | set(md) | set(pq)):
        a, m, q = pd.get(tk) or {}, md.get(tk) or {}, pq.get(tk) or {}
        years = sorted((a.get("years") or {}).keys())
        latest = years[-1] if years else None
        prior = years[-2] if len(years) > 1 else None
        periods = q.get("periods") or {}
        full = sorted(p for p in periods if "Q1-Q4" in p)
        ytd = sorted(p for p in periods if "Q1-Q4" not in p)
        doc = {"ticker": tk, "generated": now_iso(),
               "part_d": {"years": {y: round(v) for y, v in sorted((a.get("years") or {}).items())}, "latest_year": latest,
                          "top_brands": top_brands(a.get("brands") or {}, latest, prior) if latest else []},
               "part_d_quarterly": {"periods": {p: round(v) for p, v in sorted(periods.items())},
                                    "top_brands": top_brands(q.get("brands") or {}, full[-1], None) if full else [],
                                    "ytd": ytd[-1] if ytd else None},
               "medicaid": {"years": {y: round(v) for y, v in sorted((m.get("years") or {}).items())}},
               "manufacturer_labels": sorted((a.get("mftrs") or set()) | (m.get("mftrs") or set())),
               "sources": {k: v["url"] for k, v in urls.items()},
               "note": "Gross spending before manufacturer rebates; CMS manufacturer labels mapped to the parent company."}
        sb.put_json(f"medicare/{tk}.json", doc, cache="max-age=86400")
        written += 1
        ys = doc["part_d"]["years"]
        lq = full[-1] if full else None
        index.append({"ticker": tk, "part_d_latest_year": latest, "part_d": ys.get(latest) if latest else None,
                      "part_d_yoy": round(ys[latest] / ys[prior] - 1, 4) if latest and prior and ys.get(prior) else None,
                      "part_d_last_full_year": lq, "part_d_last_full_year_spend": round(periods[lq]) if lq else None,
                      "top_brand": (doc["part_d"]["top_brands"] or [{}])[0].get("brand")})
    gone = sorted(old - {r["ticker"] for r in index})
    if gone:
        # a company no longer mapped (mapping tightened) must not keep a stale file
        request(f"{sb.url}/storage/v1/object/public-data", method="DELETE", data={"prefixes": [f"medicare/{t}.json" for t in gone]},
                headers=sb._h({"Content-Type": "application/json"}), timeout=60, retries=2)
    index.sort(key=lambda r: -(r.get("part_d_last_full_year_spend") or r.get("part_d") or 0))
    sb.put_json("medicare/index.json", {"generated": now_iso(), "n": len(index), "rows": index, "sources": {k: v["url"] for k, v in urls.items()}},
                cache="max-age=86400")
    return {"companies": written, "removed": gone, "seconds": round(time.time() - t0)}
