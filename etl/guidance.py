"""Company guidance: quantitative outlook statements from stored earnings materials, how they were revised, the actuals
they are scored against, and a per-company credibility record.

Pipeline (python -m etl.run guidance):
  1. candidates   earnings-related documents in ir_documents (8-K Item 2.02 / 7.01 exhibits and IR-site releases,
                  presentations, remarks, transcripts) with their issue date (8-K filing date, or the date printed on
                  the first page of an IR-site document).
  2. windows      the parts of each document that can hold guidance (outlook / guidance sections, sentences with a
                  forward-looking verb and a figure), plus the headline block for reported results.
  3. extraction   deterministic patterns first ("revenue is expected to be $108.0 billion, plus or minus 2%",
                  "adjusted diluted EPS of $5.12 to $5.18"); a window with any guidance sentence the patterns do not
                  fully explain goes to a Fireworks model that returns strict JSON with verbatim quotes. Every item is
                  validated: the quote must exist in the document text and every printed figure must be in the quote.
  4. build        per company: revision chains (initial, raised, lowered, reaffirmed, narrowed, widened, withdrawn) for
                  each metric, basis and period; actuals from XBRL facts, published KPI values and adjusted figures the
                  company itself reported; beat or miss against the midpoint and the range; a credibility record.
  5. publish      guidance/{T}.json for the company tab, guidance/index.json for the cross-company page and screener.

Values follow the printed units: dollars and counts in base units, per-share amounts in dollars, margins, rates and
growth in percent (12.5 means 12.5%).
"""
import collections
import datetime as dt
import gzip
import hashlib
import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .common import Run, now_iso, request
from .fundamentals import FY_NAME_PRIOR, fye_at, norm_fye, period_label
from .kpitools import norm, numbers_in, text_of

VERSION = 3            # bump to re-extract documents processed by an older extractor
PRIMARY = os.environ.get("FU_GUIDANCE_MODEL", "deepseek-v4p1-flash")
FALLBACK = os.environ.get("FU_GUIDANCE_FALLBACK", "kimi-k3")
REASONING = os.environ.get("FU_GUIDANCE_REASONING", "low")
WINDOW_CHARS = 11000
MAX_CHUNKS = 3

# metric id: (display name, default unit, direction: +1 higher is better, -1 lower is better, 0 neutral)
METRICS = {
    "revenue": ("Revenue", "USD", 1),
    "eps": ("EPS", "USD_per_share", 1),
    "gross_margin": ("Gross margin", "pct", 1),
    "operating_margin": ("Operating margin", "pct", 1),
    "operating_income": ("Operating income", "USD", 1),
    "net_income": ("Net income", "USD", 1),
    "ebitda": ("EBITDA", "USD", 1),
    "ebitda_margin": ("EBITDA margin", "pct", 1),
    "opex": ("Operating expenses", "USD", -1),
    "capex": ("Capital expenditures", "USD", 0),
    "fcf": ("Free cash flow", "USD", 1),
    "operating_cash_flow": ("Operating cash flow", "USD", 1),
    "tax_rate": ("Tax rate", "pct", 0),
    "net_interest_income": ("Net interest income", "USD", 1),
    "d_and_a": ("Depreciation and amortization", "USD", 0),
    "share_count": ("Diluted shares", "count", 0),
    "buybacks": ("Share repurchases", "USD", 0),
    "other": ("Other", None, 0),
}
GAAP_DEFAULT = {"revenue", "capex", "operating_cash_flow", "net_interest_income", "d_and_a", "share_count", "buybacks"}
BASES = ("gaap", "adjusted", "organic", "constant_currency", "unspecified")
BASIS_NAME = {"gaap": "GAAP", "adjusted": "Adjusted (non-GAAP)", "organic": "Organic", "constant_currency": "Constant currency", "unspecified": "As stated"}
MEASURES = ("level", "growth", "change")
UNITS = ("USD", "USD_per_share", "pct", "count", "ratio")
SCALE = {"trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3, "none": 1, None: 1}

# XBRL statement lines (labels as on the site) an actual can come from, per metric and measure; GAAP or unstated basis only
FACT_OF = {
    ("revenue", "level"): "Revenue", ("revenue", "growth"): "Revenue growth YoY",
    ("eps", "level"): "EPS diluted", ("eps", "growth"): "EPS growth YoY",
    ("gross_margin", "level"): "Gross margin", ("operating_margin", "level"): "Operating margin",
    ("operating_income", "level"): "Operating income", ("net_income", "level"): "Net income",
    ("opex", "level"): "Operating expenses", ("capex", "level"): "Capex", ("fcf", "level"): "Free cash flow",
    ("operating_cash_flow", "level"): "Operating cash flow", ("tax_rate", "level"): "Effective tax rate",
    ("net_interest_income", "level"): "Net interest income", ("d_and_a", "level"): "D&A",
    ("share_count", "level"): "Diluted shares", ("buybacks", "level"): "Buybacks",
}
ADDITIVE = {"Revenue", "Operating income", "Net income", "Operating expenses", "Capex", "Free cash flow", "Operating cash flow",
            "Net interest income", "D&A", "Buybacks"}


# ------------------------------------------------------------------ documents
def issue_date(doc, text=None):
    """8-K exhibits carry the filing date in the stored title; IR-site documents the date printed near the top."""
    m = re.match(r"8-K (\d{4}-\d\d-\d\d) ", doc.get("title") or "")
    if m:
        return m.group(1), "8-K filing date"
    if text and text.get("kind") == "pages" and text["pages"]:
        head = "\n".join(text["pages"][:2])[:5000]
        for m in re.finditer(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", head, re.I):
            try:
                d = dt.date(int(m.group(3)), "jan feb mar apr may jun jul aug sep oct nov dec".split().index(m.group(1).lower()[:3]) + 1, int(m.group(2)))
            except ValueError:
                continue
            pe = doc.get("period_end")
            # a release is published after the period it reports and within about five months of it
            if pe and not (dt.date.fromisoformat(pe) - dt.timedelta(days=10) <= d <= dt.date.fromisoformat(pe) + dt.timedelta(days=150)):
                continue
            if d > dt.date.today():
                continue
            return d.isoformat(), "date printed in the document"
    return None, None


def items_of(doc):
    m = re.search(r"\(Items ([^)]*)\)", doc.get("title") or "")
    return m.group(1) if m else ""


def is_candidate(doc):
    if not doc.get("text_path") or (doc.get("ext") or "") in (".xlsx", ".xls", ".xlsm", ".csv"):
        return False
    title = doc.get("title") or ""
    if title.startswith("8-K "):
        its = items_of(doc)
        return "2.02" in its or "7.01" in its or "8.01" in its
    return doc.get("doc_type") in ("release", "presentation", "remarks", "transcript")


def candidates(sb, tk):
    rows = sb.select("ir_documents", "select=id,ticker,url,title,doc_type,fiscal_label,period_end,ext,pages,text_path,storage_path,fetched_at,source_page"
                                     f"&ticker=eq.{tk}")
    return [d for d in rows if is_candidate(d)]


# ------------------------------------------------------------------ windows
CUE = re.compile(r"\b(outlook|guidance|guide[sd]?|expects?|expected|expecting|anticipat\w*|forecast\w*|projects?|projected|projecting|"
                 r"reaffirm\w*|reiterat\w*|rais(?:e|es|ed|ing)|lower(?:s|ed|ing)?|narrow\w*|sees|targets?|targeted|suspend\w*|withdr[ae]w\w*|"
                 r"plus or minus|range of|now (?:sees|expects|projects))\b", re.I)
NUM = re.compile(r"\$\s?\(?\d|\d[\d,]*(?:\.\d+)?\s?(?:%|percent|per cent|billion|million|basis points|bps|cents)\b|\b\d+(?:\.\d+)?\s?%", re.I)
BOILER = re.compile(r"forward[- ]looking (?:statements|information)|safe harbor|private securities litigation reform|risks and uncertainties|"
                    r"actual results (?:could|may|might) differ|undue reliance|no obligation to update", re.I)
HEAD = re.compile(r"(outlook|guidance|forecast|financial targets|expectations)\b", re.I)
STRONG = re.compile(r"\b(outlook|guidance|reaffirm\w*|raises?|raised|narrow\w*|plus or minus|expects?|expected to be|projects?)\b", re.I)


def _lines(page):
    out, buf = [], None
    for ln in page.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if s in ("•", "–", "-", "*", "●", "▪", "◦", "o", "■", "□", "·"):
            buf = (buf or "")
            continue
        if buf is not None:
            s = ("• " + s)
            buf = None
        out.append(s)
    return out


def _sentences(line):
    if len(line) <= 600:
        return [line]
    return [s for s in re.split(r"(?<=[.;!?])\s+(?=[A-Z“\"(])", line) if s]


def windows(text):
    """Excerpts of a document that can hold guidance: [(page number, excerpt)], at most MAX_CHUNKS of WINDOW_CHARS each,
    the headline block first. Returns [] when nothing in the document looks like guidance."""
    if not text or text.get("kind") != "pages":
        return []
    units = []          # (page, line)
    for pi, page in enumerate(text["pages"]):
        for ln in _lines(page):
            for s in _sentences(ln):
                units.append((pi + 1, s))
    n = len(units)
    if not n:
        return []
    score = [0] * n
    keep = [False] * n
    for i, (_p, s) in enumerate(units):
        if BOILER.search(s):
            continue
        nxt = units[i + 1][1] if i + 1 < n else ""
        if CUE.search(s) and (NUM.search(s) or (len(s) < 200 and NUM.search(nxt))):
            score[i] = 2 + (2 if STRONG.search(s) else 0)
        if len(s) < 90 and HEAD.search(s) and not s.endswith((".", ";")) and not BOILER.search(s):
            # a heading: the table or bullets under it
            for j in range(i, min(n, i + 40)):
                score[j] = max(score[j], 3)
    for i in range(n):
        if score[i]:
            for j in range(max(0, i - 2), min(n, i + 3)):
                keep[j] = True
    if not any(score):
        return []
    # headline block: the first lines of the document (title, bullets with reported and guided figures)
    head, hl = [], 0
    for i, (p, s) in enumerate(units[:60]):
        if hl > 1800:
            break
        head.append(i)
        hl += len(s) + 1
    blocks, cur = [], []
    for i in range(n):
        if keep[i] and i not in head:
            cur.append(i)
        elif cur:
            blocks.append(cur)
            cur = []
    if cur:
        blocks.append(cur)
    blocks.sort(key=lambda b: -max(score[i] for i in b))
    chunks, cur, size = [], [head], sum(len(units[i][1]) + 1 for i in head)
    for b in blocks:
        bl = sum(len(units[i][1]) + 1 for i in b)
        if size + bl > WINDOW_CHARS and size > 2500:
            chunks.append(cur)
            if len(chunks) >= MAX_CHUNKS:
                cur = None
                break
            cur, size = [], 0
        if bl > WINDOW_CHARS:
            b = b[:max(1, int(len(b) * WINDOW_CHARS / bl))]
            bl = WINDOW_CHARS
        cur.append(b)
        size += bl
    if cur:
        chunks.append(cur)
    out = []
    for ch in chunks:
        idx = sorted({i for b in ch for i in b})
        parts, last = [], None
        for i in idx:
            p, s = units[i]
            if last is not None and i != last + 1:
                parts.append("…")
            if last is None or units[last][0] != p:
                parts.append(f"[page {p}]")
            parts.append(s)
            last = i
        out.append("\n".join(parts))
    return out


# ------------------------------------------------------------------ periods and labels
ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}


def fiscal_label(ptype, fy, q=None):
    if ptype == "Q" and q:
        return f"Q{q} FY{str(fy)[2:]}"
    if ptype in ("H1", "H2"):
        return f"{ptype} FY{str(fy)[2:]}"
    if ptype == "M":
        return f"FY{fy} target"
    return f"FY{fy}"


def period_end_est(tk, fye, ptype, fy, q=None):
    """Approximate end of a fiscal period (for ordering and charts before the actual is reported)."""
    fye = norm_fye(fye_at(tk, fye or "1231", f"{fy}-06-30"))
    m, d = int(fye[:2]), int(fye[2:])
    y = fy + (1 if tk in FY_NAME_PRIOR else 0)
    # a fiscal year named for the calendar year it ends in; year ends in Jan-Mar for fiscal years named that way
    try:
        end = dt.date(y, m, min(d, 28 if m == 2 else d))
    except ValueError:
        end = dt.date(y, m, 28)
    back = {"Q": (4 - (q or 4)) * 3, "H1": 6, "H2": 0}.get(ptype, 0)
    mm = end.month - back
    yy = end.year
    while mm <= 0:
        mm += 12
        yy -= 1
    last = [31, 29 if yy % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][mm - 1]
    return dt.date(yy, mm, min(end.day if end.day < 28 else last, last)).isoformat()


def doc_period(tk, fye, doc, issued):
    """Fiscal label of the period a release reports: the one stored with the document, else the latest quarter that
    ended before the issue date."""
    if doc.get("period_end"):
        try:
            return period_label(tk, fye, doc["period_end"], "Q"), doc["period_end"]
        except Exception:
            pass
    if doc.get("fiscal_label"):
        return doc["fiscal_label"], None
    return None, None


# ------------------------------------------------------------------ numbers
def _num(printed):
    if printed is None:
        return None
    s = str(printed).strip()
    if not s or not re.search(r"\d", s):
        return None
    ns = numbers_in(s.replace("−", "-").replace("–", "-"))
    if not ns:
        return None
    v = ns[0]
    if re.match(r"^\s*-", s) and v > 0:
        v = -v
    return v


def _decimals(printed):
    m = re.search(r"\d[\d,]*(?:\.(\d+))?", str(printed or ""))
    return len(m.group(1)) if m and m.group(1) else 0


def _in_quote(printed, quote):
    """A printed figure is in the quote when its digits appear as one number there."""
    core = re.sub(r"[^\d.]", "", str(printed or "")).strip(".")
    if not core:
        return False
    q = quote.replace(",", "")
    forms = {core}
    if "." in core:
        forms.add(core.rstrip("0").rstrip("."))
    return any(f and re.search(r"(?<![\d.])" + re.escape(f) + r"(?![\d]|\.\d)", q) for f in forms)


def resolve_values(it):
    """Numeric low / high / mid from the printed figures, the scale word and a plus-or-minus tolerance. Returns
    (low, high, mid, precision) in the stored units or raises ValueError."""
    unit = it["unit"]
    mult = SCALE.get(it.get("scale") or "none", 1) if unit in ("USD", "count") else 1
    if unit == "USD_per_share" and re.search(r"\bcents?\b", it.get("quote") or "", re.I) and not re.search(r"\$\s?\d", it.get("quote") or ""):
        mult = 0.01
    lo, hi, pt = _num(it.get("low")), _num(it.get("high")), _num(it.get("point"))
    dec = max(_decimals(it.get("low")), _decimals(it.get("high")), _decimals(it.get("point")))
    prec = 0.5 * 10 ** -dec * mult
    if lo is not None:
        lo *= mult
    if hi is not None:
        hi *= mult
    if pt is not None:
        pt *= mult
    pm = it.get("plus_minus")
    if pm and pt is not None:
        x = _num(pm)
        if x is None:
            raise ValueError("unreadable plus_minus")
        if re.search(r"basis points|bps|\bbp\b", str(pm), re.I):
            delta = x / 100.0 if unit == "pct" else abs(pt) * x / 10000.0
        elif "%" in str(pm) or "percent" in str(pm).lower():
            delta = x if unit == "pct" else abs(pt) * x / 100.0
        else:
            delta = x * mult
        lo, hi = pt - delta, pt + delta
    bound = it.get("bound") or ("range" if lo is not None and hi is not None else "point")
    if lo is None and hi is None and pt is None:
        raise ValueError("no figure")
    if bound == "at_least":
        lo = lo if lo is not None else pt
        hi = None
    elif bound == "at_most":
        hi = hi if hi is not None else pt
        lo = None
    elif lo is None and hi is None:
        lo = hi = pt
    elif lo is None or hi is None:
        lo = hi = lo if lo is not None else hi
    if lo is not None and hi is not None and lo > hi:
        lo, hi = hi, lo
    mid = (lo + hi) / 2 if lo is not None and hi is not None else (lo if lo is not None else hi)
    return lo, hi, mid, prec


# ------------------------------------------------------------------ deterministic patterns
MET_WORDS = [
    (r"(?:net )?(?:revenues?|net sales|sales)", "revenue"),
    (r"(?:diluted )?(?:earnings|eps|net income) per (?:diluted )?share|\beps\b", "eps"),
    (r"gross margins?", "gross_margin"),
    (r"operating margins?", "operating_margin"),
    (r"(?:effective )?tax rates?", "tax_rate"),
    (r"capital expenditures?|\bcapex\b", "capex"),
    (r"free cash flow", "fcf"),
    (r"operating expenses|\bopex\b", "opex"),
    (r"(?:adjusted )?ebitda", "ebitda"),
    (r"operating (?:profit|income)|segment profit|\bebit\b", "operating_income"),
    (r"net income|net earnings", "net_income"),
    (r"net interest income", "net_interest_income"),
]
NUMTOK = r"\(?\$?\s?\d[\d,]*(?:\.\d+)?\)?"
P_PM = re.compile(r"(?P<lead>[^.;•]{0,120}?)\b(?:is|are|will be)\s+expected to be\s+(?:approximately\s+|about\s+)?(?P<v>" + NUMTOK + r")\s*(?P<sc>trillion|billion|million)?\s*(?P<pc>%|percent)?,?\s+plus or minus\s+(?P<pm>\d+(?:\.\d+)?\s*(?:%|percent|basis points|bps))", re.I)
P_RANGE = re.compile(r"(?P<lead>[^.;•]{0,140}?)\b(?:of|to be|to be in the range of|in the range of|range of|between|at|to)\s+(?P<lo>" + NUMTOK + r")\s*(?P<s1>trillion|billion|million|%|percent)?\s*(?:to|and|-|–)\s*(?P<hi>" + NUMTOK + r")\s*(?P<s2>trillion|billion|million|%|percent)?", re.I)
P_PERIOD = [
    (re.compile(r"\b(first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter(?: of)?(?: fiscal(?: year)?)?\s*(20\d\d)\b", re.I), lambda m: ("Q", int(m.group(2)), ORD[m.group(1).lower()])),
    (re.compile(r"\bQ([1-4])\s*(?:FY|fiscal)?\s*['’]?(20\d\d)\b", re.I), lambda m: ("Q", int(m.group(2)), int(m.group(1)))),
    (re.compile(r"\b(?:full[- ]year|fiscal(?: year)?|FY)\s*(20\d\d)\b", re.I), lambda m: ("FY", int(m.group(1)), None)),
    (re.compile(r"\b(20\d\d)\s+(?:full[- ]year|fiscal[- ]year|annual)\b", re.I), lambda m: ("FY", int(m.group(1)), None)),
]


def _period_in(s):
    for rx, fn in P_PERIOD:
        m = rx.search(s)
        if m:
            return fn(m)
    return None


def _basis_in(s):
    t = s.lower()
    if re.search(r"\bgaap and non-gaap\b|\bgaap and adjusted\b", t):
        return "both"
    if re.search(r"non-gaap|adjusted|excluding|\bcore\b", t):
        return "adjusted"
    if re.search(r"\borganic\b", t):
        return "organic"
    if re.search(r"constant currency|currency-neutral|\bcc\b", t):
        return "constant_currency"
    if re.search(r"\bgaap\b|reported", t):
        return "gaap"
    return "unspecified"


def _metric_in(lead):
    t = lead.lower()
    best = None
    for rx, mid in MET_WORDS:
        for m in re.finditer(rx, t):
            if best is None or m.end() > best[0]:
                best = (m.end(), mid)
    return best[1] if best else None


def pattern_extract(chunk):
    """Guidance found by fixed patterns, and whether every guidance sentence in the excerpt was explained by them.
    A pattern item needs an explicit fiscal period in its sentence or in an outlook heading above it."""
    items, uncovered = [], 0
    lines = chunk.split("\n")
    ctx_period = None
    for i, s in enumerate(lines):
        if s.startswith("[page") or s == "…":
            continue
        if len(s) < 160 and HEAD.search(s):
            ctx_period = _period_in(s) or ctx_period
        if BOILER.search(s) or not (CUE.search(s) or (ctx_period and NUM.search(s))) or not NUM.search(s):
            continue
        found, used = [], []
        for rx in (P_PM, P_RANGE):
            for m in rx.finditer(s):
                if any(a <= m.start("lead") + len(m.group("lead")) < b for a, b in used):
                    continue
                lead = m.group("lead")
                met = _metric_in(lead)
                # a business unit named in the sentence ("DPT continues to expect ...") is not company guidance: leave it to the model
                if re.search(r"\bsegments?\b|\b(?!GAAP\b|EPS\b|FY\b|EBITDA\b|EBIT\b|USD?\b|Q[1-4]\b|CEO\b|CFO\b)[A-Z]{2,6}\b", lead):
                    continue
                per = _period_in(lead + s[m.end():m.end() + 80]) or _period_in(s) or ctx_period
                if not met or not per:
                    continue
                if rx is P_PM:
                    v, sc, pc = m.group("v"), m.group("sc"), m.group("pc")
                    unit = "pct" if pc else ("USD_per_share" if met == "eps" else "USD")
                    if met in ("gross_margin", "operating_margin", "tax_rate") and not pc:
                        continue
                    it = {"point": v.strip(), "plus_minus": m.group("pm"), "low": None, "high": None, "bound": "range",
                          "scale": (sc or "none").lower(), "unit": unit}
                else:
                    lo, hi, s1, s2 = m.group("lo"), m.group("hi"), m.group("s1"), m.group("s2")
                    sc = (s2 or s1 or "").lower()
                    pc = sc in ("%", "percent")
                    if met in ("gross_margin", "operating_margin", "tax_rate") and not pc:
                        continue
                    if met not in ("gross_margin", "operating_margin", "tax_rate") and pc:
                        if not re.search(r"grow|growth|increase|decline|change", lead, re.I):
                            continue
                    unit = "pct" if pc else ("USD_per_share" if met == "eps" else "USD")
                    if unit == "USD" and not sc and "$" not in lo + hi:
                        continue
                    it = {"low": lo.strip(), "high": hi.strip(), "point": None, "plus_minus": None, "bound": "range",
                          "scale": sc if sc in ("trillion", "billion", "million") else "none", "unit": unit}
                measure = "growth" if unit == "pct" and met not in ("gross_margin", "operating_margin", "tax_rate") else "level"
                basis = _basis_in(lead)
                ptype, fy, q = per
                for b in (("gaap", "adjusted") if basis == "both" else (basis,)):
                    found.append({**it, "metric": met, "label": METRICS[met][0], "segment": None, "basis": b, "measure": measure,
                                  "period_type": ptype, "fiscal_year": fy, "quarter": q, "status": None, "quote": s})
                used.append((m.start(), m.end()))
        nums_line = [x for x in re.findall(NUMTOK, s) if re.search(r"\d", x)]
        nums_used = sum(len([x for x in re.findall(NUMTOK, s[a:b]) if re.search(r"\d", x)]) for a, b in used)
        if found and nums_used >= len(nums_line) - _incidental(s):
            items += found
        elif CUE.search(s) or ctx_period:
            uncovered += 1
    return items, uncovered


def _incidental(s):
    """Figures in a guidance sentence that are not guidance (a year, a basis-point note in parentheses)."""
    return len(re.findall(r"\b20\d\d\b", s))


# ------------------------------------------------------------------ model extraction
PROMPT = """You extract quantitative financial guidance from excerpts of a company document. Use only the text below.

Company: {name} ({tk}). Fiscal year ends on {fye_text}. {naming}
Document: {doc_kind}, issued {issued}. It reports results for {period} (period ended {period_end}).

Return one JSON object: {{"guidance": [...], "reported": [...], "withdrawn": [...]}}

"guidance": every forward-looking numeric target the company itself states for a period (a quarter, a fiscal year, a half, or a
multi-year target year). One item per metric, basis, segment and period: when one sentence gives GAAP and non-GAAP values, or
two periods, write separate items. Each item:
  "metric": one of {metrics}
  "label": the metric name as printed, short (e.g. "Adjusted diluted EPS", "Data Center revenue", "Organic sales growth")
  "segment": the segment, business, product or region the target is for, or null for the whole company
  "basis": "gaap" | "adjusted" (non-GAAP, adjusted, core, excluding items) | "organic" | "constant_currency" | "unspecified"
  "measure": "level" (an amount, margin or rate) | "growth" (percent change versus the prior period) | "change" (an amount of change)
  "period_type": "Q" | "FY" | "H1" | "H2" | "M" (a multi-year or long-term target for a stated year)
  "fiscal_year": the fiscal year as the company names it (integer)
  "quarter": 1-4 for "Q", else null
  "low", "high": the printed low and high figures copied exactly as printed (e.g. "5.12", "445.5", "(0.10)"), or null
  "point": the printed figure when there is a single figure, or null
  "plus_minus": the printed tolerance in "plus or minus" wording (e.g. "2%", "50 basis points"), or null
  "scale": "trillion" | "billion" | "million" | "thousand" | "none"
  "unit": "USD" | "USD_per_share" | "pct" | "count" | "ratio"
  "bound": "range" | "point" | "approximately" | "at_least" | "at_most"
  "status": the document's own word for the change: "new" | "raised" | "lowered" | "reaffirmed" | "narrowed" | "updated" | null
  "quote": the exact sentence or table row containing the figures, copied character for character from the excerpt (no
           paraphrase, no ellipsis, no added words). For a table row, copy its cells in order separated by single spaces.

"reported": actual results this document reports for its own period ({period}, and the fiscal year when it is a fourth-quarter
document) for measures that cannot be read from GAAP financial statements: adjusted or non-GAAP EPS, adjusted EBITDA, adjusted
operating margin, organic or constant-currency growth, free cash flow as the company defines it, and segment figures the company
also guides. Fields: "metric", "label", "segment", "basis", "measure", "period_type", "fiscal_year", "quarter", "value" (printed
figure exactly as printed), "scale", "unit", "quote".

"withdrawn": statements that suspend or withdraw previously issued guidance: {{"metric": metric or "all", "period_type",
"fiscal_year", "quarter", "quote"}}.

Rules: skip analyst estimates, consensus, historical results presented as guidance, assumptions about the economy (interest rate,
FX or GDP assumptions), dividend declarations, and figures without a period. Percent values are printed numbers (12.5 means
12.5%). Return empty arrays when there is nothing. Output JSON only.

Excerpts (lines in document order; "…" marks skipped text, "[page n]" the page):
<<<
{text}
>>>"""

_llm_lock = threading.Lock()
LLM_STATS = collections.Counter()


MODEL_DOWN = []   # set once the provider refuses the account (suspended, unpaid, bad key): later calls fail fast


def complete_json(prompt, max_tokens=12000):
    if MODEL_DOWN:
        raise RuntimeError("model provider unavailable: " + MODEL_DOWN[0])
    last = None
    for model in (PRIMARY, FALLBACK):
        for attempt in range(2):
            try:
                body = {"model": f"accounts/fireworks/models/{model}", "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": max_tokens, "temperature": 0, "response_format": {"type": "json_object"}}
                if model.startswith("deepseek"):
                    # a short reasoning pass is enough to resolve periods; full reasoning triples the time and can run out of tokens
                    body["reasoning_effort"] = REASONING
                d = json.loads(request("https://api.fireworks.ai/inference/v1/chat/completions", method="POST", data=body,
                    headers={"Authorization": f"Bearer {os.environ['FIREWORKS_API_KEY']}"}, timeout=240, retries=3))
                txt = re.sub(r"(?s)<think>.*?</think>", "", d["choices"][0]["message"]["content"] or "").strip()
                m = re.search(r"(?s)\{.*\}", txt)
                j = json.loads(m.group(0) if m else txt)
                u = d.get("usage") or {}
                with _llm_lock:
                    LLM_STATS["calls"] += 1
                    LLM_STATS["prompt_tokens"] += u.get("prompt_tokens") or 0
                    LLM_STATS["completion_tokens"] += u.get("completion_tokens") or 0
                return j, model
            except Exception as e:
                last = e
                if getattr(e, "code", None) in (401, 402, 403, 412):
                    MODEL_DOWN.append(str(e)[:200])
                    raise
                time.sleep(2 + attempt * 4)
    raise last


def fye_text(fye):
    m, d = int(fye[:2]), int(fye[2:])
    return f"{'January February March April May June July August September October November December'.split()[m - 1]} {d}"


def llm_extract(chunk, ctx):
    naming = ("The company names a fiscal year after the calendar year in which most of it falls (its fiscal 2025 ends in early 2026)."
              if ctx["tk"] in FY_NAME_PRIOR else
              "A fiscal year is named for the calendar year in which it ends.")
    prompt = PROMPT.format(name=ctx["name"], tk=ctx["tk"], fye_text=fye_text(ctx["fye"]), naming=naming, doc_kind=ctx["doc_kind"],
                           issued=ctx["issued"], period=ctx.get("period") or "an unstated period", period_end=ctx.get("period_end") or "unknown",
                           metrics=" | ".join(f'"{m}"' for m in METRICS), text=chunk)
    j, model = complete_json(prompt)
    return j, model


# ------------------------------------------------------------------ validation and rows
def _clean(s):
    return norm(s).replace("’", "'").replace("“", '"').replace("”", '"')


def find_quote(quote, pages):
    """1-based page holding the quote, or None. Whitespace, case and curly quotes are ignored; as a fallback only
    letters, digits and figure punctuation are compared."""
    q = _clean(quote)
    if len(q) < 12:
        return None
    for i, p in enumerate(pages):
        if q in _clean(p):
            return i + 1
    q2 = re.sub(r"[^a-z0-9.%$]", "", q)
    for i, p in enumerate(pages):
        if q2 and q2 in re.sub(r"[^a-z0-9.%$]", "", _clean(p)):
            return i + 1
    return None


def _norm_enum(v, allowed, default):
    v = (str(v).strip().lower() if v is not None else "")
    return v if v in allowed else default


def validate_item(raw, pages, kind="guide"):
    """Checks one extracted item; returns (clean item, None) or (None, reason)."""
    it = dict(raw)
    q = (it.get("quote") or "").strip()
    if not q:
        return None, "no quote"
    page = find_quote(q, pages)
    if not page:
        return None, "quote not in document"
    it["page"] = page
    it["metric"] = _norm_enum(it.get("metric"), METRICS, "other")
    it["basis"] = _norm_enum(it.get("basis"), BASES, "unspecified")
    if it["basis"] == "unspecified" and it["metric"] in GAAP_DEFAULT:
        # accounting totals with no stated basis are the GAAP figures; keeps one series when a release says "GAAP revenue" once
        it["basis"] = "gaap"
    it["measure"] = _norm_enum(it.get("measure"), MEASURES, "level")
    it["unit"] = _norm_enum(it.get("unit"), UNITS, METRICS[it["metric"]][1] or "USD")
    it["scale"] = _norm_enum(it.get("scale"), ("trillion", "billion", "million", "thousand", "none"), "none")
    pt = str(it.get("period_type") or "").upper()
    pt = {"FULL_YEAR": "FY", "YEAR": "FY", "A": "FY", "QUARTER": "Q", "MULTI_YEAR": "M"}.get(pt, pt)
    if pt not in ("Q", "FY", "H1", "H2", "M"):
        return None, "unknown period type"
    it["period_type"] = pt
    try:
        fy = int(it.get("fiscal_year"))
    except (TypeError, ValueError):
        return None, "no fiscal year"
    if not 2000 <= fy <= dt.date.today().year + 12:
        return None, "fiscal year out of range"
    it["fiscal_year"] = fy
    if pt == "Q":
        try:
            it["quarter"] = int(it.get("quarter"))
        except (TypeError, ValueError):
            return None, "quarter missing"
        if it["quarter"] not in (1, 2, 3, 4):
            return None, "bad quarter"
    else:
        it["quarter"] = None
    if it["measure"] == "growth" or it["metric"] in ("gross_margin", "operating_margin", "ebitda_margin", "tax_rate"):
        if it["unit"] not in ("pct", "ratio"):
            it["unit"] = "pct"
    if kind == "reported":
        if _num(it.get("value")) is None:
            return None, "no value"
        if not _in_quote(it.get("value"), q):
            return None, "value not in quote"
        mult = SCALE.get(it["scale"], 1) if it["unit"] in ("USD", "count") else 1
        it["value_num"] = _num(it["value"]) * mult
        return it, None
    printed = [x for x in (it.get("low"), it.get("high"), it.get("point")) if x not in (None, "")]
    if not printed:
        return None, "no figure"
    for x in printed:
        if not _in_quote(x, q):
            return None, f"figure {x} not in quote"
    if it.get("plus_minus") and not _in_quote(it["plus_minus"], q):
        return None, "tolerance not in quote"
    it["bound"] = _norm_enum(it.get("bound"), ("range", "point", "approximately", "at_least", "at_most"), "range" if it.get("low") and it.get("high") else "point")
    try:
        lo, hi, mid, prec = resolve_values(it)
    except ValueError as e:
        return None, str(e)
    if mid is None or not math.isfinite(mid):
        return None, "no midpoint"
    it.update({"low_v": lo, "high_v": hi, "mid_v": mid, "precision": prec})
    # implausible magnitudes: a dollar total printed without its scale word, a margin above 100%
    if it["unit"] == "pct" and it["measure"] == "level" and abs(mid) > 100:
        return None, "percentage above 100"
    if it["unit"] == "USD" and abs(mid) < 1000 and it["metric"] not in ("other",):
        return None, "dollar amount without scale"
    return it, None


GENERIC = set("""revenue revenues sales net total organic adjusted non gaap diluted eps earnings per share growth margin margins gross operating
income expense expenses opex tax taxes rate rates effective free cash flow flows capital expenditures expenditure capex ebitda full year fiscal quarter
quarterly guidance outlook reported basis constant currency cc the of and a an on in for to approximately increase decrease decline change yoy over
interest depreciation amortization shares count weighted average repurchases repurchase buybacks fx neutral core comparable comparative like
related items certain specified impact impacts percent pct dollar dollars basic common profit segment business company consolidated
""".split())


def _seg_key(it):
    """Segment name plus whatever in the printed label narrows the metric ("excluding COVID-19 testing"), so two
    variants guided side by side stay two series while wording changes between releases do not split one."""
    seg = re.sub(r"[^a-z0-9]+", " ", (it.get("segment") or "").lower()).split()
    lab = re.sub(r"[^a-z0-9]+", " ", (it.get("label") or "").lower()).split()
    toks = [w for w in seg if w not in ("segment", "business", "the", "and", "of", "total")]
    toks += [w for w in lab if w not in GENERIC and w not in toks and not w.isdigit()]
    return " ".join(toks)


def series_key(it):
    return "|".join([it["metric"], it["basis"], it["measure"], it["unit"], _seg_key(it)])


def item_id(doc_id, kind, it):
    raw = "|".join(str(x) for x in (doc_id, kind, series_key(it), it["period_type"], it["fiscal_year"], it.get("quarter"),
                                     it.get("low"), it.get("high"), it.get("point"), it.get("value"), _clean(it.get("quote"))[:300]))
    return hashlib.sha1(raw.encode()).hexdigest()[:20]


def to_row(tk, fye, doc, issued, kind, it, method, model):
    lab = fiscal_label(it["period_type"], it["fiscal_year"], it.get("quarter"))
    pe = period_end_est(tk, fye, "FY" if it["period_type"] == "M" else it["period_type"], it["fiscal_year"], it.get("quarter"))
    row = {"id": item_id(doc["id"], kind, it), "ticker": tk, "kind": kind, "metric": it["metric"], "label": (it.get("label") or METRICS[it["metric"]][0])[:120],
           "segment": (it.get("segment") or None) and str(it["segment"])[:120], "basis": it["basis"], "measure": it["measure"], "unit": it["unit"],
           "series_key": series_key(it), "period_type": it["period_type"], "fiscal_year": it["fiscal_year"], "quarter": it.get("quarter"),
           "fiscal_label": lab, "period_end": pe, "issued": issued, "doc_id": doc["id"], "page": it.get("page"),
           "quote": re.sub(r"\s+", " ", it["quote"]).strip()[:1500], "method": method, "model": model, "extracted_at": now_iso(),
           "low": None, "high": None, "point": None, "mid": None, "bound": None, "precision": None, "status_words": None, "printed": None}
    if kind == "guide":
        row.update({"low": it["low_v"], "high": it["high_v"], "mid": it["mid_v"], "point": it["mid_v"] if it["low_v"] == it["high_v"] else None,
                    "bound": it["bound"], "precision": it["precision"], "status_words": _norm_enum(it.get("status"), ("new", "raised", "lowered", "reaffirmed", "narrowed", "updated"), None),
                    "printed": {"low": it.get("low"), "high": it.get("high"), "point": it.get("point"), "plus_minus": it.get("plus_minus"), "scale": it.get("scale")}})
    elif kind == "reported":
        row.update({"mid": it["value_num"], "low": it["value_num"], "high": it["value_num"], "point": it["value_num"],
                    "printed": {"value": it.get("value"), "scale": it.get("scale")}, "precision": 0.5 * 10 ** -_decimals(it.get("value")) * (SCALE.get(it["scale"], 1) if it["unit"] in ("USD", "count") else 1)})
    return row


# ------------------------------------------------------------------ one document
def process_doc(sb, tk, name, fye, doc, use_llm=True):
    t0 = time.time()
    text = text_of(sb, doc)
    issued, how = issue_date(doc, text)
    status = {"doc_id": doc["id"], "ticker": tk, "issued": issued, "version": VERSION, "windows": 0, "items": 0, "rejected": 0,
              "method": None, "model": None, "error": None, "processed_at": now_iso()}
    if not text or text.get("kind") != "pages":
        status["status"] = "no text"
        return status, []
    if not issued:
        status["status"] = "no issue date"
        return status, []
    chunks = windows(text)
    status["windows"] = len(chunks)
    if not chunks:
        status["status"] = "no guidance language"
        status["seconds"] = round(time.time() - t0, 2)
        return status, []
    pages = text["pages"]
    per, pend = doc_period(tk, fye, doc, issued)
    ctx = {"tk": tk, "name": name, "fye": norm_fye(fye_at(tk, fye, issued)), "issued": issued, "period": per, "period_end": pend,
           "doc_kind": {"release": "earnings release", "presentation": "investor presentation", "remarks": "prepared remarks",
                        "transcript": "earnings call transcript", "supplement": "financial supplement"}.get(doc.get("doc_type"), "8-K exhibit")}
    rows, rejected, methods, models = [], collections.Counter(), set(), set()
    llm_failed = False
    for ch in chunks:
        pat, uncovered = pattern_extract(ch)
        if uncovered == 0 and pat:
            for it in pat:
                v, why = validate_item(it, pages)
                if v:
                    rows.append(to_row(tk, fye, doc, issued, "guide", v, "pattern", None))
                else:
                    rejected[why] += 1
            methods.add("pattern")
            continue
        if not use_llm:
            continue
        try:
            j, model = llm_extract(ch, ctx)
        except Exception as e:
            # model unavailable: keep the validated pattern items of this excerpt and leave the document for a later re-read
            status["error"] = str(e)[:300]
            llm_failed = True
            for it in pat:
                v, why = validate_item(it, pages)
                if v:
                    rows.append(to_row(tk, fye, doc, issued, "guide", v, "pattern", None))
                else:
                    rejected[why] += 1
            if pat:
                methods.add("pattern")
            continue
        models.add(model)
        methods.add("llm")
        patkeys = {(p["metric"], p["basis"], p["period_type"], p["fiscal_year"], p.get("quarter")) for p in pat}
        for kind, arr in (("guide", j.get("guidance") or []), ("reported", j.get("reported") or [])):
            for it in arr if isinstance(arr, list) else []:
                if not isinstance(it, dict):
                    continue
                v, why = validate_item(it, pages, kind)
                if not v:
                    rejected[why] += 1
                    continue
                m = "llm+pattern" if kind == "guide" and (v["metric"], v["basis"], v["period_type"], v["fiscal_year"], v.get("quarter")) in patkeys else "llm"
                rows.append(to_row(tk, fye, doc, issued, kind, v, m, model))
        for it in j.get("withdrawn") or []:
            if not isinstance(it, dict) or not it.get("quote"):
                continue
            page = find_quote(it["quote"], pages)
            if not page:
                rejected["withdrawal quote not in document"] += 1
                continue
            pt = str(it.get("period_type") or "FY").upper()
            pt = pt if pt in ("Q", "FY", "H1", "H2") else "FY"
            try:
                fy = int(it.get("fiscal_year"))
            except (TypeError, ValueError):
                fy = int(issued[:4])
            q = it.get("quarter") if pt == "Q" else None
            met = it.get("metric") if it.get("metric") in METRICS else "all"
            wid = hashlib.sha1(f"{doc['id']}|w|{met}|{pt}|{fy}|{q}|{_clean(it['quote'])[:200]}".encode()).hexdigest()[:20]
            rows.append({"id": wid, "ticker": tk, "kind": "withdrawn", "metric": met if met != "all" else "other", "label": "All guidance" if met == "all" else METRICS[met][0],
                         "segment": None, "basis": "unspecified", "measure": "level", "unit": None, "series_key": "withdrawn|" + met,
                         "period_type": pt, "fiscal_year": fy, "quarter": q, "fiscal_label": fiscal_label(pt, fy, q),
                         "period_end": period_end_est(tk, fye, pt, fy, q), "issued": issued, "doc_id": doc["id"], "page": page,
                         "quote": re.sub(r"\s+", " ", it["quote"]).strip()[:1500], "method": "llm", "model": model, "extracted_at": now_iso(),
                         "low": None, "high": None, "point": None, "mid": None, "bound": None, "precision": None, "status_words": "withdrawn", "printed": None})
    # one row per id (the same sentence can come back from two excerpts)
    uniq = {}
    for r in rows:
        uniq.setdefault(r["id"], r)
    rows = list(uniq.values())
    if llm_failed:
        # version 0: the next run reads this document again with the model
        status["version"] = 0
    status.update({"status": "partial: model unavailable" if llm_failed else "ok", "items": len(rows), "rejected": sum(rejected.values()), "method": "+".join(sorted(methods)) or None,
                   "model": ",".join(sorted(models)) or None, "seconds": round(time.time() - t0, 2),
                   "reasons": dict(rejected) if rejected else None})
    return status, rows


# ------------------------------------------------------------------ actuals
class Actuals:
    """GAAP actuals by fiscal label from the facts table (the same rows the company pages show), with ratios and growth."""

    def __init__(self, sb, tk, fye):
        self.tk, self.fye = tk, fye
        rows = sb.select("facts", "select=statement,label,kind,period_end,period_start,value,accession,form,filed,concept,derived"
                                  f"&ticker=eq.{tk}&statement=in.(income,cashflow)&order=period_end.asc")
        self.v = collections.defaultdict(dict)    # (label, kind) -> {fiscal label: rec}
        for r in rows:
            if r["statement"] == "cashflow" and r["label"] == "Net income":
                continue
            try:
                lab = period_label(tk, fye, r["period_end"], r["kind"])
            except Exception:
                continue
            self.v[(r["label"], r["kind"])][lab] = r

    def _get(self, label, kind, lab):
        return self.v.get((label, kind), {}).get(lab)

    def prior(self, lab):
        m = re.match(r"^(Q\d|H\d) FY(\d\d)$", lab)
        if m:
            return f"{m.group(1)} FY{(int(m.group(2)) - 1) % 100:02d}"
        m = re.match(r"^FY(\d{4})$", lab)
        return f"FY{int(m.group(1)) - 1}" if m else None

    def value(self, fact_label, ptype, lab):
        """(value in stored units, [input records], formula) or None."""
        kind = "Q" if ptype == "Q" else "A"
        if ptype in ("H1", "H2"):
            if fact_label not in ADDITIVE:
                return None
            fy = lab.split(" ")[1]
            qs = ("Q1", "Q2") if ptype == "H1" else ("Q3", "Q4")
            parts = [self.value(fact_label, "Q", f"{q} {fy}") for q in qs]
            if any(p is None for p in parts):
                return None
            return parts[0][0] + parts[1][0], parts[0][1] + parts[1][1], f"{fact_label} {qs[0]} + {qs[1]}"
        g = lambda l: self._get(l, kind, lab)
        if fact_label == "Gross margin":
            rv, gp, cg = g("Revenue"), g("Gross profit"), g("Cost of revenue")
            if rv and rv["value"] and gp:
                return gp["value"] / rv["value"] * 100, [gp, rv], "Gross profit / Revenue"
            if rv and rv["value"] and cg:
                return (rv["value"] - cg["value"]) / rv["value"] * 100, [rv, cg], "(Revenue − Cost of revenue) / Revenue"
            return None
        if fact_label == "Operating margin":
            rv, oi = g("Revenue"), g("Operating income")
            return (oi["value"] / rv["value"] * 100, [oi, rv], "Operating income / Revenue") if rv and oi and rv["value"] else None
        if fact_label == "Effective tax rate":
            tx, pt = g("Income tax"), g("Pretax income")
            return (tx["value"] / pt["value"] * 100, [tx, pt], "Income tax / Pretax income") if tx and pt and pt["value"] else None
        if fact_label == "Free cash flow":
            o, x = g("Operating cash flow"), g("Capex")
            return (o["value"] - abs(x["value"]), [o, x], "Operating cash flow − Capex") if o and x else None
        if fact_label in ("Revenue growth YoY", "EPS growth YoY"):
            base = "Revenue" if fact_label.startswith("Revenue") else "EPS diluted"
            a, b = g(base), self._get(base, kind, self.prior(lab) or "")
            return ((a["value"] / b["value"] - 1) * 100, [a, b], f"{base} / {base} (prior year) − 1") if a and b and b["value"] and b["value"] > 0 else None
        r = g(fact_label)
        if not r:
            return None
        v = abs(r["value"]) if fact_label in ("Capex", "Buybacks") else r["value"]
        return v, [r], fact_label


def _rec_ref(r):
    return {"accession": r["accession"], "form": r.get("form"), "filed": r.get("filed"), "end": r["period_end"], "start": r.get("period_start"),
            "concept": r.get("concept"), "value": r["value"], "label": r["label"], "derived": r.get("derived")}


def kpi_index(sb, tk):
    defs = {d["kpi_id"]: d for d in sb.select("kpi_definitions", f"select=kpi_id,name,unit,category&ticker=eq.{tk}")}
    vals = sb.select("kpi_values", f"select=kpi_id,period_kind,period_end,fiscal_label,value,unit,source_id,anchor&ticker=eq.{tk}&source_kind=eq.ir")
    out = collections.defaultdict(dict)
    for v in vals:
        d = defs.get(v["kpi_id"])
        if not d or v["period_kind"] not in ("Q", "A"):
            continue
        out[v["kpi_id"]][(v["period_kind"], v["fiscal_label"])] = v
    return defs, out


def _tokens(s):
    stop = {"and", "of", "the", "total", "net", "adjusted", "non", "gaap", "organic", "reported", "fiscal", "year", "full", "growth", "rate"}
    return {w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in stop and len(w) > 1}


def kpi_match(defs, it):
    """A published KPI with the same name and a compatible unit (conservative: near-identical names only)."""
    want = _tokens((it.get("segment") or "") + " " + (it.get("label") or ""))
    if not want or it["measure"] != "level":
        return None
    best = None
    for kid, d in defs.items():
        have = _tokens(d["name"])
        if not have:
            continue
        j = len(want & have) / len(want | have)
        du = (d.get("unit") or "").lower()
        ok_unit = (it["unit"] == "pct" and du in ("%", "pct", "percent")) or (it["unit"] == "USD" and du in ("usd", "$")) or \
                  (it["unit"] == "count" and du in ("count", "units", "")) or (it["unit"] == "USD_per_share" and du in ("usd_per_unit", "usd_per_share"))
        if ok_unit and j >= 0.75 and (best is None or j > best[0]):
            best = (j, kid)
    return best[1] if best else None


# ------------------------------------------------------------------ build
def _same(a, b, scale):
    return abs(a - b) <= max(1e-9, abs(scale) * 0.0025)


def classify_revision(prev, cur):
    """Action of a guidance point relative to the previous point for the same series and period."""
    if prev is None:
        return "initial"
    pm, cm = prev["mid"], cur["mid"]
    ref = max(abs(pm), abs(cm), 1e-9) if cur["unit"] != "pct" else 100.0
    pw = (prev["high"] - prev["low"]) if prev["high"] is not None and prev["low"] is not None else None
    cw = (cur["high"] - cur["low"]) if cur["high"] is not None and cur["low"] is not None else None
    if cur["unit"] == "pct":
        same_mid = abs(pm - cm) <= 0.0501
    else:
        same_mid = _same(pm, cm, ref)
    if same_mid:
        if pw is not None and cw is not None:
            if cw < pw - max(1e-9, abs(ref) * 0.0005):
                return "narrowed"
            if cw > pw + max(1e-9, abs(ref) * 0.0005):
                return "widened"
        return "reaffirmed"
    return "raised" if cm > pm else "lowered"


def _direction(it):
    d = METRICS[it["metric"]][2]
    if it["metric"] == "other":
        lab = ((it.get("label") or "") + " " + (it.get("segment") or "")).lower()
        if re.search(r"expense|cost|loss|charge|dilution|tax", lab):
            return -1
        if re.search(r"revenue|sales|income|earnings|ebit|margin|bookings|growth|cash flow|profit|eps", lab):
            return 1
    if it["measure"] == "growth" and d == 0:
        return 0
    return d


class Store:
    """Per-company extraction state, kept in the private `raw` bucket as guidance/{T}.json.gz (every extracted item and
    the per-document log) and mirrored to the guidance_* tables when they exist."""

    def __init__(self, sb, log=print):
        self.sb, self.log = sb, log
        self.db = self._probe()
        self._warned = False

    def _probe(self):
        try:
            self.sb.select("guidance_items", "select=id&limit=1")
            return True
        except Exception:
            return False

    def load(self, tk):
        b = self.sb.get_object("raw", f"guidance/{tk}.json.gz")
        if not b:
            return {"ticker": tk, "items": {}, "docs": {}}
        try:
            b = gzip.decompress(b)
        except OSError:
            pass
        d = json.loads(b)
        d.setdefault("items", {})
        d.setdefault("docs", {})
        return d

    def save(self, tk, state):
        state["saved"] = now_iso()
        body = gzip.compress(json.dumps(state, default=str, separators=(",", ":")).encode())
        self.sb.put_object("raw", f"guidance/{tk}.json.gz", body, "application/gzip", cache="no-store")

    def mirror(self, table, rows, conflict):
        if not self.db or not rows:
            return
        try:
            self.sb.upsert(table, rows, conflict)
        except Exception as e:
            if not self._warned:
                self.log(f"  mirror to {table} failed: {str(e)[:160]}")
                self._warned = True

    def mirror_delete(self, table, query):
        if not self.db:
            return
        try:
            self.sb.delete(table, query)
        except Exception as e:
            self.log(f"  delete from {table} failed: {str(e)[:120]}")


ITEM_COLS = ("id", "ticker", "kind", "metric", "label", "segment", "basis", "measure", "unit", "series_key", "period_type", "fiscal_year", "quarter",
             "fiscal_label", "period_end", "low", "high", "point", "mid", "bound", "precision", "status_words", "printed", "issued", "doc_id", "page",
             "quote", "method", "model", "action", "prev_id", "extracted_at")
DOC_COLS = ("doc_id", "ticker", "issued", "version", "status", "windows", "items", "rejected", "reasons", "method", "model", "error", "seconds", "processed_at")


def build_company(sb, store, state, tk, name, sector, fye, log=print):
    """Revision chains, actuals, outcomes and the credibility record for one company; writes guidance/{T}.json."""
    rows = sorted(state["items"].values(), key=lambda r: (r["issued"], r["id"]))
    for r in rows:
        if r["basis"] == "unspecified" and r["metric"] in GAAP_DEFAULT:
            r["basis"] = "gaap"
            parts = r["series_key"].split("|")
            parts[1] = "gaap"
            r["series_key"] = "|".join(parts)
    guides = [r for r in rows if r["kind"] == "guide"]
    reported = [r for r in rows if r["kind"] == "reported"]
    withdrawn = [r for r in rows if r["kind"] == "withdrawn"]
    act = Actuals(sb, tk, fye)
    kdefs, kvals = kpi_index(sb, tk)
    doc_ids = sorted({r["doc_id"] for r in rows})
    docs = {}
    for i in range(0, len(doc_ids), 80):
        for d in sb.select("ir_documents", "select=id,url,title,doc_type,fiscal_label,ext,storage_path,text_path,source_page"
                                           f"&id=in.({','.join(doc_ids[i:i + 80])})"):
            docs[d["id"]] = d
    # adjusted actuals the company reported: the earliest report of each series and period
    rep = {}
    for r in reported:
        rep.setdefault((r["series_key"], r["fiscal_label"]), r)
    order_type = {"release": 0, "remarks": 1, "presentation": 2, "transcript": 3}
    chains = collections.defaultdict(list)
    for r in guides:
        r["action"], r["prev_id"] = None, None
        chains[(r["series_key"], r["fiscal_label"])].append(r)
    outcomes, series = [], collections.OrderedDict()
    today = dt.date.today().isoformat()
    for (skey, lab), pts in chains.items():
        # one point per issue date: the same figures in the release, the remarks and the slides of one filing collapse
        pts.sort(key=lambda r: (r["issued"], order_type.get((docs.get(r["doc_id"]) or {}).get("doc_type"), 4), 0 if r["method"] != "llm" else 1))
        chain, alts = [], collections.defaultdict(list)
        for r in pts:
            if chain and chain[-1]["issued"] == r["issued"]:
                alts[chain[-1]["id"]].append({"doc": r["doc_id"], "pg": r.get("page"), "q": r["quote"]})
                r["action"], r["prev_id"] = "duplicate", chain[-1]["id"]
                continue
            chain.append(r)
        first = chain[0]
        wd = [w for w in withdrawn if w["fiscal_label"] == lab and (w["label"] == "All guidance" or w["metric"] == first["metric"])]
        prev = None
        for r in chain:
            gap = prev and any(prev["issued"] < w["issued"] <= r["issued"] for w in wd)
            a = classify_revision(None if gap else prev, r)
            if a == "initial" and any(w["issued"] < r["issued"] for w in wd):
                a = "reinstated"
            r["action"], r["prev_id"] = a, (prev["id"] if prev and not gap else None)
            prev = r
        actual = None
        fl = FACT_OF.get((first["metric"], first["measure"]))
        ptype = first["period_type"]
        if ptype != "M":
            if fl and first["basis"] in ("gaap", "unspecified") and not first.get("segment") and first["unit"] in ("USD", "USD_per_share", "pct", "count"):
                got = act.value(fl, ptype, lab)
                if got:
                    v, recs, formula = got
                    filed = max((x["filed"] for x in recs if x.get("filed")), default=None)
                    actual = {"v": v, "src": "xbrl", "label": fl, "formula": formula, "reported_on": filed, "inputs": [_rec_ref(x) for x in recs]}
            if actual is None and (skey, lab) in rep:
                rr = rep[(skey, lab)]
                actual = {"v": rr["mid"], "src": "release", "doc": rr["doc_id"], "page": rr["page"], "quote": rr["quote"], "printed": (rr.get("printed") or {}).get("value"),
                          "reported_on": rr["issued"], "label": rr["label"], "item": rr["id"]}
            # published KPIs carry no GAAP / adjusted basis, so only segment and company-specific items are matched to them
            if actual is None and ptype in ("Q", "FY") and (first["metric"] == "other" or first.get("segment")):
                kid = kpi_match(kdefs, first)
                kv = kvals[kid].get(("Q" if ptype == "Q" else "A", lab)) if kid else None
                if kv:
                    actual = {"v": float(kv["value"]), "src": "kpi", "kpi": kid, "kpi_name": kdefs[kid]["name"], "doc": kv["source_id"],
                              "page": (kv.get("anchor") or {}).get("page"), "quote": (kv.get("anchor") or {}).get("quote"), "reported_on": None}
                    if kv["source_id"] != "derived":
                        docs.setdefault(kv["source_id"], None)
        outcome = None
        if actual is not None:
            # scored against the latest guidance issued before the actual was reported (before the period ended when that date is unknown)
            cutoff = actual.get("reported_on") or first["period_end"]
            pre = [r for r in chain if r["issued"] < cutoff]
            if pre:
                fin = pre[-1]
                v = actual["v"]
                lo, hi, mid = fin["low"], fin["high"], fin["mid"]
                tol = float(fin.get("precision") or 0)
                if fin["unit"] == "pct" or first["measure"] != "level":
                    tol = max(tol, 0.05)
                elif fin["unit"] == "USD_per_share":
                    tol = max(tol, 0.005)
                else:
                    tol = max(tol, abs(mid) * 0.0005)
                if lo is not None and v < lo - tol:
                    res = "below"
                elif hi is not None and v > hi + tol:
                    res = "above"
                else:
                    res = "within"
                if fin.get("bound") == "at_least" and res == "within" and v > lo + tol:
                    res = "above"
                if fin.get("bound") == "at_most" and res == "within" and v < hi - tol:
                    res = "below"
                pct_like = fin["unit"] == "pct" or first["measure"] != "level"
                d = _direction(first)
                met = (res != "below") if d > 0 else (res != "above") if d < 0 else (res == "within")
                outcome = {"final_id": fin["id"], "initial_id": chain[0]["id"], "final_d": fin["issued"],
                           "revisions": sum(1 for r in pre[1:] if r["action"] in ("raised", "lowered", "narrowed", "widened")),
                           "result": res, "surprise_abs": v - mid, "surprise_rel": None if pct_like or not mid else (v - mid) / abs(mid),
                           "direction": d, "met": met, "initial_mid": chain[0]["mid"], "final_mid": mid, "final_low": lo, "final_high": hi}
                outcomes.append({"ticker": tk, "series_key": skey, "fiscal_label": lab, "metric": first["metric"], "basis": first["basis"], "measure": first["measure"],
                                 "unit": first["unit"], "label": first["label"], "segment": first.get("segment"), "period_type": ptype, "period_end": first["period_end"],
                                 "initial_id": chain[0]["id"], "final_id": fin["id"], "revisions": outcome["revisions"], "initial_mid": chain[0]["mid"],
                                 "final_low": lo, "final_high": hi, "final_mid": mid, "actual": v, "actual_source": actual["src"],
                                 "actual_ref": {k: x for k, x in actual.items() if k != "v"}, "reported_on": actual.get("reported_on"),
                                 "surprise_abs": outcome["surprise_abs"], "surprise_rel": outcome["surprise_rel"], "result": res, "direction": d,
                                 "met": met, "updated_at": now_iso()})
        s = series.setdefault(skey, {"key": skey, "metric": first["metric"], "name": METRICS[first["metric"]][0] if first["metric"] != "other" else first["label"],
                                     "label": first["label"], "segment": first.get("segment"), "basis": first["basis"], "measure": first["measure"],
                                     "unit": first["unit"], "direction": _direction(first), "periods": []})
        s["periods"].append({"label": lab, "type": ptype, "end": first["period_end"], "open": actual is None and first["period_end"] >= today,
                             "points": [{"id": r["id"], "d": r["issued"], "lo": r["low"], "hi": r["high"], "mid": r["mid"], "b": r.get("bound"), "a": r["action"],
                                         "w": r.get("status_words"), "doc": r["doc_id"], "pg": r.get("page"), "q": r["quote"], "pr": r.get("printed"), "m": r["method"],
                                         "alt": alts.get(r["id"]) or None, "lab": r["label"]} for r in chain],
                             "actual": actual, "outcome": outcome})
    # documents behind KPI actuals
    missing = [k for k, v in docs.items() if v is None]
    for i in range(0, len(missing), 80):
        for d in sb.select("ir_documents", f"select=id,url,title,doc_type,fiscal_label,ext,storage_path,text_path,source_page&id=in.({','.join(missing[i:i + 80])})"):
            docs[d["id"]] = d
    cred = credibility(tk, series, outcomes, guides)
    ser = list(series.values())
    for s in ser:
        s["periods"].sort(key=lambda p: (p["end"], p["type"]))
        s["n"] = sum(len(p["points"]) for p in s["periods"])
        s["scored"] = sum(1 for p in s["periods"] if p["outcome"])
        s["last"] = max(pt["d"] for p in s["periods"] for pt in p["points"])
    rank = {m: i for i, m in enumerate(METRICS)}
    recent = (dt.date.today() - dt.timedelta(days=550)).isoformat()
    ser.sort(key=lambda s: (s["last"] < recent, rank.get(s["metric"], 99), s["segment"] is not None, -s["n"]))
    used = {pt["doc"] for s in ser for p in s["periods"] for pt in p["points"]} | \
           {a["doc"] for s in ser for p in s["periods"] for pt in p["points"] for a in (pt.get("alt") or [])} | \
           {p["actual"].get("doc") for s in ser for p in s["periods"] if p["actual"] and p["actual"].get("doc")} | {w["doc_id"] for w in withdrawn}
    dl = state["docs"].values()
    iss = sorted(x["issued"] for x in dl if x.get("issued"))
    cov = {"docs_read": len(state["docs"]), "with_items": sum(1 for x in dl if x.get("items")), "from": iss[0] if iss else None, "to": iss[-1] if iss else None,
           "methods": dict(collections.Counter(r["method"] for r in guides))}
    bundle = {"ticker": tk, "name": name, "sector": sector, "fye": fye, "generated": now_iso(), "series": ser, "credibility": cred,
              "withdrawn": [{"id": w["id"], "d": w["issued"], "per": w["fiscal_label"], "label": w["label"], "doc": w["doc_id"], "pg": w["page"], "q": w["quote"]} for w in withdrawn],
              "docs": {k: {kk: docs[k].get(kk) for kk in ("id", "url", "title", "doc_type", "fiscal_label", "ext", "storage_path", "text_path", "source_page")}
                       for k in used if docs.get(k)},
              "consensus": consensus_for(sb, store, tk), "coverage": cov}
    sb.put_json(f"guidance/{tk}.json", bundle, cache="max-age=600")
    # mirror
    store.mirror("guidance_items", [{c: r.get(c) for c in ITEM_COLS} for r in rows], "id")
    if store.db:
        # rows from earlier reads that a re-read replaced, and outcomes no longer produced
        try:
            live = {r["id"] for r in rows}
            stale = [r["id"] for r in sb.select("guidance_items", f"select=id&ticker=eq.{tk}") if r["id"] not in live]
            for i in range(0, len(stale), 100):
                store.mirror_delete("guidance_items", f"ticker=eq.{tk}&id=in.({','.join(stale[i:i + 100])})")
            keep = {(o["series_key"], o["fiscal_label"]) for o in outcomes}
            import urllib.parse as _up
            for o in sb.select("guidance_outcomes", f"select=series_key,fiscal_label&ticker=eq.{tk}"):
                if (o["series_key"], o["fiscal_label"]) not in keep:
                    store.mirror_delete("guidance_outcomes", f"ticker=eq.{tk}&series_key=eq.{_up.quote(o['series_key'])}&fiscal_label=eq.{_up.quote(o['fiscal_label'])}")
        except Exception as e:
            log(f"  {tk} stale row cleanup failed: {str(e)[:120]}")
    store.mirror("guidance_outcomes", outcomes, "ticker,series_key,fiscal_label")
    store.mirror("guidance_credibility", [{"ticker": tk, **{k: v for k, v in cred.items() if k in CRED_COLS}, "updated_at": now_iso()}], "ticker")
    return bundle


CRED_COLS = ("n_scored", "n_met", "hit_rate", "in_range_rate", "above_rate", "below_rate", "avg_surprise", "median_surprise", "raise_rate",
             "tendency", "definition_changes", "last_issued", "n_series", "n_points", "first_issued")


def credibility(tk, series, outcomes, guides):
    """Per-company record over every scored guidance period: how often the company met its own guidance, where actuals
    landed in the range, the average surprise against the midpoint, how often annual guidance was raised during the
    year, and changes in how the company defines what it guides."""
    sc = [o for o in outcomes]
    n = len(sc)
    met = sum(1 for o in sc if o["met"])
    within = sum(1 for o in sc if o["result"] == "within")
    above = sum(1 for o in sc if o["result"] == "above")
    below = sum(1 for o in sc if o["result"] == "below")
    # each surprise is capped at ±50% so one-off items (a tax benefit doubling GAAP EPS) do not swamp the average
    rel = sorted(max(-0.5, min(0.5, o["surprise_rel"] * (1 if o["direction"] >= 0 else -1))) for o in sc if o["surprise_rel"] is not None and o["direction"] != 0)
    up = [o for o in sc if o["direction"] > 0]
    # annual guidance raised between the first and the last point of the year
    fy = [o for o in sc if o["period_type"] == "FY" and o["initial_mid"] is not None and o["direction"] > 0]
    raised = sum(1 for o in fy if o["final_mid"] > o["initial_mid"] * (1 + 1e-4) if o["initial_mid"] > 0)
    tendency = None
    if len(up) >= 4:
        ar = sum(1 for o in up if o["result"] == "above") / len(up)
        br = sum(1 for o in up if o["result"] == "below") / len(up)
        if ar >= 0.6:
            tendency = "conservative"
        elif br >= 0.35:
            tendency = "optimistic"
        else:
            tendency = "balanced"
    # definition changes: the basis or the form (amount vs growth) a metric family is guided on changes from one fiscal year to the next
    fam = collections.defaultdict(lambda: collections.defaultdict(set))
    first_seen = {}
    for g in guides:
        if g["period_type"] not in ("Q", "FY") or g.get("segment"):
            continue
        fam[g["metric"]][g["fiscal_year"]].add((g["basis"], g["measure"]))
        first_seen.setdefault((g["metric"], g["fiscal_year"], g["basis"], g["measure"]), (g["issued"], g["id"], g["doc_id"]))
    changes = []
    for metric, years in fam.items():
        if metric == "other":
            continue
        ys = sorted(years)
        for a, b in zip(ys, ys[1:]):
            if b != a + 1:
                continue
            gone, new = years[a] - years[b], years[b] - years[a]
            if gone and new:
                nb = sorted(new)[0]
                when, gid, did = first_seen[(metric, b, nb[0], nb[1])]
                changes.append({"metric": METRICS[metric][0], "fy": b, "from": [f"{BASIS_NAME[x]} {y}" for x, y in sorted(gone)],
                                "to": [f"{BASIS_NAME[x]} {y}" for x, y in sorted(new)], "issued": when, "id": gid, "doc": did})
    return {"n_scored": n, "n_met": met, "hit_rate": met / n if n else None, "in_range_rate": within / n if n else None,
            "above_rate": above / n if n else None, "below_rate": below / n if n else None,
            "avg_surprise": sum(rel) / len(rel) if rel else None, "median_surprise": rel[len(rel) // 2] if rel else None,
            "raise_rate": raised / len(fy) if fy else None, "n_fy": len(fy), "tendency": tendency, "definition_changes": changes,
            "first_issued": min((g["issued"] for g in guides), default=None), "last_issued": max((g["issued"] for g in guides), default=None),
            "n_series": len(series), "n_points": len(guides)}


def consensus_for(sb, store, tk):
    """Licensed consensus estimates, when a feed fills consensus_estimates. Latest as-of per metric and period."""
    if not store.db:
        return []
    try:
        rows = sb.select("consensus_estimates", f"select=*&ticker=eq.{tk}&order=as_of.desc")
    except Exception:
        return []
    out = {}
    for r in rows:
        out.setdefault((r["metric"], r["basis"], r["fiscal_label"]), r)
    return list(out.values())


# ------------------------------------------------------------------ cross-company index
def build_index(sb, log=print):
    """guidance/index.json: recent raises, cuts, new and withdrawn guidance across companies, the credibility table and the
    screener fields, read from the published company files."""
    from .universe import UNIVERSE, SP100
    sectors = {tk: sec for tk, _c, _n, sec in UNIVERSE}
    names = {tk: n for tk, _c, n, _s in UNIVERSE}

    def get(tk):
        try:
            return sb.get_json(f"guidance/{tk}.json")
        except Exception:
            return None
    with ThreadPoolExecutor(12) as ex:
        bundles = {tk: b for tk, b in zip(sectors, ex.map(get, list(sectors))) if b}
    since = (dt.date.today() - dt.timedelta(days=200)).isoformat()
    year_ago = (dt.date.today() - dt.timedelta(days=365)).isoformat()
    ev, wd, rel, scr, docs = [], [], [], {}, {}
    for tk, b in bundles.items():
        raises = cuts = 0
        for s in b["series"]:
            for p in s["periods"]:
                if p["type"] == "M":
                    continue
                prev = None
                for pt in p["points"]:
                    if pt["d"] >= year_ago and pt["a"] in ("raised", "lowered") and s["direction"] != 0:
                        good = (pt["a"] == "raised") == (s["direction"] > 0)
                        raises += good
                        cuts += not good
                    if pt["d"] >= since:
                        ev.append({"t": tk, "id": pt["id"], "metric": s["metric"], "name": s["name"], "label": pt.get("lab") or s["label"], "seg": s["segment"],
                                   "basis": s["basis"], "measure": s["measure"], "unit": s["unit"], "dir": s["direction"], "per": p["label"], "a": pt["a"],
                                   "d": pt["d"], "lo": pt["lo"], "hi": pt["hi"], "mid": pt["mid"], "b": pt["b"], "doc": pt["doc"], "pg": pt["pg"], "q": pt["q"], "pr": pt["pr"],
                                   "prev": {k: prev[k] for k in ("d", "lo", "hi", "mid", "b", "doc", "pg", "q", "pr")} if prev else None})
                        for dd in (pt["doc"], prev["doc"] if prev else None):
                            if dd and dd in b["docs"]:
                                docs[dd] = b["docs"][dd]
                    prev = pt
        for w in b.get("withdrawn") or []:
            if w["d"] >= since:
                wd.append({"t": tk, **w})
                if w["doc"] in b["docs"]:
                    docs[w["doc"]] = b["docs"][w["doc"]]
        c = b.get("credibility") or {}
        if c.get("n_scored"):
            rel.append({"t": tk, "name": names.get(tk), "sector": sectors.get(tk), "sp100": tk in SP100, "n": c["n_scored"], "hit": c["hit_rate"], "in": c["in_range_rate"],
                        "above": c["above_rate"], "below": c["below_rate"], "surp": c["avg_surprise"], "raise": c["raise_rate"], "tend": c["tendency"],
                        "chg": len(c.get("definition_changes") or []), "last": c.get("last_issued")})
        scr[tk] = {"gd_n": c.get("n_scored") or 0, "gd_hit": c.get("hit_rate"), "gd_in": c.get("in_range_rate"), "gd_above": c.get("above_rate"),
                   "gd_surp": c.get("avg_surprise"), "gd_raise_fy": c.get("raise_rate"), "gd_raises_1y": raises, "gd_cuts_1y": cuts,
                   "gd_defchg": len(c.get("definition_changes") or []), "gd_last": c.get("last_issued"), "gd_series": len(b["series"])}
    ev.sort(key=lambda e: (e["d"], e["t"]), reverse=True)
    rel.sort(key=lambda r: (-(r["hit"] or 0), -r["n"]))
    stats = {"companies": len(bundles), "scored_companies": len(rel), "points": sum((b.get("credibility") or {}).get("n_points") or 0 for b in bundles.values()),
             "scored": sum((b.get("credibility") or {}).get("n_scored") or 0 for b in bundles.values()), "events": len(ev)}
    out = {"generated": now_iso(), "since": since, "events": ev, "withdrawn": sorted(wd, key=lambda w: w["d"], reverse=True), "reliability": rel, "screener": scr,
           "stats": stats, "docs": docs}
    size = sb.put_json("guidance/index.json", out, cache="max-age=600")
    log(f"guidance/index.json {size / 1e3:.0f} KB, {len(ev)} events, {len(rel)} scored companies")
    return stats


# ------------------------------------------------------------------ job
def _doc_order(d):
    return issue_date(d)[0] or d.get("period_end") or ""


def guidance_job(sb, tickers=None, since=None, until=None, workers=6, doc_workers=3, time_budget=None, rebuild=False, recent_days=None, log=print):
    """Reads documents not yet processed by this extractor version, then rebuilds the companies that changed and the
    index. tickers None: every company. since/until bound the issue date (8-K filing date, or the period for IR-site
    documents). recent_days: only documents stored in that many days (the cron path). rebuild: no reading, only rebuild."""
    from .universe import UNIVERSE
    t0 = time.time()
    deadline = t0 + time_budget if time_budget else None
    uni = {tk: (n, s) for tk, _c, n, s in UNIVERSE}
    tks = [t for t in (tickers or list(uni)) if t in uni]
    store = Store(sb, log)
    with Run(sb, "guidance") as run:
        cos = {r["ticker"]: r for r in sb.select("companies", "select=ticker,fiscal_year_end")}
        if recent_days and not rebuild:
            cut = (dt.datetime.utcnow() - dt.timedelta(days=recent_days)).isoformat()
            fresh = sb.select("ir_documents", "select=id,ticker,url,title,doc_type,fiscal_label,period_end,ext,pages,text_path,storage_path,fetched_at,source_page"
                                              f"&fetched_at=gte.{cut}")
            by = collections.defaultdict(list)
            for d in fresh:
                if d["ticker"] in uni and is_candidate(d):
                    by[d["ticker"]].append(d)
            tks = [t for t in tks if t in by]
        else:
            by = None
        stats = collections.Counter()
        built = []

        def company(tk):
            if deadline and time.time() > deadline - 45:
                return tk, "skipped", 0
            fye = (cos.get(tk) or {}).get("fiscal_year_end") or "1231"
            state = store.load(tk)
            new_items = 0
            if not rebuild:
                docs = by[tk] if by is not None else candidates(sb, tk)
                todo = []
                for d in docs:
                    st = state["docs"].get(d["id"])
                    if st and (st.get("version") or 0) >= VERSION:
                        continue
                    ref = issue_date(d)[0] or d.get("period_end")
                    if since and (not ref or ref < since):
                        continue
                    if until and ref and ref > until:
                        continue
                    todo.append(d)
                todo.sort(key=_doc_order, reverse=True)
                n_done = 0
                with ThreadPoolExecutor(doc_workers) as ex:
                    futs = {}
                    it = iter(todo)
                    for d in it:
                        futs[ex.submit(process_doc, sb, tk, uni[tk][0], fye, d)] = d
                        if len(futs) >= doc_workers:
                            break
                    while futs:
                        fut = next(as_completed(futs))
                        d = futs.pop(fut)
                        try:
                            st, rows = fut.result()
                        except Exception as e:
                            st, rows = {"doc_id": d["id"], "ticker": tk, "version": VERSION, "status": "error", "error": str(e)[:300], "processed_at": now_iso()}, []
                        # a re-read replaces what the document gave before
                        for k in [k for k, r in state["items"].items() if r["doc_id"] == d["id"]]:
                            del state["items"][k]
                        for r in rows:
                            state["items"][r["id"]] = r
                        state["docs"][d["id"]] = {c: st.get(c) for c in DOC_COLS}
                        stats[st.get("status") or "?"] += 1
                        new_items += len(rows)
                        n_done += 1
                        if n_done % 10 == 0:
                            store.save(tk, state)
                        if not (deadline and time.time() > deadline - 60):
                            nxt = next(it, None)
                            if nxt is not None:
                                futs[ex.submit(process_doc, sb, tk, uni[tk][0], fye, nxt)] = nxt
                if n_done:
                    store.save(tk, state)
                    store.mirror("guidance_docs", [dict(v, doc_id=k) for k, v in state["docs"].items()], "doc_id")
                if not n_done and not rebuild:
                    return tk, "unchanged", 0
            if not state["items"] and not state["docs"]:
                return tk, "no documents", 0
            build_company(sb, store, state, tk, uni[tk][0], uni[tk][1], fye, log=log)
            store.save(tk, state)
            return tk, "built", new_items

        with ThreadPoolExecutor(workers) as ex:
            futs = [ex.submit(company, tk) for tk in tks]
            for i, fut in enumerate(as_completed(futs)):
                try:
                    tk, what, n = fut.result()
                except Exception as e:
                    stats["company_errors"] += 1
                    log(f"  company failed: {str(e)[:200]}")
                    continue
                stats["items"] += n
                if what == "built":
                    built.append(tk)
                if what == "skipped":
                    stats["skipped"] += 1
                if (i + 1) % 5 == 0 or what == "built":
                    log(f"  {i + 1}/{len(tks)} {tk} {what} +{n} items; docs {dict((k, v) for k, v in stats.items() if k not in ('items',))}; llm {dict(LLM_STATS)}; {round(time.time() - t0)}s")
        idx = build_index(sb, log=log) if built or rebuild else None
        run.stats.update({"companies": len(tks), "built": len(built), "items": stats["items"], "docs": {k: v for k, v in stats.items() if k != "items"},
                          "llm": dict(LLM_STATS), "db_mirror": store.db, "index": idx})
        return dict(run.stats)
