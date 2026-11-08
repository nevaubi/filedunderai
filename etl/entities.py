"""Company registry: one identity record per covered company, and a matcher that maps names found in outside
records (federal awards, lobbying clients, supply-chain counterparties, agency data) back to a company.

Each record joins, from authoritative free sources:
  - SEC: CIK, registrant name, former names, Exhibit 21 subsidiaries (etl/gov_entities.py keeps these fresh)
  - GLEIF: the LEI of the registrant and the legal names of its ultimate children (Level 2 relationship data)
  - OpenFIGI: the composite FIGI for the US listing
  - CUSIPs from the issuer's own 13D/13G cover pages (etl/flows_holders.py cache)

Matching is exact on a normalized legal name (etl/altdata.norm_name). A name that resolves to one company through
its legal or former name is a strong match; through a subsidiary or GLEIF child it is a subsidiary match; a name
shared by several companies, or a generic subsidiary name ('Holdings LLC'), is never guessed: it goes to the review
list with the candidates.

Outputs: private raw entities/registry.json.gz (full record), public entities/index.json (identifiers and counts per
company, for the app and the assistant) and entities/review.json (ambiguous names seen by consumers).
"""
import datetime as dt
import gzip
import json
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from .altdata import LEGAL_TAIL, core_name, norm_name
from .common import now_iso, request

REGISTRY_PATH = "entities/registry.json.gz"   # raw bucket (full)
INDEX_PATH = "entities/index.json"            # public-data (compact)
REVIEW_PATH = "entities/review.json"          # public-data
GLEIF = "https://api.gleif.org/api/v1/"
OPENFIGI = "https://api.openfigi.com/v3/mapping"
UA = {"User-Agent": "FiledUnder research (entity registry)", "Accept": "application/vnd.api+json"}
REFRESH_DAYS = 30
MAX_CHILDREN = 400

# Words that make a subsidiary name too generic to use on its own once the legal tail is removed.
GENERIC = {"HOLDING", "HOLDINGS", "INTERNATIONAL", "GLOBAL", "SERVICES", "SERVICE", "FINANCE", "FINANCIAL", "CAPITAL",
           "INVESTMENTS", "INVESTMENT", "MANAGEMENT", "PROPERTIES", "REALTY", "ENTERPRISES", "VENTURES", "PARTNERS",
           "AMERICA", "AMERICAS", "USA", "US", "NORTH", "SOUTH", "EAST", "WEST", "OPERATING", "OPERATIONS", "TECHNOLOGIES",
           "TECHNOLOGY", "SOLUTIONS", "SYSTEMS", "INDUSTRIES", "LEASING", "FUNDING", "ACQUISITION", "ACQUISITIONS", "ONE",
           "TWO", "I", "II", "III", "IV", "NEWCO", "MERGER", "SUB", "LLC", "LP", "INC"}


HEADER = re.compile(r"\b(ENTITY NAME|NAME OF (SUBSIDIARY|ENTITY|COMPANY)|JURISDICTION|STATE OF (INCORPORATION|ORGANIZATION)|"
                    r"SUBSIDIARIES OF|PERCENT|OWNERSHIP|DOMESTIC SUBSIDIARIES|FOREIGN SUBSIDIARIES|EXHIBIT 21)\b")
DATE = re.compile(r"\b(JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\b|\b(19|20)\d{2}\b")


def generic(name):
    """True for names that would collide across companies ('Holdings LLC', 'International Inc', 'NewCo II') and for
    Exhibit 21 header or date rows ('Entity Name', 'December 31, 2025')."""
    up = norm_name(name)
    if HEADER.search(up) or DATE.search(up):
        return True
    words = [w for w in core_name(name).split() if w not in LEGAL_TAIL]
    if not words:
        return True
    if all(w in GENERIC or len(w) <= 2 or w.isdigit() for w in words):
        return True
    return len(" ".join(words)) < 5


# ---------------------------------------------------------------- sources

_GLEIF_LOCK = threading.Lock()
_GLEIF_LAST = [0.0]
GLEIF_GAP = 1.05  # GLEIF allows 60 requests per minute


def _gleif_get(path, params=None):
    with _GLEIF_LOCK:
        wait = _GLEIF_LAST[0] + GLEIF_GAP - time.time()
        if wait > 0:
            time.sleep(wait)
        _GLEIF_LAST[0] = time.time()
    url = GLEIF + path + (("?" + urllib.parse.urlencode(params)) if params else "")
    for attempt in range(4):
        try:
            return json.loads(request(url, headers=UA, timeout=60, retries=1))
        except Exception as e:
            if attempt == 3:
                raise
            time.sleep(2 + 3 * attempt if "429" not in str(e) else 20)
    return {}


def gleif_lei(names, country="US"):
    """The registrant's LEI: an ACTIVE record whose normalized legal name equals one of the SEC names. GLEIF's
    legalName filter is exact, and SEC names are abbreviated ('RTX Corp' vs 'RTX Corporation'), so the search is a
    full-text query on the core name; the match itself stays exact on the normalized name. US records first;
    foreign private issuers (20-F/40-F) fall back to any jurisdiction."""
    want = {norm_name(n) for n in names if n}
    hits = []
    queries = []
    for n in names:
        if not n:
            continue
        base = re.sub(r"\s*/[A-Z]{2,3}/?\s*$", "", n.strip())
        queries += [("filter[entity.legalName]", v) for v in name_variants(base)]
    queries += [("filter[fulltext]", q) for q in dict.fromkeys(core_name(n) for n in names if n and core_name(n))]
    for field, q in queries:
        d = _gleif_get("lei-records", {field: q, "page[size]": 50})
        for r in d.get("data") or []:
            a = r.get("attributes") or {}
            ent = a.get("entity") or {}
            legal = (ent.get("legalName") or {}).get("name")
            if norm_name(legal) in want and ent.get("status") == "ACTIVE":
                hits.append({"lei": a.get("lei"), "name": legal, "jurisdiction": ent.get("jurisdiction"),
                             "registration": (a.get("registration") or {}).get("status")})
        if hits:
            break
    if not hits:
        return None
    hits.sort(key=lambda h: (not (h["jurisdiction"] or "").startswith(country), h["registration"] != "ISSUED"))
    return hits[0]


ABBREV = {"CORP": "CORPORATION", "INC": "INC.", "CO": "COMPANY", "LTD": "LIMITED", "HLDGS": "HOLDINGS", "INTL": "INTERNATIONAL"}


def name_variants(name):
    """Spellings GLEIF's exact legalName filter may hold for an SEC name: as filed, title case, and with the
    abbreviated legal tail spelled out ('LOCKHEED MARTIN CORP' -> 'LOCKHEED MARTIN CORPORATION')."""
    words = name.replace(",", " ,").split()
    if not words:
        return []
    tail = words[-1].rstrip(".").upper()
    expanded = " ".join(words[:-1] + [ABBREV[tail]]).replace(" ,", ",") if tail in ABBREV else None
    out = [name, expanded, name.title(), expanded.title() if expanded else None]
    if expanded and tail == "INC":
        out.append(" ".join(words[:-1]).replace(" ,", ",") + ", Inc.")
    return list(dict.fromkeys(v for v in out if v))


def gleif_children(lei, limit=MAX_CHILDREN):
    """Legal names of the LEI's ultimate children (accounting consolidation per GLEIF Level 2)."""
    out, page = [], 1
    while len(out) < limit:
        d = _gleif_get(f"lei-records/{lei}/ultimate-children", {"page[size]": 100, "page[number]": page})
        rows = d.get("data") or []
        for r in rows:
            a = r.get("attributes") or {}
            ent = a.get("entity") or {}
            out.append({"lei": a.get("lei"), "name": (ent.get("legalName") or {}).get("name"), "jurisdiction": ent.get("jurisdiction")})
        last = ((d.get("meta") or {}).get("pagination") or {}).get("lastPage") or 1
        if page >= last or not rows:
            break
        page += 1
    return [c for c in out if c.get("name")][:limit]


def openfigi(tickers, api_key=None):
    """{ticker: composite FIGI} for US listings. 10 jobs per request without a key (25 requests/minute)."""
    out, size = {}, 100 if api_key else 10
    headers = {"Content-Type": "application/json", **({"X-OPENFIGI-APIKEY": api_key} if api_key else {})}
    for i in range(0, len(tickers), size):
        batch = tickers[i:i + size]
        jobs = [{"idType": "TICKER", "idValue": t.replace("-", "/"), "exchCode": "US"} for t in batch]
        for attempt in range(4):
            try:
                res = json.loads(request(OPENFIGI, method="POST", data=jobs, headers=headers, timeout=60, retries=1))
                break
            except Exception as e:
                if attempt == 3:
                    res = []
                    break
                time.sleep(15 if "429" in str(e) else 3)
        for t, r in zip(batch, res or []):
            data = (r or {}).get("data") or []
            eq = [x for x in data if x.get("marketSector") == "Equity"] or data
            if eq:
                out[t] = {"figi": eq[0].get("compositeFIGI") or eq[0].get("figi"), "name": eq[0].get("name"),
                          "security_type": eq[0].get("securityType")}
        if not api_key:
            time.sleep(2.6)
    return out


# ---------------------------------------------------------------- build

def build_record(tk, ent, old=None, cusips=None, figi=None, today=None, with_gleif=True):
    """One registry record from the SEC entity (gov_entities), refreshing GLEIF data every REFRESH_DAYS."""
    today = today or dt.date.today()
    old = old or {}
    names = [n for n in [ent.get("name")] + list(ent.get("former") or []) if n]
    rec = {"ticker": tk, "cik": int(ent.get("cik") or 0) or None, "name": ent.get("name"), "former": list(ent.get("former") or []),
           "sic": ent.get("sic"), "subsidiaries": list(ent.get("subsidiaries") or []), "ex21": ent.get("ex21"),
           "cusips": list(cusips or old.get("cusips") or []), "figi": figi or old.get("figi"),
           "lei": old.get("lei"), "gleif_children": old.get("gleif_children") or [], "gleif_checked": old.get("gleif_checked")}
    stale = not rec["gleif_checked"] or (today - dt.date.fromisoformat(rec["gleif_checked"][:10])).days >= REFRESH_DAYS
    if with_gleif and names and stale:
        try:
            lei = gleif_lei(names)
            rec["lei"] = lei
            rec["gleif_children"] = gleif_children(lei["lei"]) if lei else []
            rec["gleif_checked"] = now_iso()
            rec.pop("gleif_error", None)
        except Exception as e:
            rec["gleif_error"] = str(e)[:160]
    rec["updated_at"] = now_iso()
    return rec


def load_registry(sb):
    raw = sb.get_object("raw", REGISTRY_PATH)
    if not raw:
        return {"companies": {}}
    try:
        return json.loads(gzip.decompress(raw))
    except OSError:
        return json.loads(raw)


def save_registry(sb, reg):
    sb.put_object("raw", REGISTRY_PATH, gzip.compress(json.dumps(reg, separators=(",", ":")).encode()), "application/gzip", cache="no-store")


def public_index(reg):
    rows = {}
    for tk, r in sorted(reg["companies"].items()):
        rows[tk] = {"cik": r.get("cik"), "name": r.get("name"), "lei": (r.get("lei") or {}).get("lei"),
                    "figi": (r.get("figi") or {}).get("figi") if isinstance(r.get("figi"), dict) else r.get("figi"),
                    "cusips": r.get("cusips") or [], "former": r.get("former") or [],
                    "subsidiaries": len(r.get("subsidiaries") or []), "gleif_children": len(r.get("gleif_children") or []),
                    "ex21_url": (r.get("ex21") or {}).get("url")}
    return {"generated": now_iso(), "version": 1, "companies": rows,
            "sources": [{"name": "SEC EDGAR submissions and Exhibit 21", "url": "https://www.sec.gov/edgar/search/"},
                        {"name": "GLEIF LEI and Level 2 relationship data", "url": "https://www.gleif.org/en/lei-data/gleif-api"},
                        {"name": "OpenFIGI", "url": "https://www.openfigi.com/api"},
                        {"name": "Schedule 13D/13G cover pages (issuer CUSIP)", "url": "https://www.sec.gov/edgar/search/"}]}


def registry_job(sb, tickers=None, workers=4, time_budget=None, with_gleif=True, log=print):
    """Refreshes the registry for `tickers` (default: everyone due), publishes the index."""
    from . import gov_entities
    from .gov_common import get_raw_json
    from .flows_holders import cusips_from_13dg
    from .universe import UNIVERSE
    t0 = time.time()
    ents = gov_entities.load(sb)
    reg = load_registry(sb)
    comps = reg.setdefault("companies", {})
    try:
        cus = cusips_from_13dg(get_raw_json(sb, "govraw/flows/13dg.json", {"filings": {}}) or {"filings": {}})
    except Exception as e:
        log("cusips unavailable:", str(e)[:120])
        cus = {}
    universe = [t for t, *_ in UNIVERSE]
    todo = tickers or [t for t in universe if t in ents and (t not in comps or not comps[t].get("gleif_checked")
                                                             or (dt.date.today() - dt.date.fromisoformat(comps[t]["gleif_checked"][:10])).days >= REFRESH_DAYS)]
    need_figi = [t for t in todo if not (comps.get(t) or {}).get("figi")]
    figi = openfigi(need_figi) if need_figi else {}
    log(f"registry: {len(todo)} companies to refresh, {len(figi)} FIGIs")

    def one(tk):
        if time_budget and time.time() - t0 > time_budget:
            return tk, None
        return tk, build_record(tk, ents.get(tk) or {}, comps.get(tk), cus.get(tk), figi.get(tk), with_gleif=with_gleif)
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        for tk, rec in ex.map(one, todo):
            if rec:
                comps[tk] = rec
                done += 1
                if done % 100 == 0:
                    save_registry(sb, reg)
                    log(f"  registry {done}/{len(todo)}")
    # Companies with SEC entities but no refresh this run still get their SEC fields current.
    for tk in universe:
        if tk in ents and tk in comps and tk not in todo:
            e = ents[tk]
            comps[tk].update(name=e.get("name"), former=list(e.get("former") or []), subsidiaries=list(e.get("subsidiaries") or []), ex21=e.get("ex21"))
            if cus.get(tk):
                comps[tk]["cusips"] = cus[tk]
    reg["updated_at"] = now_iso()
    save_registry(sb, reg)
    idx = public_index(reg)
    sb.put_json(INDEX_PATH, idx, cache="max-age=3600")
    with_lei = sum(1 for r in comps.values() if r.get("lei"))
    return {"refreshed": done, "companies": len(comps), "with_lei": with_lei,
            "with_figi": sum(1 for r in comps.values() if r.get("figi")),
            "gleif_children": sum(len(r.get("gleif_children") or []) for r in comps.values()),
            "subsidiaries": sum(len(r.get("subsidiaries") or []) for r in comps.values())}


# ---------------------------------------------------------------- matcher

class Matcher:
    """Exact normalized-name index over the registry. resolve(name) -> {"ticker", "method", "confidence", "via"} or
    None; ambiguous names are recorded for review instead of being guessed."""

    STRONG = ("legal", "former")

    def __init__(self, reg):
        self.idx = {}
        for tk, r in (reg.get("companies") or {}).items():
            for kind, names in (("legal", [r.get("name")]), ("former", r.get("former") or []),
                                ("lei_name", [(r.get("lei") or {}).get("name")]),
                                ("subsidiary", r.get("subsidiaries") or []),
                                ("gleif_child", [c.get("name") for c in r.get("gleif_children") or []])):
                for n in names:
                    if not n:
                        continue
                    if kind in ("subsidiary", "gleif_child") and generic(n):
                        continue
                    key = norm_name(n)
                    if key:
                        self.idx.setdefault(key, {}).setdefault(tk, set()).add((kind, n))
        self.review = {}

    def candidates(self, name):
        return self.idx.get(norm_name(name)) or {}

    def resolve(self, name, context=None):
        c = self.candidates(name)
        if not c:
            return None
        strong = {tk: v for tk, v in c.items() if any(k in self.STRONG or k == "lei_name" for k, _ in v)}
        pool = strong or c
        if len(pool) == 1:
            tk, via = next(iter(pool.items()))
            kinds = sorted(k for k, _ in via)
            method = kinds[0] if strong else ("subsidiary" if "subsidiary" in kinds else "gleif_child")
            return {"ticker": tk, "method": method, "confidence": "high" if strong else "medium",
                    "via": sorted(n for _, n in via)[0]}
        key = norm_name(name)
        item = self.review.setdefault(key, {"name": name, "candidates": sorted(pool), "contexts": []})
        if context and len(item["contexts"]) < 5 and context not in item["contexts"]:
            item["contexts"].append(context)
        return None

    def names_for(self, tk, reg, include_subsidiaries=True):
        """Names a source can search for one company: legal and former names, plus subsidiaries and GLEIF children
        that resolve to this company alone (no shared or generic names)."""
        r = (reg.get("companies") or {}).get(tk) or {}
        names = [n for n in [r.get("name")] + list(r.get("former") or []) if n]
        if include_subsidiaries:
            parent_words = {w for n in names for w in core_name(n).split() if w not in LEGAL_TAIL and len(w) > 2}
            subs = [n for n in list(r.get("subsidiaries") or []) + [c.get("name") for c in r.get("gleif_children") or []]
                    if n and not generic(n) and set(self.candidates(n)) == {tk}]
            # Subsidiaries carrying the parent's name ('Raytheon Company' under RTX's former name) first: they are
            # the operating companies outside sources most often record.
            subs.sort(key=lambda n: not (set(core_name(n).split()) & parent_words))
            names.extend(subs)
        seen, out = set(), []
        for n in names:
            k = norm_name(n)
            if k and k not in seen:
                seen.add(k)
                out.append(n)
        return out

    def publish_review(self, sb, source):
        """Merges this run's ambiguous names into entities/review.json under `source`."""
        try:
            cur = sb.get_json(REVIEW_PATH) or {}
        except Exception:
            cur = {}
        cur.setdefault("sources", {})[source] = {"updated_at": now_iso(), "items": sorted(self.review.values(), key=lambda x: x["name"])[:500]}
        cur["generated"] = now_iso()
        sb.put_json(REVIEW_PATH, cur, cache="max-age=600")


def load_matcher(sb):
    reg = load_registry(sb)
    return Matcher(reg), reg


def search_phrases(names, limit=8):
    """Distinct core phrases to search an outside source with, legal name first, capped to keep API calls bounded."""
    out = []
    for n in names:
        p = core_name(n)
        if p and p not in out and len(p) >= 2:
            out.append(p)
        if len(out) >= limit:
            break
    return out
