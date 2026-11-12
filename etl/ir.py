"""Investor relations documents: find each company's IR site, list its result documents (releases, presentations,
supplementary financial data, prepared remarks, transcripts), download them into the `ir` storage bucket, and extract
page/sheet text so values can be cited and highlighted at the exact page or cell."""
import copy
import csv
import datetime as dt
import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

from .common import AggregateLease, HTTPError, SEC_UA, Supabase, dedupe_conflict_rows, now_iso, request, sec_throttle
from . import ir_official

DOC_EXT = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".pptx", ".ppt", ".docx", ".doc")
RESULT_HINT = re.compile(r"(quarter|earning|result|financial-?(info|data|result|report)|supplement|presentation|press-?release|news|events|reports?)", re.I)
DOC_HINT = re.compile(r"(earn|result|quarter|q[1-4]|fy\d|supplement|financial|fin[-_ ]?data|presentation|slides|deck|release|press|remarks|transcript|metrics|kpi|fact[-_ ]?sheet|annual|10-?k|10-?q|investor|ir[-_])", re.I)
NOISE = re.compile(r"(securities and exchange commission|form 8-?k|form 10|sec filing|10-?q[-_ ]|10-?k[-_ ]|form[-_ ]?10|proxy|governance|charter|code[-_ ]of|ethic|esg|sustainab|csr|climate|tcfd|diversity|policy|bylaws|guideline|privacy|cookie|terms|brochure|recruit|career|dividend[-_]?reinvest|tax[-_]?(form|basis)|8937|w-?9|donation|political|esop)", re.I)
UA = "Mozilla/5.0 (compatible; FiledUnder/1.0; +https://filedunder.ai)"


def tavily(query, max_results=8, include_domains=None, deadline=None):
    body = {"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": max_results, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains
    return json.loads(request("https://api.tavily.com/search", method="POST", data=body, timeout=_timeout(deadline, 60), retries=1)).get("results", [])


def firecrawl_map(url, search=None, limit=400, deadline=None):
    body = {"url": url, "limit": limit, "includeSubdomains": True}
    if search:
        body["search"] = search
    out = request("https://api.firecrawl.dev/v2/map", method="POST", data=body,
                  headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"}, timeout=_timeout(deadline, 90), retries=1)
    d = json.loads(out)
    if d.get("success") is False or not isinstance(d.get("links", (d.get("data") or {}).get("links")), list):
        raise ValueError("Invalid Firecrawl map response")
    links = d.get("links") or (d.get("data") or {}).get("links") or []
    return [l if isinstance(l, dict) else {"url": l} for l in links]


NON_IR_LABELS = {"image", "images", "img", "cdn", "static", "assets", "media", "files", "content", "s3", "shop",
                 "store", "support", "help", "docs", "developer", "developers", "blog", "careers", "jobs", "community",
                 "status", "login", "app", "go", "events", "learn", "academy", "partners"}


def _site_tier(host, path, ticker="", brand=()):
    """0: an investor site (ir./investor. host, "investor" in the domain, an /investors path or a ticker domain);
    1: a company's own subdomain (mtg.mgic.com); 2: the www/apex marketing site, used only when nothing better
    turned up; None: asset, store, support and app hosts, never an investor site."""
    labels = host.split(":")[0].split(".")
    symbol = re.sub(r"[^a-z0-9]", "", ticker.lower())
    # A www/apex domain named after a ticker that is not the company's name (intc.com, cmcsa.com) is the IR site.
    ticker_domain = (len(symbol) >= 3 and len(labels) in (2, 3) and labels[-2] == symbol and labels[0] in ("www", symbol)
                     and not any(w.startswith(symbol) for w in brand))
    if (re.search(r"(^|\.)(investors?|ir|investor-relations)\.", host) or "investor" in host
            or re.search(r"/(investors?|investor-relations|ir)(/|$)", path, re.I) or ticker_domain):
        return 0
    if labels[0] in NON_IR_LABELS:
        return None
    apex = len(labels) <= 2 or (len(labels) == 3 and len(labels[-1]) == 2 and len(labels[-2]) <= 3)
    return 2 if apex or labels[0] == "www" else 1


def ir_candidates(name, ticker, deadline=None):
    """Searches for the company's investor relations site. Returns root URLs whose host names the company or ticker,
    investor sites first, then company subdomains, then the main site; within a tier by search evidence."""
    res = tavily(f"{name} ({ticker}) investor relations quarterly results", max_results=10, deadline=deadline)
    res += tavily(f"{name} investor relations website", max_results=8, deadline=deadline)
    score, tier = {}, {}
    for i, r in enumerate(res):
        u = urllib.parse.urlparse(r["url"])
        host = u.netloc.lower()
        if any(x in host for x in ("quartr", "daloopa", "stocktitan", "tavily", "q4cdn", "annualreports", "last10k", "craft.co", "companiesmarketcap", "ycharts",
                                   "digrin", "dividend", "theglobeandmail", "investorplace", "benzinga", "gurufocus", "wallstreetzen", "koyfin", "alphaspread", "sec.gov", "nasdaq.com", "nyse.com", "yahoo", "seekingalpha", "marketwatch", "reuters", "bloomberg", "wsj", "cnbc",
                                   "fool.com", "zacks", "macrotrends", "stockanalysis", "wikipedia", "linkedin", "businesswire", "prnewswire", "globenewswire",
                                   "morningstar", "investing.com", "barrons", "tipranks", "marketbeat", "simplywall", "gurufocus", "finviz", "signals.ai")):
            continue
        brand = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in ("inc", "corp", "the", "and", "company", "group", "holdings", "international")]
        level = _site_tier(host, u.path, ticker, brand)
        if level is None:
            continue
        s = 10 - i
        if ticker.lower().replace("-", "") in host.replace("-", "") or any(w in host.replace("-", "") for w in brand[:2]):
            s += 25
        else:
            continue  # a host that names neither the company nor its ticker is a third party (aggregator, data vendor)
        if level == 0:
            s += 15
        root = f"{u.scheme}://{host}"
        m = re.search(r"^(.*?/(?:investors?|investor-relations|ir))(?:/|$)", u.path, re.I)
        if m and not re.search(r"(^|\.)(investors?|ir)\.", host):
            root += m.group(1) + ("/" if u.path[len(m.group(1)):].startswith("/") else "")
        score[root] = score.get(root, 0) + s
        tier[root] = min(tier.get(root, level), level)
    ranked = sorted(score, key=lambda k: (tier[k], -score[k]))
    return [r for r in ranked if score[r] > 0]


def _ext(url):
    path = urllib.parse.urlparse(url).path.lower()
    for e in DOC_EXT:
        if path.endswith(e):
            return e
    if "/files/doc_financials/" in path or "doc_downloads" in path or "/static-files/" in path:
        return ".pdf?"
    return None


def classify(url, title=""):
    t = f"{urllib.parse.unquote(urllib.parse.urlparse(url).path)} {(title or '').split('||')[0]}".lower()
    if re.search(r"transcript", t):
        return "transcript"
    if re.search(r"\.(xlsx|xlsm|xls|csv)$", urllib.parse.urlparse(url).path.lower()) or re.search(r"financial[-_ ]statements", t):
        return "supplement"
    if re.search(r"prepared[-_ ]?remarks|cfo[-_ ]?commentary|commentary|shareholder[-_ ]?letter|letter[-_ ]?to[-_ ]?shareholders", t):
        return "remarks"
    if re.search(r"supplement|fin(ancial)?[-_ ]?(data|tables|supplement|summary)|data[-_ ]?(book|sheet|pack)|fact[-_ ]?sheet|metrics|kpi|trended|historical", t):
        return "supplement"
    if re.search(r"present|slides|deck|webcast[-_ ]?slides|earnings[-_ ]?call[-_ ]?pres", t):
        return "presentation"
    if re.search(r"release|press|earnings|results|news", t):
        return "release"
    if re.search(r"annual[-_ ]?report|10-?k", t):
        return "annual_report"
    return "other"


_Q = [
    (re.compile(r"\b(?:q|quarter[-_ ]?)([1-4])[-_ ]?(?:fy|f)?['’]?((?:20)?\d{2})\b", re.I), lambda m: (int(m.group(1)), m.group(2))),
    (re.compile(r"\b(?:fy|f)?((?:20)?\d{2})[-_ ]?q([1-4])\b", re.I), lambda m: (int(m.group(2)), m.group(1))),
    (re.compile(r"\b(first|second|third|fourth)[-_ ]quarter[-_ ](?:fiscal[-_ ])?(?:year[-_ ])?((?:20)\d{2})", re.I),
     lambda m: ({"first": 1, "second": 2, "third": 3, "fourth": 4}[m.group(1).lower()], m.group(2))),
    (re.compile(r"\b([1-4])q[-_ ]?((?:20)?\d{2})\b", re.I), lambda m: (int(m.group(1)), m.group(2))),
]


def period_hint(url, title=""):
    t = f"{urllib.parse.unquote(url)} {title}"
    for rx, fn in _Q:
        m = rx.search(t)
        if m:
            q, y = fn(m)
            y = int(y)
            y = y + 2000 if y < 100 else y
            if 2005 <= y <= dt.date.today().year + 1:
                return f"Q{q} FY{str(y)[2:]}", y
    m = re.search(r"\b(?:fy|fiscal[-_ ]?(?:year)?)[-_ ]?((?:20)?\d{2})\b", t, re.I)
    if m:
        y = int(m.group(1))
        y = y + 2000 if y < 100 else y
        return f"FY{y}", y
    m = re.search(r"(20[12]\d)", t)
    return (None, int(m.group(1))) if m else (None, None)


def anchors(html, base):
    """(absolute url, descriptive text) for each link: anchor text plus the nearest row/heading context."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.find_all("a", href=True):
        u = urllib.parse.urljoin(base, a["href"].strip())
        t = " ".join(filter(None, [a.get_text(" ", strip=True), a.get("aria-label"), a.get("title")]))
        ctx = ""
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            txt = node.get_text(" ", strip=True)
            if 8 < len(txt) < 220:
                ctx = txt
            if len(txt) >= 220:
                break
        head = a.find_previous(["h1", "h2", "h3", "h4", "h5"])
        hx = head.get_text(" ", strip=True)[:120] if head else ""
        out.append((u, re.sub(r"\s+", " ", f"{t} || {ctx} {hx}").strip()[:400]))
    return out


def keep_doc(url, title, since_year):
    t = f"{urllib.parse.unquote(url)} {title}"
    if NOISE.search(t):
        return False
    if not DOC_HINT.search(t):
        return False
    label, year = period_hint(url, title)
    return year is None or year >= since_year


BINARY_EXT = (".pdf", ".xls", ".xlsx", ".xlsm", ".csv", ".ppt", ".pptx", ".doc", ".docx", ".zip", ".mp3", ".mp4", ".jpg", ".png")


def download(url, deadline=None):
    """Original bytes of a linked document. Some IR hosts refuse plain requests from datacenter IPs (403/429/503)
    while the same public page reads fine through Firecrawl, which the site map/scrape already uses. For HTML pages
    the fallback is Firecrawl's copy of the page; binary originals have no fallback (a rendered copy is not the file)."""
    try:
        return request(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=_timeout(deadline, 90), retries=1, raw=True)
    except HTTPError as e:
        path = urllib.parse.urlparse(url).path.lower()
        if e.code not in (401, 403, 406, 429, 503) or path.endswith(BINARY_EXT) or not os.environ.get("FIRECRAWL_API_KEY"):
            raise
        try:
            page = _scrape(url, deadline=deadline)
        except Exception:
            raise e
        html = page.get("rawHtml") or page.get("html") or ""
        if not html.lstrip()[:100].lower().startswith(("<!doctype html", "<html")):
            html = "<!doctype html>\n<html>" + html + "</html>"
        return html.encode("utf-8")


TEXT_COPY_PREFIX = "[Text-only copy via Firecrawl; original file not retrievable] "


def text_copy(url, deadline=None):
    """When a linked PDF cannot be downloaded (the host refuses datacenter IPs or answers with an HTML challenge),
    Firecrawl's PDF parser can still return its text. That text is stored as an HTML document whose title says it is
    a text-only copy, never as the original file. Returns HTML bytes or None."""
    if not os.environ.get("FIRECRAWL_API_KEY"):
        return None
    timeout = _timeout(deadline, 90)
    try:
        data = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST",
            data={"url": url, "formats": ["markdown"], "parsers": ["pdf"], "onlyMainContent": False, "timeout": max(1, int(timeout * 900))},
            headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"}, timeout=timeout, retries=1))
    except Exception:
        return None
    md = ((data or {}).get("data") or {}).get("markdown") or ""
    ctype = (((data or {}).get("data") or {}).get("metadata") or {}).get("contentType") or ""
    if len(md.strip()) < 400 or ("pdf" not in ctype.lower() and not url.lower().split("?")[0].endswith(".pdf")):
        return None
    import html as _html
    body = "<!doctype html>\n<html><head><title>Text-only copy</title></head><body><pre>" + _html.escape(md) + "</pre></body></html>"
    return body.encode("utf-8")


def sniff_ext(body, url):
    if body[:5] == b"%PDF-":
        return ".pdf"
    if body[:2] == b"PK":
        low = urllib.parse.urlparse(url).path.lower()
        for e in (".xlsx", ".xlsm", ".pptx", ".docx"):
            if low.endswith(e):
                return e
        head = body[:4000]
        if b"xl/" in head:
            return ".xlsx"
        if b"ppt/" in head:
            return ".pptx"
        if b"word/" in head:
            return ".docx"
        return ".zip"
    if body[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        low = urllib.parse.urlparse(url).path.lower()
        return ".xls" if low.endswith(".xls") else ".doc" if low.endswith(".doc") else ".ppt" if low.endswith(".ppt") else ".ole"
    if body.lstrip()[:100].lower().startswith((b"<!doctype html", b"<html")):
        return ".html"
    if urllib.parse.urlparse(url).path.lower().endswith(".csv"):
        return ".csv"
    return None


CT = {".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
      ".xls": "application/vnd.ms-excel", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".ppt": "application/vnd.ms-powerpoint",
      ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".doc": "application/msword", ".csv": "text/csv",
      ".htm": "text/plain; charset=utf-8", ".html": "text/plain; charset=utf-8"}


def _text_result(kind, content, limits=None, errors=None):
    limits, errors = limits or [], errors or []
    nonempty = (any(str(p).strip() for p in content) if kind == "pages" else
                any(v is not None and str(v).strip() for rows in content.values() for row in rows for v in row))
    status = "empty" if not nonempty else "limited" if limits or errors else "verified"
    return {"kind": kind, kind: content, "extraction": {"status": status, "limits": limits,
            "errors": errors, "nonempty": nonempty, "coordinates": "page" if kind == "pages" else "sheet,row,column"}}


WORKBOOK_MAX_SHEETS = 256
WORKBOOK_EXTRACTION_VERSION = 2
WORKBOOK_MAX_CELLS = 1_000_000
WORKBOOK_ZIP_MAX_BYTES = 128 * 1024 * 1024
WORKBOOK_ZIP_MAX_MEMBER_BYTES = 32 * 1024 * 1024
WORKBOOK_ZIP_MAX_DRAWING_BYTES = 64 * 1024 * 1024
WORKBOOK_ZIP_MAX_MEMBERS = 10000


def _workbook_zip(body, deadline=None):
    """Validate the archive and repair unambiguous Windows separators in memory only.

    Original storage bytes/hash never change. Package XML and relationship targets are
    untouched; only ZIP member names with backslashes are canonicalized.
    """
    if len(body) > WORKBOOK_ZIP_MAX_BYTES:
        raise ValueError("Workbook ZIP exceeds compressed size limit")
    with zipfile.ZipFile(io.BytesIO(body)) as source:
        entries = source.infolist()
        if len(entries) > WORKBOOK_ZIP_MAX_MEMBERS:
            raise ValueError("Workbook ZIP exceeds member count limit")
        total, seen, names, changed = 0, set(), [], False
        for entry in entries:
            name = entry.filename.replace(chr(92), "/")
            parts = name.rstrip("/").split("/")
            if (not name or name.startswith("/") or "\x00" in name or re.match(r"^[A-Za-z]:", name)
                    or any(part in ("", ".", "..") for part in parts)):
                raise ValueError("Unsafe workbook ZIP member path")
            canonical = name.rstrip("/")
            if canonical in seen:
                raise ValueError("Ambiguous workbook ZIP member names")
            seen.add(canonical)
            if entry.flag_bits & 1 or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Encrypted or symbolic-link workbook ZIP member")
            total += entry.file_size
            member_limit = (WORKBOOK_ZIP_MAX_DRAWING_BYTES if re.fullmatch(r"xl/drawings/drawing\d+\.xml", name)
                            else WORKBOOK_ZIP_MAX_MEMBER_BYTES)
            if entry.file_size > member_limit or total > WORKBOOK_ZIP_MAX_BYTES:
                raise ValueError("Workbook ZIP exceeds expanded size limit")
            names.append(name)
            changed = changed or name != entry.filename
        if not changed:
            return body
        repaired = io.BytesIO()
        with zipfile.ZipFile(repaired, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for entry, name in zip(entries, names):
                _timeout(deadline)
                member_limit = (WORKBOOK_ZIP_MAX_DRAWING_BYTES if re.fullmatch(r"xl/drawings/drawing\d+\.xml", name)
                                else WORKBOOK_ZIP_MAX_MEMBER_BYTES)
                with source.open(entry) as member:
                    data = member.read(member_limit + 1)
                if len(data) > member_limit or len(data) != entry.file_size:
                    raise ValueError("Workbook ZIP member size mismatch")
                target.writestr(name, data)
        return repaired.getvalue()


def _worksheet_data_bounds(archive, worksheet, deadline=None):
    """Bound actual OOXML values/formulas, excluding formatting-only trailing ranges.

    The pinned openpyxl read-only worksheet supplies the resolved archive member
    and shared strings; no original package member or cell is rewritten.
    """
    namespace = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
    last_row, last_column, seen = 0, 0, 0
    with archive.open(worksheet._worksheet_path) as source:
        for _, element in ET.iterparse(source, events=('end',)):
            if element.tag == namespace + 'row':
                element.clear()
                continue
            if element.tag != namespace + 'c':
                continue
            if seen % 1024 == 0:
                _timeout(deadline)
            seen += 1
            formula = element.find(namespace + 'f')
            value_node = element.find(namespace + 'v')
            value = value_node.text if value_node is not None else None
            if element.get('t') == 's' and value is not None:
                try:
                    value = worksheet._shared_strings[int(value)]
                except (ValueError, IndexError) as exc:
                    raise ValueError('Invalid workbook shared-string index') from exc
            elif element.get('t') == 'inlineStr':
                value = ''.join(n.text or '' for n in element.iter(namespace + 't'))
            if formula is not None or (value is not None and str(value).strip()):
                match = re.fullmatch(r'([A-Z]+)([1-9]\d*)', element.get('r', ''))
                if not match:
                    raise ValueError('Invalid workbook cell coordinate')
                column = 0
                for letter in match.group(1):
                    column = column * 26 + ord(letter) - 64
                row = int(match.group(2))
                if row > 1048576 or column > 16384:
                    raise ValueError('Workbook cell coordinate exceeds Excel bounds')
                last_row, last_column = max(last_row, row), max(last_column, column)
            element.clear()
    return last_row, last_column


def extract_text(body, ext, deadline=None):
    """Extract a bounded, coordinate-preserving representation; limits never imply complete text.

    'verified' checks machine-readable nonempty content, not OCR accuracy or financial correctness.
    Interior empty rows/columns retain their coordinates. Formatting-only trailing
    XLSX ranges are described in sheet metadata, without consuming the cell budget.
    """
    _timeout(deadline)
    limits, errors = [], []
    if ext == ".pdf":
        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            if len(pdf.pages) > 400:
                limits.append("pages:400")
            for i, page in enumerate(pdf.pages[:400]):
                if _expired(deadline):
                    limits.append("deadline")
                    break
                try:
                    pages.append(page.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                    if not pages[-1].strip():
                        errors.append(f"page:{i + 1}:empty; OCR may be required")
                except Exception as e:
                    pages.append("")
                    errors.append(f"page:{i + 1}:{str(e)[:120]}")
        # Some publisher PDF endpoints render a password screen as a valid PDF.
        # Match the whole known template so an article quoting its notice stays valid.
        document_text = " ".join(" ".join(pages).split())
        if re.fullmatch(r"(?:News Release Archive )?This site is not available for public viewing\. "
                        r"Password:? Submit(?: https?://\S+)?", document_text, re.I):
            raise ValueError("PDF is an access-restricted placeholder")
        return _text_result("pages", pages, limits, errors)
    if ext in (".xlsx", ".xlsm", ".xls"):
        sheets, cell_budget, budget_exhausted = {}, WORKBOOK_MAX_CELLS, False
        cell_metadata, sheet_metadata = {}, {}
        missing_formula_caches, source_cell_errors = 0, 0
        def bounded_rows(source):
            nonlocal cell_budget, budget_exhausted
            rows = []
            for row in source:
                if _expired(deadline):
                    limits.append("deadline")
                    budget_exhausted = True
                    break
                if len(row) > cell_budget:
                    limits.append(f"cells:{WORKBOOK_MAX_CELLS}")
                    budget_exhausted = True
                    break
                rows.append(row)
                cell_budget -= len(row)
            return rows
        if ext == ".xls":
            import xlrd
            book = xlrd.open_workbook(file_contents=body, on_demand=True, formatting_info=True, logfile=io.StringIO())
            try:
                if book.nsheets > WORKBOOK_MAX_SHEETS:
                    limits.append(f"sheets:{WORKBOOK_MAX_SHEETS}")
                for sheet_index in range(min(book.nsheets, WORKBOOK_MAX_SHEETS)):
                    if budget_exhausted:
                        break
                    ws = book.sheet_by_index(sheet_index)
                    if _expired(deadline):
                        limits.append("deadline")
                        break
                    cell_metadata[ws.name] = {}
                    sheet_metadata[ws.name] = {"rows": ws.nrows, "columns": ws.ncols,
                        "state": {0: "visible", 1: "hidden", 2: "veryHidden"}.get(ws.visibility, "unknown")}
                    def value(cell):
                        if cell.ctype == xlrd.XL_CELL_ERROR:
                            return xlrd.error_text_from_code.get(cell.value, "#ERROR!")
                        if cell.ctype == xlrd.XL_CELL_DATE:
                            return xlrd.xldate_as_datetime(cell.value, book.datemode)
                        if cell.ctype == xlrd.XL_CELL_BOOLEAN:
                            return bool(cell.value)
                        return cell.value
                    def source_rows():
                        nonlocal source_cell_errors
                        for i in range(ws.nrows):
                            row = []
                            for j in range(ws.ncols):
                                cell = ws.cell(i, j)
                                row.append(value(cell))
                                if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                                    fmt = book.format_map.get(book.xf_list[cell.xf_index].format_key)
                                    ref = xlrd.cellname(i, j)
                                    meta = {
                                        "data_type": cell.ctype, "number_format": fmt.format_str if fmt else "General"}
                                    if cell.ctype == xlrd.XL_CELL_ERROR:
                                        meta["source_error"] = row[-1]
                                        source_cell_errors += 1
                                        if len(errors) < 100:
                                            errors.append(f"{ws.name}!{ref}:source cell error {row[-1]}")
                                    cell_metadata[ws.name][ref] = meta
                            yield row
                    sheets[ws.name] = bounded_rows(source_rows())
            finally:
                book.release_resources()
        else:
            import openpyxl
            package = _workbook_zip(body, deadline)
            book = openpyxl.load_workbook(io.BytesIO(package), data_only=True, read_only=True)
            formulas = None
            content_archive = None
            try:
                formulas = openpyxl.load_workbook(io.BytesIO(package), data_only=False, read_only=True)
                content_archive = zipfile.ZipFile(io.BytesIO(package))
                if len(book.worksheets) > WORKBOOK_MAX_SHEETS:
                    limits.append(f"sheets:{WORKBOOK_MAX_SHEETS}")
                for ws in book.worksheets[:WORKBOOK_MAX_SHEETS]:
                    if budget_exhausted:
                        break
                    if _expired(deadline):
                        limits.append("deadline")
                        break
                    cell_metadata[ws.title] = {}
                    sheet_metadata[ws.title] = {"rows": ws.max_row, "columns": ws.max_column, "state": ws.sheet_state}
                    data_rows, data_columns = _worksheet_data_bounds(content_archive, formulas[ws.title], deadline)
                    sheet_metadata[ws.title].update(data_rows=data_rows, data_columns=data_columns)
                    if not data_rows or not data_columns:
                        sheets[ws.title] = []
                        continue
                    bounds = {"max_row": data_rows, "max_col": data_columns}
                    def source_rows():
                        nonlocal missing_formula_caches, source_cell_errors
                        for cached, source in zip(ws.iter_rows(**bounds), formulas[ws.title].iter_rows(**bounds), strict=True):
                            values = []
                            for cell, original in zip(cached, source, strict=True):
                                values.append("" if cell.value is None else cell.value)
                                if original.value is None:
                                    continue
                                meta = {"data_type": original.data_type, "number_format": original.number_format}
                                if cell.data_type == "e":
                                    meta["source_error"] = cell.value
                                    source_cell_errors += 1
                                    if len(errors) < 100:
                                        errors.append(f"{ws.title}!{original.coordinate}:source cell error {cell.value}")
                                if original.data_type == "f":
                                    meta["formula"] = original.value if isinstance(original.value, str) else getattr(original.value, "text", None)
                                    meta["cached"] = cell.value is not None
                                    if not meta["cached"]:
                                        missing_formula_caches += 1
                                        if len(errors) < 100:
                                            errors.append(f"{ws.title}!{original.coordinate}:formula cache missing")
                                cell_metadata[ws.title][original.coordinate] = meta
                            yield values
                    sheets[ws.title] = bounded_rows(source_rows())
            finally:
                book.close()
                if formulas is not None:
                    formulas.close()
                if content_archive is not None:
                    content_archive.close()
        for name, rows in sheets.items():
            sheet_metadata[name].update(extracted_rows=len(rows), extracted_columns=max(map(len, rows), default=0))
            # A row prepared just as a deadline/cell budget expired is not extracted evidence.
            cell_metadata[name] = {ref: meta for ref, meta in cell_metadata[name].items()
                                   if int(re.search(r"\d+$", ref).group()) <= len(rows)}
        if missing_formula_caches + source_cell_errors > len(errors):
            limits.append(f"cell errors:{missing_formula_caches + source_cell_errors}; first {len(errors)} listed")
        # Workbooks expose cached formula results only; no formula evaluation is attempted.
        result = _text_result("sheets", sheets, limits, errors)
        result["cell_metadata"], result["sheet_metadata"] = cell_metadata, sheet_metadata
        result["extraction"]["formula_values"] = "cached_only"
        result["extraction"]["workbook_version"] = WORKBOOK_EXTRACTION_VERSION
        result["extraction"]["formula_inspection"] = "unavailable_for_xls" if ext == ".xls" else "formula_and_cache"
        result["extraction"]["missing_formula_caches"] = missing_formula_caches if ext != ".xls" else None
        result["extraction"]["source_cell_errors"] = source_cell_errors
        return result
    if ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(body))
        pages = []
        if len(prs.slides) > 400:
            limits.append("slides:400")
        for slide in list(prs.slides)[:400]:
            if _expired(deadline):
                limits.append("deadline")
                break
            parts = []
            for sh in slide.shapes:
                if sh.has_text_frame:
                    parts.append(sh.text_frame.text)
                if getattr(sh, "has_table", False):
                    for row in sh.table.rows:
                        parts.append(" | ".join(c.text for c in row.cells))
            pages.append("\n".join(parts))
        return _text_result("pages", pages, limits)
    if ext == ".docx":
        import docx
        doc = docx.Document(io.BytesIO(body))
        text = "\n".join(p.text for p in doc.paragraphs)
        for table in doc.tables:
            for row in table.rows:
                text += "\n" + " | ".join(c.text for c in row.cells)
        result = _text_result("pages", [text])
        result["extraction"]["coordinates"] = "document; no physical page mapping"
        return result
    if ext in (".htm", ".html", ".md"):
        from bs4 import BeautifulSoup
        if ext == ".md":
            text = body.decode("utf-8", "replace")
        else:
            soup = BeautifulSoup(body, "lxml")
            article_loader = soup.select_one(".press-release-details")
            if article_loader is not None:
                article = soup.select_one(".press-release-details-body")
                article_text = article.get_text(" ", strip=True) if article is not None else ""
                if not article_text or re.search(r"unable to load press releases|generic error", article_text, re.I):
                    return _text_result("pages", [], errors=["article body unavailable; dynamic loader is not document text"])
            for tag in soup(["script", "style"]):
                tag.decompose()
            text = soup.get_text("\n", strip=True)
        return _text_result("pages", [text])
    if ext == ".csv":
        rows = []
        decoded = body.decode("utf-8-sig", "strict")
        for i, row in enumerate(csv.reader(io.StringIO(decoded, newline=""), strict=True)):
            if i >= 2000 or _expired(deadline):
                limits.append("rows:2000" if i >= 2000 else "deadline")
                break
            if len(row) > 200:
                limits.append(f"row:{i + 1}:columns:200")
            rows.append(row[:200])
        return _text_result("sheets", {"csv": rows}, limits)
    return {"kind": "pages", "pages": [], "extraction": {"status": "unsupported", "nonempty": False, "limits": [], "errors": [f"unsupported format:{ext}"]}}


def doc_id(url):
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def safe_name(url, ext):
    base = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]) or "document"
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-")[:80] or "document"
    if not base.lower().endswith(ext):
        base += ext
    return base


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


PERIOD_ENDS = {}


_NAMED_Q = [re.compile(r"(?<![a-z])q([1-4])[\s_.-]*(?:fy)?[\s_.-]*((?:19|20)?\d\d)(?!\d)", re.I),
            re.compile(r"(?<!\d)((?:19|20)\d\d)[\s_.-]*q([1-4])(?![0-9])", re.I),
            re.compile(r"(?<![a-z])(first|second|third|fourth)[\s_.-]*quarter[\s_.-]*(?:fy)?[\s_.-]*((?:19|20)\d\d)(?!\d)", re.I)]
_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4}


def named_quarter(fye, name, d, ends_known=None):
    """(label, quarter end) when a file name states its quarter ("q32024", "2026q3", "q3-fy24", "third-quarter-2024")
    and that quarter is the latest or the one before it to end before date d; None otherwise."""
    q = yy = None
    for i, rx in enumerate(_NAMED_Q):
        m = rx.search(name or "")
        if not m:
            continue
        a, b = m.group(1), m.group(2)
        if i == 0:
            q, yy = int(a), b
        elif i == 1:
            q, yy = int(b), a
        else:
            q, yy = _ORD[a.lower()], b
        break
    if not q:
        return None
    from .fundamentals import norm_fye
    if norm_fye(fye) != "1231":
        # off-calendar filers often name files by calendar quarter, which would map to the wrong fiscal quarter
        return None
    yy = int(yy) % 100
    want = f"Q{q} FY{yy:02d}"
    probe = d
    for _ in range(2):
        label, e = quarter_for_date(fye, probe, ends_known)
        if not e:
            return None
        if label == want:
            return label, e
        probe = e
    return None


def quarter_for_date(fye, d, ends_known=None):
    """(label, quarter end) of the latest fiscal quarter that ended at least three days before date d. Uses the
    company's actual period end dates when known (52/53-week years), else calendar quarter ends from the FYE."""
    from .fundamentals import fy_label, norm_fye
    fye = norm_fye(fye)
    if ends_known:
        cands = [e for e in ends_known if e <= d - dt.timedelta(days=3)]
        if cands:
            e = max(cands)
            # known period ends come from filed 10-Q/10-Ks, so the quarter an earnings release reports on is often
            # not among them yet: step forward 13 weeks at a time while another quarter has ended before date d
            _l, cal = quarter_for_date(fye, d)
            while e + dt.timedelta(days=91) <= d - dt.timedelta(days=3):
                nxt = e + dt.timedelta(days=91)
                # calendar-quarter filers end on month ends; 52/53-week filers 13 weeks after the last known end
                if cal and abs((cal - nxt).days) <= 7:
                    nxt = cal
                elif cal:
                    snap = quarter_for_date(fye, nxt + dt.timedelta(days=8))[1]
                    if snap and abs((snap - nxt).days) <= 3:
                        nxt = snap
                e = nxt
            return fy_label(fye, e.isoformat(), "Q"), e
    fm, fd = int(fye[:2]), int(fye[2:])
    ends = []
    for y in (d.year - 1, d.year, d.year + 1):
        for k in range(4):
            m = (fm - 3 * k - 1) % 12 + 1
            yy = y if m <= fm else y - 1
            day = min(fd, 28) if m != fm else fd
            try:
                ends.append(dt.date(yy, m, day if m == fm else (30 if m in (4, 6, 9, 11) else 28 if m == 2 else 31) if fd >= 28 else day))
            except ValueError:
                ends.append(dt.date(yy, m, 28))
    ends = sorted(e for e in ends if e <= d - dt.timedelta(days=5))
    if not ends:
        return None, None
    e = ends[-1]
    return fy_label(fye, e.isoformat(), "Q"), e


def date_in(text):
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2}),?\s+(20\d{2})", text or "", re.I)
    if not m:
        return None
    try:
        return dt.date(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)))
    except ValueError:
        return None


def first_page_period(text):
    """Fiscal period from the opening text of a document, when the file name had none."""
    t = (text or "")[:3000]
    label, year = period_hint("", t)
    return label, year


# Checkpoints live in the private raw bucket, never in public-data.
CHECKPOINT_VERSION = 1
CHECKPOINT_BYTES = 16 * 1024 * 1024
QUEUE_LIMIT = 20000
MAX_ATTEMPTS = 3
QUEUE_NAMES = ("submissions", "filings", "maps", "pages", "documents", "extractions")
ACTIVE = ("pending", "retry")


class DeadlineReached(RuntimeError):
    pass


def _expired(deadline):
    return deadline is not None and time.monotonic() >= deadline


def _timeout(deadline, maximum=60):
    if deadline is None:
        return maximum
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineReached("time budget exhausted")
    return min(maximum, remaining)


def _sec_request(url, deadline=None, raw=False):
    _timeout(deadline)
    sec_throttle()
    return request(url, headers={"User-Agent": SEC_UA}, timeout=_timeout(deadline, 40), retries=1, raw=raw)


def _sec_json(url, deadline=None):
    return json.loads(_sec_request(url, deadline))


def _scrape(url, deadline=None):
    timeout = _timeout(deadline, 60)
    data = json.loads(request("https://api.firecrawl.dev/v2/scrape", method="POST",
        data={"url": url, "formats": ["html"], "onlyMainContent": False, "timeout": max(1, int(timeout * 900))},
        headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"}, timeout=timeout, retries=1))
    if not data.get("success") or not isinstance(data.get("data"), dict) or not data["data"].get("html"):
        raise ValueError("Firecrawl returned no archive HTML")
    return data["data"]


class IRClient(Supabase):
    """IR-scoped Supabase adapter: one attempt, remaining-time timeout per I/O.

    No global request monkeypatching and no change to unrelated ETL clients. The
    absolute I/O deadline includes persistence and lease acquisition/renewal.
    """
    def __init__(self, sb, deadline):
        self.__dict__.update(sb.__dict__)
        previous = getattr(sb, "io_deadline", None)
        self.io_deadline = min(previous, deadline) if previous is not None and deadline is not None else previous if deadline is None else deadline

    def _call(self, url, **kwargs):
        kwargs["timeout"] = _timeout(self.io_deadline, kwargs.get("timeout", 30))
        kwargs["retries"] = 1
        return request(url, **kwargs)

    def rpc(self, fn, args):
        return json.loads(self._call(f"{self.url}/rest/v1/rpc/{fn}", method="POST", data=args, headers=self._h()) or "null")

    def select(self, table, query="select=*"):
        rows, offset = [], 0
        while True:
            page = json.loads(self._call(f"{self.url}/rest/v1/{table}?{query}", headers=self._h({"Range-Unit": "items", "Range": f"{offset}-{offset + 999}"})))
            if not isinstance(page, list):
                raise ValueError("Invalid relational IR response")
            rows.extend(page)
            if len(page) < 1000:
                return rows
            offset += 1000

    def upsert(self, table, rows, on_conflict, batch=500):
        self._check_lease()
        rows = dedupe_conflict_rows(rows, on_conflict)
        for start in range(0, len(rows), batch):
            self._check_lease()
            self._call(f"{self.url}/rest/v1/{table}?on_conflict={on_conflict}", method="POST", data=rows[start:start + batch],
                       headers=self._h({"Prefer": "resolution=merge-duplicates,return=minimal", "Content-Type": "application/json"}))
        return len(rows)

    def insert(self, table, row, returning=True):
        result = self._call(f"{self.url}/rest/v1/{table}", method="POST", data=row,
                            headers=self._h({"Prefer": "return=representation" if returning else "return=minimal"}))
        return json.loads(result) if returning and result else None

    def update(self, table, match, values):
        self._check_lease()
        query = "&".join(f"{key}=eq.{urllib.parse.quote(str(value))}" for key, value in match.items())
        self._call(f"{self.url}/rest/v1/{table}?{query}", method="PATCH", data=values, headers=self._h({"Prefer": "return=minimal"}))

    def put_object(self, bucket, path, content, content_type="application/json", cache="max-age=300"):
        self._check_lease()
        if isinstance(content, str):
            content = content.encode()
        self._call(f"{self.url}/storage/v1/object/{bucket}/{urllib.parse.quote(path)}", method="POST", data=content,
                   headers=self._h({"Content-Type": content_type, "x-upsert": "true", "cache-control": cache}))
        return len(content)

    def get_object(self, bucket, path, public=False):
        url, headers = self._storage_read_request(bucket, path, public)
        try:
            return self._call(url, headers=headers, raw=True)
        except HTTPError as error:
            if _object_missing(error):
                return None
            raise


def bounded_client(sb, deadline):
    """Copy the client's state; never attach worker deadlines or leases to shared sb."""
    return IRClient(sb, deadline) if isinstance(sb, Supabase) else copy.copy(sb)


def _object_missing(error):
    if error.code == 404:
        return True
    if error.code == 400:
        try:
            data = json.loads(error.body)
        except (ValueError, TypeError):
            data = {}
        return str(data.get("statusCode")) == "404" and data.get("error") == "not_found" and data.get("message") == "Object not found"
    return False


def _stored_object(sb, bucket, path):
    # Generic Supabase.get_object conflates HTTP 400 with absence. IRClient is strict.
    if isinstance(sb, IRClient):
        return sb.get_object(bucket, path)
    if isinstance(sb, Supabase):
        url, headers = sb._storage_read_request(bucket, path)
        try:
            return request(url, headers=headers, raw=True, retries=1, timeout=30)
        except HTTPError as error:
            if _object_missing(error):
                return None
            raise
    return sb.get_object(bucket, path)

def _read_checkpoint(sb, ticker):
    raw = _stored_object(sb, "raw", f"ir/checkpoints/{ticker}.json")
    if raw is None:
        return {"version": CHECKPOINT_VERSION, "ticker": ticker, "scope": {}, "queues": {q: {} for q in QUEUE_NAMES},
                "outcomes": {}, "limits": [], "sec_checked_at": None, "web_checked_at": None, "sec_polled_at": None, "web_polled_at": None}
    if not raw or len(raw) > CHECKPOINT_BYTES:
        raise ValueError("Empty or oversized IR checkpoint; refusing replacement")
    state = json.loads(raw)
    if (not isinstance(state, dict) or state.get("version") != CHECKPOINT_VERSION or state.get("ticker") != ticker
            or not isinstance(state.get("scope"), dict) or not isinstance(state.get("outcomes"), dict)
            or not isinstance(state.get("limits"), list) or not isinstance(state.get("queues"), dict)):
        raise ValueError("Invalid IR checkpoint; refusing replacement")
    scope = state["scope"]
    if scope and (not isinstance(scope.get("since_year"), int) or not isinstance(scope.get("web"), bool)
                  or not isinstance(scope.get("sec"), bool) or scope.get("history") not in ("recent", "available_archive")):
        raise ValueError("Invalid IR checkpoint scope")
    if scope:
        dt.date.fromisoformat(scope.get("sec_since", ""))
    for quality in state["outcomes"].values():
        if not isinstance(quality, dict) or quality.get("status") not in ("verified", "limited", "empty", "unsupported", "failed", "unverified"):
            raise ValueError("Invalid IR extraction outcome")
    for name in QUEUE_NAMES:
        queue = state["queues"].get(name)
        if not isinstance(queue, dict) or len(queue) > QUEUE_LIMIT:
            raise ValueError("Invalid IR checkpoint queue")
        for item in queue.values():
            if (not isinstance(item, dict) or item.get("status") not in (*ACTIVE, "done", "blocked")
                    or not isinstance(item.get("attempts"), int) or item["attempts"] < 0):
                raise ValueError("Invalid IR checkpoint task")
            required = {"submissions": ("url", "cik", "root"), "filings": ("filing", "cik"), "maps": ("url", "search"),
                        "pages": ("url",), "documents": ("url", "source"), "extractions": ("id",)}[name]
            if any(key not in item for key in required):
                raise ValueError("Incomplete IR checkpoint task")
            if name in ("submissions", "pages", "documents") and not _url(item["url"]):
                raise ValueError("Invalid IR checkpoint task URL")
            if name == "documents" and item["source"] not in ("sec", "web"):
                raise ValueError("Invalid IR checkpoint document source")
            if name == "filings" and (not isinstance(item["filing"], dict) or any(k not in item["filing"] for k in ("a", "d", "f"))):
                raise ValueError("Invalid IR checkpoint filing")
    return state


def _save_checkpoint(sb, state):
    state["updated_at"] = now_iso()
    body = json.dumps(state, separators=(",", ":"), default=str)
    if len(body.encode()) > CHECKPOINT_BYTES:
        raise ValueError("IR checkpoint size limit; refusing to replace previous progress")
    sb.put_object("raw", f"ir/checkpoints/{state['ticker']}.json", body, "application/json", "no-store")


def _enqueue(state, queue, key, **values):
    tasks = state["queues"][queue]
    if key in tasks:
        return tasks[key]
    if len(tasks) >= QUEUE_LIMIT:
        marker = f"queue:{queue}:capacity:{QUEUE_LIMIT}; enumeration incomplete"
        if marker not in state["limits"]:
            state["limits"].append(marker)
        return None
    tasks[key] = {"status": "pending", "attempts": 0, **values}
    return tasks[key]


REQUEUE_VERSION = 1


def requeue_blocked(state):
    """Documents blocked before the Firecrawl fallbacks existed (403 on HTML pages, HTML answers or refusals for
    PDFs) get one more attempt, once per REQUEUE_VERSION."""
    if state.get("requeue_version", 0) >= REQUEUE_VERSION:
        return 0
    n = 0
    for url, item in state["queues"]["documents"].items():
        if item.get("status") != "blocked" or item.get("source") != "web":
            continue
        err = str(item.get("error") or "")
        path = urllib.parse.urlparse(url).path.lower()
        html_page = item.get("ext_hint") in (".html", ".htm") or not path.endswith(BINARY_EXT)
        refused = re.match(r"HTTP (401|403|406|429|503)\b", err)
        if (refused and (html_page or item.get("ext_hint") in (".pdf", ".pdf?"))) or (
                err.startswith("Downloaded HTML instead") and item.get("ext_hint") in (".pdf", ".pdf?")):
            item["status"], item["attempts"] = "retry", MAX_ATTEMPTS - 1
            n += 1
    state["requeue_version"] = REQUEUE_VERSION
    return n


def _failure(item, exc, stats, category):
    item["attempts"] += 1
    item["status"] = "blocked" if item["attempts"] >= MAX_ATTEMPTS else "retry"
    item["error"] = str(exc)[:300]
    item["attempted_at"] = now_iso()
    stats["failed"] += 1
    stats[category + "_failed"] += 1


def _complete(item):
    item["status"] = "done"
    item.pop("error", None)
    item["completed_at"] = now_iso()


def _url(url):
    # Query IDs and trailing slashes can be semantically significant.
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _web_doc(url):
    return _ext(url) or (".html" if re.search(r"press-release-details|news-release-details|news-details/.+", url, re.I) else None)


def _add_links(state, links, root, source_page, since_year):
    root_host = urllib.parse.urlparse(root).netloc.lower()
    for link in links:
        url, title = _url(link.get("url") or ""), link.get("title") or ""
        if not url:
            continue
        if _web_doc(url):
            if keep_doc(url, title, since_year) and classify(url, title) != "other":
                _enqueue(state, "documents", url, url=url, title=title[:400], source="web", source_page=source_page,
                         ext_hint=_web_doc(url))
        elif urllib.parse.urlparse(url).netloc.lower() == root_host and (RESULT_HINT.search(url) or RESULT_HINT.search(title)):
            _enqueue(state, "pages", url, url=url)


def _enumerate_sec(state, ticker, cik, deadline, stats, sb, page_budget):
    from . import sec
    from .universe import PREDECESSORS
    scope = state["scope"]
    if not state.get("sec_checked_at"):
        for registrant, until in [(cik, None)] + list(PREDECESSORS.get(ticker, [])):
            url = f"https://data.sec.gov/submissions/CIK{int(registrant):010d}.json"
            _enqueue(state, "submissions", url, url=url, cik=registrant, until=until, root=True)
    # Poll current submissions first, then fresh filing indices. Historical
    # submission pages retain a separate share of the discovery work cap.
    visited = set()
    def visit_submissions(root_only):
        while page_budget[0] > 0 and not _expired(deadline):
            pending = [(k, v) for k, v in state["queues"]["submissions"].items() if v["status"] in ACTIVE and k not in visited and bool(v.get("root")) == root_only]
            if not pending:
                break
            key, item = pending[0]; visited.add(key); page_budget[0] -= 1
            try:
                data = _sec_json(item["url"], deadline=deadline)
                recent = (data.get("filings") or {}).get("recent") if item.get("root") else data
                required = ("accessionNumber", "filingDate", "form", "primaryDocument", "items")
                if not isinstance(recent, dict) or any(not isinstance(recent.get(k), list) for k in required):
                    raise ValueError("Incomplete SEC submissions response")
                if len({len(recent[k]) for k in required}) != 1:
                    raise ValueError("Mismatched SEC submissions columns")
                for filing in sec.filing_list({"filings": {"recent": recent}}):
                    if filing["f"] not in ("8-K", "8-K/A") or filing["d"] < scope["sec_since"] or (item.get("until") and filing["d"] > item["until"]):
                        continue
                    if "2.02" not in (filing.get("i") or "") and "7.01" not in (filing.get("i") or ""):
                        continue
                    _enqueue(state, "filings", f"{item['cik']}:{filing['a']}", filing=filing, cik=item["cik"])
                if item.get("root"):
                    files = (data.get("filings") or {}).get("files")
                    if not isinstance(files, list):
                        raise ValueError("Missing SEC archive file listing")
                    for archive in files:
                        if not archive.get("name"):
                            raise ValueError("Invalid SEC archive page")
                        if archive.get("filingTo", "9999") >= scope["sec_since"]:
                            url = f"https://data.sec.gov/submissions/{archive['name']}"
                            _enqueue(state, "submissions", url, url=url, cik=item["cik"], until=item.get("until"), root=False)
                _complete(item)
                if item.get("root"):
                    state["sec_polled_at"] = item["completed_at"]
            except DeadlineReached:
                break
            except Exception as e:
                _failure(item, e, stats, "discovery")
            _save_checkpoint(sb, state)
    visit_submissions(True)
    archives_pending = any(not item.get("root") and item["status"] in ACTIVE for item in state["queues"]["submissions"].values())
    index_budget = max(1, page_budget[0] // 2) if archives_pending else page_budget[0]
    for key, item in sorted(state["queues"]["filings"].items(), key=lambda pair: pair[1]["filing"]["d"], reverse=True):
        if item["status"] not in ACTIVE or page_budget[0] <= 0 or index_budget <= 0 or _expired(deadline):
            continue
        page_budget[0] -= 1
        index_budget -= 1
        f = item["filing"]
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(item['cik'])}/{f['a'].replace('-', '')}/"
        try:
            page = _sec_request(folder + f"{f['a']}-index.htm", deadline=deadline)
            names = _exhibit_names(page, f)
            for name in names:
                url = urllib.parse.urljoin(folder, name)
                low = urllib.parse.urlparse(url).path.lower()
                if not low.endswith(DOC_EXT + (".htm", ".html")):
                    # Retain unsupported exhibits as unresolved rather than silently treating an index as complete.
                    _enqueue(state, "documents", url, url=url, title=f"8-K {f['d']} {name}", source="sec", source_page=folder,
                             published=f["d"], ext_hint=None)
                    continue
                items = f.get("i") or ""
                hint = "release" if "2.02" in items and (re.search(r"99[-_.]?1\b|99\.?1[^0-9]|ex991", low) or name == f.get("p")) else None
                _enqueue(state, "documents", url, url=url, title=f"8-K {f['d']} {name} (Items {items})", source="sec", source_page=folder,
                         published=f["d"], ref_date=f["d"] if "2.02" in items else None, kind_hint=hint, ext_hint=".html" if low.endswith((".htm", ".html")) else _ext(url))
            _complete(item)
        except DeadlineReached:
            break
        except Exception as e:
            _failure(item, e, stats, "discovery")
        _save_checkpoint(sb, state)
    visit_submissions(False)
    if state["queues"]["submissions"] and all(i["status"] == "done" for q in ("submissions", "filings") for i in state["queues"][q].values()):
        state["sec_checked_at"] = max((i.get("completed_at", "") for q in ("submissions", "filings") for i in state["queues"][q].values()), default=None)


def _exhibit_names(page, filing):
    if not isinstance(page, str) or not re.search(r"<table", page, re.I):
        raise ValueError("SEC index is not a document listing")
    names, document_rows = [], 0
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S | re.I):
        cells = [re.sub(r"<[^>]+>", "", c).replace("&nbsp;", " ").strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S | re.I)]
        href = re.findall(r'''href=["']([^"']+)["']''', row)
        if len(cells) >= 4 and href:
            document_rows += 1
        if len(cells) >= 4 and cells[3].upper().startswith("EX-99") and href:
            names.append(href[0].rsplit("/", 1)[-1])
    if not document_rows:
        raise ValueError("SEC index has no document rows")
    if not names and "2.02" in (filing.get("i") or "") and filing.get("p"):
        names.append(filing["p"])
    return names


def _drop_other_site_work(state, root):
    """Forget unfinished discovery from a previously recorded site after the IR site changes.

    Pages and maps are only ever queued for the current root's host, so pending work under another host
    came from an earlier (wrong) site. Unstarted web documents found on those pages go too; stored
    documents and SEC work are untouched. Returns the number of entries removed.
    """
    host = urllib.parse.urlparse(root).netloc.lower()
    def other(url):
        return bool(url) and urllib.parse.urlparse(url).netloc.lower() != host
    removed = 0
    for queue, field in (("pages", "url"), ("maps", "url"), ("documents", "source_page")):
        for key, item in list(state["queues"][queue].items()):
            if item.get("search") == "site-search" or item["status"] not in ACTIVE:
                continue
            if queue == "documents" and item.get("source") != "web":
                continue
            if other(item.get(field)):
                del state["queues"][queue][key]
                removed += 1
    return removed


def _enumerate_web(state, site, deadline, stats, sb, page_budget):
    root = site["ir_url"]
    _drop_other_site_work(state, root)
    _enqueue(state, "pages", root, url=root)
    for search in ("quarterly results earnings", "financial information presentation supplement"):
        _enqueue(state, "maps", root + " " + search, url=root, search=search)
    for item in state["queues"]["maps"].values():
        if item.get("search") == "site-search":
            if item["status"] != "done":
                _complete(item)
            continue
        if item["status"] not in ACTIVE or _expired(deadline) or page_budget[0] <= 0:
            continue
        page_budget[0] -= 1
        try:
            links = firecrawl_map(item["url"], search=item["search"], limit=300, deadline=deadline)
            _add_links(state, links, root, root, state["scope"]["since_year"])
            if len(links) >= 300 and "map_result_limit:300" not in state["limits"]:
                state["limits"].append("map_result_limit:300")
            _complete(item)
        except DeadlineReached:
            break
        except Exception as e:
            _failure(item, e, stats, "discovery")
        _save_checkpoint(sb, state)
    visited = set()
    while page_budget[0] > 0 and not _expired(deadline):
        pending = [(k, v) for k, v in state["queues"]["pages"].items() if v["status"] in ACTIVE and k not in visited]
        if not pending:
            break
        key, item = pending[0]; visited.add(key); page_budget[0] -= 1
        try:
            data = _scrape(item["url"], deadline=deadline)
            html = data.get("html")
            if not isinstance(html, str) or not html.strip():
                raise ValueError("Empty archive HTML")
            _add_links(state, [{"url": u, "title": t} for u, t in anchors(html, item["url"])], root, item["url"], state["scope"]["since_year"])
            if re.search(r"load[ -]?more|infinite[ -]?scroll", html, re.I):
                marker = "dynamic_archive_requires_verification"
                if marker not in state["limits"]:
                    state["limits"].append(marker)
            _complete(item)
            if item["url"] == root:
                state["web_polled_at"] = item["completed_at"]
        except DeadlineReached:
            break
        except Exception as e:
            _failure(item, e, stats, "discovery")
        _save_checkpoint(sb, state)
    if all(i["status"] == "done" for q in ("maps", "pages") for i in state["queues"][q].values()):
        state["web_checked_at"] = max((i.get("completed_at", "") for q in ("maps", "pages") for i in state["queues"][q].values()), default=None)


def _correct_listed(sb, official, rows):
    """Brings stored documents in line with the company's own listing (type, fiscal period, release date). Rows are
    changed in place too, so later writes of the same row in this run keep the corrected fields."""
    fixes = ir_official.corrections(official, rows)
    changed = []
    for row in rows:
        diff = fixes.get(row["id"])
        if diff:
            row.update(diff)
            changed.append(row)
    for i in range(0, len(changed), 200):
        sb.upsert("ir_documents", changed[i:i + 200], "id")
    return len(changed)


def _queue_official(state, official, rows, extended=False):
    """Queues every listed file that is not stored yet, with the period, type and date the company states. A file
    found earlier by crawling keeps its queue entry and takes the listed fields; one that was blocked is tried again
    once per list version, because the list was checked against the live site."""
    stored = {ir_official.url_key(r.get("url")) for r in rows}
    queue = state["queues"]["documents"]
    by_key = {ir_official.url_key(u): u for u in queue}
    n = 0
    for rec in ir_official.storable(official, extended=extended):
        key = ir_official.url_key(rec["url"])
        if key in stored:
            continue
        fields = ir_official.queue_item(rec)
        url = by_key.get(key) or _url(rec["url"])
        if not url:
            continue
        item = queue.get(url)
        if item is None:
            if _enqueue(state, "documents", url, **{**fields, "url": url}) is not None:
                n += 1
            continue
        retry = item.get("status") == "blocked" and item.get("official") != fields["official"]
        item.update({k: v for k, v in fields.items() if k not in ("url", "source") and v is not None})
        if retry:
            item.update(status="pending", attempts=0)
            item.pop("error", None)
            n += 1
    return n


def _coverage(state, rows):
    scope = state["scope"]
    def count(queues, statuses):
        return sum(i["status"] in statuses for q in queues for i in state["queues"][q].values())
    discovery = ("submissions", "filings", "maps", "pages")
    pending = {"discovery": count(discovery, ACTIVE), "documents": count(("documents",), ACTIVE), "extractions": count(("extractions",), ACTIVE)}
    failed = {"discovery": count(discovery, ("retry", "blocked")), "documents": count(("documents",), ("retry", "blocked")), "extractions": count(("extractions",), ("retry", "blocked"))}
    extraction = dict.fromkeys(("verified", "limited", "empty", "unsupported", "failed", "unverified"), 0)
    for row in rows:
        status = row["extraction"]["status"]
        extraction[status if status in extraction else "unverified"] += 1
    def enumeration(queues, checked):
        if count(queues, ("retry", "blocked")):
            return "failed"
        if count(queues, ACTIVE):
            return "pending"
        return "complete" if checked else "unverified"
    sec = enumeration(("submissions", "filings"), state.get("sec_checked_at"))
    web = enumeration(("maps", "pages"), state.get("web_checked_at")) if scope.get("web") else "not_requested"
    if web == "complete":
        web = "link_traversal_unverified"
    unresolved = any(pending.values()) or any(failed.values()) or any(extraction[s] for s in ("limited", "empty", "unsupported", "failed")) or bool(state["limits"])
    status = "partial" if unresolved else "unverified" if scope.get("web") or sec != "complete" or extraction["unverified"] else "complete"
    return {"version": 1, "status": status, "scope": dict(scope), "enumeration": {"sec": sec, "web": web},
            "pending": pending, "failed": failed, "extraction": extraction, "total_documents": len(rows),
            "updated_at": now_iso(), "limits": list(state["limits"])}


def _repair_dates(sb, ticker, rows):
    """Use the exact existing accession+ticker relationship only; never retrieval time."""
    filings = sb.select("filings", f"select=accession,filed&ticker=eq.{ticker}")
    dates = {str(r.get("accession", "")).replace("-", ""): r.get("filed") for r in filings if r.get("filed")}
    changed = []
    for row in rows:
        if row.get("published"):
            continue
        try:
            source = urllib.parse.urlsplit(row.get("url") or "")
            if (source.scheme not in ("http", "https") or source.hostname not in ("sec.gov", "www.sec.gov")
                    or source.username is not None or source.password is not None):
                continue
            if source.port not in (None, 443 if source.scheme == "https" else 80):
                continue
            path = urllib.parse.unquote(source.path)
        except (TypeError, ValueError):
            continue
        if "\\" in path or any(part in (".", "..") for part in path.split("/")):
            continue
        match = re.match(r"^/Archives/edgar/data/[0-9]+/([0-9]{18})/[^/]", path)
        published = dates.get(match.group(1)) if match else None
        if published:
            dt.date.fromisoformat(published)
            row["published"] = published
            changed.append(dict(row))
    if changed:
        sb.upsert("ir_documents", changed, "id", batch=500)
    return len(changed)


def process_company(sb, ticker, name, cik, since_year=None, max_docs=90, refresh_site=False, sec_years=3, log=print,
                    fye="1231", web=True, deadline=None, repair_existing=False, max_pages=40, backfill=False, publish_only=False, io_deadline=None,
                    sec=True):
    """Resume private discovery/download/extraction queues; cap work, never coverage.

    Deadline is an absolute monotonic time. Existing successes never consume max_docs.
    repair_existing uses stored originals and relational dates only, without discovery providers.
    Backfill expands scope to every available archive and maintenance never narrows it.
    """
    if not re.fullmatch(r"[A-Za-z0-9.^_-]{1,20}", ticker):
        raise ValueError("Invalid ticker")
    # A per-company lease must never be installed on the job's shared client.
    worker = bounded_client(sb, io_deadline if io_deadline is not None else deadline)
    if isinstance(worker, IRClient):
        _timeout(worker.io_deadline)
    with AggregateLease(worker, name=f"ir-company:{ticker}") as lease:
        worker._aggregate_lease = lease
        return _process_company(worker, ticker, name, cik, since_year, max_docs, refresh_site, sec_years, log,
                                fye, web, deadline, repair_existing, max_pages, backfill, publish_only, sec)


def _process_company(sb, ticker, name, cik, since_year, max_docs, refresh_site, sec_years, log, fye, web, deadline,
                     repair_existing, max_pages, backfill, publish_only, sec=True):
    state = _read_checkpoint(sb, ticker)  # Fail closed before any storage or row mutation.
    requeue_blocked(state)
    requested_since = 1900 if backfill else since_year if since_year is not None else dt.date.today().year - 2
    requested_sec = "1900-01-01" if backfill else (dt.date.today() - dt.timedelta(days=int(365 * max(0, sec_years)))).isoformat()
    old_scope = state["scope"]
    expanded = bool(old_scope) and (requested_since < old_scope["since_year"] or requested_sec < old_scope["sec_since"])
    state["scope"] = {"web": bool(web or old_scope.get("web")), "sec": True,
                      "since_year": min(requested_since, old_scope.get("since_year", requested_since)),
                      "sec_since": min(requested_sec, old_scope.get("sec_since", requested_sec)),
                      "history": "available_archive" if backfill or old_scope.get("history") == "available_archive" else "recent"}
    if (repair_existing or publish_only) and old_scope and not backfill:
        state["scope"] = dict(old_scope)
        expanded = False
    stats = {"sec": 0, "web": 0, "web_text_only": 0, "failed": 0, "discovery_failed": 0, "download_failed": 0,
             "extraction_failed": 0, "repaired": 0, "dates_repaired": 0, "deferred": 0}
    existing = sb.select("ir_documents", f"select=*&ticker=eq.{ticker}")
    official = ir_official.load(ticker)
    if official and not publish_only:
        stats["official_corrected"] = _correct_listed(sb, official, existing)
        if web and not repair_existing:
            stats["official_queued"] = _queue_official(state, official, existing, extended=os.environ.get("FU_IR_EXTENDED") == "1")
    by_id = {r["id"]: r for r in existing}
    have_hashes = {r["sha256"] for r in existing if r.get("sha256")}
    site = (sb.select("ir_sites", f"select=*&ticker=eq.{ticker}") or [None])[0]
    # Poll source roots independently from old archive failures. Successful pages
    # keep their actual completed_at; no-op invocations cannot renew freshness.
    day = dt.date.today().isoformat()
    week = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not repair_existing and not publish_only:
        for queue in ("submissions", "filings", "maps", "pages"):
            for item in state["queues"][queue].values():
                activity = max(item.get("completed_at", ""), item.get("attempted_at", ""))
                sec_poll_due = queue == "submissions" and item.get("root") and activity[:10] < day
                web_poll_due = web and queue in ("maps", "pages") and item["status"] == "done" and activity < week
                if refresh_site or expanded or sec_poll_due or web_poll_due:
                    item.update(status="pending", attempts=0)
                    if queue in ("submissions", "filings"):
                        state["sec_checked_at"] = None
                    else:
                        state["web_checked_at"] = None
    if refresh_site:
        for queue in ("documents", "extractions"):
            for item in state["queues"][queue].values():
                if item["status"] == "blocked":
                    item.update(status="pending", attempts=0)
    # Missing text and previously failed extraction repairs are independent of discovery cooldown.
    for row in sorted(existing, key=lambda r: bool(r.get("text_path"))):
        outcome = state["outcomes"].get(row["id"], {})
        stale_workbook = (repair_existing and row.get("ext") in (".xls", ".xlsx", ".xlsm")
                          and outcome.get("workbook_version") != WORKBOOK_EXTRACTION_VERSION)
        if not row.get("text_path") or (repair_existing and (outcome.get("status") != "verified" or stale_workbook)):
            task = _enqueue(state, "extractions", row["id"], id=row["id"])
            if stale_workbook and task is not None and task.get("workbook_version") != WORKBOOK_EXTRACTION_VERSION:
                task.update(status="pending", attempts=0, workbook_version=WORKBOOK_EXTRACTION_VERSION)
                task.pop("error", None)
    if repair_existing:
        stats["dates_repaired"] = _repair_dates(sb, ticker, existing)
    elif not publish_only:
        # Separate page budget from document budget so large sites do not starve downloads.
        page_budget = [max(0, int(max_pages))]
        discovery_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / 2 if deadline is not None else None
        # sec=False is the web lane of the bulk backfill: SEC's request budget is spent by a separate lane.
        if sec:
            _enumerate_sec(state, ticker, cik, discovery_deadline, stats, sb, page_budget)
        if web and not _expired(discovery_deadline):
            if not site or not site.get("ir_url") or refresh_site:
                task = _enqueue(state, "maps", "site-search", url="", search="site-search")
                if task and task["status"] in ACTIVE:
                    try:
                        candidates = ir_candidates(name, ticker, deadline=discovery_deadline)
                        if not candidates:
                            raise ValueError("No investor relations site found")
                        site = {"ticker": ticker, "ir_url": candidates[0], "discovered_at": now_iso()}
                        sb.upsert("ir_sites", [site], "ticker")
                        _complete(task)
                    except DeadlineReached:
                        pass
                    except Exception as e:
                        _failure(task, e, stats, "discovery")
            if site and site.get("ir_url"):
                _enumerate_web(state, site, discovery_deadline, stats, sb, [max(0, int(max_pages))])
    _save_checkpoint(sb, state)
    remaining = max(0, int(max_docs))
    pending_downloads = any(item["status"] in ACTIVE and doc_id(url) not in by_id and (web or item.get("source") == "sec")
                            and (sec or item.get("source") != "sec")
                            for url, item in state["queues"]["documents"].items())
    download_reserve = max(1, remaining // 2) if remaining and pending_downloads and not repair_existing else 0
    repair_budget = remaining - download_reserve
    repair_deadline = time.monotonic() + max(0, deadline - time.monotonic()) / 2 if download_reserve and deadline is not None else deadline
    # Repairs are attempted once per invocation, preserving originals and source IDs.
    for did, item in list(state["queues"]["extractions"].items()):
        if item["status"] not in ACTIVE or did not in by_id or repair_budget <= 0 or _expired(repair_deadline) or publish_only:
            continue
        remaining -= 1
        repair_budget -= 1
        row = by_id[did]
        quality = None
        try:
            body = _stored_object(sb, "ir", row["storage_path"])
            if body is None:
                raise ValueError("Stored original missing")
            if row.get("sha256") and hashlib.sha256(body).hexdigest() != row["sha256"]:
                raise ValueError("Stored original hash mismatch")
            text = extract_text(body, row["ext"], deadline=repair_deadline)
            quality = {**text["extraction"], "attempted_at": now_iso()}
            state["outcomes"][did] = quality
            if quality.get("nonempty") and (quality["status"] == "verified" or not row.get("text_path")):
                payload = json.dumps(text, default=str)
                # Preserve every previous text object, including a complete legacy extraction.
                digest = hashlib.sha256(payload.encode()).hexdigest()[:16]
                path = f"{ticker}/text/{did}-v2-{digest}.json"
                sb.put_object("ir", path, payload, "application/json", "public, max-age=31536000, immutable")
                stamp = now_iso()
                sb.upsert("ir_documents", [{**row, "text_path": path, "extracted_at": stamp,
                           "pages": len(text.get("pages", text.get("sheets", {})))}], "id")
                stats["repaired"] += 1
            if quality["status"] != "verified":
                raise ValueError("Extraction " + quality["status"])
            _complete(item)
        except DeadlineReached:
            break
        except Exception as e:
            if quality is None:
                state["outcomes"][did] = {"status": "failed", "nonempty": False, "limits": [], "errors": [str(e)[:200]], "attempted_at": now_iso()}
                if row.get("ext") in (".xls", ".xlsx", ".xlsm"):
                    state["outcomes"][did]["workbook_version"] = WORKBOOK_EXTRACTION_VERSION
            _failure(item, e, stats, "extraction")
        _save_checkpoint(sb, state)
    # Completed/known document candidates do not spend the work cap.
    def document_date(pair):
        url, item = pair
        return item.get("published") or item.get("period_end") or f"{period_hint(url, item.get('title', ''))[1] or 0:04d}-00-00"
    for url, item in sorted(state["queues"]["documents"].items(), key=document_date, reverse=True):
        did = doc_id(url)
        if did in by_id:
            _complete(item)
            continue
        if (item["status"] not in ACTIVE or remaining <= 0 or _expired(deadline) or repair_existing or publish_only or (not web and item.get("source") == "web")
                or (not sec and item.get("source") == "sec")):
            continue
        remaining -= 1
        try:
            title = item.get("title")
            pdf_hint = item.get("ext_hint") in (".pdf", ".pdf?")
            try:
                body = _sec_request(url, deadline=deadline, raw=True) if item["source"] == "sec" else download(url, deadline=deadline)
            except HTTPError as e:
                alt = text_copy(url, deadline=deadline) if item["source"] == "web" and pdf_hint and e.code in (401, 403, 406, 429, 503) else None
                if alt is None:
                    raise
                body, title = alt, TEXT_COPY_PREFIX + (title or "")
            ext = sniff_ext(body, url) or item.get("ext_hint")
            if ext in (".html", ".htm") and item.get("ext_hint") not in (".html", ".htm") and not (title or "").startswith(TEXT_COPY_PREFIX):
                alt = text_copy(url, deadline=deadline) if item["source"] == "web" and pdf_hint else None
                if alt is None:
                    raise ValueError("Downloaded HTML instead of the linked document")
                body, title, ext = alt, TEXT_COPY_PREFIX + (title or ""), ".html"
            if not body or not ext or ext in (".zip", ".ole", ".pdf?"):
                raise ValueError("Unsupported or empty downloaded original")
            stored = store_document(sb, ticker, url, body, ext, title, item.get("source_page"), have_hashes,
                                    kind_hint=item.get("kind_hint"), log=log, fye=fye,
                                    ref_date=dt.date.fromisoformat(item["ref_date"]) if item.get("ref_date") else None,
                                    published=item.get("published"), outcomes=state["outcomes"], deadline=deadline,
                                    period=(item.get("fiscal_label"), item.get("period_end")) if item.get("official") else None)
            if stored:
                stats[item["source"]] += 1
                by_id[did] = {"id": did}
                quality = state["outcomes"][did]
                if quality["status"] != "verified":
                    task = _enqueue(state, "extractions", did, id=did)
                    if task:
                        _failure(task, ValueError("Extraction " + quality["status"]), stats, "extraction")
            _complete(item)
        except DeadlineReached:
            break
        except Exception as e:
            _failure(item, e, stats, "download")
        _save_checkpoint(sb, state)
    rows = sb.select("ir_documents", f"select=*&ticker=eq.{ticker}")
    for row in rows:
        for key in ("period_end", "published", "extracted_at"):
            row.setdefault(key, None)
        row["extraction"] = state["outcomes"].get(row["id"], {"status": "unverified", "limits": [], "errors": []})
    rows.sort(key=lambda r: (r.get("period_end") or r.get("published") or "", r.get("published") or "", r["id"]), reverse=True)
    coverage = _coverage(state, rows)
    if _expired(deadline) and not repair_existing and not state.get("sec_checked_at") and not coverage["pending"]["discovery"]:
        coverage["pending"]["discovery"] = 1
        coverage["status"] = "partial"
    state["coverage"] = coverage
    _save_checkpoint(sb, state)
    index = {"ticker": ticker, "generated": now_iso(), "site": site.get("ir_url") if site else None,
             "results_page": site.get("results_url") if site else None, "documents": rows, "coverage": coverage}
    if official:
        # What the company lists on its own pages, by fiscal period, and which stored document holds each file.
        listed = ir_official.coverage(official, rows, extended=os.environ.get("FU_IR_EXTENDED") == "1")
        # Extended material that was not requested stays a count; its files are in etl/ir_sources/{ticker}.json.
        index["official"] = {"version": official["version"], "pages": sorted({b["page"] for b in official["built_from"] if b.get("page")}),
                             **{**listed, "files": [f for f in listed["files"] if f["state"] != "not_collected"]}}
        stats["official"] = {k: v for k, v in listed.items() if k != "files"}
    sb.put_json(f"ir/{ticker}.json", index, cache="max-age=600")
    stats.update(total=len(rows), coverage=coverage, status=coverage["status"], deferred=sum(coverage["pending"].values()))
    return stats


def store_document(sb, ticker, url, body, ext, title, source_page, have_hashes, kind_hint=None, log=print, fye=None, ref_date=None, published=None, outcomes=None, deadline=None,
                   period=None):
    """Stores the original file and its extracted text, classifies it and writes the ir_documents row.

    period is (fiscal label, period end) as the company states it on its own results page (etl/ir_official.py). When
    given, the period and kind_hint are taken as stated: nothing is inferred from the file name, the retrieval date
    or the first page."""
    did = doc_id(url)
    sha = hashlib.sha256(body).hexdigest()
    if sha in have_hashes:
        return False
    path = f"{ticker}/{did}-{safe_name(url, ext)}"
    sb.put_object("ir", path, body, CT.get(ext, "application/octet-stream"), "public, max-age=31536000, immutable")
    text = None
    try:
        text = extract_text(body, ext, deadline=deadline)
    except Exception as e:
        text = {"kind": "pages", "pages": [], "extraction": {"status": "failed", "nonempty": False, "limits": [], "errors": [str(e)[:200]]}}
        log(f"  {ticker} text {url[-60:]} failed: {str(e)[:100]}")
    quality = {**text["extraction"], "attempted_at": now_iso()}
    if outcomes is not None:
        outcomes[did] = quality
    text_path, npages, first = None, None, ""
    if quality.get("nonempty"):
        text_path = f"{ticker}/text/{did}.json"
        sb.put_object("ir", text_path, json.dumps(text, default=str), "application/json", "public, max-age=31536000, immutable")
        if text["kind"] == "pages":
            npages = len(text["pages"])
            first = "\n".join(text["pages"][:2])[:4000]
        else:
            npages = len(text["sheets"])
            first = json.dumps(list(text["sheets"].items())[:1], default=str)[:3000]
    period_end = None
    if period is not None:
        label, period_end = period
    elif ref_date and fye:
        from .fundamentals import fye_at
        fye = fye_at(ticker, fye, ref_date.isoformat())
        # an 8-K exhibit belongs to the latest fiscal quarter that ended before the filing, unless its file name
        # names an earlier quarter (recast tables such as "q32024pressreleasetables.htm" filed after the quarter)
        label, pe = named_quarter(fye, url.rsplit("/", 1)[-1], ref_date, PERIOD_ENDS.get(ticker)) or \
            quarter_for_date(fye, ref_date, PERIOD_ENDS.get(ticker))
        period_end = pe.isoformat() if pe else None
        if pe:
            from .fundamentals import period_label
            label = period_label(ticker, fye, period_end, "Q")
    else:
        label, year = period_hint(url, title)
        when = date_in(title)
        if not label and when and fye:
            label, pe = quarter_for_date(fye, when, PERIOD_ENDS.get(ticker))
            period_end = pe.isoformat() if pe else None
        if not label:
            label, _ = first_page_period(first)
    dtype = kind_hint or classify(url, title)
    if dtype in ("other", "release") and first and period is None:
        c2 = classify("", first[:500])
        if c2 in ("supplement", "presentation", "remarks", "transcript"):
            dtype = c2
    sb.upsert("ir_documents", [{"id": did, "ticker": ticker, "url": url, "title": (title or safe_name(url, ext))[:400], "doc_type": dtype,
                                "fiscal_label": label, "period_end": period_end, "ext": ext, "content_type": CT.get(ext), "bytes": len(body), "sha256": sha,
                                "storage_path": path, "source_page": source_page, "pages": npages, "text_path": text_path, "published": published,
                                "extracted_at": now_iso() if text_path else None, "fetched_at": now_iso()}], "id")
    have_hashes.add(sha)
    return True
