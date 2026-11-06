"""Pure helpers for the supply-chain map: filing text, verbatim quote checks, passage selection, counterparty
resolution, rule-based link candidates, agreement terms and change detection. No network or storage access,
so the extraction rules are unit-tested offline (tests/supply)."""
import hashlib
import html as _html
import re

RELATIONS = ("customer", "supplier", "partner", "competitor")
LINK_TYPES = ("foundry", "contract_manufacturer", "component", "materials", "logistics", "licensor", "licensee",
              "distributor", "reseller", "cloud", "joint_venture", "co_development", "end_customer", "government",
              "competitor", "other")
QUOTE_MIN_WORDS, QUOTE_MAX_WORDS = 8, 40

# ---------------------------------------------------------------- text

_BLOCK = re.compile(r"</(p|div|tr|li|h\d|table|section|article|ul|ol|center)>|<br\s*/?>", re.I)
_CELL = re.compile(r"</t[dh]>", re.I)


def html_to_text(raw):
    """Plain text of an EDGAR HTML document: one paragraph per line, entities decoded, hidden XBRL header removed."""
    s = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
    s = re.sub(r"(?is)<ix:header.*?</ix:header>", " ", s)
    s = re.sub(r"(?is)<(script|style|head|title)\b.*?</\1>", " ", s)
    s = re.sub(r"(?is)<div[^>]*display:\s*none[^>]*>.*?</div>", " ", s)
    s = _CELL.sub(" \t ", s)
    s = _BLOCK.sub("\n", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = _html.unescape(s)
    s = s.replace(" ", " ").replace("​", "").replace("﻿", "")
    lines = []
    for line in s.split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", line).strip()
        if line:
            lines.append(line)
    # join lines broken mid-sentence (EDGAR often wraps <font> runs inside one <p>)
    out = []
    for line in lines:
        if out and not re.search(r"[.:;!?)\]\"”]$", out[-1]) and line[:1].islower():
            out[-1] += " " + line
        else:
            out.append(line)
    return "\n".join(out)


_QUOTES = str.maketrans({"‘": "'", "’": "'", "‛": "'", "′": "'", "“": '"', "”": '"', "‟": '"',
                         "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
                         " ": " ", " ": " ", " ": " "})


def norm(s):
    """Whitespace, quote and dash normalisation used for every verbatim check (lowercased)."""
    return re.sub(r"\s+", " ", str(s or "").translate(_QUOTES)).strip().lower()


def words(s):
    return [w for w in re.split(r"\s+", str(s or "").strip()) if w]


def quote_ok(quote, text_norm):
    """A quote counts only if it is 8-40 words and appears verbatim (after normalisation) in the source text."""
    n = len(words(quote))
    if n < QUOTE_MIN_WORDS or n > QUOTE_MAX_WORDS:
        return False
    q = norm(quote)
    return bool(q) and q in text_norm


def sentences(text):
    """Sentence split that keeps each sentence a verbatim substring of its paragraph."""
    out = []
    for para in text.split("\n"):
        if len(para) < 30:
            continue
        start = 0
        for m in re.finditer(r"(?<=[a-z0-9%)\]\"”])[.;!?](?=\s+[A-Z(\"“])", para):
            seg = para[start:m.end()].strip()
            if seg:
                out.append(seg)
            start = m.end()
        seg = para[start:].strip()
        if seg:
            out.append(seg)
    return out


def window_quote(sentence, anchor, max_words=QUOTE_MAX_WORDS, min_words=QUOTE_MIN_WORDS):
    """A verbatim run of whole words from `sentence` (at most max_words) that contains `anchor`."""
    toks = list(re.finditer(r"\S+", sentence))
    if len(toks) < min_words:
        return None
    if len(toks) <= max_words:
        return sentence.strip()
    pos = sentence.lower().find(anchor.lower()) if anchor else 0
    pos = max(pos, 0)
    i = next((k for k, t in enumerate(toks) if t.end() > pos), 0)
    lo = max(0, min(i - max_words // 3, len(toks) - max_words))
    hi = lo + max_words
    return sentence[toks[lo].start():toks[hi - 1].end()]


RELEVANT = re.compile(r"customer|supplier|vendor|manufactur|foundr|depend|concentrat|% of (our )?(total |net |consolidated )?(revenue|sales)|"
                      r"percent of (our )?(total |net )?(revenue|sales)|partner|alliance|collaborat|licens|compet|distribut|sole source|"
                      r"single source|sole supplier|contract with|agreement with|joint venture|wholesaler|bottler|reseller|outsourc|"
                      r"accounted for|government", re.I)


def select_passages(text, cap=90_000):
    """Paragraphs that discuss counterparties, in filing order, up to `cap` characters (what the model reads)."""
    out, size = [], 0
    for p in text.split("\n"):
        if len(p) < 60 or not RELEVANT.search(p) or not re.search(r"[A-Z][a-z]+", p):
            continue
        if size + len(p) > cap:
            break
        out.append(p)
        size += len(p) + 1
    return "\n".join(out)


# ---------------------------------------------------------------- counterparties

SUFFIX = re.compile(r"\b(incorporated|inc|corp|corporation|co|company|ltd|limited|plc|llc|lp|l\.p|ag|sa|s\.a|nv|n\.v|se|holdings?|group|the|de|cv)\b\.?", re.I)

# Well-known names written several ways in filings, mapped to one canonical key.
ALIASES = [
    (re.compile(r"taiwan semiconductor|\btsmc\b"), "TSM"),
    (re.compile(r"\bsamsung"), "samsung"),
    (re.compile(r"sk hynix|\bhynix\b"), "sk hynix"),
    (re.compile(r"foxconn|hon hai"), "hon hai"),
    (re.compile(r"amazon web services|\baws\b"), "AMZN"),
    (re.compile(r"google cloud"), "GOOGL"),
    (re.compile(r"microsoft azure|\bazure\b"), "MSFT"),
    (re.compile(r"^(the )?u\.?s\.? (federal )?government|^united states (federal )?government|^(the )?u\.s\. department of defense|^department of defense|^(the )?dod$"), "us government"),
]


def entity_key(name, ticker=None):
    """Stable identity for a counterparty: its ticker, a known alias, or its normalised name minus corporate suffixes."""
    if ticker:
        return ticker.upper()
    n = norm(name)
    for rx, k in ALIASES:
        if rx.search(n):
            return k
    n = re.sub(r"[.,'()]", " ", n)
    n = SUFFIX.sub(" ", n)
    return re.sub(r"\s+", " ", n).strip()


def link_key(link):
    return f"{link['rel']}:{entity_key(link['cp'], link.get('cpt'))}"


def link_id(filer, accn, rel, cp, cpt=None):
    return hashlib.sha1(f"{filer}|{accn}|{rel}|{entity_key(cp, cpt)}".encode()).hexdigest()[:12]


def _tokens(name):
    return [t for t in re.sub(r"[^a-z0-9& ]", " ", SUFFIX.sub(" ", norm(name))).split() if len(t) > 1]


def names_match(a, b):
    """True when two company names plausibly denote the same issuer (all distinctive tokens of the shorter appear in the longer)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return all(t in long_ for t in short)


def resolve_ticker(name, proposed, covered_names, sec_titles):
    """Counterparty ticker only when it is certain: a proposed ticker whose registered name matches the counterparty,
    or an exact name match in our universe. Never guessed from general knowledge.
    covered_names: {ticker: [display name, legal name...]}; sec_titles: {ticker: SEC registrant title}."""
    p = (proposed or "").strip().upper().replace(".", "-")
    if p:
        for nm in covered_names.get(p, []):
            if names_match(name, nm):
                return p
        title = sec_titles.get(p)
        if title and names_match(name, title):
            return p
    key = entity_key(name)
    for tk, nms in covered_names.items():
        if any(entity_key(nm) == key for nm in nms):
            return tk
    return None


# ---------------------------------------------------------------- rule-based candidates (pilot seed and LLM-free fallback)

# Counterparties that matter for supply chains but are not in our universe. US listing tickers are only given when
# the company itself is listed in the US under that symbol (checked against SEC's company tickers file at run time).
EXTERNAL = [
    ("TSMC", "TSM", "Taiwan", r"Taiwan Semiconductor Manufacturing(?: Company)?(?: Limited| Co\.?,? Ltd\.?)?|\bTSMC\b"),
    ("Samsung", None, "South Korea", r"Samsung(?: Electronics)?(?: Co\.?,? Ltd\.?)?"),
    ("SK hynix", None, "South Korea", r"SK [Hh]ynix(?: Inc\.)?"),
    ("Hon Hai (Foxconn)", None, "Taiwan", r"Hon Hai(?: Precision Industry)?(?: Co\.?,? Ltd\.?)?|Foxconn"),
    ("ASML", "ASML", "Netherlands", r"\bASML(?: Holding N\.V\.)?"),
    ("Sony", "SONY", "Japan", r"\bSony(?: Group)?(?: Corporation)?\b"),
    ("Toyota", "TM", "Japan", r"\bToyota(?: Motor Corporation)?\b"),
    ("Honda", "HMC", "Japan", r"\bHonda(?: Motor Co\.?,? Ltd\.?)?\b"),
    ("Panasonic", None, "Japan", r"\bPanasonic(?: Energy| Corporation| Holdings)?\b"),
    ("LG Energy Solution", None, "South Korea", r"LG Energy Solution|\bLGES\b"),
    ("CATL", None, "China", r"Contemporary Amperex Technology(?: Co\.?,? Limited)?|\bCATL\b"),
    ("SK On", None, "South Korea", r"\bSK On(?: Co\.?,? Ltd\.?)?"),
    ("Arm", "ARM", "United Kingdom", r"\bArm Holdings(?: plc)?|\bArm Limited\b"),
    ("GlobalFoundries", "GFS", "United States", r"GlobalFoundries|GLOBALFOUNDRIES"),
    ("UMC", "UMC", "Taiwan", r"United Microelectronics(?: Corporation)?|\bUMC\b"),
    ("Amkor", "AMKR", "United States", r"\bAmkor(?: Technology)?\b"),
    ("ASE Technology", "ASX", "Taiwan", r"\bASE Technology|Advanced Semiconductor Engineering"),
    ("Kioxia", None, "Japan", r"\bKioxia\b"),
    ("Huawei", None, "China", r"\bHuawei(?: Technologies)?\b"),
    ("Alibaba", "BABA", "China", r"\bAlibaba(?: Group)?\b"),
    ("Tencent", None, "China", r"\bTencent\b"),
    ("Baidu", "BIDU", "China", r"\bBaidu\b"),
    ("Xiaomi", None, "China", r"\bXiaomi\b"),
    ("Lenovo", None, "China", r"\bLenovo(?: Group)?\b"),
    ("OpenAI", None, "United States", r"\bOpenAI\b"),
    ("Anthropic", None, "United States", r"\bAnthropic\b"),
    ("xAI", None, "United States", r"\bxAI\b"),
    ("CoreWeave", "CRWV", "United States", r"\bCoreWeave\b"),
    ("Nokia", "NOK", "Finland", r"\bNokia\b"),
    ("Ericsson", "ERIC", "Sweden", r"\bEricsson\b"),
    ("Airbus", None, "France", r"\bAirbus\b"),
    ("Embraer", "ERJ", "Brazil", r"\bEmbraer\b"),
    ("Safran", None, "France", r"\bSafran\b"),
    ("Rolls-Royce", None, "United Kingdom", r"Rolls-Royce"),
    ("Spirit AeroSystems", None, "United States", r"Spirit AeroSystems"),
    ("CFM International", None, None, r"CFM International|\bCFM\b"),
    ("Novo Nordisk", "NVO", "Denmark", r"Novo Nordisk"),
    ("Roche", None, "Switzerland", r"\bRoche\b|Genentech"),
    ("Novartis", "NVS", "Switzerland", r"\bNovartis\b"),
    ("AstraZeneca", "AZN", "United Kingdom", r"AstraZeneca"),
    ("Sanofi", "SNY", "France", r"\bSanofi\b"),
    ("GSK", "GSK", "United Kingdom", r"\bGSK\b|GlaxoSmithKline"),
    ("Bayer", None, "Germany", r"\bBayer(?: AG)?\b"),
    ("BioNTech", "BNTX", "Germany", r"BioNTech"),
    ("Daiichi Sankyo", None, "Japan", r"Daiichi Sankyo"),
    ("Boehringer Ingelheim", None, "Germany", r"Boehringer Ingelheim"),
    ("Genmab", "GMAB", "Denmark", r"\bGenmab\b"),
    ("Moderna", "MRNA", "United States", r"\bModerna\b"),
    ("Coca-Cola FEMSA", "KOF", "Mexico", r"Coca-Cola FEMSA"),
    ("Coca-Cola Europacific Partners", "CCEP", "United Kingdom", r"Coca-Cola Europacific Partners"),
    ("Coca-Cola HBC", None, "Switzerland", r"Coca-Cola HBC"),
    ("Coca-Cola Consolidated", "COKE", "United States", r"Coca-Cola Consolidated"),
    ("Nestle", None, "Switzerland", r"Nestl[eé](?: S\.A\.)?"),
    ("Tengizchevroil", None, "Kazakhstan", r"Tengizchevroil"),
    ("Chevron Phillips Chemical", None, "United States", r"Chevron Phillips Chemical"),
    ("BlueOval SK", None, "United States", r"BlueOval SK"),
    ("Ultium Cells", None, "United States", r"Ultium Cells"),
    ("Mobileye", "MBLY", "Israel", r"\bMobileye\b"),
    ("Altera", None, "United States", r"\bAltera\b"),
    ("Waymo", None, "United States", r"\bWaymo\b"),
    ("Hyundai", None, "South Korea", r"\bHyundai(?: Motor)?\b"),
    ("Siemens", None, "Germany", r"\bSiemens\b"),
    ("BAE Systems", None, "United Kingdom", r"BAE Systems"),
    ("Hulu", None, "United States", r"\bHulu\b"),
    ("Express Scripts", None, "United States", r"Express Scripts"),
    ("Walgreens", None, "United States", r"Walgreens(?: Boots Alliance)?"),
    ("Optum Rx", None, "United States", r"OptumRx|Optum Rx"),
    ("U.S. government", None, "United States", r"(?:U\.S\.|United States|US) (?:federal )?[Gg]overnment|U\.S\. Department of Defense|\bDepartment of Defense\b|\bDoD\b"),
    ("Centers for Medicare & Medicaid Services", None, "United States", r"Centers for Medicare (?:&|and) Medicaid Services|\bCMS\b"),
    ("NASA", None, "United States", r"\bNASA\b"),
]

# single generic words that are also company names; only matched when followed by a corporate suffix
AMBIGUOUS = {"rtx", "arm", "on", "ge", "hp", "target", "visa", "ball", "gap", "corning", "general", "best", "united", "southern",
             "progressive", "match", "public", "first", "ross", "dollar", "regions", "citizens", "american", "hess", "global",
             "republic", "western", "carrier", "masco", "nvr", "ppl", "f", "c", "lowe's", "xylem", "trane", "dover", "halliburton"}

CUES = [
    ("competitor", re.compile(r"\bcompet(e|es|ing|itor|itors|ition)\b|\brival", re.I)),
    ("customer", re.compile(r"% of (our |the company's |total |net |consolidated )*(revenue|sales|net sales|net revenue)|(?:%|percent) or more of|percent of (our )?(total |net )?(revenue|sales)|"
                            r"\bcustomers?\b|\bsales to\b|\bsold to\b|\bwholesaler|\bdistributor|\bbottler|\bretailer|purchased? (our|from us)", re.I)),
    ("supplier", re.compile(r"\bsuppl(y|ier|iers|ied)\b|\bvendor|\bmanufactur(e|ed|es|ing|er)s? (by|for us|our)|\bfoundr|\bsource[ds]? from|\bpurchase[sd]? \w+(?: \w+)? from|\bsole[- ]source|"
                            r"\bsingle[- ]source|\boutsourc|\bpurchase[sd]? (from|of)|\bcontract manufactur|\bwafer", re.I)),
    ("partner", re.compile(r"\bpartner|\balliance|\bcollaborat|\bjoint venture|\blicens|\bco-develop|\bco-commercial|\bstrategic (agreement|relationship)|\bequity method", re.I)),
]
SHARE = re.compile(r"(\d{1,2}(?:\.\d)?)\s?(?:%|percent)\s+of\s+(?:our\s+|the company's\s+|its\s+)?(?:total\s+|net\s+|consolidated\s+|worldwide\s+)*(?:revenue|net revenue|sales|net sales|revenues)", re.I)
AMOUNT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s?(trillion|billion|million)", re.I)
GE10 = re.compile(r"10\s?(?:%|percent) or more|more than 10\s?(?:%|percent)|at least 10\s?(?:%|percent)|greater than 10\s?(?:%|percent)|10\s?(?:%|percent) or greater|in excess of 10\s?(?:%|percent)", re.I)
SINGLE = re.compile(r"sole[- ]source|single[- ]source|sole supplier|single supplier|limited number of suppliers|only (supplier|source)|depend(s|ent)? (heavily |substantially )?on", re.I)
COUNTRIES = ["Taiwan", "China", "Hong Kong", "Japan", "South Korea", "Korea", "India", "Vietnam", "Mexico", "Canada", "Malaysia", "Thailand",
             "Israel", "Germany", "Ireland", "Netherlands", "Singapore", "Philippines", "Indonesia", "Brazil", "Russia", "Ukraine", "United Kingdom",
             "France", "Italy", "Switzerland", "Kazakhstan", "Middle East", "Europe", "Asia"]
COUNTRY_RX = re.compile(r"\b(" + "|".join(re.escape(c) for c in COUNTRIES) + r")\b")


def _amount(m):
    v = float(m.group(1).replace(",", ""))
    return v * {"trillion": 1e12, "billion": 1e9, "million": 1e6}[m.group(2).lower()]


# Other names covered companies appear under in filings (brands, former names, operating units that sign contracts).
COVERED_ALIASES = {
    "GOOGL": ["Google", "Alphabet"], "META": ["Meta Platforms", "Facebook"], "HPQ": ["HP Inc."], "HPE": ["Hewlett Packard Enterprise"],
    "DIS": ["Walt Disney", "The Walt Disney Company"], "JNJ": ["Johnson & Johnson", "Janssen"], "LLY": ["Eli Lilly", "Lilly"],
    "PG": ["Procter & Gamble", "P&G"], "KO": ["The Coca-Cola Company"], "WMT": ["Walmart", "Wal-Mart", "Sam's Club"],
    "TGT": ["Target Corporation"], "UNH": ["UnitedHealth", "Optum"], "CVS": ["CVS Health", "Aetna", "CVS Caremark", "Caremark"],
    "CI": ["Cigna", "Evernorth"], "COR": ["Cencora", "AmerisourceBergen"], "MCK": ["McKesson"], "CAH": ["Cardinal Health"],
    "GE": ["General Electric", "GE Aerospace"], "RTX": ["Raytheon", "Pratt & Whitney", "Collins Aerospace"], "MSFT": ["Microsoft"],
    "AMZN": ["Amazon", "Amazon.com"], "AAPL": ["Apple Inc."], "NVDA": ["NVIDIA", "Nvidia"], "AMD": ["Advanced Micro Devices", "AMD"],
    "INTC": ["Intel"], "QCOM": ["Qualcomm", "QUALCOMM"], "AVGO": ["Broadcom"], "MU": ["Micron"], "ORCL": ["Oracle Corporation"],
    "CSCO": ["Cisco"], "DELL": ["Dell"], "TSLA": ["Tesla"], "F": ["Ford Motor", "Ford"], "GM": ["General Motors"], "BA": ["Boeing"],
    "LMT": ["Lockheed Martin"], "CAT": ["Caterpillar"], "DE": ["Deere", "John Deere"], "COST": ["Costco"], "HD": ["Home Depot"],
    "PEP": ["PepsiCo"], "PFE": ["Pfizer"], "MRK": ["Merck & Co.", "Merck"], "ABBV": ["AbbVie"], "XOM": ["ExxonMobil", "Exxon Mobil"],
    "CVX": ["Chevron"], "NKE": ["Nike", "NIKE"], "SBUX": ["Starbucks"], "NFLX": ["Netflix"], "CMCSA": ["Comcast", "NBCUniversal"],
    "CHTR": ["Charter Communications", "Charter"], "TMUS": ["T-Mobile"], "VZ": ["Verizon"], "T": ["AT&T"], "IBM": ["IBM"],
    "UPS": ["UPS", "United Parcel Service"], "FDX": ["FedEx"], "LOW": ["Lowe's"], "KR": ["Kroger"], "BMY": ["Bristol Myers Squibb", "Bristol-Myers Squibb"],
    "REGN": ["Regeneron"], "AMGN": ["Amgen"], "GILD": ["Gilead"], "MRVL": ["Marvell"], "ADI": ["Analog Devices"], "JBL": ["Jabil"],
    "NXPI": ["NXP"], "ON": ["onsemi", "ON Semiconductor"], "WDC": ["Western Digital"], "STX": ["Seagate"], "CRM": ["Salesforce"],
    "SNPS": ["Synopsys"], "CDNS": ["Cadence Design"], "TXN": ["Texas Instruments"], "AMAT": ["Applied Materials"], "LRCX": ["Lam Research"],
    "KLAC": ["KLA"], "GD": ["General Dynamics"], "NOC": ["Northrop Grumman"], "HON": ["Honeywell"], "ADBE": ["Adobe"],
}


def entity_patterns(covered):
    """Compiled matchers for covered companies ({ticker: name or [names]}) plus EXTERNAL counterparties."""
    pats = []
    for tk, names in covered.items():
        names = [names] if isinstance(names, str) else list(names)
        display = names[0]
        for name in dict.fromkeys(names + COVERED_ALIASES.get(tk, [])):
            if not name:
                continue
            base = re.escape(name).replace(r"\ ", r"\s+")
            if name.lower() in AMBIGUOUS or (len(name) <= 3 and not name.isupper()):
                rx = re.compile(r"\b" + base + r"(?:,)?\s+(?:Inc\.?|Corporation|Corp\.?|Company|Co\.)")
            else:
                rx = re.compile(r"(?<![A-Za-z&])" + base + r"(?![A-Za-z])")
            pats.append((display, tk, None, rx, name.split()[0].lower()))
    for name, tk, hq, rx in EXTERNAL:
        pats.append((name, tk, hq, re.compile(rx), None))
    return pats


def classify(sentence, mention_at):
    """Relation suggested by cue words, nearest cue to the mention wins; competitor cues dominate a sentence."""
    best, dist = None, 10 ** 9
    for rel, rx in CUES:
        for m in rx.finditer(sentence):
            d = abs(m.start() - mention_at)
            if rel == "competitor":
                d -= 60
            if d < dist:
                best, dist = rel, d
    return best


GOV_SALES = re.compile(r"(sales|revenue|net sales|contracts?)\s+(to|from|with)\s+(the\s+)?(U\.S\.|United States|US)\b|principal customers?|largest customer|"
                       r"(U\.S\.|United States) Government\s+(accounted|represented|comprised)|agencies of the U\.S\. Government|% of [^.]{0,80}(U\.S\.|United States) Government|"
                       r"(customer|customers) (is|are|include)[^.]{0,40}(U\.S\.|United States)|NASA|Department of Defense \(DoD\)|Medicare (Advantage )?(premiums|revenue)", re.I)
# executive biographies, litigation and board descriptions name companies without describing a business relationship
NOISE = re.compile(r"\b(served|serves|worked|was employed|has been|was) (as|at|with)\b[^.]{0,120}\b(officer|president|director|chairman|ceo|cfo|partner at)\b|"
                   r"Chief [A-Z][a-z]+ Officer|\bcomplaints?\b|lawsuit|\bjury\b|plaintiff|district court|class action|\bsued\b|infring|"
                   r"board of directors of|Exhibit \d|incorporated by reference", re.I)
BULLET = re.compile(r"^\s*[\u2022\u25cf\u00b7\u25aa\u2013\u2014\-\*o\uf0b7\uf0a7]\s+")


def _contexts(text):
    """(paragraph, inherited relation) pairs: list items inherit the relation of their lead-in sentence
    ("Our competitors include:") or of a section heading such as "Competition"."""
    lead, lead_rel, heading_rel = None, None, None
    for p in text.split("\n"):
        stripped = p.strip()
        is_heading = len(stripped) < 90 and not re.search(r"[.;:]$", stripped) and not BULLET.match(stripped)
        if is_heading:
            heading_rel = "competitor" if re.search(r"^competition\b|competitive (landscape|environment)", stripped, re.I) else (
                None if re.search(r"^[A-Z]", stripped) and len(stripped) > 3 else heading_rel)
        bullet = bool(BULLET.match(stripped))
        inherited = lead_rel if bullet else None
        strong = bool(inherited)
        if not inherited and heading_rel and (bullet or len(stripped) < 400):
            inherited, strong = heading_rel, bullet
        yield p, inherited, strong
        if stripped.endswith(":"):
            lead = stripped
            lead_rel = classify(lead, len(lead))
        elif not bullet and not is_heading:
            lead, lead_rel = None, None


def _table_rows(text):
    """(lead-in sentence, row line) pairs for concentration tables: "the following customers each accounted for more
    than 10% of total revenues:" followed by short rows that carry the names."""
    lines = text.split("\n")
    for i, l in enumerate(lines):
        if l.rstrip().endswith(":") and GE10.search(l) and re.search(r"customer|distributor|licensee", l, re.I):
            lead = sentences(l)[-1] if sentences(l) else l
            desc = None
            for r in lines[i + 1:i + 8]:
                years = re.findall(r"\b(?:19|20)\d{2}\b", r)
                if desc is None and len(years) >= 2 and len(r) < 120:
                    desc = years == sorted(years, reverse=True)
                    continue
                if len(r) < 400:
                    yield lead, r, desc


def rule_candidates(text, filer, covered, self_names=()):
    """Candidate links from a filing's text by cue words around named counterparties. These are proposals for review
    (pilot seed) or a fallback when no model is available; every quote is verbatim by construction."""
    pats = entity_patterns({t: n for t, n in covered.items() if t != filer})
    own = [norm(x) for x in self_names if x]
    out, seen = [], {}
    for para, inherited, strong in _contexts(text):
        if len(para) < 30 or (not inherited and not RELEVANT.search(para)):
            continue
        for s in sentences(para):
            if len(s) > 1600 or NOISE.search(s):
                continue
            low = s.lower()
            for name, tk, hq, rx, needle in pats:
                if needle and needle not in low:
                    continue
                m = rx.search(s)
                if not m:
                    continue
                if own and any(o and (o in norm(m.group(0)) or norm(m.group(0)) in o) for o in own):
                    continue
                rel = inherited if inherited == "competitor" and strong else (classify(s, m.start()) or inherited)
                if not rel:
                    continue
                q = window_quote(s, m.group(0))
                if not q or not quote_ok(q, norm(s)):
                    continue
                if hq == "United States" and re.search(r"government|NASA|Medicare", name) and (rel != "customer" or not GOV_SALES.search(s)):
                    continue  # government actions that are not purchases are policy factors, not links
                sh = None
                for sm in SHARE.finditer(s):
                    if abs(sm.start() - m.start()) < 220:
                        sh = float(sm.group(1))
                        break
                am = AMOUNT.search(s)
                regions = sorted({("South Korea" if c == "Korea" else c) for c in COUNTRY_RX.findall(s)})
                c = {"cp": name, "cpt": tk, "rel": rel, "share": sh if rel == "customer" else None,
                     "amt": _amount(am) if am and abs(am.start() - m.start()) < 200 and rel != "competitor" else None,
                     "ss": bool(SINGLE.search(s)) and rel in ("supplier", "partner"), "rg": regions, "hq": hq,
                     "lt": link_type_of(s, rel, name), "q": q, "sentence": s,
                     "ge10": rel == "customer" and bool(GE10.search(s))}
                if c["ge10"] and c["share"] == 10:
                    c["share"] = None
                key = (rel, name)
                score = (c["share"] is not None) * 4 + c["ge10"] * 3 + (c["amt"] is not None) * 2 + c["ss"] * 2 + bool(regions)
                if key in seen:
                    i = seen[key]
                    if score > out[i]["_score"]:
                        out[i] = dict(c, _score=score)
                    continue
                seen[key] = len(out)
                out.append(dict(c, _score=score))
    # named rows of concentration tables
    for lead, row, desc in _table_rows(text):
        for name, tk, hq, rx, needle in pats:
            if needle and needle not in row.lower():
                continue
            if not rx.search(row) or ("customer", name) in seen:
                continue
            if not quote_ok(lead, norm(text)):
                continue
            seen[("customer", name)] = len(out)
            pm = re.search(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s?%", row[rx.search(row).end():])
            share = float(pm.group(1)) if pm and desc else None
            out.append({"cp": name, "cpt": tk, "rel": "customer", "share": share, "amt": None, "ss": False, "ge10": True, "rg": [], "hq": hq,
                        "lt": "end_customer", "q": lead, "row": row.strip(), "sentence": lead, "_score": 5})
    for c in out:
        c.pop("_score", None)
    return out


POLICY = re.compile(r"export control|export restriction|export licens|entity list|tariff|sanction|embargo|trade war|trade restriction|"
                    r"conflict|war\b|hostilit|military action|invasion|geopolit|political instability|nationaliz", re.I)
POLICY_KIND = [("export_controls", re.compile(r"export control|export restriction|export licens|entity list", re.I)), ("tariffs", re.compile(r"tariff|trade war|trade restriction|duties", re.I)),
               ("sanctions", re.compile(r"sanction|embargo", re.I)), ("conflict", re.compile(r"conflict|war\b|hostilit|military action|invasion", re.I)),
               ("political", re.compile(r"geopolit|political instability|nationaliz", re.I))]


def policy_candidates(text, cap=40):
    """Stated external/political factors: sentences in the filing that tie export controls, tariffs, sanctions or
    conflict to a named country, with the verbatim quote."""
    out, seen = [], set()
    for s in sentences(text):
        if len(s) > 1200 or not POLICY.search(s):
            continue
        countries = sorted({("South Korea" if c == "Korea" else c) for c in COUNTRY_RX.findall(s)} - {"Europe", "Asia"})
        if not countries:
            continue
        kind = next((k for k, rx in POLICY_KIND if rx.search(s)), "political")
        anchor = POLICY.search(s).group(0)
        q = window_quote(s, anchor)
        if not q or not quote_ok(q, norm(s)):
            continue
        k = (kind, tuple(countries))
        if k in seen:
            continue
        seen.add(k)
        out.append({"kind": kind, "countries": countries, "q": q, "sentence": s})
        if len(out) >= cap:
            break
    return out


CONC = re.compile(r"(?:customer|distributor|direct customer|indirect customer|wholesaler|reseller)s?\b[^.]{0,160}?(\d{1,2}(?:\.\d)?)\s?(?:%|percent)\s+of\s+(?:our\s+|the company's\s+)?(?:total\s+|net\s+|consolidated\s+)*(revenue|net revenue|sales|net sales|revenues)|"
                  r"(\d{1,2}(?:\.\d)?)\s?(?:%|percent)\s+of\s+(?:our\s+|the company's\s+)?(?:total\s+|net\s+|consolidated\s+)*(?:revenue|net revenue|sales|net sales|revenues)[^.]{0,120}?\b(?:customer|distributor|wholesaler|reseller)", re.I)


GEO_SHARE = re.compile(r"headquartered (outside|in)|outside (of )?the United States|international customers|customers (located )?(in|outside)|regions outside|"
                       r"shipped (to|into)|by geograph|foreign customers", re.I)


def concentration_candidates(text, cap=12):
    """Stated customer concentration percentages (named or not), e.g. "Sales to Customer A represented 16% of total revenue"."""
    out, seen = [], set()
    for s in sentences(text):
        if len(s) > 900:
            continue
        m = CONC.search(s)
        if not m:
            continue
        if GEO_SHARE.search(s):
            continue
        pct = float(m.group(1) or m.group(3))
        q = window_quote(s, m.group(0)[:20])
        key = re.sub(r"\s+", "", norm(q or ""))
        if not q or not quote_ok(q, norm(s)) or key in seen:
            continue
        seen.add(key)
        unnamed = re.search(r"\b(customers?|distributors?) [A-H]\b|\b(one|two|three|four|five|another|a|single|largest) (direct |indirect |end )?(customer|distributor)s?\b|top (five|ten) (end )?customers", s, re.I)
        out.append({"pct": pct, "unnamed": bool(unnamed), "q": q, "sentence": s})
        if len(out) >= cap:
            break
    return out


LT_RULES = [
    ("foundry", re.compile(r"foundr|wafer", re.I)), ("contract_manufacturer", re.compile(r"contract manufactur|subcontract|assembl|outsourc", re.I)),
    ("government", re.compile(r"government|department of|agency|\bnasa\b|medicare|medicaid", re.I)),
    ("distributor", re.compile(r"distribut|wholesal", re.I)), ("reseller", re.compile(r"resell|retailer", re.I)),
    ("cloud", re.compile(r"cloud|data cent", re.I)), ("joint_venture", re.compile(r"joint venture|equity method|\bJV\b", re.I)),
    ("licensor", re.compile(r"licens", re.I)), ("co_development", re.compile(r"collaborat|co-develop|co-commercial", re.I)),
    ("materials", re.compile(r"raw material|commodit|steel|aluminum|lithium|resin", re.I)), ("logistics", re.compile(r"shipping|freight|logistic|transport", re.I)),
    ("component", re.compile(r"component|memory|chip|semiconductor|part", re.I)),
]
LT_LABEL = {"foundry": "Foundry", "contract_manufacturer": "Contract manufacturer", "component": "Component supplier", "materials": "Materials",
            "logistics": "Logistics", "licensor": "Licence", "licensee": "Licensee", "distributor": "Distributor / wholesaler", "reseller": "Reseller / retailer",
            "cloud": "Cloud / data center", "joint_venture": "Joint venture", "co_development": "Collaboration", "end_customer": "Customer",
            "government": "Government", "competitor": "Competitor", "other": "Named counterparty"}


def link_type_of(sentence, rel, name=""):
    if rel == "competitor":
        return "competitor"
    if re.search(r"government|department of|nasa|medicare", name, re.I):
        return "government"
    for lt, rx in LT_RULES:
        if rx.search(sentence):
            return lt
    return "end_customer" if rel == "customer" else "other"


# ---------------------------------------------------------------- agreement terms (port of the reference's region.ts)

MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December|Jan\.?|Feb\.?|Mar\.?|Apr\.?|Jun\.?|Jul\.?|Aug\.?|Sept?\.?|Oct\.?|Nov\.?|Dec\.?)"
DATE_RE = re.compile(rf"\b{MONTH}\s+\d{{1,2}},\s*(?:19|20)\d{{2}}\b|\b{MONTH}\s+(?:19|20)\d{{2}}\b|\b(?:fiscal(?: year)?|FY)\s*(?:19|20)\d{{2}}\b|"
                     r"\b(?:Q[1-4]|(?:first|second|third|fourth) quarter(?: of)?)\s+(?:fiscal\s+)?(?:19|20)\d{2}\b|\b(?:through|until|by|in|since|beginning|ending)\s+(?:19|20)\d{2}\b", re.I)
NUMWORD = r"(?:\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty)"
DURATION_RE = re.compile(rf"\b{NUMWORD}[- ](?:year|month|quarter)s?\b(?:[- ](?:term|agreement|contract|period|commitment|supply agreement))?", re.I)
AMOUNT_RE = re.compile(r"(?:US)?\$\s?\d[\d,]*(?:\.\d+)?\s?(?:trillion|billion|million|thousand|bn|mm|[BMK]\b)?", re.I)
PCT_RE = re.compile(r"\b\d{1,3}(?:\.\d+)?\s?(?:%|percent\b)", re.I)
CLAUSES = [
    (re.compile(r"take[- ]or[- ]pay", re.I), "Take-or-pay"),
    (re.compile(r"prepay|prepaid|advance payment|deposit", re.I), "Prepayment / deposit"),
    (re.compile(r"non[- ]?cancel+able|cannot be cancel+ed", re.I), "Non-cancellable"),
    (re.compile(r"purchase (?:obligation|commitment)s?|minimum purchase|commit(?:ted|ment)s? to (?:buy|purchase)", re.I), "Purchase commitment"),
    (re.compile(r"capacity (?:reservation|agreement|commitment)|reserve(?:d)? capacity", re.I), "Capacity reservation"),
    (re.compile(r"exclusiv", re.I), "Exclusivity"),
    (re.compile(r"sole[- ]source|single[- ]source|only (?:supplier|source)|limited number of suppliers", re.I), "Sole / limited source"),
    (re.compile(r"long[- ]term (?:supply|agreement|contract)", re.I), "Long-term agreement"),
    (re.compile(r"terminat", re.I), "Termination right"),
    (re.compile(r"renew|extend(?:ed)? (?:the|its|our) (?:agreement|term)|extension", re.I), "Renewal / extension"),
    (re.compile(r"licens", re.I), "Licence"),
    (re.compile(r"warrant(?:s)? to (?:purchase|acquire)|equity stake|invest(?:ed|ment) in", re.I), "Equity / warrant"),
    (re.compile(r"most[- ]favou?red", re.I), "Most-favoured terms"),
    (re.compile(r"volume (?:discount|rebate|commitment)|rebate", re.I), "Volume rebate"),
    (re.compile(r"penalt|liquidated damages", re.I), "Penalties"),
    (re.compile(r"tariff|export control|license requirement|sanction", re.I), "Trade-control exposure"),
]


def _uniq(xs):
    seen, out = set(), []
    for x in xs:
        k = re.sub(r"\s+", " ", x.lower()).strip()
        if k not in seen:
            seen.add(k)
            out.append(re.sub(r"\s+", " ", x).strip())
    return out


def agreement_terms(text):
    t = text or ""
    return {
        "dates": _uniq(m.group(0) for m in DATE_RE.finditer(t)),
        "durations": _uniq(m.group(0) for m in DURATION_RE.finditer(t)),
        "amounts": _uniq(a for a in (m.group(0) for m in AMOUNT_RE.finditer(t)) if re.search(r"\d", a)),
        "percents": _uniq(m.group(0) for m in PCT_RE.finditer(t)),
        "clauses": [label for rx, label in CLAUSES if rx.search(t)],
    }


def term_count(a):
    return sum(len(a[k]) for k in ("dates", "durations", "amounts", "percents", "clauses"))


# ---------------------------------------------------------------- change detection across annual reports

def annual_filings(ticker, links):
    m = {}
    for l in links:
        if l["t"] == ticker and l["form"] == "10-K":
            m[l["accn"]] = l["filed"]
    return sorted(({"accn": a, "filed": f} for a, f in m.items()), key=lambda x: x["filed"], reverse=True)


def link_changes(ticker, links, newer, older):
    """Counterparties named in the newer 10-K but not the older (added), the reverse (dropped), and stated-share moves of 1pt or more."""
    def pick(accn):
        m = {}
        for l in links:
            if l["t"] == ticker and l["accn"] == accn and link_key(l) not in m:
                m[link_key(l)] = l
        return m
    a, b = pick(newer), pick(older)
    out = []
    for k, l in a.items():
        o = b.get(k)
        if o is None:
            out.append({"key": k, "kind": "added", "from": None, "to": l.get("sh"), "link": l["id"]})
        elif l.get("sh") is not None and o.get("sh") is not None and abs(l["sh"] - o["sh"]) >= 1:
            out.append({"key": k, "kind": "share", "from": o["sh"], "to": l["sh"], "link": l["id"], "prior": o["id"]})
    for k, l in b.items():
        if k not in a:
            out.append({"key": k, "kind": "dropped", "from": l.get("sh"), "to": None, "link": l["id"]})
    order = {"share": 0, "added": 1, "dropped": 2}
    return sorted(out, key=lambda x: (order[x["kind"]], x["key"]))


# ---------------------------------------------------------------- earnings sympathy (port of core.ts eventReturn / spillover)

def event_return(closes, date):
    """Return from the close before `date` to the close after it; closes is [(iso date, close)] ascending."""
    i = next((k for k, (d, _) in enumerate(closes) if d >= date), -1)
    if i < 1 or i + 1 >= len(closes):
        return None
    return closes[i + 1][1] / closes[i - 1][1] - 1


def spillover(focus, other, dates):
    rows = []
    for d in dates:
        f, o = event_return(focus, d), event_return(other, d)
        if f is not None and o is not None:
            rows.append({"date": d, "focus": f, "other": o})
    if len(rows) < 3:
        return None
    n = len(rows)
    mf = sum(r["focus"] for r in rows) / n
    mo = sum(r["other"] for r in rows) / n
    cov = sum((r["focus"] - mf) * (r["other"] - mo) for r in rows)
    vf = sum((r["focus"] - mf) ** 2 for r in rows)
    sign = lambda x: (x > 0) - (x < 0)
    return {"events": n, "meanMove": mo, "meanAbs": sum(abs(r["other"]) for r in rows) / n,
            "sameDir": sum(1 for r in rows if sign(r["focus"]) == sign(r["other"])) / n,
            "beta": cov / vf if vf > 0 else None, "rows": rows}
