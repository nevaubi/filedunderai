"""Investor relations documents: find each company's IR site, list its result documents (releases, presentations,
supplementary financial data, prepared remarks, transcripts), download them into the `ir` storage bucket, and extract
page/sheet text so values can be cited and highlighted at the exact page or cell."""
import datetime as dt
import hashlib
import io
import json
import os
import re
import urllib.parse

from .common import HTTPError, now_iso, request, sec_get, sec_get_raw
from .docs import firecrawl

DOC_EXT = (".pdf", ".xlsx", ".xls", ".xlsm", ".csv", ".pptx", ".ppt", ".docx", ".doc")
RESULT_HINT = re.compile(r"(quarter|earning|result|financial-?(info|data|result|report)|supplement|presentation|press-?release|news|events|reports?)", re.I)
DOC_HINT = re.compile(r"(earn|result|quarter|q[1-4]|fy\d|supplement|financial|fin[-_ ]?data|presentation|slides|deck|release|press|remarks|transcript|metrics|kpi|fact[-_ ]?sheet|annual|10-?k|10-?q|investor|ir[-_])", re.I)
NOISE = re.compile(r"(securities and exchange commission|form 8-?k|form 10|sec filing|10-?q[-_ ]|10-?k[-_ ]|form[-_ ]?10|proxy|governance|charter|code[-_ ]of|ethic|esg|sustainab|csr|climate|tcfd|diversity|policy|bylaws|guideline|privacy|cookie|terms|brochure|recruit|career|dividend[-_]?reinvest|tax[-_]?(form|basis)|8937|w-?9|donation|political|esop)", re.I)
UA = "Mozilla/5.0 (compatible; FiledUnder/1.0; +https://filedunder.ai)"


def tavily(query, max_results=8, include_domains=None):
    body = {"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": max_results, "search_depth": "basic"}
    if include_domains:
        body["include_domains"] = include_domains
    return json.loads(request("https://api.tavily.com/search", method="POST", data=body, timeout=60, retries=2)).get("results", [])


def firecrawl_map(url, search=None, limit=400):
    body = {"url": url, "limit": limit, "includeSubdomains": True}
    if search:
        body["search"] = search
    out = request("https://api.firecrawl.dev/v2/map", method="POST", data=body,
                  headers={"Authorization": f"Bearer {os.environ['FIRECRAWL_API_KEY']}"}, timeout=180, retries=2)
    d = json.loads(out)
    links = d.get("links") or (d.get("data") or {}).get("links") or []
    return [l if isinstance(l, dict) else {"url": l} for l in links]


def ir_candidates(name, ticker):
    """Searches for the company's investor relations site. Returns ranked root URLs whose host matches the company."""
    res = tavily(f"{name} ({ticker}) investor relations quarterly results", max_results=10)
    res += tavily(f"{name} investor relations website", max_results=8)
    score = {}
    for i, r in enumerate(res):
        u = urllib.parse.urlparse(r["url"])
        host = u.netloc.lower()
        if any(x in host for x in ("quartr", "daloopa", "stocktitan", "tavily", "q4cdn", "annualreports", "last10k", "craft.co", "companiesmarketcap", "ycharts",
                                   "digrin", "dividend", "theglobeandmail", "investorplace", "benzinga", "gurufocus", "wallstreetzen", "koyfin", "alphaspread", "sec.gov", "nasdaq.com", "nyse.com", "yahoo", "seekingalpha", "marketwatch", "reuters", "bloomberg", "wsj", "cnbc",
                                   "fool.com", "zacks", "macrotrends", "stockanalysis", "wikipedia", "linkedin", "businesswire", "prnewswire", "globenewswire",
                                   "morningstar", "investing.com", "barrons", "tipranks", "marketbeat", "simplywall", "gurufocus", "finviz")):
            continue
        s = 10 - i
        brand = [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in ("inc", "corp", "the", "and", "company", "group", "holdings", "international")]
        if ticker.lower().replace("-", "") in host.replace("-", "") or any(w in host.replace("-", "") for w in brand[:2]):
            s += 25
        else:
            s -= 15
        if re.search(r"(^|\.)(investors?|ir|investor-relations)\.", host) or re.search(r"/(investors?|investor-relations|ir)(/|$)", u.path, re.I):
            s += 15
        root = f"{u.scheme}://{host}"
        m = re.search(r"^(.*?/(?:investors?|investor-relations|ir))(?:/|$)", u.path, re.I)
        if m and not re.search(r"(^|\.)(investors?|ir)\.", host):
            root += m.group(1)
        score[root] = score.get(root, 0) + s
    ranked = sorted(score, key=lambda k: -score[k])
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


def discover_documents(ir_root, log=print):
    """Maps the IR site, then scrapes the result/financials pages for document links. Returns (docs, pages)."""
    links = []
    for search in ("quarterly results earnings", "financial information presentation supplement"):
        try:
            links += firecrawl_map(ir_root, search=search, limit=300)
        except Exception as e:
            log(f"  map {ir_root} failed: {str(e)[:120]}")
    docs, pages = {}, []
    for l in links:
        u = l.get("url") or ""
        if _ext(u):
            docs.setdefault(u, l.get("title") or "")
        elif RESULT_HINT.search(u) and len(pages) < 40:
            pages.append(u)
    # rank pages: quarterly-results style pages first
    pages = sorted(set(pages), key=lambda u: (0 if re.search(r"quarter|earning|result", u, re.I) else 1, len(u)))[:4]
    for p in pages + [ir_root]:
        try:
            d = firecrawl(p, "html")
            for u, t in anchors(d.get("html", ""), p):
                if _ext(u):
                    if t and len(t) > len(docs.get(u, "")):
                        docs[u] = t
                    else:
                        docs.setdefault(u, t)
        except Exception as e:
            log(f"  scrape {p} failed: {str(e)[:120]}")
    return docs, pages


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


def download(url):
    body = request(url, headers={"User-Agent": UA, "Accept": "*/*"}, timeout=120, retries=2, raw=True)
    return body


def sniff_ext(body, url):
    if body[:5] == b"%PDF-":
        return ".pdf"
    if body[:2] == b"PK":
        low = url.lower()
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
        return ".xls" if url.lower().endswith(".xls") else ".doc" if url.lower().endswith(".doc") else ".ppt" if url.lower().endswith(".ppt") else ".ole"
    if url.lower().endswith(".csv"):
        return ".csv"
    return None


CT = {".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
      ".xls": "application/vnd.ms-excel", ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation", ".ppt": "application/vnd.ms-powerpoint",
      ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document", ".doc": "application/msword", ".csv": "text/csv",
      ".htm": "text/plain; charset=utf-8", ".html": "text/plain; charset=utf-8"}


def extract_text(body, ext):
    """Page or sheet text for citation: {"kind": "pages", "pages": [...]} or {"kind": "sheets", "sheets": {...}}."""
    if ext == ".pdf":
        import pdfplumber
        pages = []
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            for p in pdf.pages[:400]:
                try:
                    pages.append(p.extract_text(x_tolerance=1.5, y_tolerance=3) or "")
                except Exception:
                    pages.append("")
        return {"kind": "pages", "pages": pages}
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(body), data_only=True, read_only=True)
        sheets = {}
        for ws in wb.worksheets[:60]:
            rows = []
            for r_i, row in enumerate(ws.iter_rows(values_only=True)):
                if r_i > 600:
                    break
                vals = ["" if v is None else (round(v, 6) if isinstance(v, float) else v) for v in row[:60]]
                while vals and vals[-1] == "":
                    vals.pop()
                rows.append(vals)
            while rows and not rows[-1]:
                rows.pop()
            sheets[ws.title] = rows
        return {"kind": "sheets", "sheets": sheets}
    if ext == ".pptx":
        from pptx import Presentation
        prs = Presentation(io.BytesIO(body))
        pages = []
        for slide in prs.slides:
            parts = []
            for sh in slide.shapes:
                if sh.has_text_frame:
                    parts.append(sh.text_frame.text)
                if getattr(sh, "has_table", False) and sh.has_table:
                    for row in sh.table.rows:
                        parts.append(" | ".join(c.text for c in row.cells))
            pages.append("\n".join(parts))
        return {"kind": "pages", "pages": pages}
    if ext == ".docx":
        import docx
        d = docx.Document(io.BytesIO(body))
        text = "\n".join(p.text for p in d.paragraphs)
        for t in d.tables:
            for row in t.rows:
                text += "\n" + " | ".join(c.text for c in row.cells)
        return {"kind": "pages", "pages": [text]}
    if ext in (".htm", ".html"):
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(body, "lxml")
        for t in soup(["script", "style"]):
            t.decompose()
        txt = soup.get_text("\n", strip=True)
        return {"kind": "pages", "pages": [txt]}
    if ext == ".csv":
        return {"kind": "sheets", "sheets": {"csv": [l.split(",") for l in body.decode("utf-8", "replace").splitlines()[:2000]]}}
    return None


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


def quarter_for_date(fye, d, ends_known=None):
    """(label, quarter end) of the latest fiscal quarter that ended at least three days before date d. Uses the
    company's actual period end dates when known (52/53-week years), else calendar quarter ends from the FYE."""
    from .fundamentals import fy_label
    if ends_known:
        cands = [e for e in ends_known if e <= d - dt.timedelta(days=3)]
        if cands:
            e = max(cands)
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


def process_company(sb, ticker, name, cik, since_year=None, max_docs=90, refresh_site=False, sec_years=3, log=print, fye="1231"):
    """Collects a company's results documents from two places and stores every file in the `ir` bucket:
    8-K exhibits on EDGAR (earnings releases, supplements, slides; full history for sec_years) and the company's IR
    website (presentations, supplementary workbooks, prepared remarks and transcripts that are not filed)."""
    from .jobs import periodic_filings
    since_year = since_year or dt.date.today().year - 2
    existing = sb.select("ir_documents", f"select=id,sha256&ticker=eq.{ticker}")
    have_ids = {r["id"] for r in existing}
    have_hashes = {r["sha256"] for r in existing if r.get("sha256")}
    stats = {"sec": 0, "web": 0, "web_text_only": 0, "failed": 0}
    # 1. EDGAR 8-K exhibits
    cut = (dt.date.today() - dt.timedelta(days=int(365 * sec_years))).isoformat()
    try:
        eights = [f for f in periodic_filings(cik, cut, forms=("8-K", "8-K/A")) if "2.02" in (f.get("i") or "") or "7.01" in (f.get("i") or "")]
        stats["sec"] = sec_exhibits(sb, ticker, cik, eights, have_ids, have_hashes, log=log, fye=fye)
    except Exception as e:
        log(f"  {ticker} 8-K exhibits failed: {str(e)[:160]}")
    # 2. IR website
    site = (sb.select("ir_sites", f"select=*&ticker=eq.{ticker}") or [None])[0]
    if not site or refresh_site or not site.get("ir_url"):
        cands = ir_candidates(name, ticker)
        site = {"ticker": ticker, "ir_url": cands[0] if cands else None, "discovered_at": now_iso(), "notes": json.dumps({"candidates": cands[:5]})}
        sb.upsert("ir_sites", [site], "ticker")
    pages = []
    if site.get("ir_url"):
        docs, pages = discover_documents(site["ir_url"], log=log)
        if pages:
            sb.upsert("ir_sites", [{"ticker": ticker, "ir_url": site["ir_url"], "results_url": pages[0]}], "ticker")
        cand = [(u, t) for u, t in docs.items() if keep_doc(u, t, since_year) and classify(u, t) != "other"]
        prio = {"supplement": 0, "presentation": 1, "remarks": 2, "release": 3, "transcript": 4, "annual_report": 5, "other": 6}
        cand.sort(key=lambda ut: (-(period_hint(*ut)[1] or 0), prio[classify(*ut)]))
        for url, title in cand[:max_docs]:
            if doc_id(url) in have_ids:
                continue
            try:
                body = download(url)
                ext = sniff_ext(body, url)
                if not ext or ext in (".zip", ".ole") or len(body) < 1500:
                    stats["failed"] += 1
                    continue
                if store_document(sb, ticker, url, body, ext, title, site.get("results_url") or site["ir_url"], have_hashes, log=log, fye=fye):
                    stats["web"] += 1
                    have_ids.add(doc_id(url))
            except HTTPError as e:
                if e.code in (401, 403, 429) and stats["web_text_only"] < 25 and classify(url, title) in ("presentation", "supplement", "remarks", "transcript", "release"):
                    # the host refuses automated downloads: keep the text through Firecrawl, flagged as text-only
                    try:
                        md, _ = firecrawl_text(url)
                        if len(md) > 500:
                            body = md.encode("utf-8")
                            did = doc_id(url)
                            path = f"{ticker}/{did}-{safe_name(url, '.md')}"
                            sb.put_object("ir", path, body, "text/markdown; charset=utf-8", "public, max-age=31536000, immutable")
                            text_path = f"{ticker}/text/{did}.json"
                            sb.put_object("ir", text_path, json.dumps({"kind": "pages", "pages": [md]}), "application/json", "public, max-age=31536000, immutable")
                            label, _ = period_hint(url, title)
                            if not label:
                                label, _ = first_page_period(md[:3000])
                            dtype = classify(url, title)
                            if dtype == "other":
                                dtype = classify("", md[:500])
                            sb.upsert("ir_documents", [{"id": did, "ticker": ticker, "url": url, "title": ((title or "") + " [text only: host blocks direct download]")[:400],
                                                        "doc_type": dtype, "fiscal_label": label, "ext": ".md", "content_type": "text/markdown", "bytes": len(body),
                                                        "sha256": hashlib.sha256(body).hexdigest(), "storage_path": path, "source_page": site.get("results_url") or site["ir_url"],
                                                        "pages": 1, "text_path": text_path, "fetched_at": now_iso()}], "id")
                            stats["web_text_only"] += 1
                            have_ids.add(did)
                    except Exception as e2:
                        stats["failed"] += 1
                        log(f"  {ticker} firecrawl text {url[-60:]} failed: {str(e2)[:100]}")
                else:
                    stats["failed"] += 1
            except Exception as e:
                stats["failed"] += 1
                log(f"  {ticker} download {url[-80:]} failed: {str(e)[:120]}")
    rows = sb.select("ir_documents", f"select=id,url,title,doc_type,fiscal_label,ext,bytes,storage_path,pages,text_path,fetched_at,source_page&ticker=eq.{ticker}")
    rows.sort(key=lambda r: (r["fetched_at"]), reverse=True)
    sb.put_json(f"ir/{ticker}.json", {"ticker": ticker, "generated": now_iso(), "site": site.get("ir_url") if site else None,
                                      "results_page": pages[0] if pages else None, "documents": rows}, cache="max-age=600")
    stats["total"] = len(rows)
    return stats


EX99 = re.compile(r"(ex[-_]?99|exhibit[-_]?99|dex99|ex99|exh99)", re.I)


def store_document(sb, ticker, url, body, ext, title, source_page, have_hashes, kind_hint=None, log=print, fye=None, ref_date=None):
    """Stores the original file and its extracted text, classifies it and writes the ir_documents row."""
    did = doc_id(url)
    sha = hashlib.sha256(body).hexdigest()
    if sha in have_hashes:
        return False
    path = f"{ticker}/{did}-{safe_name(url, ext)}"
    sb.put_object("ir", path, body, CT.get(ext, "application/octet-stream"), "public, max-age=31536000, immutable")
    text = None
    try:
        text = extract_text(body, ext)
    except Exception as e:
        log(f"  {ticker} text {url[-60:]} failed: {str(e)[:100]}")
    text_path, npages, first = None, None, ""
    if text:
        text_path = f"{ticker}/text/{did}.json"
        sb.put_object("ir", text_path, json.dumps(text, default=str), "application/json", "public, max-age=31536000, immutable")
        if text["kind"] == "pages":
            npages = len(text["pages"])
            first = "\n".join(text["pages"][:2])[:4000]
        else:
            npages = len(text["sheets"])
            first = json.dumps(list(text["sheets"].items())[:1], default=str)[:3000]
    period_end = None
    if ref_date and fye:
        # an 8-K exhibit belongs to the latest fiscal quarter that ended before the filing
        label, pe = quarter_for_date(fye, ref_date, PERIOD_ENDS.get(ticker))
        period_end = pe.isoformat() if pe else None
    else:
        label, year = period_hint(url, title)
        when = date_in(title)
        if not label and when and fye:
            label, pe = quarter_for_date(fye, when, PERIOD_ENDS.get(ticker))
            period_end = pe.isoformat() if pe else None
        if not label:
            label, _ = first_page_period(first)
    dtype = kind_hint or classify(url, title)
    if dtype in ("other", "release") and first:
        c2 = classify("", first[:500])
        if c2 in ("supplement", "presentation", "remarks", "transcript"):
            dtype = c2
    sb.upsert("ir_documents", [{"id": did, "ticker": ticker, "url": url, "title": (title or safe_name(url, ext))[:400], "doc_type": dtype,
                                "fiscal_label": label, "period_end": period_end, "ext": ext, "content_type": CT.get(ext), "bytes": len(body), "sha256": sha,
                                "storage_path": path, "source_page": source_page, "pages": npages, "text_path": text_path, "fetched_at": now_iso()}], "id")
    have_hashes.add(sha)
    return True


def sec_exhibits(sb, ticker, cik, filings, have_ids, have_hashes, log=print, fye=None):
    """Earnings releases, supplements and presentations filed as 8-K exhibits (Items 2.02 and 7.01)."""
    stored = 0
    for f in filings:
        folder = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{f['a'].replace('-', '')}/"
        try:
            idx = json.loads(sec_get(folder + "index.json", timeout=40))
        except Exception as e:
            log(f"  {ticker} 8-K index {f['a']} failed: {str(e)[:100]}")
            continue
        for it in idx.get("directory", {}).get("item", []):
            name = it["name"]
            low = name.lower()
            if not (EX99.search(low) or (low.endswith((".pdf", ".xlsx", ".xls")) and "ex" in low)):
                continue
            if not low.endswith((".htm", ".html", ".pdf", ".xlsx", ".xls", ".pptx")):
                continue
            url = folder + name
            if doc_id(url) in have_ids:
                continue
            try:
                body = sec_get_raw(url)
                ext = sniff_ext(body, url) or (".htm" if low.endswith((".htm", ".html")) else None)
                if not ext or len(body) < 1500:
                    continue
                items = f.get("i") or ""
                title = f"8-K {f['d']} {name} (Items {items})"
                hint = "release" if "2.02" in items and re.search(r"99[-_.]?1\b|99\.?1[^0-9]|ex991", low) else None
                ref = dt.date.fromisoformat(f["d"]) if "2.02" in items else None
                if store_document(sb, ticker, url, body, ext, title, folder, have_hashes, kind_hint=hint, log=log, fye=fye, ref_date=ref):
                    stored += 1
                    have_ids.add(doc_id(url))
            except Exception as e:
                log(f"  {ticker} exhibit {name} failed: {str(e)[:100]}")
    return stored


def firecrawl_text(url):
    """Text of a document through Firecrawl, used when the host refuses direct downloads."""
    d = firecrawl(url, "markdown")
    md = d.get("markdown") or ""
    pages = [p for p in re.split(r"\n(?=#+ |\f)", md)] if md else []
    return md, pages
