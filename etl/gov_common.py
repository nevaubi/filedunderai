"""Shared plumbing for the government, politics and market-flow pipeline (gov/ and flows/ prefixes).

Every published record carries `src`: {"url", "retrieved_at", "method"} so the site can show where it came from.
Raw source documents go to the private `raw` bucket under govraw/; per-company bundles go to public-data under
gov/{T}.json and flows/{T}.json. Nothing here writes outside those prefixes.
"""
import datetime as dt
import gzip
import json
import re
import threading
import time
import unicodedata

from .common import now_iso, request, WEB_UA, SEC_UA
from .universe import BY_TICKER, UNIVERSE

PUBLIC_PREFIXES = ("gov/", "flows/")
RAW_PREFIX = "govraw/"
UA_GOV = {"User-Agent": f"{WEB_UA} research; contact@filedunder.ai"}
UA_SEC = {"User-Agent": SEC_UA}


def today():
    return dt.datetime.now(dt.timezone.utc).date()


def src(url, method, retrieved_at=None, **extra):
    """Provenance stamp for one record."""
    out = {"url": url, "retrieved_at": retrieved_at or now_iso(), "method": method}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


# ---------------------------------------------------------------- storage (own prefixes only)

def _check_public(path):
    if not path.startswith(PUBLIC_PREFIXES):
        raise ValueError(f"gov pipeline may only write gov/ or flows/ in public-data, not {path}")


def put_public(sb, path, obj, cache="max-age=900"):
    _check_public(path)
    return sb.put_json(path, obj, cache=cache)


def get_public(sb, path, default=None):
    _check_public(path)
    try:
        v = sb.get_json(path)
    except Exception as e:
        print("gov get_public", path, str(e)[:120])
        return default
    return default if v is None else v


def put_raw(sb, path, content, content_type="application/gzip"):
    """Stores a source document (gzipped) in the private raw bucket under govraw/."""
    if not path.startswith(RAW_PREFIX):
        raise ValueError("raw writes must stay under govraw/")
    data = content.encode() if isinstance(content, str) else content
    if not path.endswith(".gz"):
        path += ".gz"
    sb.put_object("raw", path, gzip.compress(data), content_type, cache="no-store")
    return path


def get_raw(sb, path):
    if not path.endswith(".gz"):
        path += ".gz"
    b = sb.get_object("raw", path)
    if not b:
        return None
    try:
        return gzip.decompress(b)
    except OSError:
        return b


def get_raw_json(sb, path, default=None):
    b = get_raw(sb, path)
    if not b:
        return default
    try:
        return json.loads(b)
    except ValueError:
        return default


def put_raw_json(sb, path, obj):
    return put_raw(sb, path, json.dumps(obj, separators=(",", ":"), default=str))


def update_company(sb, prefix, tk, sections, lock=None):
    """Read-modify-write of one company bundle: replaces the given top-level sections and source statuses."""
    path = f"{prefix}/{tk}.json"
    cur = get_public(sb, path, {}) or {}
    statuses = sections.pop("_sources", {})
    cur.update(sections)
    cur.setdefault("sources", {}).update(statuses)
    cur.update({"ticker": tk, "name": BY_TICKER.get(tk, {}).get("name"), "generated": now_iso(), "version": 1})
    put_public(sb, path, cur, cache="max-age=900")
    return cur


def status(state, note=None, count=None, url=None, method=None):
    """Per-source status shown in the UI: ok | empty | unavailable | not_collected | error."""
    out = {"status": state, "checked_at": now_iso()}
    for k, v in (("note", note), ("count", count), ("url", url), ("method", method)):
        if v is not None:
            out[k] = v
    return out


# ---------------------------------------------------------------- text helpers

QUOTES = {"‘": "'", "’": "'", "‚": "'", "‛": "'", "“": '"', "”": '"', "„": '"',
          "–": "-", "—": "-", " ": " ", "‐": "-", "‑": "-", "``": '"', "''": '"'}


def norm_text(s):
    """Whitespace/quote-normalised text used for verbatim checks."""
    s = unicodedata.normalize("NFKC", s or "")
    for a, b in QUOTES.items():
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip()


def verbatim(quote, source_text):
    """True when the quote appears in the source after whitespace/quote normalisation."""
    q = norm_text(quote)
    return bool(q) and q in norm_text(source_text)


# ---------------------------------------------------------------- company aliases

ENTITY = (r"(?:,?\s+(?:Inc\.?|INC\.?|Incorporated|INCORPORATED|Corp\.?|CORP\.?|Corporation|CORPORATION|Company|COMPANY|Co\.|CO\.|"
          r"Ltd\.?|LTD\.?|Limited|LIMITED|plc|PLC|N\.V\.|Holdings?|HOLDINGS?|Group|GROUP|L\.P\.))")
# Single-word or short names that are ordinary words, abbreviations or very common tokens. They only match in a
# legal form ("Target Corporation", "Ball Corp.") so a hearing about apples or a visa backlog is not attributed.
AMBIGUOUS = {
    "APPLE", "BOOKING", "BLOCK", "BALL", "TARGET", "VISA", "SOUTHERN", "NEWS", "NORTHERN", "PROGRESSIVE", "EQUITABLE",
    "CROWN", "FLEX", "FOX", "POST", "POOL", "MATCH", "BOX", "BILL", "GAP", "PATH", "NOBLE", "INTEGER", "CACTUS", "GRAHAM",
    "DOVER", "DOW", "EVEREST", "HARTFORD", "KIRBY", "POLARIS", "CRANE", "TORO", "MOSAIC", "RELIANCE", "SPIRE", "CELSIUS",
    "TOAST", "BRUNSWICK", "WATERS", "CARNIVAL", "SAIA", "GEO", "WAY", "LIFE", "STEP", "UNUM", "ENSIGN", "VALARIS",
    "FORD", "CHEWY", "ROKU", "MOOG", "DEERE", "MERCK", "LINDE", "EATON", "LEAR", "CABOT", "BRADY", "VICOR", "BELDEN",
    "KADANT", "HAYWARD", "GRIFFON", "TANGER", "MATSON", "TEXTRON", "TIMKEN", "GARMIN", "HUMANA", "LOEWS", "PENTAIR",
    "STERIS", "STRYKER", "SYSCO", "VENTAS", "WABTEC", "CORNING", "FASTENAL", "CHUBB", "AFLAC", "CIGNA", "CENTENE",
    "INSULET", "MASCO", "KROGER", "NUCOR", "PACCAR", "ROLLINS", "DOXIMITY", "EXPONENT", "GREIF", "MARZETTI", "PLEXUS",
    "IMPINJ", "COVISTA", "RAYONIER", "TREX", "CAVA", "ESSENT", "HERC", "RADIAN", "LAZARD", "WESBANCO", "PALOMAR", "DIODES",
    "UNIFIRST", "AVIENT", "ENPRO", "FRONTDOOR", "BALCHEM", "TENABLE", "ARCHROCK", "ITRON", "WORKIVA", "KARMAN", "AMAZON",
    "ORACLE", "TESLA", "STARBUCKS", "NIKE", "INTEL", "INTUIT", "EVERGY", "EVERPURE", "VYLOR", "RALLIANT", "SKYDANCE",
    "OPENLANE", "WAYSTAR", "STEPSTONE", "LIQUIDIA", "NEXTPOWER", "SANDISK", "MAPLEBEAR", "POST", "ATI", "API", "IES",
}
DISTINCT_ACRONYMS = {"AIG", "AMD", "IBM", "CSX", "RTX", "BNY", "KBR", "LKQ", "XPO", "PVH", "UDR", "AECOM", "IQVIA", "MSCI",
                     "CBRE", "AGCO", "GATX", "NVIDIA", "UGI", "CDW", "EQT", "CRH", "AES", "PPL", "ADT", "ITT", "UPS"}
# Brand names that identify the company as clearly as its legal name. Only names that are not also common words.
BRANDS = {
    "GOOGL": ["Google", "YouTube", "Alphabet Inc"], "META": ["Facebook", "Instagram", "WhatsApp", "Meta Platforms"],
    "AMZN": ["Amazon.com", "Amazon Web Services", "Amazon's"], "MSFT": ["Microsoft"], "AAPL": ["Apple Inc", "Apple's App Store", "iPhone"],
    "TSLA": ["Tesla, Inc", "Tesla Motors", "Tesla Inc"], "BRK-B": ["Berkshire Hathaway"], "JPM": ["JPMorgan", "JP Morgan", "J.P. Morgan"],
    "GS": ["Goldman Sachs"], "MS": ["Morgan Stanley"], "WFC": ["Wells Fargo"], "BAC": ["Bank of America"], "C": ["Citigroup", "Citibank"],
    "LMT": ["Lockheed Martin", "Lockheed"], "NOC": ["Northrop Grumman"], "GD": ["General Dynamics"], "BA": ["Boeing"],
    "RTX": ["Raytheon", "Pratt & Whitney", "Pratt and Whitney"], "LHX": ["L3Harris"], "HII": ["Huntington Ingalls"],
    "GE": ["General Electric", "GE Aerospace"], "GEV": ["GE Vernova"], "XOM": ["ExxonMobil", "Exxon Mobil", "Exxon"],
    "CVX": ["Chevron"], "PFE": ["Pfizer"], "JNJ": ["Johnson & Johnson", "Johnson and Johnson"], "UNH": ["UnitedHealth", "Optum"],
    "NVDA": ["NVIDIA", "Nvidia"], "INTC": ["Intel Corporation", "Intel's"], "CMCSA": ["Comcast", "NBCUniversal"],
    "DIS": ["Disney"], "T": ["AT&T"], "VZ": ["Verizon"], "TMUS": ["T-Mobile"], "WMT": ["Walmart", "Wal-Mart"],
    "KO": ["Coca-Cola", "Coca Cola"], "PEP": ["PepsiCo"], "MO": ["Altria", "Philip Morris USA"], "PM": ["Philip Morris International"],
    "MCD": ["McDonald's"], "SBUX": ["Starbucks Corporation"], "NKE": ["Nike, Inc"], "HD": ["Home Depot"], "LOW": ["Lowe's"],
    "CVS": ["CVS Health", "CVS Caremark", "Aetna"], "CI": ["Cigna", "Express Scripts", "Evernorth"], "ELV": ["Elevance", "Anthem"],
    "MCK": ["McKesson"], "COR": ["Cencora", "AmerisourceBergen"], "ABBV": ["AbbVie"], "LLY": ["Eli Lilly", "Lilly"],
    "MRK": ["Merck & Co", "Merck and Co", "Merck Sharp"], "BMY": ["Bristol Myers", "Bristol-Myers"], "AMGN": ["Amgen"],
    "GILD": ["Gilead"], "MRNA": ["Moderna"], "ORCL": ["Oracle Corporation", "Oracle's"], "CRM": ["Salesforce"],
    "PLTR": ["Palantir"], "UBER": ["Uber"], "ABNB": ["Airbnb"], "NFLX": ["Netflix"], "PYPL": ["PayPal"], "V": ["Visa Inc", "Visa U.S.A"],
    "MA": ["Mastercard"], "AXP": ["American Express"], "COF": ["Capital One"], "SCHW": ["Charles Schwab"], "BLK": ["BlackRock"],
    "BX": ["Blackstone"], "KKR": ["KKR & Co"], "F": ["Ford Motor"], "GM": ["General Motors"], "UPS": ["United Parcel Service"],
    "FDX": ["FedEx"], "DAL": ["Delta Air Lines"], "UAL": ["United Airlines"], "AAL": ["American Airlines"], "LUV": ["Southwest Airlines"],
    "CAT": ["Caterpillar"], "DE": ["Deere & Company", "John Deere"], "HON": ["Honeywell"], "MMM": ["3M Company", "3M's"],
    "DUK": ["Duke Energy"], "SO": ["Southern Company"], "NEE": ["NextEra", "Florida Power & Light"], "PCG": ["PG&E", "Pacific Gas and Electric"],
    "TGT": ["Target Corporation"], "COST": ["Costco"], "KR": ["Kroger"], "AVGO": ["Broadcom"], "QCOM": ["Qualcomm"], "AMD": ["Advanced Micro Devices"],
    "MU": ["Micron"], "TXN": ["Texas Instruments"], "AMAT": ["Applied Materials"], "LRCX": ["Lam Research"], "IBM": ["IBM"],
    "CSCO": ["Cisco"], "ADBE": ["Adobe"], "SMCI": ["Super Micro Computer", "Supermicro"], "CRWD": ["CrowdStrike"],
    "BKNG": ["Booking Holdings", "Priceline"], "XYZ": ["Block, Inc", "Square, Inc", "Cash App"], "HCA": ["HCA Healthcare"],
    "MDT": ["Medtronic"], "ABT": ["Abbott Laboratories"], "TMO": ["Thermo Fisher"], "DHR": ["Danaher"], "ISRG": ["Intuitive Surgical"],
    "LDOS": ["Leidos"], "BAH": ["Booz Allen"], "CACI": ["CACI"], "SAIC": ["Science Applications International"], "TDG": ["TransDigm"],
    "COIN": ["Coinbase"], "HOOD": ["Robinhood"], "MSTR": ["MicroStrategy", "Strategy Inc"], "DASH": ["DoorDash"], "LYFT": ["Lyft"],
}


def _phrase_rx(phrase, case_sensitive=True):
    p = re.escape(phrase).replace(r"\ ", r"\s+")
    return re.compile(r"(?<![A-Za-z0-9&])" + p + r"(?![A-Za-z0-9])", 0 if case_sensitive else re.I)


def company_aliases(tk, legal=None, extra=None):
    """Conservative alias set for text matching: list of (label, compiled regex, kind).

    kind 'legal' = registrant legal name forms; 'brand' = curated brand; 'name' = distinctive display name.
    Ambiguous single words and short acronyms are only matched in a legal form.
    """
    from .altdata import core_name, norm_name
    info = BY_TICKER.get(tk) or {}
    names = [n for n in [info.get("name")] + list(legal or []) if n]
    out, seen = [], set()

    def add(label, rx, kind):
        key = (rx.pattern, rx.flags)
        if key not in seen:
            seen.add(key)
            out.append((label, rx, kind))

    for n in names:
        core = core_name(n)
        if not core:
            continue
        words = core.split()
        # legal form: core words followed by an entity word, case-insensitive ("APPLE INC.", "Apple Inc.")
        # case-sensitive: Title or UPPER core words followed by a capitalised entity word, so "target limited
        # resources" or "a target group" never match "Target Corporation"
        title = r"\s+".join(re.escape(w.title() if not re.search(r"\d", w) else w) for w in words)
        upper = r"\s+".join(re.escape(w) for w in words)
        disp_words = (info.get("name") or "").split()
        forms = [title, upper]
        if disp_words and len(disp_words) >= len(words) and not (info.get("name") or "").isupper():
            forms.append(r"\s+".join(re.escape(w) for w in disp_words[:len(words)]))
        alt = "(?:" + "|".join(dict.fromkeys(forms)) + ")"
        add(core.title() + " (legal name)", re.compile(r"(?<![A-Za-z0-9&])" + alt + ENTITY + r"(?![A-Za-z])"), "legal")
        ambiguous = (len(words) == 1 and (core in AMBIGUOUS or (len(core) <= 3 and core not in DISTINCT_ACRONYMS)))
        if not ambiguous and len(core) >= 3:
            # distinctive name, case-sensitive in its display form, title form or upper form
            disp = info.get("name") if info.get("name") and norm_name(info.get("name")).startswith(core) else core.title()
            forms = {disp, core.title(), core}
            if len(words) == 1 and len(core) <= 5:
                forms = {core} | ({disp} if disp and disp.isupper() else set())  # short names only as written in caps
            for f in forms:
                if f:
                    add(f, _phrase_rx(f), "name")
    for b in BRANDS.get(tk, []) + list(extra or []):
        add(b, _phrase_rx(b), "brand")
    return out


NEGATIVE_CONTEXT = re.compile(r"^\s*(?:rainforest|river|basin|jungle|region|forest|tribe|parrot)", re.I)


def find_mentions(text, aliases, limit=50):
    """Positions of alias matches in text: list of (start, end, label, kind). Skips obvious non-company senses."""
    hits = []
    for label, rx, kind in aliases:
        for m in rx.finditer(text):
            if NEGATIVE_CONTEXT.match(text[m.end():m.end() + 20]):
                continue
            hits.append((m.start(), m.end(), label, kind))
            if len(hits) > limit * 4:
                break
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    merged = []
    for h in hits:
        if merged and h[0] < merged[-1][1]:
            continue
        merged.append(h)
    return merged[:limit]


# ---------------------------------------------------------------- committee jurisdiction

# Full committees whose jurisdiction covers an industry. Used only to flag a member's trade; the flag records the
# committee names and this basis so readers can judge it.
SECTOR_COMMITTEES = {
    "Information Technology": ["HSIF", "SSCM", "HSSY", "HSZS"],
    "Communication Services": ["HSIF", "SSCM", "SSJU", "HSJU"],
    "Health Care": ["HSIF", "SSHR", "SSFI", "HSWM", "SPAG"],
    "Financials": ["HSBA", "SSBK", "SSFI", "HSWM"],
    "Energy": ["HSIF", "SSEG", "HSII", "SSEV"],
    "Utilities": ["HSIF", "SSEG", "SSEV"],
    "Materials": ["HSII", "SSEG", "SSEV", "HSIF"],
    "Industrials": ["HSPW", "SSCM", "SSEV"],
    "Consumer Discretionary": ["HSIF", "SSCM"],
    "Consumer Staples": ["HSAG", "SSAF", "HSIF"],
    "Real Estate": ["HSBA", "SSBK"],
}
DEFENSE_SIC = {"3721", "3724", "3728", "3760", "3761", "3764", "3769", "3795", "3812", "3483", "3489", "3731", "8711"}
DEFENSE_TICKERS = {"LMT", "NOC", "GD", "RTX", "LHX", "HII", "LDOS", "BAH", "CACI", "SAIC", "TDG", "KBR", "PSN", "AVAV",
                   "KRMN", "BWXT", "HEI", "TXT", "CW", "MRCY", "KTOS", "AXON", "PLTR", "SARO", "MOG-A", "WWD", "HXL", "VSEC"}
DEFENSE_COMMITTEES = ["HSAS", "SSAS", "HSAP", "SSAP"]
BANK_COMMITTEES = ["HSBA", "SSBK"]


def overseeing_committees(tk, sic=None):
    info = BY_TICKER.get(tk) or {}
    ids = list(SECTOR_COMMITTEES.get(info.get("sector"), []))
    if tk in DEFENSE_TICKERS or (sic and str(sic) in DEFENSE_SIC):
        ids = DEFENSE_COMMITTEES + ids
    return list(dict.fromkeys(ids))


# ---------------------------------------------------------------- misc

class Deadline:
    def __init__(self, seconds):
        self.end = time.monotonic() + seconds if seconds else None

    def left(self):
        return float("inf") if self.end is None else self.end - time.monotonic()

    def passed(self, margin=0):
        return self.end is not None and time.monotonic() > self.end - margin


_gap_lock = threading.Lock()
_last = {}


def polite(host, gap):
    """Simple per-host spacing for public APIs."""
    with _gap_lock:
        wait = _last.get(host, 0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[host] = time.time()


def fetch(url, host_gap=0.0, headers=None, raw=False, timeout=60, retries=3, method="GET", data=None):
    host = url.split("/")[2]
    if host_gap:
        polite(host, host_gap)
    return request(url, method=method, data=data, headers=headers or UA_GOV, timeout=timeout, retries=retries, raw=raw)


def tickers():
    return [t for t, *_ in UNIVERSE]


def symbol_map():
    """Exchange symbol variants -> our ticker (BRK.B, BRK/B, BRKB -> BRK-B)."""
    out = {}
    for tk in tickers():
        for v in {tk, tk.replace("-", "."), tk.replace("-", "/"), tk.replace("-", "")}:
            out.setdefault(v, tk)
    return out
