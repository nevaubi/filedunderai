"""Official IR source lists: every link a company publishes on its own investor-relations pages, by fiscal period.

A source list starts as research: a crawl of the company's results archive (quarterly earnings, events, annual
reports), saved as it was collected under etl/ir_sources/raw/{TICKER}--{name}.json. `build` reads every raw file of
a ticker, turns each listed link into one record (type, fiscal period, release date, the page it was listed on,
whether the file belongs in the document store) and writes etl/ir_sources/{TICKER}.json. Those files are generated:
change the raw file or this module and rebuild, never edit them by hand.

The collector (etl/ir.py) uses a list three ways. It queues every storable file that is not stored yet, with the
period, type and release date the company states instead of guesses from the file name. Earnings documents are
always queued ("core"); the rest of what a company lists (SEC filing copies and the filings it links on sec.gov,
the web page of a release, offering and regulatory documents, XBRL archives, images) is queued when the run asks
for everything (FU_IR_EXTENDED=1). Only recordings and event pages stay links: there is no document in them. It
corrects the same fields on documents already stored under a listed address. It publishes the list with the
document index, so a reader sees what the company lists for each quarter, which of it is stored, and which stored
copy stands in for a missing file.

    python -m etl.ir_official build [TICKER ...]     raw research files -> etl/ir_sources/{T}.json
    python -m etl.ir_official report [TICKER ...]    coverage of the published document index against each list
    python -m etl.ir_official check [TICKER ...]     request every storable link once; lists the ones that fail
"""
import collections
import datetime as dt
import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "ir_sources")
RAW = os.path.join(SOURCES, "raw")
VERSION = 3

# Earnings document types: always stored. KEPT_TYPES are written to ir_documents under their own name; any other
# stored file is typed "other".
STORED_TYPES = ("release", "presentation", "supplement", "transcript", "remarks", "annual_report")
KEPT_TYPES = STORED_TYPES + ("sec_filing", "image", "archive")
TYPE_ORDER = ("release", "presentation", "supplement", "transcript", "remarks", "annual_report", "regulatory",
              "prospectus", "proxy", "other", "sec_filing", "webcast", "image", "archive")
TYPE_NAMES = {"release": "Earnings release", "presentation": "Earnings presentation", "supplement": "Financial supplement",
              "transcript": "Earnings call transcript", "remarks": "Prepared remarks", "annual_report": "Annual report",
              "sec_filing": "SEC filing", "webcast": "Webcast", "proxy": "Proxy statement", "regulatory": "Regulatory disclosure",
              "prospectus": "Offering document", "image": "Image", "archive": "File archive", "other": "Document"}
QUARTERLY = "Quarterly earnings"
SECTION_ORDER = (QUARTERLY, "Historical supplements", "Annual reports", "Investor days")
EXTS = (".pdf", ".xlsx", ".xlsm", ".xls", ".csv", ".pptx", ".ppt", ".docx", ".doc", ".zip", ".mp3", ".mp4", ".wav", ".jpg", ".jpeg", ".png",
        ".htm", ".html", ".aspx", ".jsp")
PAGE_EXTS = (".htm", ".html", ".aspx", ".jsp")
MEDIA_EXTS = (".mp3", ".mp4", ".wav")
# Files kept as they are, without text: there is nothing in them to read by page.
ORIGINAL_EXTS = (".zip", ".jpg", ".png")
# Hosts that ask for a sign-in for files the company's public site serves at the same path: (host, path prefix) -> public host.
PUBLIC_HOSTS = {("www.citivelocity.com", "/rcs/citigpa/"): "www.citigroup.com"}
RELEASE_PAGE = "web page of the release; the release file is stored too"
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "1st": 1, "2nd": 2, "3rd": 3, "4th": 4}
_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
_GENERIC = re.compile(r"^(view )?(pdf|ppt|xls|xlsx|report|document|agenda|download|file|link|here|pdf icon.*)?( pdf| xbrl)?$", re.I)

# Keys of a research file that hold the link itself, its name, its type and its dates.
_URL_KEYS = ("url", "file_url")
_NAME_KEYS = ("title", "name", "label", "filename")
_TYPE_KEYS = ("document_type", "type", "category", "form")
_DATE_KEYS = ("release_date", "date", "filing_date")
_PAGE_KEYS = ("earnings_release_page", "page_url", "source_page", "archive_url", "index", "source")


# ---------------------------------------------------------------- small readers
def url_key(url):
    """Address used to tell whether two links are the same file. Host case, fragments and the build token some IR
    hosts put in the path (/_0123...abcdef/, new on every site release) do not identify a file."""
    p = urllib.parse.urlsplit((url or "").strip())
    path = re.sub(r"/_[0-9a-f]{32}(?=/)", "", p.path)
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path, p.query, ""))


def public_url(url):
    """Where a listed file is fetched from: its own address, or the same path on the company's public site when the
    listed host asks for a sign-in."""
    p = urllib.parse.urlsplit(url or "")
    for (host, prefix), public in PUBLIC_HOSTS.items():
        if p.netloc.lower() == host and p.path.startswith(prefix):
            return urllib.parse.urlunsplit((p.scheme, public, p.path, p.query, p.fragment))
    return url


def ext_of(url):
    path = urllib.parse.urlsplit(url or "").path.lower()
    for e in EXTS:
        if path.endswith(e):
            return ".jpg" if e == ".jpeg" else e
    return None


def iso_date(text):
    """ISO date from "October 15, 2026", "2026-10-15" or "2026 Nov 05"; None when the text has no full date."""
    t = str(text or "")
    m = re.search(r"\b(20\d\d|19\d\d)-(\d\d)-(\d\d)\b", t)
    if m:
        return m.group(0)
    m = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+((?:19|20)\d\d)\b", t)
    if not m:
        m2 = re.search(r"\b((?:19|20)\d\d)\s+([A-Za-z]{3})[a-z]*\s+(\d{1,2})\b", t)
        if not m2 or m2.group(2).lower() not in _MONTHS:
            return None
        y, mon, d = int(m2.group(1)), _MONTHS[m2.group(2).lower()], int(m2.group(3))
    else:
        if m.group(1).lower() not in _MONTHS:
            return None
        y, mon, d = int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2))
    try:
        return dt.date(y, mon, d).isoformat()
    except ValueError:
        return None


def _year(y):
    y = int(y)
    return y + (2000 if y < 80 else 1900) if y < 100 else y


def quarter_in(text):
    """(fiscal year, quarter) stated in a name such as "Third Quarter 2026", "Q3 2026", "3Q26" or "2026-Q3"."""
    t = str(text or "")
    for rx, pick in (
            (r"\b(first|second|third|fourth|1st|2nd|3rd|4th)[\s_-]+quarter[\s_-]+(?:of[\s_-]+)?((?:19|20)\d\d)\b", lambda m: (m.group(2), _ORD[m.group(1).lower()])),
            (r"(?<![A-Za-z0-9])([1-4])Q[\s_-]?((?:19|20)?\d\d)(?!\d)", lambda m: (m.group(2), int(m.group(1)))),
            (r"(?<![A-Za-z0-9])Q([1-4])[\s_-]?(?:FY)?[\s_-]?((?:19|20)?\d\d)(?!\d)", lambda m: (m.group(2), int(m.group(1)))),
            (r"(?<!\d)((?:19|20)\d\d)[\s_-]?Q([1-4])(?!\d)", lambda m: (m.group(1), int(m.group(2))))):
        m = re.search(rx, t, re.I)
        if m:
            y, q = pick(m)
            return _year(y), q
    return None


def year_in(text):
    m = re.search(r"(?<!\d)((?:19|20)\d\d)(?!\d)", str(text or ""))
    return int(m.group(1)) if m else None


def quarter_end(fye, fy, q):
    """Last day of fiscal quarter q of fiscal year fy for a company whose year ends on fye (MMDD)."""
    fm = int((fye or "1231")[:2])
    month = fm - 3 * (4 - q)
    year = fy
    while month <= 0:
        month, year = month + 12, year - 1
    first_next = dt.date(year + (month == 12), month % 12 + 1, 1)
    return (first_next - dt.timedelta(days=1)).isoformat()


def labels(fye, fy, q):
    """(fiscal label, period end) in the store's own wording ("Q3 FY26", "FY2025")."""
    from .fundamentals import fy_label
    if q:
        end = quarter_end(fye, fy, q)
        return fy_label(fye, end, "Q"), end
    end = quarter_end(fye, fy, 4)
    return fy_label(fye, end, "A"), end


def doc_type(words, url="", section=QUARTERLY):
    """Type of a listed link from the words the company (or the researcher) put on it."""
    t = re.sub(r"[_\-/]+", " ", f"{words} ").lower()
    ext = ext_of(url)
    if ext in (".jpg", ".png"):
        return "image"
    if ext == ".zip" and "xbrl" not in t:
        return "archive"
    if ext in MEDIA_EXTS or re.search(r"webcast|audio|conference call|event page|replay(?! transcript)", t) and "transcript" not in t:
        return "webcast"
    if re.search(r"\b(10 ?[qk]|8 ?k|sec (filing|quarterly|annual)|xbrl|def 14a)\b", t):
        return "sec_filing"
    if "transcript" in t:
        return "transcript"
    if re.search(r"prepared remarks|cfo commentary|shareholder letter", t):
        return "remarks"
    if re.search(r"supplement|historical quarterly summary|financial data", t):
        return "supplement"
    if re.search(r"proxy", t):
        return "proxy"
    if re.search(r"annual report", t):
        return "annual_report"
    if re.search(r"presentation|slides|\bppt\b", t):
        return "presentation"
    if re.search(r"(press|earnings|news) release|results", t):
        return "release"
    s = section.lower()
    if "investor day" in s or "fixed income investor overview" in s:
        return "other" if "faq" in s and "presentation" not in t else "presentation"
    if "regulatory" in s:
        return "regulatory"
    if re.search(r"debt|capital securities|credit card|prospectus", s):
        return "prospectus"
    return "other"


def section_of(path, category):
    """Section of the company's IR pages a link was listed under."""
    c = (category or "").strip()
    if c:
        low = c.lower()
        if "annual report" in low:
            return "Annual reports"
        if "investor day" in low:
            return "Investor days"
        return c[0].upper() + c[1:]
    joined = " ".join(path).lower()
    if "annual_report" in joined:
        return "Annual reports"
    if "other_archive" in joined:
        return "Historical supplements"
    return QUARTERLY


# ---------------------------------------------------------------- raw research file -> records
def _read(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _first(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def _walk(node, ctx, path, out):
    """Collects every dict that carries a link, with what its ancestors say about period, date, page and section."""
    if isinstance(node, list):
        for item in node:
            _walk(item, ctx, path, out)
        return
    if not isinstance(node, dict):
        return
    here = dict(ctx)
    if isinstance(node.get("year"), int) and isinstance(node.get("quarter"), int):
        here["period"] = (node["year"], node["quarter"])
    else:
        for k in ("quarter", "period"):
            v = node.get(k)
            if isinstance(v, str):
                found = quarter_in(f"{v} {node.get('year') or ''}") or (
                    (int(node["year"]), int(v[1])) if re.fullmatch(r"Q[1-4]", v.strip()) and str(node.get("year") or "").isdigit() else None)
                if found:
                    here["period"] = found
                    break
    when = iso_date(_first(node, _DATE_KEYS))
    if when:
        here["published"] = when
    for k in _PAGE_KEYS:
        v = node.get(k)
        if isinstance(v, str) and v.startswith("http") and not ext_of(v) in (".pdf", ".xlsx", ".xls", ".zip"):
            here["page"] = v
            break
    if isinstance(node.get("category"), str) and any(isinstance(v, list) for v in node.values()):
        here["category"] = node["category"]
    if isinstance(node.get("title"), str) and any(isinstance(v, list) for v in node.values()):
        here["heading"] = node["title"]
        if node.get("year_section"):
            here["heading_year"] = year_in(node["year_section"])
    link = _first(node, _URL_KEYS)
    if link and link.startswith("http") and (_first(node, _NAME_KEYS) or _first(node, _TYPE_KEYS)):
        out.append((node, here, path))
    for k, v in node.items():
        # "pages_checked" lists the pages a researcher visited, not files the company lists
        if isinstance(v, (list, dict)) and k != "pages_checked":
            _walk(v, here, path + [k], out)


def _name_from_url(url):
    base = urllib.parse.unquote_plus(urllib.parse.urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1])
    base = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", base)
    return re.sub(r"[_\-+]+", " ", base).strip()


def _record(node, ctx, path, fye, default_page):
    url = _first(node, _URL_KEYS)
    name = _first(node, _NAME_KEYS) or ""
    kind_words = " ".join(str(node.get(k) or "") for k in _TYPE_KEYS)
    category = ctx.get("category") if "additional_ir_materials" in path or not node.get("category") else None
    section = section_of(path, category)
    generic = not name or bool(_GENERIC.match(name)) or name.lower().endswith((".pdf", ".xlsx", ".xls"))
    dtype = doc_type(f"{kind_words} {name} {_name_from_url(url) if generic or section != QUARTERLY else ''}", url, section)
    if section == QUARTERLY and dtype == "annual_report":
        section = "Annual reports"
    ext = ext_of(url)
    if ext is None and str(node.get("format") or node.get("file_type") or "").lower() in ("pdf", "xlsx", "xls", "zip", "jpg"):
        ext = "." + str(node.get("format") or node.get("file_type")).lower()
    period = ctx.get("period")
    own = quarter_in(name) or quarter_in(_name_from_url(url))
    fy = q = None
    if section == QUARTERLY:
        fy, q = period or own or (None, None)
        if fy is None and ctx.get("published"):
            # an earnings page without a stated quarter reports the quarter that ended before it was published
            d = dt.date.fromisoformat(ctx["published"])
            fy, q = (d.year - 1, 4) if d.month <= 3 else (d.year, (d.month - 1) // 3)
    elif section == "Annual reports" or dtype in ("annual_report", "proxy"):
        fy = year_in(name) or year_in(_name_from_url(url)) or (period[0] if period else None)
        if dtype == "proxy" and fy:
            fy -= 1  # a proxy statement is named for the meeting year and reports on the year before
    elif own:
        fy, q = own
    elif section == "Historical supplements":
        fy = ctx.get("heading_year")
    elif section == "Investor days":
        fy = year_in(category)
    else:
        fy = year_in(name) if dtype not in ("prospectus", "other") else None
    if dtype in ("annual_report",) or (dtype == "sec_filing" and re.search(r"10[\s_-]?k", f"{kind_words} {name}", re.I)):
        q = None if section != QUARTERLY else q
    label = end = None
    if fy and section in (QUARTERLY, "Annual reports", "Historical supplements") or (fy and q):
        annual = dtype in ("annual_report", "proxy") or (dtype == "sec_filing" and re.search(r"10[\s_-]?k", f"{kind_words} {name}", re.I))
        if section == "Historical supplements":
            label, end = None, None
        else:
            label, end = labels(fye, fy, None if annual else q)
    if section == QUARTERLY and fy and q:
        stamp = f"Q{q} {fy}"
    elif fy:
        stamp = str(fy)
    else:
        stamp = ""
    title = name
    if generic:
        title = _name_from_url(url) or TYPE_NAMES[dtype]
    title = re.sub(r"^(view|download)\s+", "", title, flags=re.I)
    title = re.sub(r"\s*\((pdf|xls|xlsx)\)\s*$|\s+(pdf|xbrl|excel)$", "", title, flags=re.I).strip()
    if section != QUARTERLY and len(title.split()) < 2 and _name_from_url(url):
        # "French", "Series Y": the file name says which document this is
        title = f"{_name_from_url(url)} ({title})" if title and title.lower() not in _name_from_url(url).lower() else _name_from_url(url)
    if section == QUARTERLY and dtype in TYPE_NAMES:
        kind_name = TYPE_NAMES[dtype]
        if dtype == "supplement" and ext in (".xls", ".xlsx", ".xlsm"):
            kind_name += " (Excel)"
        if dtype == "release" and (ext is None or ext in PAGE_EXTS):
            kind_name += " (web page)"
        if dtype == "sec_filing":
            form = re.search(r"\b(10[\s_-]?[QK]|8[\s_-]?K)\b", f"{kind_words} {name}", re.I)
            kind_name = (form.group(1).upper().replace(" ", "-").replace("_", "-") if form else "SEC filing")
            kind_name = re.sub(r"^(10|8)(?=[QK])", r"\1-", kind_name)
            if ext == ".zip" or "xbrl" in kind_words.lower():
                kind_name += " (XBRL)"
        title = f"{stamp} {kind_name}".strip()
    cite = _first(node, ("url_citation", "file_url_citation"))
    page = ctx.get("page") if cite in (None, url) or ext_of(cite or "") in (".pdf", ".xlsx", ".xls") else cite
    rec = {"url": url, "title": title[:200], "doc_type": dtype, "ext": ext, "section": section,
           "fiscal_year": fy, "fiscal_quarter": q if section == QUARTERLY or own else None,
           "fiscal_label": label, "period_end": end,
           "published": iso_date(node.get("filing_date")) or (ctx.get("published") if section in (QUARTERLY, "Historical supplements") else None) or iso_date(name) or iso_date(ctx.get("heading")),
           "source_page": page or default_page, "listed_as": name or None}
    if section == "Investor days":
        rec["event"] = category
    if public_url(url) != url:
        rec["fetch_url"] = public_url(url)
    return rec


def store_class(rec, siblings):
    """core: an earnings document, always stored. extended: stored when a run asks for everything the company lists
    (SEC filing copies, the web page of a release, offering and regulatory documents, archives, images). link: kept
    in the list only, with the reason."""
    t, ext = rec["doc_type"], rec["ext"]
    if t == "webcast" or ext in MEDIA_EXTS:
        return "link", "recording or event page; no document to read"
    if t == "sec_filing":
        return "extended", "the filing as the company links it; the filing itself is also read from EDGAR"
    if t in ("image", "archive"):
        return "extended", "kept as the original file, without text"
    if t in STORED_TYPES:
        if t == "release" and (ext is None or ext in PAGE_EXTS) and any(
                s is not rec and s["doc_type"] == "release" and s["ext"] == ".pdf" and s["period_end"] == rec["period_end"] for s in siblings):
            return "extended", RELEASE_PAGE
        return "core", None
    if "investor day" in rec["section"].lower():
        return "core", None
    return "extended", None


def fetch_of(rec):
    """How a listed link is retrieved. sec: a document on sec.gov, requested the way EDGAR asks. original: a file kept
    as it is, without text (archive, image). page: a web page. file: a document file."""
    host = urllib.parse.urlsplit(rec["url"]).netloc.lower()
    if host == "sec.gov" or host.endswith(".sec.gov"):
        return "sec"
    if rec["ext"] in ORIGINAL_EXTS:
        return "original"
    if rec["ext"] in PAGE_EXTS or (rec["ext"] is None and rec["doc_type"] == "release"):
        return "page"
    return "file"


def _merge(a, b):
    """Two listings of the same file: keep every field either one states."""
    out = dict(a)
    for k, v in b.items():
        if out.get(k) in (None, "") and v not in (None, ""):
            out[k] = v
    return out


def build_ticker(ticker, raw_files, meta=None):
    """Canonical source list of one ticker from its raw research files ({file name: parsed JSON})."""
    meta = meta or {}
    fye = meta.get("fye") or "1231"
    by_key, inputs = {}, []
    for name in sorted(raw_files):
        raw = raw_files[name]
        default_page = next((raw[k] for k in _PAGE_KEYS + tuple(f"{k}_citation" for k in _PAGE_KEYS)
                             if isinstance(raw.get(k), str) and raw[k].startswith("http")), None)
        found = []
        _walk(raw, {}, [], found)
        n = 0
        for node, ctx, path in found:
            rec = _record(node, ctx, path, fye, default_page)
            key = url_key(rec["url"])
            by_key[key] = _merge(by_key[key], rec) if key in by_key else rec
            n += 1
        inputs.append({"file": f"raw/{name}", "page": default_page, "retrieved": raw.get("retrieved_utc"), "links": n})
    docs = list(by_key.values())
    for rec in docs:
        rec["store"], why = store_class(rec, docs)
        if why:
            rec["why"] = why
    docs.sort(key=_order)
    return {"version": VERSION, "ticker": ticker, "company": meta.get("company"), "fye": fye, "built_from": inputs, "documents": docs}


def _order(rec):
    sec = rec["section"]
    return (SECTION_ORDER.index(sec) if sec in SECTION_ORDER else len(SECTION_ORDER), sec,
            -(rec["fiscal_year"] or 0), -(rec["fiscal_quarter"] or 0),
            TYPE_ORDER.index(rec["doc_type"]), rec["ext"] or "", url_key(rec["url"]))


def build(tickers=None, log=print):
    meta_path = os.path.join(RAW, "_meta.json")
    meta = _read(meta_path) if os.path.exists(meta_path) else {}
    groups = collections.defaultdict(dict)
    for name in sorted(os.listdir(RAW)):
        if name.startswith("_") or not name.endswith(".json") or "--" not in name:
            continue
        tk = name.split("--", 1)[0]
        if tickers and tk not in tickers:
            continue
        groups[tk][name] = _read(os.path.join(RAW, name))
    out = {}
    for tk, files in sorted(groups.items()):
        lst = build_ticker(tk, files, meta.get(tk))
        with open(os.path.join(SOURCES, f"{tk}.json"), "w", encoding="utf-8") as f:
            f.write(dumps(lst))
        out[tk] = lst
        c = collections.Counter(d["store"] for d in lst["documents"])
        log(f"{tk}: {len(lst['documents'])} links ({c['core']} to store, {c['extended']} extended, {c['link']} link only) from {len(files)} file(s)")
    return out


def dumps(lst):
    """One document per line, stable key order, so a rebuild shows up as a readable diff."""
    head = {k: v for k, v in lst.items() if k != "documents"}
    lines = ",\n".join("  " + json.dumps(d, ensure_ascii=False, separators=(", ", ": ")) for d in lst["documents"])
    return json.dumps(head, ensure_ascii=False, indent=1)[:-2] + ',\n "documents": [\n' + lines + "\n ]\n}\n"


# ---------------------------------------------------------------- using a list
_CACHE = {}


def load(ticker):
    """The ticker's source list, or None when there is none."""
    if ticker not in _CACHE:
        path = os.path.join(SOURCES, f"{ticker}.json")
        _CACHE[ticker] = _read(path) if os.path.exists(path) else None
    return _CACHE[ticker]


def tickers():
    return sorted(n[:-5] for n in os.listdir(SOURCES) if n.endswith(".json"))


def storable(lst, extended=False):
    return [d for d in lst["documents"] if d["store"] == "core" or (extended and d["store"] == "extended")]


def stored_type(rec):
    """Type written to ir_documents for a stored file: the store's own vocabulary."""
    return rec["doc_type"] if rec["doc_type"] in KEPT_TYPES else "other"


def queue_item(rec):
    """Fields of a documents-queue entry for a listed file."""
    fetch, ext = fetch_of(rec), rec["ext"]
    if fetch == "sec":
        # EDGAR documents are .htm, or .txt for filings before 2002; a link to the filing's folder answers with its index page
        hint = ".txt" if urllib.parse.urlsplit(rec["url"]).path.lower().endswith(".txt") else ".html" if ext is None or ext in PAGE_EXTS else ext
    elif fetch == "page":
        hint = ".html"
    else:
        hint = ext or ".pdf?"
    item = {"url": rec["url"], "title": rec["title"], "source": "sec" if fetch == "sec" else "web", "source_page": rec.get("source_page"),
            "ext_hint": hint, "kind_hint": stored_type(rec), "published": rec.get("published"), "fiscal_label": rec.get("fiscal_label"),
            "period_end": rec.get("period_end"), "official": VERSION}
    if fetch == "original":
        item["original_only"] = True
    if rec.get("fetch_url"):
        item["fetch_url"] = rec["fetch_url"]
    return item


def corrections(lst, rows):
    """Field changes that bring stored documents in line with the list: {document id: {field: listed value}}.
    Type and period come from the list; a release date and a title are only filled in, never replaced."""
    by_key = {url_key(d["url"]): d for d in lst["documents"]}
    out = {}
    for row in rows:
        rec = by_key.get(url_key(row.get("url")))
        # A document on sec.gov keeps what the EDGAR collector wrote: it types and dates exhibits by the filing.
        if not rec or rec["store"] == "link" or fetch_of(rec) == "sec":
            continue
        want = {"doc_type": stored_type(rec)}
        if rec.get("period_end") or rec["section"] != QUARTERLY:
            want["fiscal_label"], want["period_end"] = rec.get("fiscal_label"), rec.get("period_end")
        if rec.get("source_page") and not row.get("source_page"):
            want["source_page"] = rec["source_page"]
        if rec.get("published") and not row.get("published"):
            want["published"] = rec["published"]
        if not (row.get("title") or "").strip():
            want["title"] = rec["title"]
        diff = {k: v for k, v in want.items() if row.get(k) != v}
        if diff:
            out[row["id"]] = diff
    return out


def coverage(lst, rows, extended=False, same=None):
    """What of the list is stored. Per listed file: the stored document under the same address, if any, or the stored
    document that holds the same bytes under another address (same: {url_key of the listed file: document id}, as the
    collector records it). Per quarterly slot (period, type) without one: whether another stored copy covers it."""
    by_key, ids = {}, set()
    for row in rows:
        by_key.setdefault(url_key(row.get("url")), row)
        ids.add(row.get("id"))
    slots = collections.defaultdict(list)
    for row in rows:
        if row.get("period_end") and row.get("doc_type"):
            slots[(row["period_end"], row["doc_type"])].append(row)
    files, counts = [], collections.Counter()
    for rec in lst["documents"]:
        key = url_key(rec["url"])
        row = by_key.get(key)
        held = None if row else (same or {}).get(key)
        want = rec["store"] == "core" or (extended and rec["store"] == "extended")
        if row:
            state = "stored"
        elif held and held in ids:
            # A company can serve one file at several addresses; it is stored once and every listing of it opens that copy.
            files.append({**_brief(rec), "state": "stored", "id": held, "same_file": True})
            counts["stored"] += 1
            counts["same_file"] += 1
            continue
        elif not want:
            state = "link" if rec["store"] == "link" else "not_collected"
        else:
            # Only an earnings document can be stood in for by another copy: the SEC exhibit of the same release, or
            # the release file for the web page of that release.
            other = [r for r in slots.get((rec.get("period_end"), stored_type(rec)), []) if url_key(r.get("url")) != key]
            covered = rec["section"] == QUARTERLY and (rec["store"] == "core" or rec.get("why") == RELEASE_PAGE)
            state = "copy" if other and covered else "missing"
            if state == "copy":
                files.append({**_brief(rec), "state": state, "copy_id": other[0]["id"]})
                counts[state] += 1
                continue
        counts[state] += 1
        files.append({**_brief(rec), "state": state, **({"id": row["id"]} if row else {})})
    return {"listed": len(lst["documents"]), "storable": sum(1 for d in lst["documents"] if d["store"] == "core"),
            "stored": counts["stored"], "same_file": counts["same_file"], "copy": counts["copy"], "missing": counts["missing"],
            "link": counts["link"], "not_collected": counts["not_collected"], "files": files}


def _brief(rec):
    return {k: rec.get(k) for k in ("url", "title", "doc_type", "ext", "section", "fiscal_year", "fiscal_quarter", "fiscal_label",
                                    "period_end", "published", "source_page", "store")}


def tree(files):
    """Listed files as section -> fiscal year -> quarter (0 for the year itself) -> files, in display order."""
    out = collections.OrderedDict()
    for f in files:
        year = out.setdefault(f["section"], collections.OrderedDict()).setdefault(f.get("fiscal_year") or 0, collections.OrderedDict())
        year.setdefault(f.get("fiscal_quarter") or 0, []).append(f)
    return out


# ---------------------------------------------------------------- command line
PUBLIC_INDEX = os.environ.get("FU_PUBLIC_DATA") or "https://apxfeteebnspwyajlxfh.supabase.co/storage/v1/object/public/public-data"


_PUBLISHED = {}


def _published(ticker):
    import urllib.request
    if ticker not in _PUBLISHED:
        with urllib.request.urlopen(f"{PUBLIC_INDEX}/ir/{ticker}.json", timeout=60) as r:
            _PUBLISHED[ticker] = json.loads(r.read())
    return _PUBLISHED[ticker]


def _published_rows(ticker):
    return _published(ticker).get("documents") or []


def _published_same(ticker):
    """Listed files the last run found stored under another address, as its index records them."""
    files = (_published(ticker).get("official") or {}).get("files") or []
    return {url_key(f["url"]): f["id"] for f in files if f.get("same_file") and f.get("id")}


def report(names, log=print, rows_for=_published_rows, same_for=None):
    out = {}
    if same_for is None:
        same_for = _published_same if rows_for is _published_rows else (lambda tk: {})
    for tk in names or tickers():
        lst = load(tk)
        if not lst:
            log(f"{tk}: no source list")
            continue
        rows = rows_for(tk)
        extended = os.environ.get("FU_IR_EXTENDED") == "1"
        cov = coverage(lst, rows, extended=extended, same=same_for(tk))
        fixes = corrections(lst, rows)
        out[tk] = {"coverage": cov, "corrections": fixes, "documents": len(rows)}
        wanted = len(storable(lst, extended=extended))
        other = f" ({cov['same_file']} under another address)" if cov["same_file"] else ""
        log(f"{tk}: {cov['listed']} listed, {wanted} to store{' (everything listed)' if extended else ''}: {cov['stored']} stored{other}, "
            f"{cov['copy']} covered by another copy, {cov['missing']} missing; {cov['link']} link only, {cov['not_collected']} extended not collected; "
            f"{len(fixes)} stored documents need a field corrected; index holds {len(rows)} documents")
    return out


def check(names, log=print, pause=0.25):
    """Requests each storable link once (first bytes only). Returns {ticker: [(url, problem)]}."""
    import time
    import urllib.request
    bad = {}
    for tk in names or tickers():
        lst = load(tk)
        if not lst:
            continue
        failures = []
        for rec in storable(lst):
            req = urllib.request.Request(rec.get("fetch_url") or rec["url"],
                                         headers={"User-Agent": "Mozilla/5.0 (compatible; FiledUnder/1.0; +https://filedunder.ai)", "Range": "bytes=0-1023"})
            try:
                with urllib.request.urlopen(req, timeout=40) as r:
                    head = r.read(1024)
                    ctype = (r.headers.get("Content-Type") or "").split(";")[0]
                if rec["ext"] == ".pdf" and not head.startswith(b"%PDF") and b"%PDF" not in head:
                    failures.append((rec["url"], f"not a PDF ({ctype})"))
            except Exception as e:  # noqa: BLE001 - every failure is reported, none is fatal
                failures.append((rec["url"], str(e)[:120]))
            time.sleep(pause)
        bad[tk] = failures
        log(f"{tk}: {len(storable(lst)) - len(failures)} of {len(storable(lst))} storable links answered" + (f"; {len(failures)} failed" if failures else ""))
    return bad


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd, names = (argv[0] if argv else "report"), [a.upper() for a in argv[1:]]
    if cmd == "build":
        build(names or None)
    elif cmd == "report":
        report(names)
    elif cmd == "check":
        for tk, failures in check(names).items():
            for url, problem in failures:
                print(f"  {tk} {problem}: {url}")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
